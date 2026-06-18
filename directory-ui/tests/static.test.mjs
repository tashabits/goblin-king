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

test("runtime actions cover functions and services by directory name", () => {
  assert.match(js, /directory\/functions\/.*\/run/);
  assert.match(js, /directory\/services\/.*\/start/);
  assert.match(js, /directory\/services\/.*\/probe/);
  assert.match(js, /directory\/services\/.*\/proxy/);
  assert.match(js, /directory\/services\/.*\/stop/);
});

test("review actions are guarded by the current user admin flag", () => {
  assert.match(js, /state\.me\.is_admin/);
  assert.match(js, /approve/);
  assert.match(js, /publish/);
  assert.match(js, /retire/);
});

test("review view loads approved entries so admins can publish after approval", () => {
  assert.match(js, /status=approved/);
  assert.match(js, /Approved and awaiting publication/);
  assert.match(js, /Publish/);
});

test("published entries support retirement, deletion after retirement, and notebook cells", () => {
  assert.match(js, /Retire from Directory/);
  assert.match(js, /Delete Entry Permanently/);
  assert.match(js, /Copy Notebook Cell/);
  assert.match(html, /snippetModal/);
  assert.match(js, /method: "DELETE"/);
});

test("notebook snippets use helper calls for functions and services", () => {
  assert.match(js, /run_directory_function/);
  assert.match(js, /input=\{"name": "Directory"\}/);
  assert.match(js, /directory_service/);
  assert.match(js, /service\.start\(progress=True\)/);
  assert.match(js, /service\.proxy\("\/hello"\)/);
});
