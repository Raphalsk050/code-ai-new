import * as nodePath from "path";

/**
 * Where the bridge executable came from. Surfaced in the UI so a failed spawn
 * says which of the three paths was taken instead of only which name failed.
 */
export type CommandSource = "configured" | "bundled" | "venv" | "path";

export interface ResolvedCommand {
  command: string;
  source: CommandSource;
  /** Set when `code-ai.command` was overridden, saying what and why. */
  ignoredConfig?: string;
}

/**
 * A script is not an executable. `spawn` cannot run one (Windows answers
 * EFTYPE, POSIX needs the shebang bit), so a setting pointing at `__main__.py`
 * is a configuration that can never work - not a preference to honour.
 */
const SCRIPT = /\.(py|pyw)$/i;

/** The filesystem calls this module makes, injected so tests need no disk. */
export interface ResolveHost {
  platform: NodeJS.Platform;
  exists(file: string): boolean;
}

/** The value of `code-ai.command` that means "find it yourself". */
export const DEFAULT_COMMAND = "code-ai";

/** Basename of the frozen binary the VSIX carries, per platform. */
export function binaryName(platform: NodeJS.Platform): string {
  return platform === "win32" ? "code-ai.exe" : "code-ai";
}

/**
 * Path of the binary staged inside the extension by `scripts/bundle-binary.mjs`.
 * Kept in one place because the packaging script and the extension have to
 * agree on it; a mismatch is a VSIX that installs and never runs.
 */
export function bundledBinaryPath(extensionPath: string, platform: NodeJS.Platform): string {
  const p = platform === "win32" ? nodePath.win32 : nodePath.posix;
  return p.join(extensionPath, "bin", binaryName(platform));
}

/**
 * Candidate virtualenv executables under a project root, newest layout first.
 * Windows venvs put console scripts in `Scripts\` with an `.exe` shim; POSIX
 * ones in `bin/` with no extension.
 */
function venvCandidates(root: string, platform: NodeJS.Platform): string[] {
  const p = platform === "win32" ? nodePath.win32 : nodePath.posix;
  return platform === "win32"
    ? [p.join(root, ".venv", "Scripts", "code-ai.exe")]
    : [p.join(root, ".venv", "bin", "code-ai")];
}

/**
 * Resolve the executable that serves `code-ai bridge`.
 *
 * Order matters. The binary the VSIX ships wins over a virtualenv so that a
 * first-time install works with nothing else present; a developer who wants
 * their working tree instead sets `code-ai.command` explicitly, which wins over
 * everything. The bare name is the last resort and usually fails: VSCode
 * launched from the GUI does not inherit the shell PATH.
 *
 * @param configured   the `code-ai.command` setting, verbatim
 * @param extensionPath  the installed extension's own directory
 * @param workspaceRoots  open workspace folders, searched for a dev virtualenv
 */
export function resolveCommand(
  configured: string,
  extensionPath: string,
  workspaceRoots: string[],
  host: ResolveHost
): ResolvedCommand {
  const trimmed = configured.trim();
  let ignoredConfig: string | undefined;
  if (trimmed && trimmed !== DEFAULT_COMMAND) {
    if (!SCRIPT.test(trimmed)) return { command: trimmed, source: "configured" };
    // Falling through rather than failing: the setting is unusable whatever we
    // do with it, and the bundled backend is right there.
    ignoredConfig =
      `"code-ai.command" points at a Python script (${trimmed}), which cannot be launched ` +
      `directly. Using the backend shipped with the extension instead. Clear the setting, or ` +
      `set it to "python" with "code-ai.args" of ["-m", "code_ai"] to run from source.`;
  }
  const tag = (resolved: ResolvedCommand): ResolvedCommand =>
    ignoredConfig ? { ...resolved, ignoredConfig } : resolved;

  const bundled = bundledBinaryPath(extensionPath, host.platform);
  if (host.exists(bundled)) {
    return tag({ command: bundled, source: "bundled" });
  }

  const p = host.platform === "win32" ? nodePath.win32 : nodePath.posix;
  const roots = [p.dirname(extensionPath), ...workspaceRoots];
  for (const root of roots) {
    for (const candidate of venvCandidates(root, host.platform)) {
      if (host.exists(candidate)) return tag({ command: candidate, source: "venv" });
    }
  }

  return tag({ command: DEFAULT_COMMAND, source: "path" });
}

/**
 * Human-readable reason a spawn failed, naming the path that was tried and how
 * it was chosen. `source` is what turns "code-ai not found" into something the
 * reader can act on without knowing the resolution order.
 */
export function spawnFailureHint(resolved: ResolvedCommand, platform: NodeJS.Platform): string {
  switch (resolved.source) {
    case "bundled":
      return (
        `the binary shipped with the extension (${resolved.command}) could not be started. ` +
        `The install may be incomplete — reinstall the VSIX.`
      );
    case "configured":
      return `"code-ai.command" points at ${resolved.command}, which was not found.`;
    case "venv":
      return `${resolved.command} was found but could not be started.`;
    default: {
      const example =
        platform === "win32" ? "C:\\path\\to\\.venv\\Scripts\\code-ai.exe" : "/path/to/.venv/bin/code-ai";
      return (
        `this build of the extension carries no bundled binary and no virtualenv was found, ` +
        `so it fell back to "code-ai" on PATH. Set "code-ai.command" to the executable ` +
        `(e.g. ${example}), or install a VSIX built with the binary bundled.`
      );
    }
  }
}
