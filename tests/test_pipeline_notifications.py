"""Journal-backed notifications survive acknowledgement loss without duplicate admin events."""

from sqlalchemy import func, select

from goblin_king.pipeline_notifications import deliver_notifications
from goblin_king.pipeline_settings import PipelineSettings
from goblin_king.store_schema import events_table, pipeline_notifications_table
from tests.test_pipeline_state import attached


def test_admin_notifications_are_deduplicated_after_lost_ack(tmp_path):
    state, _, _ = attached(tmp_path)
    settings = PipelineSettings()
    deliver_notifications(state, settings)
    with state.transaction() as connection:
        count = connection.scalar(select(func.count()).select_from(events_table))
        assert count > 0
        connection.execute(pipeline_notifications_table.update().values(status="pending"))
    deliver_notifications(state, settings)
    with state.store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(events_table)) == count
        assert set(connection.execute(select(pipeline_notifications_table.c.status)).scalars()) == {
            "delivered"
        }


def test_authenticated_https_sink_retries_with_stable_id(tmp_path, monkeypatch):
    import ssl
    import threading
    from datetime import UTC, datetime, timedelta
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.request import HTTPSHandler, build_opener

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    import goblin_king.pipeline_notifications as delivery

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(
                (self.headers.get("Authorization"), self.headers.get("Idempotency-Key"), body)
            )
            self.send_response(503 if len(received) == 1 else 202)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    verified = ssl.create_default_context(cafile=str(cert_path))
    monkeypatch.setattr(
        delivery,
        "build_opener",
        lambda *handlers: build_opener(*handlers, HTTPSHandler(context=verified)),
    )
    state, _, _ = attached(tmp_path)
    token = tmp_path / "sink-token"
    token.write_text("fixture-sink-token")
    settings = PipelineSettings(
        notification_url=f"https://localhost:{server.server_port}/events",
        notification_token_path=token,
    )
    try:
        deliver_notifications(state, settings)
        with state.transaction() as connection:
            row = connection.execute(select(pipeline_notifications_table)).mappings().one()
            assert row["status"] == "pending" and row["attempts"] == 1 and row["not_before"] > 0
            connection.execute(pipeline_notifications_table.update().values(not_before=0))
        deliver_notifications(state, settings)
        with state.store.engine.connect() as connection:
            assert (
                connection.execute(select(pipeline_notifications_table.c.status)).scalar_one()
                == "delivered"
            )
        assert received[0] == received[1]
        assert received[0][0] == "Bearer fixture-sink-token"
        assert received[0][1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
