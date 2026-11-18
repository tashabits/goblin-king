"""Authenticated HTTPS fixture with observable effect and acknowledgement boundaries."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import ssl
import threading
import time

ROOT=Path('/data')
TOKEN=Path('/tls/token').read_text().strip()
ADMIN=Path('/tls/admin-token').read_text().strip()
lock=threading.RLock()
state={'mode':'normal','attempts':[],'effects':{},'unauthorized':0}

def save():
    temporary=ROOT/'state.tmp'
    temporary.write_text(json.dumps(state,sort_keys=True))
    temporary.replace(ROOT/'state.json')

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass

    def respond(self,status,body):
        data=json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()

    def do_GET(self):
        if self.path!='/admin/state' or self.headers.get('Authorization')!='Bearer '+ADMIN:
            return self.respond(401,{'error':'authentication required'})
        with lock: return self.respond(200,state)

    def do_POST(self):
        length=int(self.headers.get('Content-Length','0'))
        if length>65536: return self.respond(413,{'error':'bounded body required'})
        raw=self.rfile.read(length)
        body=json.loads(raw or b'{}')
        if self.path=='/admin/control':
            if self.headers.get('Authorization')!='Bearer '+ADMIN:
                return self.respond(401,{'error':'authentication required'})
            with lock:
                if 'mode' in body:
                    assert body['mode'] in ('normal','unavailable','hold_before_effect','hold_after_effect')
                    state['mode']=body['mode']
                if 'release' in body:
                    for row in state['attempts']:
                        if row['waiting'] and row['id']==body['release']:
                            row['release']=body.get('response',202)
                save()
            return self.respond(200,{'configured':True})
        if self.path!='/events': return self.respond(404,{'error':'unknown route'})
        if self.headers.get('Authorization')!='Bearer '+TOKEN:
            with lock:
                state['unauthorized']+=1
                save()
            return self.respond(401,{'error':'authentication required'})
        identity=self.headers.get('Idempotency-Key')
        if not identity or body.get('id')!=identity:
            return self.respond(400,{'error':'stable identity required'})
        checksum=hashlib.sha256(raw).hexdigest()
        with lock:
            mode=state['mode']
            row={'id':identity,'checksum':checksum,'mode':mode,'authenticated':True,
                 'waiting':False,'response_sent':False,'release':None}
            state['attempts'].append(row)
            if mode=='unavailable':
                row['response']=503
                save()
                return self.respond(503,{'error':'fixture outage'})
            if mode!='hold_before_effect':
                previous=state['effects'].get(identity)
                if previous is not None and previous['checksum']!=checksum:
                    return self.respond(409,{'error':'identity conflict'})
                state['effects'].setdefault(identity,{'checksum':checksum,'body':body,'commits':1})
            if mode.startswith('hold_'): row['waiting']=True
            save()
        if row['waiting']:
            deadline=time.monotonic()+120
            while row['release'] is None and time.monotonic()<deadline: time.sleep(.05)
            status=row['release'] or 503
        else: status=202
        with lock:
            row['waiting']=False
            row['response']=status
            save()
        try:
            self.respond(status,{'id':identity})
            with lock:
                row['response_sent']=True
                save()
        except (BrokenPipeError,ConnectionResetError,ssl.SSLError):
            with lock:
                row['connection_lost']=True
                save()

server=ThreadingHTTPServer(('0.0.0.0',8443),Handler)
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain('/tls/cert.pem','/tls/key.pem')
server.socket=context.wrap_socket(server.socket,server_side=True)
server.serve_forever()
