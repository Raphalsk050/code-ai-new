import assert from "node:assert/strict";
import test from "node:test";

import { resolveImage } from "../workspace-images";

const posix = [{ path: "/home/ana/app", base: "https://res.test/home/ana/app" }];
const windows = [{ path: "C:\\work\\app", base: "https://res.test/c%3A/work/app" }];

test("a path relative to the workspace is served from its first folder", () => {
  assert.equal(resolveImage("docs/flow.png", posix), "https://res.test/home/ana/app/docs/flow.png");
  assert.equal(resolveImage("./docs/my flow.png", posix), "https://res.test/home/ana/app/docs/my%20flow.png");
  assert.equal(resolveImage("docs/my%20flow.png", posix), "https://res.test/home/ana/app/docs/my%20flow.png");
});

test("an absolute path inside a folder is served from that folder", () => {
  assert.equal(resolveImage("/home/ana/app/img/a.png", posix), "https://res.test/home/ana/app/img/a.png");
  assert.equal(resolveImage("file:///home/ana/app/img/a.png", posix), "https://res.test/home/ana/app/img/a.png");
  assert.equal(resolveImage("C:\\work\\app\\img\\a.png", windows), "https://res.test/c%3A/work/app/img/a.png");
  assert.equal(resolveImage("file:///c:/Work/App/img/a.png", windows), "https://res.test/c%3A/work/app/img/a.png");
});

test("anything outside the workspace is left as it was, and loads nothing", () => {
  assert.equal(resolveImage("/etc/passwd.png", posix), "/etc/passwd.png");
  assert.equal(resolveImage("../secrets/a.png", posix), "../secrets/a.png");
  assert.equal(resolveImage("/home/ana/app/../other/a.png", posix), "/home/ana/app/../other/a.png");
  assert.equal(resolveImage("/home/ana/application/a.png", posix), "/home/ana/application/a.png");
});

test("a URL stays the URL it is", () => {
  assert.equal(resolveImage("https://example.com/a.png", posix), "https://example.com/a.png");
  assert.equal(resolveImage("data:image/png;base64,AAAA", posix), "data:image/png;base64,AAAA");
  assert.equal(resolveImage("docs/a.png", []), "docs/a.png");
});
