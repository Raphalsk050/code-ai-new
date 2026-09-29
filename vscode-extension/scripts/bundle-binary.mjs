#!/usr/bin/env node
// Stage the frozen `code-ai` executable inside the extension, so the VSIX
// carries its own backend and a first-time install needs nothing else on the
// machine - no Python, no pip, no virtualenv, no PATH entry.
//
// The binary itself comes from PyInstaller (packaging/code-ai.spec), built
// either locally or by .github/workflows/build-binaries.yml. This script only
// moves it into place and refuses the mistakes that produce a VSIX which
// installs cleanly and then cannot start.

import {
  chmodSync,
  closeSync,
  copyFileSync,
  cpSync,
  mkdirSync,
  openSync,
  readdirSync,
  readFileSync,
  readSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const EXT_ROOT = fileURLToPath(new URL("..", import.meta.url));
const REPO_ROOT = resolve(EXT_ROOT, "..");
export const BIN_DIR = join(EXT_ROOT, "bin");

/** The two platforms CI builds, and where each one's binary tends to be. */
export const PLATFORMS = {
  win32: {
    /** Name inside the VSIX; must match src/resolve-command.ts. */
    staged: "code-ai.exe",
    /** Magic bytes of a PE executable. */
    magic: Buffer.from("MZ", "ascii"),
    format: "Windows PE",
    candidates: [
      join(REPO_ROOT, "dist", "code-ai.exe"),
      join(REPO_ROOT, "out", "code-ai-windows-x86_64.exe"),
    ],
    /** Artifact name produced by the build workflow. */
    artifact: "code-ai-windows-x86_64.exe",
  },
  linux: {
    staged: "code-ai",
    magic: Buffer.from([0x7f, 0x45, 0x4c, 0x46]), // \x7fELF
    format: "Linux ELF",
    candidates: [join(REPO_ROOT, "dist", "code-ai"), join(REPO_ROOT, "out", "code-ai-linux-x86_64")],
    artifact: "code-ai-linux-x86_64",
  },
};

/**
 * Read the first bytes of a file. Used to tell a Windows binary from a Linux
 * one: copying the wrong one into the wrong slot is the single mistake that
 * survives packaging and only shows up on the user's machine.
 */
function magicBytes(file, length) {
  const fd = openSync(file, "r");
  try {
    const buf = Buffer.alloc(length);
    readSync(fd, buf, 0, length, 0);
    return buf;
  } finally {
    closeSync(fd);
  }
}

/** Fail loudly when the file is not the executable format the slot expects. */
export function assertBinary(file, platform) {
  const spec = PLATFORMS[platform];
  const stat = statSync(file);
  if (!stat.isFile()) throw new Error(`${file} is not a file`);
  // A PyInstaller one-file build carries a Python runtime, Playwright's Node
  // driver and pdfium: tens of megabytes. Anything tiny is a stub, an LFS
  // pointer or a truncated download.
  if (stat.size < 5 * 1024 * 1024) {
    throw new Error(
      `${file} is only ${(stat.size / 1024 / 1024).toFixed(1)} MB — too small for a one-file ` +
        `PyInstaller build. Point at the real binary, or rebuild it.`
    );
  }
  const head = magicBytes(file, spec.magic.length);
  if (!head.equals(spec.magic)) {
    const other = Object.entries(PLATFORMS).find(([name, p]) =>
      name !== platform && magicBytes(file, p.magic.length).equals(p.magic)
    );
    const found = other ? ` It looks like a ${other[1].format} binary.` : "";
    throw new Error(`${file} is not a ${spec.format} executable.${found}`);
  }
  return stat.size;
}

/**
 * Copy one platform's binary into `bin/`, replacing whatever was there. The
 * directory holds a single platform at a time: packaging targets one VSIX per
 * platform, and a leftover binary from the other would double the download.
 */
export function stageBinary(platform, source) {
  const spec = PLATFORMS[platform];
  const size = assertBinary(source, platform);
  rmSync(BIN_DIR, { recursive: true, force: true });
  mkdirSync(BIN_DIR, { recursive: true });
  const target = join(BIN_DIR, spec.staged);
  copyFileSync(source, target);
  // The zip keeps this bit on POSIX hosts; the extension chmods on first spawn
  // for the VSIXes built on Windows, where there is no bit to keep.
  chmodSync(target, 0o755);
  return { target, size };
}

/**
 * Stage a macOS build, the folder scripts/build_mac_bundle.py writes: its
 * launcher lands at bin/code-ai, where the extension looks on any POSIX host,
 * with the runtime beside it. Returns the files (relative to bin/) that must
 * stay executable, as the bundle's manifest lists them.
 */
export function stageBundle(arch, source) {
  if (!statSync(source).isDirectory()) {
    throw new Error(
      `${source} is not a folder. Unpack code-ai-mac-${arch}-v*.tar.gz and pass the ` +
        `code-ai-mac-${arch} folder in it.`
    );
  }
  const manifest = JSON.parse(readFileSync(join(source, "code-ai-bundle.json"), "utf8"));
  if (manifest.arch !== arch) {
    throw new Error(`${source} holds the ${manifest.arch} build, not ${arch}`);
  }
  rmSync(BIN_DIR, { recursive: true, force: true });
  cpSync(source, BIN_DIR, { recursive: true });
  for (const file of manifest.executables) chmodSync(join(BIN_DIR, file), 0o755);
  return { executables: manifest.executables, size: treeSize(BIN_DIR) };
}

/**
 * What Mac users download: packaging/mac-install.sh with the darwin VSIX after
 * it, so one file installs the command and the extension (see the script).
 */
export function writeMacInstaller(file, vsix, { version, arch }) {
  const template = readFileSync(join(REPO_ROOT, "packaging", "mac-install.sh"), "utf8");
  // A Windows checkout may have given it CRLF, which no Mac shell would run.
  const header = template.replace(/\r\n/g, "\n").replaceAll("__VERSION__", version).replaceAll("__ARCH__", arch);
  if (!header.endsWith("\n__CODE_AI_PAYLOAD__\n")) {
    throw new Error("packaging/mac-install.sh has to end with its payload marker line");
  }
  writeFileSync(file, Buffer.concat([Buffer.from(header, "utf8"), readFileSync(vsix)]));
  chmodSync(file, 0o755);
}

function treeSize(dir) {
  let total = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    total += entry.isDirectory() ? treeSize(path) : statSync(path).size;
  }
  return total;
}
