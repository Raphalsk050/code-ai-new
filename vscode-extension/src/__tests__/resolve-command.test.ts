import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_COMMAND,
  binaryName,
  bundledBinaryPath,
  resolveCommand,
  spawnFailureHint,
  type ResolveHost,
} from "../resolve-command";

/** A fake filesystem: only the listed paths exist. */
function host(platform: NodeJS.Platform, present: string[] = []): ResolveHost {
  const set = new Set(present);
  return { platform, exists: (file) => set.has(file) };
}

const WIN_EXT = "C:\\Users\\dev\\.vscode\\extensions\\code-ai";
const POSIX_EXT = "/home/dev/.vscode/extensions/code-ai";

test("bundled binary is named per platform", () => {
  assert.equal(binaryName("win32"), "code-ai.exe");
  assert.equal(binaryName("linux"), "code-ai");
  assert.equal(binaryName("darwin"), "code-ai");
});

test("bundled path uses the platform's separator", () => {
  assert.equal(bundledBinaryPath(WIN_EXT, "win32"), `${WIN_EXT}\\bin\\code-ai.exe`);
  assert.equal(bundledBinaryPath(POSIX_EXT, "linux"), `${POSIX_EXT}/bin/code-ai`);
});

test("the bundled binary wins when the setting is left at the default", () => {
  for (const [platform, ext] of [
    ["win32", WIN_EXT],
    ["linux", POSIX_EXT],
  ] as const) {
    const bundled = bundledBinaryPath(ext, platform);
    const resolved = resolveCommand(DEFAULT_COMMAND, ext, [], host(platform, [bundled]));
    assert.deepEqual(resolved, { command: bundled, source: "bundled" });
  }
});

test("an explicit setting beats the bundled binary", () => {
  const bundled = bundledBinaryPath(POSIX_EXT, "linux");
  const resolved = resolveCommand(
    "/opt/code-ai/code-ai",
    POSIX_EXT,
    [],
    host("linux", [bundled, "/opt/code-ai/code-ai"])
  );
  assert.deepEqual(resolved, { command: "/opt/code-ai/code-ai", source: "configured" });
});

test("a Windows virtualenv is found under Scripts, not bin", () => {
  const workspace = "C:\\work\\code-ai";
  const script = `${workspace}\\.venv\\Scripts\\code-ai.exe`;
  // The POSIX layout must not match on Windows, or the resolver reports a hit
  // that cannot be spawned.
  const posixLayout = `${workspace}\\.venv\\bin\\code-ai`;
  const resolved = resolveCommand(
    DEFAULT_COMMAND,
    WIN_EXT,
    [workspace],
    host("win32", [script, posixLayout])
  );
  assert.deepEqual(resolved, { command: script, source: "venv" });
});

test("a POSIX virtualenv is found under bin", () => {
  const workspace = "/home/dev/code-ai";
  const script = `${workspace}/.venv/bin/code-ai`;
  const resolved = resolveCommand(DEFAULT_COMMAND, POSIX_EXT, [workspace], host("linux", [script]));
  assert.deepEqual(resolved, { command: script, source: "venv" });
});

test("the extension's parent directory is searched before the workspace", () => {
  // Running from source (F5), the extension dir is <repo>/vscode-extension, so
  // the repo's own virtualenv is one level up.
  const repo = "/home/dev/code-ai";
  const extension = `${repo}/vscode-extension`;
  const script = `${repo}/.venv/bin/code-ai`;
  const resolved = resolveCommand(DEFAULT_COMMAND, extension, ["/other/ws"], host("linux", [script]));
  assert.deepEqual(resolved, { command: script, source: "venv" });
});

test("with nothing on disk it falls back to the bare name", () => {
  const resolved = resolveCommand(DEFAULT_COMMAND, POSIX_EXT, ["/ws"], host("linux"));
  assert.deepEqual(resolved, { command: DEFAULT_COMMAND, source: "path" });
});

test("whitespace-only settings count as the default", () => {
  const bundled = bundledBinaryPath(POSIX_EXT, "linux");
  const resolved = resolveCommand("   ", POSIX_EXT, [], host("linux", [bundled]));
  assert.equal(resolved.source, "bundled");
});

test("a Python script as the command is explained, not left as EFTYPE", () => {
  const script = "C:\\proj\\src\\code_ai\\__main__.py";
  const resolved = resolveCommand(script, WIN_EXT, [], host("win32", [script]));
  assert.equal(resolved.source, "configured");
  const hint = spawnFailureHint(resolved, "win32");
  assert.match(hint, /cannot be spawned directly/);
  assert.match(hint, /"-m", "code_ai"/);
  assert.match(hint, /Scripts\\code-ai\.exe/);
});

test("the failure hint names the path and how it was chosen", () => {
  const bundled = bundledBinaryPath(POSIX_EXT, "linux");
  assert.match(spawnFailureHint({ command: bundled, source: "bundled" }, "linux"), /reinstall the VSIX/);
  assert.match(
    spawnFailureHint({ command: "code-ai", source: "path" }, "win32"),
    /\\\.venv\\Scripts\\code-ai\.exe/
  );
  assert.match(
    spawnFailureHint({ command: "code-ai", source: "path" }, "linux"),
    /\/\.venv\/bin\/code-ai/
  );
});
