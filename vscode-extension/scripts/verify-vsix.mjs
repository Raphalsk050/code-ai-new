#!/usr/bin/env node
// Assert that a built VSIX is actually self-contained.
//
// A VSIX is a zip, and every way of getting this wrong produces a file that
// installs without complaint and only fails on the user's machine: the binary
// missing, the wrong platform's binary, the executable bit dropped, a stub
// copied in by mistake. This reads the zip's central directory and checks all
// of it, so the failure happens here instead of on a first install.

import { openSync, readSync, writeSync, closeSync, statSync } from "node:fs";
import { basename } from "node:path";

const EOCD_SIGNATURE = 0x06054b50;
const CENTRAL_SIGNATURE = 0x02014b50;
const HOST_UNIX = 3;

/**
 * Entries of a zip, read from the central directory: name, uncompressed size
 * and the POSIX mode when the archive was written on a Unix host.
 *
 * Deliberately not zip64-aware: an entry over 4 GB would need it, and a VSIX
 * that large is a bug of its own.
 */
export function readZipEntries(file) {
  const size = statSync(file).size;
  const fd = openSync(file, "r");
  try {
    // The end-of-central-directory record sits last, after an optional comment
    // of up to 64 KB, so scan backwards over that window for its signature.
    const tailLength = Math.min(size, 65557);
    const tail = Buffer.alloc(tailLength);
    readSync(fd, tail, 0, tailLength, size - tailLength);
    let eocd = -1;
    for (let i = tail.length - 22; i >= 0; i--) {
      if (tail.readUInt32LE(i) === EOCD_SIGNATURE) {
        eocd = i;
        break;
      }
    }
    if (eocd < 0) throw new Error(`${basename(file)} is not a zip archive`);

    const count = tail.readUInt16LE(eocd + 10);
    const directorySize = tail.readUInt32LE(eocd + 12);
    const directoryOffset = tail.readUInt32LE(eocd + 16);

    const directory = Buffer.alloc(directorySize);
    readSync(fd, directory, 0, directorySize, directoryOffset);

    const entries = [];
    let cursor = 0;
    for (let i = 0; i < count; i++) {
      if (directory.readUInt32LE(cursor) !== CENTRAL_SIGNATURE) {
        throw new Error(`corrupt central directory at entry ${i}`);
      }
      const hostSystem = directory.readUInt8(cursor + 5);
      const uncompressed = directory.readUInt32LE(cursor + 24);
      const nameLength = directory.readUInt16LE(cursor + 28);
      const extraLength = directory.readUInt16LE(cursor + 30);
      const commentLength = directory.readUInt16LE(cursor + 32);
      const externalAttrs = directory.readUInt32LE(cursor + 38);
      const name = directory.toString("utf8", cursor + 46, cursor + 46 + nameLength);
      entries.push({
        name,
        size: uncompressed,
        // The high 16 bits hold st_mode, but only when a Unix host wrote it.
        mode: hostSystem === HOST_UNIX ? (externalAttrs >>> 16) & 0xffff : null,
        externalAttrs,
        /** Absolute offset of this entry's central header, for patching. */
        headerOffset: directoryOffset + cursor,
      });
      cursor += 46 + nameLength + extraLength + commentLength;
    }
    return entries;
  } finally {
    closeSync(fd);
  }
}

/**
 * Add the executable bits to one entry, in place.
 *
 * vsce takes the mode from the filesystem, and Windows has no executable bit to
 * take - it writes 0666, which VSCode then unpacks as a file Linux refuses to
 * run. Rather than require a Linux host to build a working Linux package, patch
 * the four bytes that hold it. Only the central directory carries the mode, so
 * this is a single 32-bit write per entry and the archive stays valid.
 */
export function makeEntryExecutable(file, name) {
  const entry = readZipEntries(file).find((e) => e.name === name);
  if (!entry) throw new Error(`${basename(file)} has no entry named ${name}`);
  if (entry.mode !== null && entry.mode & 0o111) return entry.mode;

  // Exactly rwxr-xr-x, keeping the file-type bits: OR-ing onto whatever vsce
  // recorded would leave a world-writable executable when it wrote 0666.
  const mode = (((entry.mode ?? 0o100644) & ~0o777) | 0o755) & 0xffff;
  // `|` works on signed 32-bit ints, so the shifted mode has to be brought back
  // to unsigned after the merge as well as before it.
  const attrs = (((mode << 16) >>> 0) | (entry.externalAttrs & 0xffff)) >>> 0;
  const fd = openSync(file, "r+");
  try {
    // Byte 5 of the header is the host system: the mode is only read back when
    // it says Unix, and on a Windows-built package it may not.
    const host = Buffer.from([HOST_UNIX]);
    writeSync(fd, host, 0, 1, entry.headerOffset + 5);
    const buf = Buffer.alloc(4);
    buf.writeUInt32LE(attrs, 0);
    writeSync(fd, buf, 0, 4, entry.headerOffset + 38);
  } finally {
    closeSync(fd);
  }
  return mode;
}

/** Minimum plausible size of a one-file PyInstaller build, in bytes. */
const MIN_BINARY_BYTES = 5 * 1024 * 1024;

const EXPECTED = {
  "win32-x64": {
    binary: "extension/bin/code-ai.exe",
    forbidden: "extension/bin/code-ai",
    // Windows decides by extension; the zip's POSIX mode is never consulted.
    needsExecutableBit: false,
  },
  "linux-x64": {
    binary: "extension/bin/code-ai",
    forbidden: "extension/bin/code-ai.exe",
    needsExecutableBit: true,
  },
};

/**
 * Check one VSIX against the target it claims to serve. Returns a list of
 * problems (empty means good) plus warnings that cannot be decided from here.
 */
export function verify(file, target) {
  const expected = EXPECTED[target];
  if (!expected) throw new Error(`unknown target "${target}"`);

  const entries = readZipEntries(file);
  const byName = new Map(entries.map((e) => [e.name, e]));
  const problems = [];
  const warnings = [];

  // dist/flow.js is the orchestrator-graph editor: a package without it
  // installs fine and then shows a blank tab on any *.flow.json.
  const REQUIRED = [
    "extension/dist/extension.js",
    "extension/dist/webview.js",
    "extension/dist/flow.js",
    "extension/package.json",
  ];
  for (const required of REQUIRED) {
    if (!byName.has(required)) problems.push(`missing ${required}`);
  }

  const binary = byName.get(expected.binary);
  if (!binary) {
    problems.push(`missing ${expected.binary} — the VSIX carries no backend, so a fresh install cannot start`);
  } else if (binary.size < MIN_BINARY_BYTES) {
    problems.push(
      `${expected.binary} is ${(binary.size / 1024 / 1024).toFixed(1)} MB, too small to be the real binary`
    );
  } else if (expected.needsExecutableBit) {
    if (binary.mode === null) {
      problems.push(
        `${expected.binary} carries no POSIX mode, so it unpacks unrunnable on Linux ` +
          `(run makeEntryExecutable on the package)`
      );
    } else if (!(binary.mode & 0o111)) {
      problems.push(`${expected.binary} has mode ${(binary.mode & 0o777).toString(8)} — not executable`);
    }
  }

  if (byName.has(expected.forbidden)) {
    problems.push(`${expected.forbidden} should not be in a ${target} package`);
  }

  const nodeModules = entries.filter((e) => e.name.includes("node_modules/"));
  if (nodeModules.length) problems.push(`${nodeModules.length} node_modules entries leaked into the package`);

  const sources = entries.filter((e) => /^extension\/(src|webview)\//.test(e.name));
  if (sources.length) warnings.push(`${sources.length} source files shipped (check .vscodeignore)`);

  return { entries, problems, warnings, binary };
}

function main() {
  const [file, target] = process.argv.slice(2);
  if (!file || !target) {
    console.error("usage: verify-vsix.mjs <file.vsix> <win32-x64|linux-x64>");
    process.exit(2);
  }
  const { entries, problems, warnings, binary } = verify(file, target);
  const total = statSync(file).size;
  console.log(
    `${basename(file)}: ${entries.length} entries, ${(total / 1024 / 1024).toFixed(1)} MB packed` +
      (binary ? `, backend ${(binary.size / 1024 / 1024).toFixed(1)} MB` : "")
  );
  for (const warning of warnings) console.log(`  warning: ${warning}`);
  if (problems.length) {
    for (const problem of problems) console.error(`  FAIL: ${problem}`);
    process.exit(1);
  }
  console.log("  ok: self-contained");
}

if (process.argv[1]?.endsWith("verify-vsix.mjs")) main();
