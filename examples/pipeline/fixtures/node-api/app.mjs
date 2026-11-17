import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';

export function recordWork(root = process.env.DATA_DIR || '/data') {
  fs.mkdirSync(root, { recursive: true });
  fs.appendFileSync(path.join(root, 'activity.jsonl'), JSON.stringify({
    time: Date.now(), revision: process.env.SOURCE_REVISION || 'development',
  }) + '\n');
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.includes('--work-once')) recordWork();
  else if (process.argv.includes('--background')) {
    recordWork();
    setInterval(recordWork, 2000);
  } else http.createServer((req, res) => {
    const root = process.env.DATA_DIR || '/data';
    fs.mkdirSync(root, { recursive: true });
    const sentinel = path.join(root, 'sentinel');
    if (!fs.existsSync(sentinel)) fs.writeFileSync(sentinel, randomUUID());
    const healthy = process.env.FAIL_HEALTH !== '1';
    res.writeHead(healthy ? 200 : 503, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ healthy, implementation: 'node',
      revision: process.env.SOURCE_REVISION || 'development',
      sentinel: fs.readFileSync(sentinel, 'utf8'),
    }));
  }).listen(8080, '0.0.0.0');
}
