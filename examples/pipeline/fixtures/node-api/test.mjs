import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { recordWork } from './app.mjs';

test('background activity persists across invocations', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pipeline-test-'));
  try {
    recordWork(root);
    recordWork(root);
    assert.equal(fs.readFileSync(path.join(root, 'activity.jsonl'), 'utf8').trim().split('\n').length, 2);
  } finally { fs.rmSync(root, { recursive: true }); }
});
