import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const source = readFileSync(join(root, 'lib', 'index.js'), 'utf8');

if (pkg.name !== 'goblin-king-jupyterlab') {
  throw new Error('unexpected package name');
}
if (!pkg.jupyterlab?.extension) {
  throw new Error('package does not declare a JupyterLab extension');
}
for (const required of [
  'Goblin Directory',
  'goblin-directory/api',
  'Run Function',
  'Start Service',
  'Probe',
  'Proxy',
  'Stop'
]) {
  if (!source.includes(required)) {
    throw new Error(`missing expected picker source: ${required}`);
  }
}

console.log('JupyterLab Goblin Directory package metadata is build-ready');
