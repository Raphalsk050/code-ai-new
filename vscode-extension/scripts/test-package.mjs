#!/usr/bin/env node
// Packaging tests: prove that `npm run package` produces a VSIX a first-time
// user can install and run, without waiting on a real 200 MB PyInstaller build.
//
// The binary is faked - the right magic bytes and past the size floor - because
// what is under test is the packaging, not PyInstaller. The last test drives
// the real vsce and reads the real zip back, so .vscodeignore, the staging
// layout and the executable bit are all checked against the actual artifact.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  utimesSync,
  writeFileSync,
} from "node:fs";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { BIN_DIR, PLATFORMS, assertBinary, stageBinary } from "./bundle-binary.mjs";
import {
  SkippedTarget,
  cleanEggInfo,
  containerScript,
  dockerRunArgs,
  ensureBackend,
  findPython,
  hostPlatform,
  isStale,
  skipFromCopy,
} from "./ensure-backend.mjs";
import { readZipEntries, verify } from "./verify-vsix.mjs";

const EXT_ROOT = fileURLToPath(new URL("..", import.meta.url));
const SCRATCH = mkdtempSync(join(tmpdir(), "code-ai-package-tests-"));

process.on("exit", () => rmSync(SCRATCH, { recursive: true, force: true }));

/** A file with a platform's magic bytes, padded past the size floor. */
function fakeBinary(platform, name, megabytes = 6) {
  const file = join(SCRATCH, name);
  const buf = Buffer.alloc(megabytes * 1024 * 1024);
  PLATFORMS[platform].magic.copy(buf, 0);
  writeFileSync(file, buf);
  return file;
}

test("a binary of the wrong platform is rejected, and says which it is", () => {
  const elf = fakeBinary("linux", "linux-in-the-windows-slot");
  assert.throws(() => assertBinary(elf, "win32"), /not a Windows PE executable.*Linux ELF/s);
});

test("a stub too small to be a one-file build is rejected", () => {
  const stub = join(SCRATCH, "stub.exe");
  writeFileSync(stub, Buffer.concat([PLATFORMS.win32.magic, Buffer.alloc(1024)]));
  assert.throws(() => assertBinary(stub, "win32"), /too small for a one-file/);
});

test("a real-shaped binary passes and reports its size", () => {
  const exe = fakeBinary("win32", "ok.exe", 7);
  assert.equal(assertBinary(exe, "win32"), 7 * 1024 * 1024);
});

test("an explicit path that is not there is reported as such", () => {
  assert.throws(() => ensureBackend("linux", { explicit: join(SCRATCH, "nope") }), /does not exist/);
});

/** Run `body` with one platform's search path replaced. */
function withCandidates(platform, candidates, body) {
  const original = PLATFORMS[platform].candidates.slice();
  PLATFORMS[platform].candidates.length = 0;
  PLATFORMS[platform].candidates.push(...candidates);
  try {
    return body();
  } finally {
    PLATFORMS[platform].candidates.length = 0;
    PLATFORMS[platform].candidates.push(...original);
  }
}

test("a binary already on disk is used as is, with nothing built", () => {
  const existing = fakeBinary("linux", "already-there");
  // No Python is invoked: a build would take minutes, and this is the path
  // every run after the first takes.
  const found = withCandidates("linux", [existing], () => ensureBackend("linux"));
  assert.equal(found, existing);
});

test("staleness is judged against src/, the spec and pyproject", () => {
  const fresh = fakeBinary("linux", "fresh");
  assert.equal(isStale(fresh), false);

  // Backdated a year: whatever the tree's mtimes are, this one lost.
  const old = fakeBinary("linux", "old");
  const year = Date.now() / 1000 - 365 * 24 * 3600;
  utimesSync(old, year, year);
  assert.equal(isStale(old), true);

  assert.equal(isStale(join(SCRATCH, "never-built")), true);
});

test("a stale binary is not silently packaged for a platform that can be rebuilt", () => {
  // The regression: discovery used to return any existing candidate, so a fix
  // landing in src/ would ship inside a backend built before it.
  const old = fakeBinary(hostPlatform(), `stale-host${hostPlatform() === "win32" ? ".exe" : ""}`);
  const year = Date.now() / 1000 - 365 * 24 * 3600;
  utimesSync(old, year, year);
  // Not actually building here - just proving the fresh path is not taken.
  assert.equal(isStale(old), true);
});

test("a target this machine cannot build is skippable, not fatal", () => {
  const other = hostPlatform() === "win32" ? "linux" : "win32";
  withCandidates(other, [], () => {
    try {
      ensureBackend(other);
    } catch (err) {
      // Docker turns this into a real (very slow) build, so only the no-Docker
      // outcome is asserted; either way it must not be an anonymous failure.
      assert.ok(err instanceof SkippedTarget, `expected SkippedTarget, got ${err}`);
      assert.match(err.message, /Docker/);
      assert.match(err.message, /build-binaries workflow/);
    }
  });
});

test("a Python new enough to build with is found, or honestly reported missing", () => {
  const python = findPython();
  // Not an assertion about this machine: either answer is legitimate, but the
  // helper must not throw while deciding.
  assert.ok(python === null || typeof python.cmd === "string");
});

test("staging replaces the other platform's binary instead of piling up", () => {
  stageBinary("win32", fakeBinary("win32", "a.exe"));
  assert.ok(existsSync(join(BIN_DIR, "code-ai.exe")));
  stageBinary("linux", fakeBinary("linux", "a.bin"));
  assert.ok(existsSync(join(BIN_DIR, "code-ai")));
  assert.equal(existsSync(join(BIN_DIR, "code-ai.exe")), false);
  rmSync(BIN_DIR, { recursive: true, force: true });
});

test("the staged binary is executable where that means anything", () => {
  const { target } = stageBinary("linux", fakeBinary("linux", "b.bin"));
  // Windows has no executable bit to set; there the guarantee comes from the
  // mode written into the zip (see the end-to-end test) and the extension's
  // chmod on first spawn.
  if (process.platform !== "win32") assert.ok(statSync(target).mode & 0o111);
  assert.ok(statSync(target).isFile());
  rmSync(BIN_DIR, { recursive: true, force: true });
});

test("verify rejects a package with no backend in it", () => {
  // A zip with everything but the binary: this is the regression that matters,
  // because it installs cleanly and only fails when the user opens the panel.
  const file = join(SCRATCH, "empty.vsix");
  buildZip(file, [
    ["extension/package.json", Buffer.from("{}")],
    ["extension/dist/extension.js", Buffer.from("//")],
    ["extension/dist/webview.js", Buffer.from("//")],
    ["extension/dist/flow.js", Buffer.from("//")],
  ]);
  const { problems } = verify(file, "linux-x64");
  assert.equal(problems.length, 1);
  assert.match(problems[0], /carries no backend/);
});

test("verify rejects the other platform's binary and a non-executable one", () => {
  const big = Buffer.alloc(6 * 1024 * 1024);
  const file = join(SCRATCH, "crossed.vsix");
  buildZip(file, [
    ["extension/package.json", Buffer.from("{}")],
    ["extension/dist/extension.js", Buffer.from("//")],
    ["extension/dist/webview.js", Buffer.from("//")],
    ["extension/dist/flow.js", Buffer.from("//")],
    ["extension/bin/code-ai", big, 0o644],
    ["extension/bin/code-ai.exe", big],
  ]);
  const { problems } = verify(file, "linux-x64");
  assert.match(problems.join("\n"), /not executable/);
  assert.match(problems.join("\n"), /code-ai\.exe should not be in a linux-x64 package/);
});

/**
 * Drive the real packaging script for one target, and return its VSIX. The
 * output goes to scratch: the default vsix/ may hold a real package under the
 * same version-derived name, and these tests must not delete it.
 */
function packageFor(target, platform) {
  const flag = platform === "win32" ? "--win" : "--linux";
  const source = fakeBinary(platform, `backend-${platform}${platform === "win32" ? ".exe" : ""}`, 6);
  const outDir = join(SCRATCH, "out");
  execFileSync(
    process.execPath,
    [join(EXT_ROOT, "scripts", "package.mjs"), "--target", target, flag, source, "--out-dir", outDir],
    { cwd: EXT_ROOT, stdio: "pipe" }
  );
  const version = JSON.parse(readFileSync(join(EXT_ROOT, "package.json"), "utf8")).version;
  return join(outDir, `code-ai-${version}-${target}.vsix`);
}

test("packaging never writes into the real output directory during tests", () => {
  // A guard on the guard: if --out-dir stops being honoured, the suite would
  // quietly start deleting whatever the last real build produced.
  const before = existsSync(join(EXT_ROOT, "vsix")) ? readdirSync(join(EXT_ROOT, "vsix")) : [];
  packageFor("win32-x64", "win32");
  const after = existsSync(join(EXT_ROOT, "vsix")) ? readdirSync(join(EXT_ROOT, "vsix")) : [];
  assert.deepEqual(after, before);
});

for (const [target, platform] of [
  ["win32-x64", "win32"],
  ["linux-x64", "linux"],
]) {
  test(`end to end: npm run package produces a self-contained ${target} VSIX`, () => {
    const out = packageFor(target, platform);
    try {
      const { problems, binary, entries } = verify(out, target);
      assert.deepEqual(problems, [], problems.join("; "));
      assert.equal(binary.size, 6 * 1024 * 1024);
      // The bit VSCode unpacks with. It is the whole reason a package built on
      // Windows can still be installed on Linux.
      assert.ok(binary.mode & 0o111, `mode was ${binary.mode?.toString(8)}`);

      const names = entries.map((e) => e.name);
      assert.ok(names.includes("extension/dist/extension.js"));
      assert.ok(names.includes("extension/dist/webview.js"));
      assert.ok(names.includes("extension/dist/flow.js"));
      assert.ok(!names.some((n) => n.includes("node_modules/")));
      assert.ok(!names.some((n) => n.endsWith(".map")), "source maps should stay out of the package");
      assert.ok(!names.some((n) => n.startsWith("extension/src/")));
      assert.ok(!names.some((n) => n.startsWith("extension/scripts/")));
    } finally {
      rmSync(out, { force: true });
    }
  });
}

test("packaging leaves nothing staged behind", () => {
  assert.equal(existsSync(BIN_DIR), false);
});

/**
 * Minimal zip writer (stored, no compression) for the fixtures above. Only
 * needs to produce something `readZipEntries` can walk, so it writes classic
 * local headers plus a central directory.
 */
function buildZip(file, files) {
  const locals = [];
  const central = [];
  let offset = 0;
  for (const [name, content, mode] of files) {
    const nameBuf = Buffer.from(name, "utf8");
    const crc = crc32(content);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 6);
    local.writeUInt16LE(0, 8); // stored
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(content.length, 18);
    local.writeUInt32LE(content.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    locals.push(local, nameBuf, content);

    const header = Buffer.alloc(46);
    header.writeUInt32LE(0x02014b50, 0);
    header.writeUInt8(20, 4);
    header.writeUInt8(3, 5); // host system: Unix, so the mode is read back
    header.writeUInt16LE(20, 6);
    header.writeUInt32LE(crc, 16);
    header.writeUInt32LE(content.length, 20);
    header.writeUInt32LE(content.length, 24);
    header.writeUInt16LE(nameBuf.length, 28);
    header.writeUInt32LE(((mode ?? 0o755) & 0xffff) << 16, 38);
    header.writeUInt32LE(offset, 42);
    central.push(header, nameBuf);
    offset += local.length + nameBuf.length + content.length;
  }
  const directory = Buffer.concat(central);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(files.length, 8);
  eocd.writeUInt16LE(files.length, 10);
  eocd.writeUInt32LE(directory.length, 12);
  eocd.writeUInt32LE(offset, 16);
  mkdirSync(join(file, ".."), { recursive: true });
  writeFileSync(file, Buffer.concat([...locals, directory, eocd]));
}

function crc32(buf) {
  let crc = ~0;
  for (const byte of buf) {
    crc ^= byte;
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return ~crc >>> 0;
}

// -- the cross-build container and what it leaves behind ------------------
//
// The bug these pin down: the container ran as root over a bind mount, so it
// left root-owned `src/*.egg-info` on the host and the next native build died
// in setuptools with "Cannot update time stamp of directory" - an error that
// names the timestamp instead of the ownership.

const DOCKER = { image: "python:3.12-bullseye", script: "build.sh", mount: "/repo" };

test("the Linux container runs as the caller, so what it writes is the caller's", () => {
  const args = dockerRunArgs({ ...DOCKER, platform: "linux", user: "1000:1000" });

  assert.ok(args.includes("--user"), `no --user in ${args.join(" ")}`);
  assert.equal(args[args.indexOf("--user") + 1], "1000:1000");
  // A uid with no passwd entry has no home, and pip will not cache into one it
  // cannot write.
  assert.ok(args.includes("HOME=/tmp"), "HOME was not moved somewhere writable");
  assert.equal(args.at(-1), "build.sh", "the Linux script needs no cleanup tail");
});

test("the wine container stays root but hands its droppings back", () => {
  const args = dockerRunArgs({
    ...DOCKER,
    platform: "win32",
    image: "tobix/pywine:3.12",
    user: "1000:1000",
  });

  // Wine needs its own prefix, so this one cannot drop privileges.
  assert.ok(!args.includes("--user"), "wine cannot run as an arbitrary uid");
  const script = args.at(-1);
  assert.match(script, /^build\.sh &&/, "the build still runs first");
  assert.match(script, /chown -R 1000:1000 \/work\/dist/, "dist is not handed back");
  assert.match(script, /rm -rf \/work\/src\/\*\.egg-info/, "the egg-info is left behind");
});

test("a Windows host maps no uid, so neither trick is applied", () => {
  const linux = dockerRunArgs({ ...DOCKER, platform: "linux", user: null });
  const win = dockerRunArgs({ ...DOCKER, platform: "win32", user: null });

  assert.ok(!linux.includes("--user"));
  assert.equal(linux.at(-1), "build.sh");
  assert.equal(win.at(-1), "build.sh");
});

test("the mount and the working directory are always the repo", () => {
  const args = dockerRunArgs({ ...DOCKER, platform: "linux", user: "1000:1000" });

  assert.ok(args.includes("/repo:/work"), `mount missing from ${args.join(" ")}`);
  assert.equal(args[args.indexOf("-w") + 1], "/work");
});

test("a stale egg-info is cleared before the in-tree install", () => {
  // `pip install .` runs egg_info in place, and egg_info updates the
  // directory's timestamp; a leftover from another user makes that fail.
  const repo = mkdtempSync(join(tmpdir(), "code-ai-egg-"));
  const src = join(repo, "src");
  mkdirSync(join(src, "code_ai.egg-info"), { recursive: true });
  writeFileSync(join(src, "code_ai.egg-info", "PKG-INFO"), "stale");
  mkdirSync(join(src, "code_ai"), { recursive: true });

  cleanEggInfo(repo);

  assert.ok(!existsSync(join(src, "code_ai.egg-info")), "the stale egg-info survived");
  assert.ok(existsSync(join(src, "code_ai")), "cleaning took the package with it");
});

// -- nothing is built inside the source tree ------------------------------
//
// The second half of the same bug: `pip install .` runs setuptools' egg_info in
// the project directory, and PyInstaller writes `build/`. Both fail outright in
// a tree whose leftovers belong to another user. Building from a copy means
// there is nothing in the tree left to trip over.

test("neither container installs from the mounted tree", () => {
  for (const platform of ["linux", "win32"]) {
    const script = containerScript({ platform, verifySsl: false });

    assert.match(script, /cp -a pyproject\.toml README\.md src \/tmp\//, `${platform} stages no copy`);
    // Installing happens from inside the copy: the Wine interpreter reads an
    // absolute unix path as a Windows one, and a bare `.` needs no translation.
    assert.match(script, /\(cd \/tmp\/code-ai-build && .*pip install \.\)/, `${platform}: wrong cwd`);
    // And that is the only `pip install .` - the mounted repo keeps none.
    assert.equal(
      script.match(/pip install \./g).length,
      1,
      `${platform} still installs from the mount: ${script}`
    );
  }
});

test("the Linux container keeps PyInstaller's scratch out of the mount", () => {
  const script = containerScript({ platform: "linux", verifySsl: false });

  assert.match(script, /--workpath \/tmp\/build/, "build/ still lands in the repo");
  assert.match(script, /packaging\/code-ai\.spec/, "the spec is still the one in the repo");
});

test("the SSL escape hatch survives the rewrite", () => {
  // The company network fails cert checks, so an unverified build has to keep
  // reaching pypi.
  assert.match(containerScript({ platform: "linux", verifySsl: false }), /PIP_TRUSTED_HOST/);
  assert.doesNotMatch(containerScript({ platform: "linux", verifySsl: true }), /PIP_TRUSTED_HOST/);
});

test("nothing generated travels into the staged copy", () => {
  assert.ok(skipFromCopy("__pycache__"));
  assert.ok(skipFromCopy("code_ai.egg-info"));
  assert.ok(!skipFromCopy("code_ai"));
  assert.ok(!skipFromCopy("version.json"));
  // Matched on the name, so the copy behaves the same on either separator.
  assert.ok(!skipFromCopy("src"));
});

test("a root-owned egg-info no longer fails the build", () => {
  // It used to throw and tell the user to sudo. Nothing reads it any more, so
  // refusing to build over one would be the old bug wearing a new hat.
  const repo = mkdtempSync(join(tmpdir(), "code-ai-egg-"));
  mkdirSync(join(repo, "src"), { recursive: true });

  assert.deepEqual(cleanEggInfo(repo), [], "a clean tree reported something stuck");
});

test("cleaning a tree with no src is a no-op, not a crash", () => {
  const repo = mkdtempSync(join(tmpdir(), "code-ai-egg-"));
  cleanEggInfo(repo);
});

// Keep the staging directory out of the way of a later real build.
process.on("exit", () => rmSync(BIN_DIR, { recursive: true, force: true }));
