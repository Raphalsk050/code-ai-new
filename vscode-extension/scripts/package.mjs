#!/usr/bin/env node
// Build one VSIX per platform, each carrying its own frozen backend.
//
// Platform-specific VSIXes rather than a single universal one: the backend is
// the bulk of the download, and a package holding both would make every user
// fetch a binary they cannot run. VSCode picks the right one when installing
// from a marketplace, and `code --install-extension` accepts the file directly.

import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { BIN_DIR, PLATFORMS, stageBinary } from "./bundle-binary.mjs";
import { SkippedTarget, ensureBackend } from "./ensure-backend.mjs";
import { makeEntryExecutable, verify } from "./verify-vsix.mjs";

const EXT_ROOT = fileURLToPath(new URL("..", import.meta.url));
const DEFAULT_OUT_DIR = join(EXT_ROOT, "vsix");
// vsce is a devDependency rather than an `npx --yes` download: packaging then
// works offline and on the company network, and the version is pinned by the
// lockfile instead of being whatever npm resolved that morning.
const VSCE = join(EXT_ROOT, "node_modules", "@vscode", "vsce", "vsce");

/** VSCode's target triples, and the PyInstaller platform each one needs. */
const TARGETS = {
  "win32-x64": "win32",
  "linux-x64": "linux",
};

function run(command, args, label) {
  process.stdout.write(`  ${label}\n`);
  execFileSync(command, args, { cwd: EXT_ROOT, stdio: "inherit" });
}

function parseArgs(argv) {
  const args = {
    targets: [],
    win: null,
    linux: null,
    skipVerify: false,
    verifySsl: false,
    refresh: false,
    outDir: null,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--target") args.targets.push(...argv[++i].split(","));
    else if (arg === "--win") args.win = argv[++i];
    else if (arg === "--linux") args.linux = argv[++i];
    else if (arg === "--skip-verify") args.skipVerify = true;
    else if (arg === "--verify-ssl") args.verifySsl = true;
    else if (arg === "--refresh-backend") args.refresh = true;
    else if (arg === "--out-dir") args.outDir = argv[++i];
    else throw new Error(`unknown option: ${arg}`);
  }
  return args;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const version = JSON.parse(readFileSync(join(EXT_ROOT, "package.json"), "utf8")).version;
  // No target list means every platform. Whichever ones this machine cannot
  // produce are reported at the end instead of stopping the build: a Windows
  // box with no Docker should still come out of this holding a Windows package.
  const chosen = args.targets.length > 0;
  const targets = chosen ? args.targets : Object.keys(TARGETS);

  console.log(`Building bundles`);
  run(process.execPath, ["esbuild.mjs"], "esbuild");

  // Tests build into a scratch directory: writing to vsix/ would delete a real
  // package sitting there under the same version-derived name.
  const outDir = args.outDir ? resolve(args.outDir) : DEFAULT_OUT_DIR;
  mkdirSync(outDir, { recursive: true });
  const built = [];
  const skipped = [];

  try {
    for (const target of targets) {
      const platform = TARGETS[target];
      if (!platform) {
        throw new Error(`unknown target "${target}" (expected ${Object.keys(TARGETS).join(", ")})`);
      }
      console.log(`\n${target}`);

      let source;
      try {
        source = ensureBackend(platform, {
          explicit: platform === "win32" ? args.win : args.linux,
          verifySsl: args.verifySsl,
          refresh: args.refresh,
        });
      } catch (err) {
        // A target the user named has to be built or the run has failed; one we
        // picked ourselves is allowed to be out of reach on this machine.
        if (!(err instanceof SkippedTarget) || chosen) throw err;
        console.log(`  skipped: ${err.message}`);
        skipped.push([target, err.message]);
        continue;
      }
      const { size } = stageBinary(platform, source);
      console.log(`  staged ${relative(EXT_ROOT, source)} (${(size / 1024 / 1024).toFixed(1)} MB)`);

      const out = join(outDir, `code-ai-${version}-${target}.vsix`);
      rmSync(out, { force: true });
      run(
        process.execPath,
        [VSCE, "package", "--target", target, "--out", out, "--allow-missing-repository", "--skip-license"],
        `vsce package --target ${target}`
      );

      // vsce copies the mode off the filesystem, and a Windows host has no
      // executable bit to copy: without this, a Linux package built here
      // unpacks as a file Linux will not run.
      const mode = makeEntryExecutable(out, `extension/bin/${PLATFORMS[platform].staged}`);
      console.log(`  backend mode ${(mode & 0o777).toString(8)}`);

      if (!args.skipVerify) {
        const { problems, warnings, binary, entries } = verify(out, target);
        console.log(`  verify: ${entries.length} entries, backend ${(binary.size / 1024 / 1024).toFixed(1)} MB`);
        for (const warning of warnings) console.log(`  warning: ${warning}`);
        if (problems.length) {
          for (const problem of problems) console.error(`  FAIL: ${problem}`);
          process.exit(1);
        }
      }
      built.push(out);
    }
  } finally {
    // bin/ is staging, not an artifact: leaving a binary there would silently
    // bloat the next package built with --skip-verify, and it survives a
    // failure halfway through the loop.
    rmSync(BIN_DIR, { recursive: true, force: true });
  }

  if (!built.length) {
    throw new Error(
      `No package could be built.\n\n` + skipped.map(([target, why]) => `${target}: ${why}`).join("\n\n")
    );
  }

  console.log(`\nBuilt ${built.length} package(s):`);
  for (const file of built) console.log(`  ${relative(resolve(EXT_ROOT, ".."), file)}`);
  for (const [target] of skipped) console.log(`  ${target}: not built on this machine (see above)`);
  console.log(`\nInstall with: code --install-extension <file>`);
}

try {
  main();
} catch (err) {
  console.error(String(err.message ?? err));
  process.exit(1);
}
