import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomUUID, createHash } from 'node:crypto';
import tls from 'node:tls';

export function outboundTLS(ca) {
  return new Promise((resolve, reject) => {
    if (!process.env.HTTPS_PROXY) { reject(new Error('HTTPS proxy is not configured')); return; }
    const hostname = 'allowed.fixture.example.test';
    const request = http.request(process.env.HTTPS_PROXY, {
      method: 'CONNECT', path: hostname + ':8443', timeout: 10000,
    });
    request.on('timeout', () => request.destroy(new Error('CONNECT timeout')));
    request.on('error', reject);
    request.on('connect', (response, socket, head) => {
      if (response.statusCode !== 200 || head.length) { socket.destroy(); reject(new Error('CONNECT denied')); return; }
      const secure = tls.connect({ socket, servername: hostname, ca }, () => {
        secure.write('GET /work/' + (process.env.SOURCE_REVISION || 'development') +
          '/service-tls HTTP/1.1\r\nHost: ' + hostname + ':8443\r\nConnection: close\r\n\r\n');
      });
      secure.setTimeout(10000, () => secure.destroy(new Error('TLS timeout')));
      secure.on('error', reject);
      let raw = '';
      secure.on('data', chunk => { raw += chunk; if (raw.length > 16384) secure.destroy(new Error('response too large')); });
      secure.on('end', () => {
        try {
          const split = raw.indexOf('\r\n\r\n');
          if (!raw.startsWith('HTTP/1.1 200') && !raw.startsWith('HTTP/1.0 200')) throw new Error('upstream failed');
          resolve({ status: 200, tls_verified: true, body: JSON.parse(raw.slice(split + 4)) });
        } catch (error) { reject(error); }
      });
    });
    request.end();
  });
}

export function outbound(activity = 'service') {
  if (!process.env.HTTP_PROXY) return Promise.resolve({ configured: false });
  return new Promise(resolve => {
    const request = http.request(process.env.HTTP_PROXY, {
      path: 'http://allowed.fixture.example.test:8080/work/' + (process.env.SOURCE_REVISION || 'development') + '/' + activity,
      headers: { Host: 'allowed.fixture.example.test:8080' }, timeout: 5000,
    }, response => {
      let body = '';
      response.on('data', data => { body += data; if (body.length > 8192) request.destroy(); });
      response.on('end', () => {
        try { resolve({ status: response.statusCode, body: JSON.parse(body) }); }
        catch { resolve({ status: response.statusCode }); }
      });
    });
    request.on('timeout', () => request.destroy());
    request.on('error', error => resolve({ error: error.code || 'connection-error' }));
    request.end();
  });
}

export function recordWork(root = process.env.DATA_DIR || '/data') {
  fs.mkdirSync(root, { recursive: true });
  fs.appendFileSync(path.join(root, 'activity.jsonl'), JSON.stringify({
    time: Date.now(), revision: process.env.SOURCE_REVISION || 'development',
  }) + '\n');
  if (process.env.HTTP_PROXY) void outbound(process.argv.includes('--background') ? 'background' : 'schedule').then(result => {
    fs.appendFileSync(path.join(root, 'outbound.jsonl'), JSON.stringify(result) + '\n');
  });
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.includes('--work-once')) recordWork();
  else if (process.argv.includes('--background')) {
    recordWork();
    setInterval(recordWork, 2000);
  } else {
    const server = http.createServer(async (req, res) => {
    if (req.url === '/outbound') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(await outbound())); return;
    }
    if (req.url === '/slow') await new Promise(resolve => setTimeout(resolve, 5000));
    if (req.method === 'POST') {
      let bytes = 0;
      for await (const chunk of req) {
        bytes += chunk.length;
        if (bytes > 10485760) { res.writeHead(413); res.end(); return; }
      }
      res.writeHead(200); res.end(JSON.stringify({ bytes })); return;
    }
    const root = process.env.DATA_DIR || '/data';
    fs.mkdirSync(root, { recursive: true });
    const sentinel = path.join(root, 'sentinel');
    if (!fs.existsSync(sentinel)) fs.writeFileSync(sentinel, randomUUID());
    const healthy = process.env.FAIL_HEALTH !== '1';
    res.writeHead(healthy ? 200 : 503, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ healthy, implementation: 'node',
      revision: process.env.SOURCE_REVISION || 'development',
      sentinel: fs.readFileSync(sentinel, 'utf8'),
      forwarded_for: req.headers['x-forwarded-for'],
      forwarded_proto: req.headers['x-forwarded-proto'],
    }));
    });
    server.on('upgrade', (req, socket, head) => {
      if (req.url !== '/ws' || !req.headers['sec-websocket-key']) { socket.destroy(); return; }
      const accept = createHash('sha1').update(req.headers['sec-websocket-key'] +
        '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
      socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n' +
        'Connection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n');
      socket.setTimeout(60000, () => socket.destroy());
      socket.on('error', () => socket.destroy());
      let pending = head;
      socket.on('data', data => {
        pending = Buffer.concat([pending, data]);
        while (pending.length >= 2) {
          let length = pending[1] & 127, offset = 2;
          if (length === 126) {
            if (pending.length < 4) return;
            length = pending.readUInt16BE(2); offset = 4;
          }
          if (!(pending[1] & 128) || length > 4096 || length === 127 || (pending[0] & 15) === 8) {
            socket.destroy(); return;
          }
          if (pending.length < offset + 4 + length) return;
          const mask = pending.subarray(offset, offset + 4);
          const body = Buffer.from(pending.subarray(offset + 4, offset + 4 + length));
          for (let i = 0; i < length; i++) body[i] ^= mask[i % 4];
          const header = length < 126 ? Buffer.from([129, length]) : Buffer.from([129, 126, length >> 8, length & 255]);
          socket.write(Buffer.concat([header, body]));
          pending = pending.subarray(offset + 4 + length);
        }
      });
    });
    server.listen(8080, '0.0.0.0');
  }
}
