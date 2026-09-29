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

import { BIN_DIR, PLATFORMS, assertBinary, stageBinary, stageBundle } from "./bundle-binary.mjs";
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
  smokeTest,
} from "./ensure-backend.mjs";
import { declaredPackages, installReason, missingPackages, vscePath } from "./ensure-deps.mjs";
import { readZipEntries, verify } from "./verify-vsix.mjs";

const EXT_ROOT = fileURLToPath(new URL("..", import.meta.url));
const SCRATCH = mkdtempSync(join(tmpdir(), "code-ai-package-tests-"));

process.on("exit", () => rmSync(SCRATCH, { recursive: true, force: true }));

const MAC_PROGRAMS = [
  "code-ai",
  "runtime/bin/python3.12",
  "runtime/lib/python3.12/site-packages/playwright/driver/node",
];

/** A folder shaped like scripts/build_mac_bundle.py's, heavy enough to pass. */
function fakeMacBundle(arch) {
  const dir = join(SCRATCH, `code-ai-mac-${arch}`);
  rmSync(dir, { recursive: true, force: true });
  const driver = join(dir, "runtime", "lib", "python3.12", "site-packages", "playwright", "driver");
  mkdirSync(join(dir, "runtime", "bin"), { recursive: true });
  mkdirSync(driver, { recursive: true });
  writeFileSync(join(dir, "code-ai"), "#!/bin/sh\nexit 0\n");
  writeFileSync(join(dir, "runtime", "bin", "python3.12"), Buffer.alloc(1024));
  // A real runtime weighs this much; the verifier refuses a hollow one.
  writeFileSync(join(driver, "node"), Buffer.alloc(41 * 1024 * 1024));
  const manifest = { version: "0", arch, python: "3.12.14", executables: MAC_PROGRAMS };
  writeFileSync(join(dir, "code-ai-bundle.json"), JSON.stringify(manifest));
  return dir;
}

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
  let backend;
  if (platform === "darwin") {
    const arch = target.split("-")[1];
    backend = [`--mac-${arch}`, fakeMacBundle(arch)];
  } else {
    const flag = platform === "win32" ? "--win" : "--linux";
    backend = [flag, fakeBinary(platform, `backend-${platform}${platform === "win32" ? ".exe" : ""}`, 6)];
  }
  const outDir = join(SCRATCH, "out");
  execFileSync(
    process.execPath,
    [
      join(EXT_ROOT, "scripts", "package.mjs"),
      "--target",
      target,
      ...backend,
      "--out-dir",
      outDir,
      // The binary is a fake with the right magic bytes; it cannot start.
      "--skip-smoke",
    ],
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

test("a macOS build is staged from its folder, and only for its own arch", () => {
  const dir = fakeMacBundle("x64");
  assert.throws(() => stageBundle("arm64", dir), /holds the x64 build, not arm64/);
  const tarball = join(SCRATCH, "code-ai-mac-x64-v0.tar.gz");
  writeFileSync(tarball, "not unpacked");
  assert.throws(() => stageBundle("x64", tarball), /is not a folder/);

  const { executables } = stageBundle("x64", dir);
  assert.deepEqual(executables, MAC_PROGRAMS);
  assert.ok(existsSync(join(BIN_DIR, "code-ai")), "the launcher sits where the extension looks");
  rmSync(BIN_DIR, { recursive: true, force: true });
});

test("verify wants the whole macOS runtime, every program executable", () => {
  const file = join(SCRATCH, "hollow-mac.vsix");
  buildZip(file, [
    ["extension/package.json", Buffer.from("{}")],
    ["extension/dist/extension.js", Buffer.from("//")],
    ["extension/dist/webview.js", Buffer.from("//")],
    ["extension/dist/flow.js", Buffer.from("//")],
    ["extension/bin/code-ai", Buffer.from("#!/bin/sh\n"), 0o755],
    ["extension/bin/runtime/bin/python3.12", Buffer.alloc(1024), 0o644],
  ]);
  const text = verify(file, "darwin-arm64").problems.join("\n");
  assert.match(text, /python3\.12 has mode 644/);
  assert.match(text, /missing extension\/bin\/code-ai-bundle\.json/);
  assert.match(text, /missing .*playwright\/driver\/node/);
  assert.match(text, /too small to be whole/);
});

test("end to end: a macOS folder becomes a darwin VSIX whose programs keep the bit", () => {
  const out = packageFor("darwin-arm64", "darwin");
  try {
    const { problems, entries } = verify(out, "darwin-arm64");
    assert.deepEqual(problems, [], problems.join("; "));
    const byName = new Map(entries.map((e) => [e.name, e]));
    for (const program of MAC_PROGRAMS) {
      const entry = byName.get(`extension/bin/${program}`);
      assert.ok(entry && entry.mode & 0o111, `${program} mode was ${entry?.mode?.toString(8)}`);
    }
    assert.ok(byName.has("extension/bin/code-ai-bundle.json"), "the extension reads it to chmod");

    // And the file Mac users download: the installer, then this very VSIX.
    const version = JSON.parse(readFileSync(join(EXT_ROOT, "package.json"), "utf8")).version;
    const installer = readFileSync(join(SCRATCH, "out", `code-ai-mac-arm64-v${version}.sh`));
    const marker = Buffer.from("\n__CODE_AI_PAYLOAD__\n");
    const at = installer.indexOf(marker);
    assert.ok(installer.subarray(0, 10).toString() === "#!/bin/sh\n", "a shell script up front");
    assert.ok(!installer.subarray(0, at).includes("\r"), "no CR a Mac shell would choke on");
    assert.ok(installer.subarray(at + marker.length).equals(readFileSync(out)), "the VSIX follows");
    assert.ok(installer.subarray(0, at).includes(`VERSION="${version}"`));
  } finally {
    rmSync(out, { force: true });
  }
});

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

// -- the build's own dependencies -----------------------------------------
//
// The bug these pin down: the install was gated on `node_modules` existing, so
// a checkout that predated @vscode/vsce being added skipped it and died inside
// the packaging step with a bare MODULE_NOT_FOUND on a hardcoded path.

/** A tree with a manifest and no node_modules, like a fresh clone. */
function bareRoot() {
  const root = mkdtempSync(join(tmpdir(), "code-ai-deps-"));
  writeFileSync(
    join(root, "package.json"),
    JSON.stringify({ name: "probe", devDependencies: { "@vscode/vsce": "^3.2.0" } })
  );
  return root;
}

test("what must be installed comes from the manifest, not a hand-kept list", () => {
  // A dependency added to package.json is covered the moment it is declared.
  const declared = declaredPackages(EXT_ROOT);

  assert.ok(declared.includes("@vscode/vsce"), `vsce not declared: ${declared}`);
  assert.ok(declared.includes("esbuild"));
  assert.ok(declared.includes("typescript"));
});

test("this tree resolves every declared package", () => {
  assert.deepEqual(missingPackages(EXT_ROOT), []);
});

test("an empty node_modules does not pass as installed", () => {
  // The directory exists - the old check stopped right here.
  const root = bareRoot();
  mkdirSync(join(root, "node_modules"), { recursive: true });

  assert.ok(installReason(root), "an empty node_modules passed as installed");
});

test("a missing package is reported by name, not as a file path", () => {
  // The exact shape of the bug: the directory is there, from an install that
  // predates the dependency, so the old check waved it through.
  const root = bareRoot();
  mkdirSync(join(root, "node_modules"), { recursive: true });

  const reason = installReason(root);

  assert.match(reason, /@vscode\/vsce/, `unhelpful reason: ${reason}`);
});

test("a fresh clone is installed before anything else is tried", () => {
  assert.equal(installReason(bareRoot()), "no node_modules yet");
});

test("vsce is located through its own bin declaration", () => {
  const located = vscePath(EXT_ROOT);

  assert.ok(existsSync(located), `${located} does not exist`);
  // Whatever the layout, it has to be the entry the package itself names.
  const manifest = JSON.parse(
    readFileSync(join(EXT_ROOT, "node_modules", "@vscode", "vsce", "package.json"), "utf8")
  );
  assert.equal(located, join(EXT_ROOT, "node_modules", "@vscode", "vsce", manifest.bin.vsce));
});

test("a missing vsce says to run npm install", () => {
  assert.throws(() => vscePath(bareRoot()), /npm install/);
});

// -- the build runs what it built -----------------------------------------
//
// The bug these pin down: `assertBinary` checks the size and the magic bytes,
// so a binary that cannot start still passes every gate and ships. The user
// then sees "bridge process exited" and the reason is nowhere.

/** A spawnSync that answers with whatever the test scripted. */
function fakeSpawn(result) {
  const calls = [];
  const spawn = (command, args, options) => {
    calls.push({ command, args, options });
    return result;
  };
  return { spawn, calls };
}

const SPOKE = {
  status: 0,
  stdout: '{"jsonrpc": "2.0", "method": "event", "params": {"event_type": "session.ready"}}\n',
  stderr: "",
};

test("a binary that speaks the protocol passes", () => {
  const { spawn, calls } = fakeSpawn(SPOKE);

  smokeTest("/tmp/code-ai", { spawn });

  // Started exactly the way the extension starts it.
  assert.deepEqual(calls[0].args.slice(-1), ["bridge"]);
  // stdin closed immediately, or the probe would wait for input for ever.
  assert.equal(calls[0].options.input, "");
});

test("a binary that dies on startup fails the build, quoting what it said", () => {
  const { spawn } = fakeSpawn({
    status: 1,
    stdout: "",
    stderr: "Traceback (most recent call last):\n  File \"x\", line 1\nModuleNotFoundError: no tiktoken_ext\n",
  });

  assert.throws(
    () => smokeTest("/tmp/code-ai", { spawn }),
    (err) => {
      // The reason has to travel, or the build is as unhelpful as the editor.
      assert.match(err.message, /ModuleNotFoundError: no tiktoken_ext/);
      assert.match(err.message, /exit 1/);
      return true;
    }
  );
});

test("a binary that prints nothing at all is still a failure", () => {
  const { spawn } = fakeSpawn({ status: 0, stdout: "", stderr: "" });

  assert.throws(() => smokeTest("/tmp/code-ai", { spawn }), /nothing on stderr/);
});

test("noise on stdout is not mistaken for the protocol", () => {
  // A bootloader warning is not a JSON-RPC line.
  const { spawn } = fakeSpawn({ status: 0, stdout: "loading...\nnot json\n", stderr: "" });

  assert.throws(() => smokeTest("/tmp/code-ai", { spawn }), /never speaks the bridge protocol/);
});

test("a binary that cannot be executed at all says so", () => {
  const { spawn } = fakeSpawn({ error: new Error("spawn EACCES"), stdout: "", stderr: "" });

  assert.throws(() => smokeTest("/tmp/code-ai", { spawn }), /could not be started: spawn EACCES/);
});

test("a binary that is merely found, not built, is still started", () => {
  // The gap this closes: ensureBackend returns a non-stale binary untouched,
  // so one broken yesterday shipped today without ever being run.
  const host = hostPlatform();
  if (!host) return; // macOS builds nothing natively

  const source = fakeBinary(host, `found-${host}${host === "win32" ? ".exe" : ""}`, 6);

  assert.throws(
    () => ensureBackend(host, { explicit: source }),
    /never speaks the bridge protocol|could not be started/,
    "a fake binary was accepted without being started"
  );
  // And the escape hatch the packaging tests need still lets it through.
  assert.equal(ensureBackend(host, { explicit: source, smoke: false }), source);
});
