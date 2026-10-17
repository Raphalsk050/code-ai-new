#!/usr/bin/env node
// Produce the frozen `code-ai` binary the VSIX has to carry, on any machine.
//
// Packaging used to stop with "no binary found" and a paragraph of what to do
// about it. Everything in that paragraph is mechanical, so it happens here
// instead: find a Python, build an isolated virtualenv, run PyInstaller. The
// only case that still cannot be served is the *other* platform's binary
// without Docker, and that one is skipped with a reason rather than failed.

import { execFileSync, spawnSync } from "node:child_process";
import {
  accessSync,
  constants,
  copyFileSync,
  cpSync,
  existsSync,
  mkdtempSync,
  readdirSync,
  rmSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { PLATFORMS, assertBinary } from "./bundle-binary.mjs";

const EXT_ROOT = fileURLToPath(new URL("..", import.meta.url));
const REPO_ROOT = resolve(EXT_ROOT, "..");
const BUILD_VENV = join(REPO_ROOT, ".build-venv");
const SPEC = join(REPO_ROOT, "packaging", "code-ai.spec");

/** Raised for a target this machine cannot produce, but could skip. */
export class SkippedTarget extends Error {}

/** Which PLATFORMS key describes the machine running this. */
export function hostPlatform() {
  if (process.platform === "win32") return "win32";
  if (process.platform === "linux") return "linux";
  return null; // macOS: the spec has no target for it, so nothing to build natively
}

/**
 * The company proxy re-signs TLS with a certificate the build environment does
 * not have, so pip is told to trust the PyPI hosts - the same two the browser
 * installer trusts. `--verify-ssl` puts the checks back for a normal network.
 */
function pipFlags(verifySsl) {
  return verifySsl ? [] : ["--trusted-host", "pypi.org", "--trusted-host", "files.pythonhosted.org"];
}

function run(cmd, args, label, { quiet = false } = {}) {
  if (label) console.log(`  ${label}`);
  execFileSync(cmd, args, { cwd: REPO_ROOT, stdio: quiet ? "pipe" : "inherit" });
}

/** Does this interpreter exist and satisfy requires-python? */
function usable(cmd, args) {
  const probe = spawnSync(cmd, [...args, "-c", "import sys; raise SystemExit(sys.version_info < (3, 11))"], {
    stdio: "ignore",
  });
  return probe.status === 0;
}

/**
 * An interpreter new enough to build with. Newest first, but through the `py`
 * launcher on Windows: a bare `python` there is as likely to be a Store stub as
 * a real install.
 */
export function findPython() {
  const candidates =
    process.platform === "win32"
      ? [
          ["py", ["-3.13"]],
          ["py", ["-3.12"]],
          ["py", ["-3.11"]],
          ["py", ["-3"]],
          ["python", []],
        ]
      : [
          ["python3.13", []],
          ["python3.12", []],
          ["python3.11", []],
          ["python3", []],
          ["python", []],
        ];
  for (const [cmd, args] of candidates) {
    if (usable(cmd, args)) return { cmd, args };
  }
  return null;
}

/** The interpreter inside a virtualenv, whichever layout the platform uses. */
function venvPython(dir) {
  const win = join(dir, "Scripts", "python.exe");
  return existsSync(win) ? win : join(dir, "bin", "python");
}

/** Newest mtime under a directory tree, for deciding whether to rebuild. */
function newestMtime(dir) {
  let newest = 0;
  const walk = (current) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (entry.name === "__pycache__" || entry.name.endsWith(".pyc")) continue;
      const full = join(current, entry.name);
      if (entry.isDirectory()) walk(full);
      else newest = Math.max(newest, statSync(full).mtimeMs);
    }
  };
  walk(dir);
  return newest;
}

/**
 * True when the binary is older than the sources it was built from. Freezing a
 * one-file bundle costs minutes, so it is worth not doing when nothing moved -
 * and worth always doing when something did, or a fix ships inside a backend
 * that predates it.
 */
export function isStale(binary) {
  if (!existsSync(binary)) return true;
  const built = statSync(binary).mtimeMs;
  const sources = Math.max(
    newestMtime(join(REPO_ROOT, "src")),
    statSync(SPEC).mtimeMs,
    statSync(join(REPO_ROOT, "pyproject.toml")).mtimeMs
  );
  return sources > built;
}

/**
 * The uid/gid to run a container as, so what it writes into the mounted repo
 * belongs to the caller.
 *
 * Without this the container runs as root and leaves root-owned
 * `src/*.egg-info`, `build/` and `dist/` behind on the host; the next native
 * build then dies in setuptools with "Cannot update time stamp of directory".
 * Windows hosts do not map uids into the daemon, so there it is left alone.
 */
function hostUser() {
  if (process.platform === "win32") return null;
  try {
    return `${process.getuid()}:${process.getgid()}`;
  } catch {
    return null;
  }
}

/**
 * The `docker run` argv for a cross-build, with the ownership problem handled.
 *
 * The Linux image is happy as any uid, so it runs as the caller and writes
 * files the caller owns; HOME has to move with it, because a uid with no passwd
 * entry has no home and pip will not cache into one it cannot write. Wine needs
 * to be root for its prefix, so that image stays root and hands its droppings
 * back instead - otherwise the next native build meets a root-owned egg-info
 * and dies in setuptools.
 *
 * Exported for the tests: the argv is the whole behaviour, and it is cheaper to
 * assert on than to observe through a container.
 */
export function dockerRunArgs({ platform, image, script, mount, user }) {
  const asCaller = user && platform === "linux" ? ["--user", user, "-e", "HOME=/tmp"] : [];
  const handBack =
    user && platform !== "linux"
      ? ` && chown -R ${user} /work/dist /work/build 2>/dev/null; rm -rf /work/src/*.egg-info`
      : "";
  return [
    "run",
    "--rm",
    ...asCaller,
    "-v",
    `${mount}:/work`,
    "-w",
    "/work",
    image,
    "bash",
    "-euc",
    script + handBack,
  ];
}

/**
 * Sweep away an `*.egg-info` an older build left in the source tree.
 *
 * Nothing depends on this any more - the install builds from a copy, so the
 * source tree is never written to - but a leftover from a build that predates
 * that change is confusing to come across, and clearing it costs nothing. One
 * that belongs to another user is left alone: it is inert, and failing a build
 * over a directory nothing reads would be the old bug wearing a new hat.
 *
 * Returns the paths it could not remove, so a caller can mention them.
 */
export function cleanEggInfo(root = REPO_ROOT) {
  const src = join(root, "src");
  if (!existsSync(src)) return [];
  const stuck = [];
  for (const name of readdirSync(src)) {
    if (!name.endsWith(".egg-info")) continue;
    const path = join(src, name);
    try {
      rmSync(path, { recursive: true, force: true });
    } catch {
      stuck.push(path);
    }
  }
  return stuck;
}

/**
 * The files a wheel build needs: what pyproject declares, and nothing else.
 *
 * `readme` points at README.md and the version is read from the package, so a
 * copy of these three is a complete, buildable project.
 */
const BUILD_INPUTS = ["pyproject.toml", "README.md", "src"];

/** Where the cross-build container stages that copy, inside the container. */
const STAGE = "/tmp/code-ai-build";

/** Names that never travel into the staging copy. Exported for the tests. */
export function skipFromCopy(name) {
  return name === "__pycache__" || name.endsWith(".egg-info");
}

/**
 * Install the project into `python`, from a copy rather than in place.
 *
 * setuptools runs egg_info inside the project directory and updates that
 * directory's timestamp. In a tree whose `src/*.egg-info` belongs to another
 * user the install dies there - and says "Cannot update time stamp of
 * directory", which sends people looking at clocks instead of at ownership.
 * Building from a copy means the source tree is never written to at all, so a
 * poisoned leftover (or a read-only checkout) stops mattering.
 */
function installProject(python, flags) {
  const staging = mkdtempSync(join(tmpdir(), "code-ai-build-"));
  try {
    for (const name of BUILD_INPUTS) {
      const from = join(REPO_ROOT, name);
      if (!existsSync(from)) continue;
      cpSync(from, join(staging, name), {
        recursive: true,
        // Nothing generated travels: an egg-info or a __pycache__ from the
        // source tree would defeat the point of copying. Matched on the name
        // rather than on the path, so no separator has to be guessed.
        filter: (path) => !skipFromCopy(basename(path)),
      });
    }
    // Not editable: PyInstaller's analysis follows the installed package, and
    // an editable install points back at the tree and hides its data files.
    run(python, ["-m", "pip", "install", ...flags, staging], "pip: installing code-ai (a few minutes)");
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }
}

/**
 * Fail before the slow part when the repo cannot take the artifact.
 *
 * `dist/` is the one path the build must write in the tree, so a root-owned one
 * is worth catching in the first second rather than after several minutes of
 * pip and PyInstaller.
 */
function assertDistWritable() {
  const dist = join(REPO_ROOT, "dist");
  if (!existsSync(dist)) return;
  try {
    accessSync(dist, constants.W_OK);
  } catch {
    throw new Error(
      `${dist} is not writable by this user.\n` +
        "It belongs to another user - usually a container or a sudo build that ran\n" +
        "here before. Take it back and build again:\n\n" +
        `    sudo chown -R "$(id -u):$(id -g)" ${dist}\n`
    );
  }
}

/**
 * Build the host platform's binary with PyInstaller, in a virtualenv kept apart
 * from the developer's own `.venv`: this one carries PyInstaller and a
 * non-editable install of the project, and neither belongs in a dev env.
 */
/**
 * Start the binary the way the extension does and require it to answer.
 *
 * `bridge` with stdin already closed is the whole contract: the backend boots,
 * writes its startup events to stdout as JSON lines, sees EOF and exits. A
 * binary missing a hidden import dies before the first line, which is the
 * failure this catches - and it catches it here, where the stderr is visible,
 * rather than in an editor that logs it somewhere nobody looks.
 *
 * Only ever called for a binary this machine can execute; a cross-built one is
 * verified by the CI job that runs on its own platform.
 */
export function smokeTest(binary, { timeout = 180_000, spawn = spawnSync } = {}) {
  const probe = spawn(binary, ["--workspace", REPO_ROOT, "bridge"], {
    input: "",
    encoding: "utf8",
    timeout,
    cwd: REPO_ROOT,
  });
  if (probe.error) {
    throw new Error(`${binary} could not be started: ${probe.error.message}`);
  }
  const stdout = probe.stdout || "";
  const spoke = stdout.split(/\r?\n/).some((line) => {
    const text = line.trim();
    if (!text.startsWith("{")) return false;
    try {
      return typeof JSON.parse(text) === "object";
    } catch {
      return false;
    }
  });
  if (spoke) return;
  const said = (probe.stderr || "").trim().split(/\r?\n/).slice(-12).join("\n");
  throw new Error(
    `${binary} starts but never speaks the bridge protocol (exit ${probe.status}).\n` +
      "The extension would show \"bridge process exited\". What it printed:\n\n" +
      (said || "(nothing on stderr)") +
      "\n"
  );
}

/**
 * The desktop avatar, put into the package before it is frozen, as CI's
 * desktop-avatar job does for its builds: the page it shows, and on this
 * machine's own platform the window that shows it (desktop-avatar/, in Rust).
 * Missing either, the backend still builds, only without the avatar, and
 * switching it on then says it is not part of this build - which is how a
 * package built here once shipped without it unnoticed. So it is said here.
 */
function prepareDesktopAvatar({ host }) {
  const assets = join(REPO_ROOT, "src", "code_ai", "assets", "desktop_avatar");
  try {
    run(process.execPath, [join(EXT_ROOT, "scripts", "desktop-avatar.mjs")], "desktop avatar: page", {
      quiet: true,
    });
  } catch (err) {
    console.log(`  warning: no desktop avatar in this package, its page did not build: ${err.message ?? err}`);
    return;
  }
  // macOS draws it from Python, already in the package; another platform's
  // window is CI's to build.
  if (!host || process.platform === "darwin") return;
  const name = process.platform === "win32" ? "code-ai-avatar.exe" : "code-ai-avatar";
  const crate = join(REPO_ROOT, "desktop-avatar");
  try {
    run("cargo", ["build", "--release", "--manifest-path", join(crate, "Cargo.toml")], "desktop avatar: window (cargo)");
    copyFileSync(join(crate, "target", "release", name), join(assets, name));
  } catch (err) {
    console.log(
      "  warning: no desktop avatar in this package, its window did not build. " +
        `It needs Rust (rustup.rs) and crates.io: ${err.message ?? err}`
    );
  }
}

function buildNative({ verifySsl }) {
  assertDistWritable();
  prepareDesktopAvatar({ host: true });
  const spec = PLATFORMS[hostPlatform()];
  const output = join(REPO_ROOT, "dist", spec.staged);
  let python = venvPython(BUILD_VENV);
  if (!existsSync(python)) {
    const found = findPython();
    if (!found) {
      throw new Error(
        "Python 3.11 or newer is required to build the backend, and none was found.\n" +
          "Install it from python.org, or pass a prebuilt binary with --win/--linux."
      );
    }
    console.log("  creating .build-venv");
    run(found.cmd, [...found.args, "-m", "venv", BUILD_VENV], null, { quiet: true });
    python = venvPython(BUILD_VENV);
  }

  const flags = pipFlags(verifySsl);
  cleanEggInfo();
  run(python, ["-m", "pip", "install", ...flags, "--upgrade", "pip"], "pip: upgrading", { quiet: true });
  installProject(python, flags);
  run(python, ["-m", "pip", "install", ...flags, "pyinstaller>=6,<7"], "pip: installing pyinstaller", {
    quiet: true,
  });
  // `build/` is scratch, so it goes to a temp directory: one less path in the
  // repo that has to be writable, and one less leftover between builds.
  const workpath = mkdtempSync(join(tmpdir(), "code-ai-pyinstaller-"));
  try {
    run(
      python,
      ["-m", "PyInstaller", "--clean", "--noconfirm", "--workpath", workpath, SPEC],
      "pyinstaller: freezing"
    );
  } finally {
    rmSync(workpath, { recursive: true, force: true });
  }

  if (!existsSync(output)) throw new Error(`PyInstaller finished but ${output} is not there`);
  return output;
}

/**
 * The shell the cross-build container runs.
 *
 * It mirrors the native build: the project is installed from a copy under
 * `/tmp`, so setuptools never runs egg_info inside the mounted repo, and the
 * only thing the container writes back into the tree is `dist/`. Installing
 * happens from inside the copy rather than by absolute path, because the Wine
 * image's interpreter reads its arguments as Windows paths and a bare `.`
 * needs no translation.
 *
 * Exported for the tests: a container run costs minutes, the string does not.
 */
export function containerScript({ platform, verifySsl }) {
  const trusted = verifySsl ? "" : "export PIP_TRUSTED_HOST='pypi.org files.pythonhosted.org'; ";
  const stage =
    `rm -rf ${STAGE} && mkdir -p ${STAGE} && cp -a ${BUILD_INPUTS.join(" ")} ${STAGE}/ && ` +
    `rm -rf ${STAGE}/src/*.egg-info`;
  if (platform === "linux") {
    return (
      `${trusted}python -m venv /tmp/venv && . /tmp/venv/bin/activate && ` +
      `pip install --upgrade pip && ${stage} && (cd ${STAGE} && pip install .) && ` +
      `pip install "pyinstaller>=6,<7" && ` +
      // Scratch to /tmp as well, so a root-owned `build/` on the host - from a
      // container that ran before this - cannot fail the freeze.
      `pyinstaller --clean --noconfirm --workpath /tmp/build packaging/code-ai.spec`
    );
  }
  return (
    `${trusted}${stage} && wine python -m pip install --upgrade pip && ` +
    `(cd ${STAGE} && wine python -m pip install .) && ` +
    `wine python -m pip install "pyinstaller>=6,<7" && ` +
    // The Wine image keeps the default workpath: it runs as root and hands
    // `build/` back afterwards, and a unix --workpath would reach its
    // interpreter as a Windows path.
    `wine python -m PyInstaller --clean --noconfirm packaging/code-ai.spec`
  );
}

/** Is Docker usable from here? Cross-building needs it and nothing else. */
function hasDocker() {
  return spawnSync("docker", ["info"], { stdio: "ignore" }).status === 0;
}

/**
 * Cross-build the other platform's binary in a container. PyInstaller cannot
 * cross-compile, so the Windows one runs a real Windows interpreter under Wine
 * - the same images the CI workflow uses.
 */
function buildInDocker(platform, { verifySsl }) {
  const spec = PLATFORMS[platform];
  const output = join(REPO_ROOT, "dist", spec.staged);
  const image = platform === "linux" ? "python:3.12-bullseye" : "tobix/pywine:3.12";
  const script = containerScript({ platform, verifySsl });

  console.log(`  docker: cross-building in ${image} (slow the first time)`);
  prepareDesktopAvatar({ host: false });
  cleanEggInfo();
  run("docker", dockerRunArgs({ platform, image, script, mount: REPO_ROOT, user: hostUser() }));
  if (!existsSync(output)) throw new Error(`the container finished but ${output} is not there`);
  return output;
}

/**
 * Path to a usable binary for `platform`, building one if that is what it
 * takes. Throws {@link SkippedTarget} when this machine cannot produce it, so
 * the caller can carry on with the targets it can.
 */
/**
 * Check a binary is what it claims to be, and - when this machine can run it -
 * that it actually starts.
 *
 * The shape check alone let a binary that dies on its first import ship: it is
 * an ELF of the right size, so nothing objected until the editor tried to talk
 * to it. A binary for another platform cannot be started here, so it gets the
 * shape check and the CI job on its own platform.
 */
function accept(path, platform, smoke = true) {
  assertBinary(path, platform);
  if (smoke && platform === hostPlatform()) {
    console.log("  smoke: starting the binary");
    smokeTest(path);
  }
  return path;
}

export function ensureBackend(
  platform,
  { explicit = null, verifySsl = false, refresh = false, smoke = true } = {}
) {
  if (explicit) {
    const path = resolve(explicit);
    if (!existsSync(path)) throw new Error(`${path} does not exist`);
    return accept(path, platform, smoke);
  }

  const buildable = platform === hostPlatform();
  const found = refresh ? null : PLATFORMS[platform].candidates.find((file) => existsSync(file));
  if (found) {
    // A binary older than src/ would ship yesterday's agent inside today's
    // package. Rebuild it where that is possible; say so where it is not,
    // because a downloaded artifact is still better than no package at all.
    if (!isStale(found)) return accept(found, platform, smoke);
    if (!buildable) {
      console.log(`  warning: ${found} is older than src/, and only a ${platform} machine can refresh it`);
      return accept(found, platform, smoke);
    }
    console.log("  backend is older than src/, rebuilding");
  }

  if (buildable) {
    return accept(buildNative({ verifySsl }), platform, smoke);
  }

  if (hasDocker()) {
    return accept(buildInDocker(platform, { verifySsl }), platform, smoke);
  }

  throw new SkippedTarget(
    `no ${PLATFORMS[platform].format} binary, and this machine cannot build one: ` +
      `cross-building needs Docker. Run the build-binaries workflow and pass ` +
      `--${platform === "win32" ? "win" : "linux"} <path>, or build it on a ${platform} machine.`
  );
}
