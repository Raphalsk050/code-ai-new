#!/usr/bin/env node
// Lay out the backend one platform's package carries, as the plugin's bin/
// folder, so that installing the package is all there is to do: no Python, no
// pip, no PATH entry.
//
// The backend is the VS Code extension's, made by the same scripts: the frozen
// binary for Linux and Windows (vscode-extension/scripts/ensure-backend.mjs),
// the folder with its own Python for macOS (scripts/build_mac_bundle.py). This
// asks them for the one a target needs, builds it when it is not there or is
// older than the sources, and copies it where the build picks it up.
//
//   node scripts/stage-backend.mjs --target linux-x64 --out build/backend/linux-x64
//
//   --target T      linux-x64, win32-x64, darwin-arm64 or darwin-x64
//   --out DIR       the folder to fill (emptied first)
//   --from PATH     use this binary, or this macOS folder, instead of building one
//   --refresh       build it again even if it looks up to date
//   --verify-ssl    keep pip's certificate checks on
//
// Exit status 3 means this machine cannot make that target's backend, and why
// is on the last line.

import { execFileSync } from "node:child_process";
import { chmodSync, copyFileSync, cpSync, existsSync, mkdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { PLATFORMS } from "../../vscode-extension/scripts/bundle-binary.mjs";
import {
  SkippedTarget,
  ensureBackend,
  findPython,
  isStale,
} from "../../vscode-extension/scripts/ensure-backend.mjs";

const REPO_ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));

/** The packages there are, as the VS Code extension names its own, and the backend each needs. */
export const TARGETS = {
  "linux-x64": "linux",
  "win32-x64": "win32",
  "darwin-arm64": "darwin",
  "darwin-x64": "darwin",
};

function parseArgs(argv) {
  const args = { target: null, out: null, from: null, refresh: false, verifySsl: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--target") args.target = argv[++i];
    else if (arg === "--out") args.out = argv[++i];
    else if (arg === "--from") args.from = argv[++i];
    else if (arg === "--refresh") args.refresh = true;
    else if (arg === "--verify-ssl") args.verifySsl = true;
    else throw new Error(`unknown option: ${arg}`);
  }
  if (!args.target || !args.out) throw new Error("--target and --out are both needed");
  if (!TARGETS[args.target]) {
    throw new Error(`unknown target "${args.target}" (expected ${Object.keys(TARGETS).join(", ")})`);
  }
  return args;
}

/** What scripts/build_mac_bundle.py says of a folder it wrote, or null when it is not one. */
function macManifest(folder) {
  try {
    return JSON.parse(readFileSync(join(folder, "code-ai-bundle.json"), "utf8"));
  } catch {
    return null;
  }
}

/**
 * A macOS folder that can still be shipped: of the right processor, and with
 * its Python packed. One that was started where it lies has unpacked it, into
 * some eleven thousand files, and thrown the archive away.
 */
function shippable(folder, arch) {
  return macManifest(folder)?.arch === arch && existsSync(join(folder, "runtime.tar.gz"));
}

/**
 * The folder holding the macOS build for one processor, assembled when it is
 * missing or older than the sources. Any machine can: it is a relocatable
 * CPython and macOS wheels, downloaded, with no compiler in sight.
 */
function macBundle(arch, { from, refresh }) {
  if (from) {
    const folder = resolve(from);
    if (!shippable(folder, arch)) {
      throw new Error(
        `${folder} is not a macOS ${arch} build that can be shipped: it needs code-ai-bundle.json ` +
          `for ${arch} and runtime.tar.gz, as scripts/build_mac_bundle.py leaves them.`
      );
    }
    return folder;
  }
  const folder = join(REPO_ROOT, "dist", "mac", `code-ai-mac-${arch}`);
  if (!refresh && shippable(folder, arch) && !isStale(join(folder, "code-ai-bundle.json"))) return folder;

  const python = findPython();
  if (!python) {
    throw new SkippedTarget(
      "assembling the macOS build needs Python 3.11 or newer, and none was found. " +
        `Install one, or pass a folder made elsewhere with -PbackendDir.`
    );
  }
  console.log(`  assembling the macOS ${arch} build (downloads a Python and the wheels the first time)`);
  execFileSync(
    python.cmd,
    [...python.args, join(REPO_ROOT, "scripts", "build_mac_bundle.py"), "--arch", arch],
    { cwd: REPO_ROOT, stdio: "inherit" }
  );
  if (!shippable(folder, arch)) throw new Error(`scripts/build_mac_bundle.py finished and ${folder} is not there`);
  return folder;
}

function megabytes(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const platform = TARGETS[args.target];
  const out = resolve(args.out);
  console.log(`Backend for ${args.target}`);

  let staged;
  if (platform === "darwin") {
    const folder = macBundle(args.target.split("-")[1], args);
    rmSync(out, { recursive: true, force: true });
    cpSync(folder, out, { recursive: true });
    for (const file of macManifest(out).executables) chmodSync(join(out, file), 0o755);
    staged = `${folder} (${megabytes(statSync(join(out, "runtime.tar.gz")).size)})`;
  } else {
    const binary = ensureBackend(platform, {
      explicit: args.from,
      verifySsl: args.verifySsl,
      refresh: args.refresh,
    });
    rmSync(out, { recursive: true, force: true });
    mkdirSync(out, { recursive: true });
    const target = join(out, PLATFORMS[platform].staged);
    copyFileSync(binary, target);
    // The package keeps this bit; the plugin sets it again on its first start,
    // for a package that was unpacked by something that does not.
    chmodSync(target, 0o755);
    staged = `${binary} (${megabytes(statSync(target).size)})`;
  }
  console.log(`  staged ${staged}`);
}

try {
  main();
} catch (err) {
  console.error(String(err.message ?? err));
  process.exit(err instanceof SkippedTarget ? 3 : 1);
}
