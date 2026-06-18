import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const html = readFileSync(join(root, "static", "index.html"), "utf8");
const js = readFileSync(join(root, "static", "app.js"), "utf8");

test("directory, submission, review, and runtime views are present", () => {
  for (const view of ["directory", "submit", "mine", "review", "detail", "runtime"]) {
    assert.match(html, new RegExp(`data-view="${view}"`));
  }
});

test("bundle upload calls preview and submit endpoints", () => {
  assert.match(js, /ui-api\/bundles\/preview/);
  assert.match(js, /ui-api\/bundles\/submit/);
});

test("runtime actions cover functions and services by repository name", () => {
  assert.match(js, /repository\/functions\/.*\/run/);
  assert.match(js, /repository\/services\/.*\/start/);
  assert.match(js, /repository\/services\/.*\/probe/);
  assert.match(js, /repository\/services\/.*\/proxy/);
  assert.match(js, /repository\/services\/.*\/stop/);
});

test("review actions are guarded by the current user admin flag", () => {
  assert.match(js, /state\.me\.is_admin/);
  assert.match(js, /approve/);
  assert.match(js, /publish/);
  assert.match(js, /retire/);
});
