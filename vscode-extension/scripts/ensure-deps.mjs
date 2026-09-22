#!/usr/bin/env node
// Make sure the build's devDependencies are actually installed.
//
// The old check was `[ -d node_modules ]`, which is true of any tree that ever
// ran an install - including one that predates a dependency being added. That
// tree then skipped the install and died several minutes later inside the
// packaging step, pointing at a file path instead of at the missing package.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const EXT_ROOT = fileURLToPath(new URL("..", import.meta.url));

/**
 * Every package name the manifest declares, runtime and dev alike.
 *
 * The declarations are the question, not a hand-kept list: a dependency added
 * to package.json and never installed is exactly the failure this guards.
 */
export function declaredPackages(root = EXT_ROOT) {
  const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
  return [
    ...Object.keys(manifest.dependencies ?? {}),
    ...Object.keys(manifest.devDependencies ?? {}),
  ];
}

/** The declared packages that cannot be resolved from `root`. */
export function missingPackages(root = EXT_ROOT) {
  const require = createRequire(join(root, "package.json"));
  return declaredPackages(root).filter((name) => {
    try {
      require.resolve(`${name}/package.json`);
      return false;
    } catch {
      // Some packages export no package.json; resolving the entry proves the
      // install just as well.
      try {
        require.resolve(name);
        return false;
      } catch {
        return true;
      }
    }
  });
}

/** Why an install is needed, or "" when the tree is already good. */
export function installReason(root = EXT_ROOT) {
  if (!existsSync(join(root, "node_modules"))) return "no node_modules yet";
  const missing = missingPackages(root);
  return missing.length ? `missing ${missing.join(", ")}` : "";
}

/**
 * Absolute path to vsce's entry script, read from the package's own `bin`.
 *
 * Hardcoding `node_modules/@vscode/vsce/vsce` assumed one layout of one
 * version; asking the package where its binary lives survives the next one.
 */
export function vscePath(root = EXT_ROOT) {
  const require = createRequire(join(root, "package.json"));
  let manifest;
  try {
    manifest = require.resolve("@vscode/vsce/package.json");
  } catch {
    throw new Error("@vscode/vsce is not installed. Run `npm install` in vscode-extension/.");
  }
  const { bin } = require(manifest);
  const entry = typeof bin === "string" ? bin : bin?.vsce;
  if (!entry) throw new Error(`@vscode/vsce declares no vsce binary (${manifest})`);
  return join(dirname(manifest), entry);
}

/** Install the build dependencies when any of them is missing. */
export function ensureDeps(root = EXT_ROOT) {
  const reason = installReason(root);
  if (!reason) return "";
  console.log(`Installing build dependencies (${reason})...`);
  // npm.cmd directly rather than through a shell: a shell here only buys an
  // unescaped argv. --prefer-offline takes what the cache already has, which
  // is most of the tree and matters behind a proxy.
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  execFileSync(npm, ["install", "--no-audit", "--no-fund", "--prefer-offline"], {
    cwd: root,
    stdio: "inherit",
  });
  return reason;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) ensureDeps();
