#!/usr/bin/env node
// Produce the frozen `code-ai` binary the VSIX has to carry, on any machine.
//
// Packaging used to stop with "no binary found" and a paragraph of what to do
// about it. Everything in that paragraph is mechanical, so it happens here
// instead: find a Python, build an isolated virtualenv, run PyInstaller. The
// only case that still cannot be served is the *other* platform's binary
// without Docker, and that one is skipped with a reason rather than failed.

import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
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
 * Build the host platform's binary with PyInstaller, in a virtualenv kept apart
 * from the developer's own `.venv`: this one carries PyInstaller and a
 * non-editable install of the project, and neither belongs in a dev env.
 */
function buildNative({ verifySsl }) {
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
  run(python, ["-m", "pip", "install", ...flags, "--upgrade", "pip"], "pip: upgrading", { quiet: true });
  // The project itself, not editable: PyInstaller's analysis follows the
  // installed package, and an editable install hides its data files.
  run(python, ["-m", "pip", "install", ...flags, "."], "pip: installing code-ai (a few minutes)");
  run(python, ["-m", "pip", "install", ...flags, "pyinstaller>=6,<7"], "pip: installing pyinstaller", {
    quiet: true,
  });
  run(python, ["-m", "PyInstaller", "--clean", "--noconfirm", SPEC], "pyinstaller: freezing");

  if (!existsSync(output)) throw new Error(`PyInstaller finished but ${output} is not there`);
  return output;
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
  const trusted = verifySsl ? "" : "export PIP_TRUSTED_HOST='pypi.org files.pythonhosted.org'; ";
  const image = platform === "linux" ? "python:3.12-bullseye" : "tobix/pywine:3.12";
  const script =
    platform === "linux"
      ? `${trusted}python -m venv /tmp/venv && . /tmp/venv/bin/activate && ` +
        `pip install --upgrade pip && pip install . "pyinstaller>=6,<7" && ` +
        `pyinstaller --clean --noconfirm packaging/code-ai.spec`
      : `${trusted}wine python -m pip install --upgrade pip && ` +
        `wine python -m pip install . "pyinstaller>=6,<7" && ` +
        `wine python -m PyInstaller --clean --noconfirm packaging/code-ai.spec`;

  console.log(`  docker: cross-building in ${image} (slow the first time)`);
  run("docker", ["run", "--rm", "-v", `${REPO_ROOT}:/work`, "-w", "/work", image, "bash", "-euc", script]);
  if (!existsSync(output)) throw new Error(`the container finished but ${output} is not there`);
  return output;
}

/**
 * Path to a usable binary for `platform`, building one if that is what it
 * takes. Throws {@link SkippedTarget} when this machine cannot produce it, so
 * the caller can carry on with the targets it can.
 */
export function ensureBackend(platform, { explicit = null, verifySsl = false, refresh = false } = {}) {
  if (explicit) {
    const path = resolve(explicit);
    if (!existsSync(path)) throw new Error(`${path} does not exist`);
    assertBinary(path, platform);
    return path;
  }

  const buildable = platform === hostPlatform();
  const found = refresh ? null : PLATFORMS[platform].candidates.find((file) => existsSync(file));
  if (found) {
    assertBinary(found, platform);
    // A binary older than src/ would ship yesterday's agent inside today's
    // package. Rebuild it where that is possible; say so where it is not,
    // because a downloaded artifact is still better than no package at all.
    if (!isStale(found)) return found;
    if (!buildable) {
      console.log(`  warning: ${found} is older than src/, and only a ${platform} machine can refresh it`);
      return found;
    }
    console.log("  backend is older than src/, rebuilding");
  }

  if (buildable) {
    const built = buildNative({ verifySsl });
    assertBinary(built, platform);
    return built;
  }

  if (hasDocker()) {
    const cross = buildInDocker(platform, { verifySsl });
    assertBinary(cross, platform);
    return cross;
  }

  throw new SkippedTarget(
    `no ${PLATFORMS[platform].format} binary, and this machine cannot build one: ` +
      `cross-building needs Docker. Run the build-binaries workflow and pass ` +
      `--${platform === "win32" ? "win" : "linux"} <path>, or build it on a ${platform} machine.`
  );
}
