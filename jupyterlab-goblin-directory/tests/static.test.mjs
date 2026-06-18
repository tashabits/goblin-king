import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const source = readFileSync(join(root, 'lib', 'index.js'), 'utf8');
const css = readFileSync(join(root, 'style', 'index.css'), 'utf8');
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));

test('package declares a real JupyterLab extension entrypoint', () => {
  assert.equal(pkg.name, 'goblin-king-jupyterlab');
  assert.equal(pkg.jupyterlab.extension, true);
  assert.equal(pkg.main, 'lib/index.js');
});

test('picker uses the user-server proxy route and no pasted tokens', () => {
  assert.match(source, /PageConfig\.getBaseUrl/);
  assert.match(source, /ServerConnection\.makeRequest/);
  assert.match(source, /goblin-directory\/api/);
  assert.doesNotMatch(source, /Authorization/);
  assert.doesNotMatch(source, /token/i);
});

test('picker displays published Directory metadata', () => {
  for (const field of ['display_name', 'name', 'type', 'project_id', 'published_version', 'tags']) {
    assert.match(source, new RegExp(field));
  }
  assert.match(source, /Published goblin/);
});

test('picker can run functions and control services by name', () => {
  assert.match(source, /functions\/\$\{encodeURIComponent\(entry\.name\)\}\/run/);
  assert.match(source, /serviceAction\('start'\)/);
  assert.match(source, /serviceAction\('probe'\)/);
  assert.match(source, /serviceAction\('stop'\)/);
  assert.match(source, /services\/\$\{encodeURIComponent\(entry\.name\)\}\/\$\{action\}/);
  assert.match(source, /services\/\$\{encodeURIComponent\(entry\.name\)\}\/proxy/);
});

test('picker has stable sidebar styling hooks', () => {
  assert.match(css, /\.gk-directory-picker/);
  assert.match(css, /gk-directory-picker__actions/);
  assert.match(css, /gk-directory-picker__output/);
});
