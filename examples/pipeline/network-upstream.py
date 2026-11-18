"""Controlled HTTP, TLS, DNS and socket endpoints outside the acceptance cluster."""

import hmac
import ipaddress
import json
import os
import socket
import socketserver
import ssl
import struct
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

RUN = os.environ["FIXTURE_ID"]
ADDRESS = socket.gethostbyname(socket.gethostname())
LOCK = threading.Lock()
EVENTS = deque(maxlen=2000)
RECORDS = {
    name + ".fixture.example.test": [ADDRESS] for name in ("allowed", "denied", "rebind", "mixed")
}


def event(kind, peer, **fields):
    value = {"time": time.time(), "fixture": RUN, "kind": kind, "peer": peer, **fields}
    with LOCK:
        EVENTS.append(value)
    print(json.dumps(value), flush=True)


class HTTP(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass

    def reply(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        return self.server.server_port == 9000 and hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + os.environ["CONTROL_TOKEN"]
        )

    def do_GET(self):
        path = urlsplit(self.path).path
        if self.server.server_port == 9000:
            if not self.authorized():
                return self.reply(403, {"error": "control identity required"})
            with LOCK:
                snapshot = list(EVENTS)
                records = dict(RECORDS)
            return self.reply(200, {"fixture": RUN, "events": snapshot, "dns": records})
        event("http", self.client_address[0], port=self.server.server_port, path=path)
        if path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://denied.fixture.example.test:8080/forbidden")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/slow":
            time.sleep(5)
        if path == "/stream":
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for _ in range(120):
                    self.wfile.write(b"4\r\nlive\r\n")
                    self.wfile.flush()
                    time.sleep(1)
            except (OSError, ssl.SSLError):
                event("stream-closed", self.client_address[0])
            self.close_connection = True
            return
        self.reply(200, {"fixture": RUN, "path": path, "port": self.server.server_port})

    def do_POST(self):
        if self.server.server_port == 9000 and not self.authorized():
            self.close_connection = True
            return self.reply(403, {"error": "control identity required"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.close_connection = True
            return self.reply(400, {"error": "invalid content length"})
        if not 0 <= length <= 1048576:
            return self.reply(413, {"error": "body too large"})
        raw = self.rfile.read(length)
        if self.server.server_port == 9000:
            if not self.authorized():
                return self.reply(403, {"error": "control identity required"})
            try:
                updates = json.loads(raw)
                if not isinstance(updates, dict) or len(updates) > 16:
                    raise ValueError("invalid DNS update")
                for name, addresses in updates.items():
                    if (
                        name not in RECORDS
                        or not isinstance(addresses, list)
                        or len(addresses) > 16
                    ):
                        raise ValueError("invalid DNS update")
                    updates[name] = [str(ipaddress.ip_address(value)) for value in addresses]
            except (ValueError, TypeError):
                return self.reply(400, {"error": "invalid DNS update"})
            with LOCK:
                RECORDS.update(updates)
            return self.reply(200, {"updated": sorted(updates)})
        event(
            "http-post",
            self.client_address[0],
            port=self.server.server_port,
            path=urlsplit(self.path).path,
            length=length,
        )
        self.reply(200, {"fixture": RUN, "length": length})


def dns_response(raw, peer):
    if len(raw) < 12 or struct.unpack("!H", raw[4:6])[0] != 1:
        return b""
    offset, labels = 12, []
    while offset < len(raw):
        length = raw[offset]
        offset += 1
        if length == 0:
            break
        if length > 63 or offset + length > len(raw):
            return b""
        labels.append(raw[offset : offset + length].decode("ascii"))
        offset += length
    if offset + 4 > len(raw):
        return b""
    query_type, query_class = struct.unpack("!HH", raw[offset : offset + 4])
    name = ".".join(labels).lower()
    with LOCK:
        addresses = RECORDS.get(name)
    answers = []
    for value in addresses or []:
        address = ipaddress.ip_address(value)
        record_type = 1 if address.version == 4 else 28
        if query_type == record_type and query_class == 1:
            answers.append(
                b"\xc0\x0c"
                + struct.pack("!HHIH", record_type, 1, 1, len(address.packed))
                + address.packed
            )
    event("dns", peer, name=name, query_type=query_type, answers=addresses or [])
    return (
        raw[:2]
        + struct.pack("!HHHHH", 0x8180 if addresses is not None else 0x8183, 1, len(answers), 0, 0)
        + raw[12 : offset + 4]
        + b"".join(answers)
    )


class DNSUDP(socketserver.BaseRequestHandler):
    def handle(self):
        raw, transport = self.request
        response = dns_response(raw, self.client_address[0])
        if response:
            transport.sendto(response, self.client_address)


class DNSTCP(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        header = self.rfile.read(2)
        if len(header) != 2:
            return
        response = dns_response(
            self.rfile.read(struct.unpack("!H", header)[0]), self.client_address[0]
        )
        self.wfile.write(struct.pack("!H", len(response)) + response)


class EchoTCP(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(180)
        event("tcp-open", self.client_address[0])
        try:
            while raw := self.request.recv(8192):
                self.request.sendall(raw)
        finally:
            event("tcp-close", self.client_address[0])


class EchoUDP(socketserver.BaseRequestHandler):
    def handle(self):
        raw, transport = self.request
        event("udp", self.client_address[0], length=len(raw))
        transport.sendto(raw, self.client_address)


def run():
    servers = [ThreadingHTTPServer(("0.0.0.0", port), HTTP) for port in (8080, 8443, 9000)]
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/fixture/tls.crt", "/fixture/tls.key")
    servers[1].socket = context.wrap_socket(servers[1].socket, server_side=True)
    servers += [
        socketserver.ThreadingUDPServer(("0.0.0.0", 53), DNSUDP),
        socketserver.ThreadingTCPServer(("0.0.0.0", 53), DNSTCP),
        socketserver.ThreadingTCPServer(("0.0.0.0", 9002), EchoTCP),
        socketserver.ThreadingUDPServer(("0.0.0.0", 9003), EchoUDP),
    ]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    event("ready", ADDRESS)
    threading.Event().wait()


if __name__ == "__main__":
    run()
