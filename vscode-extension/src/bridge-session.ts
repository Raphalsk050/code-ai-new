import * as fs from "fs";
import * as vscode from "vscode";

import { BridgeClient } from "./bridge-client";
import { DEFAULT_COMMAND, resolveCommand, spawnFailureHint } from "./resolve-command";
import type { BridgeMethod, EventEnvelope } from "./protocol";

/**
 * One backend process per window, shared by every view that needs it.
 *
 * The chat panel used to own the bridge, which meant the graph editor could
 * only work if the chat had been opened first, and two views would have spawned
 * two Python processes on the same workspace. The session owns the lifecycle
 * instead and fans events out to whoever registered, so a view is just a
 * subscriber.
 */
export interface BridgeSubscriber {
  /** A bus event arrived from the backend (or a synthetic one from here). */
  onEvent(event: EventEnvelope): void;
  /** The bridge was replaced; a view holding per-session state should drop it. */
  onRestart?(): void;
}

export class BridgeSession {
  constructor(private readonly extensionPath: string) {}

  private bridge?: BridgeClient;
  private readonly subscribers = new Set<BridgeSubscriber>();
  /** Last `code-ai.command` warning shown, so a restart does not repeat it. */
  private warnedAbout?: string;

  /** The live client, or `undefined` while the backend is down. */
  get client(): BridgeClient | undefined {
    return this.bridge;
  }

  get running(): boolean {
    return this.bridge !== undefined;
  }

  subscribe(subscriber: BridgeSubscriber): vscode.Disposable {
    this.subscribers.add(subscriber);
    return new vscode.Disposable(() => this.subscribers.delete(subscriber));
  }

  /** Spawn the backend if it is not already up. Safe to call from every view. */
  ensureStarted(): void {
    if (this.bridge) return;
    this.spawn();
  }

  send(method: BridgeMethod, params: Record<string, unknown> = {}): void {
    this.bridge?.send(method, params);
  }

  request<T = unknown>(
    method: BridgeMethod,
    params: Record<string, unknown> = {},
    timeoutMs?: number
  ): Promise<T> {
    const client = this.bridge;
    if (!client) return Promise.reject(new Error("O backend do Code-AI não está em execução."));
    return client.request<T>(method, params, timeoutMs);
  }

  /**
   * Tear down the running bridge and spawn a fresh one. This re-reads the
   * on-disk config and `code-ai.*` settings, so edits take effect without
   * reloading the whole extension host.
   */
  restart(): void {
    const previous = this.bridge;
    this.bridge = undefined;
    previous?.dispose();
    this.broadcast(syntheticStatus("RECONNECTING"));
    this.broadcast(syntheticNotice("Restarting Code-AI to apply your settings…"));
    for (const subscriber of this.subscribers) subscriber.onRestart?.();
    this.spawn();
  }

  dispose(): void {
    this.bridge?.dispose();
    this.bridge = undefined;
    this.subscribers.clear();
  }

  broadcast(event: EventEnvelope): void {
    for (const subscriber of this.subscribers) {
      try {
        subscriber.onEvent(event);
      } catch (err) {
        console.error("[code-ai] bridge subscriber failed", err);
      }
    }
  }

  /**
   * Spawn the bridge child process. Reads the configuration fresh on every
   * call, so a {@link restart} picks up edited `code-ai.*` settings. Failures
   * are surfaced as synthetic events inside the already-rendered views rather
   * than left as a blank panel.
   */
  private spawn(): void {
    const config = vscode.workspace.getConfiguration("code-ai");
    const extraArgs = config.get<string[]>("args", []);
    const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const resolved = resolveCommand(
      config.get<string>("command", DEFAULT_COMMAND),
      this.extensionPath,
      (vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath),
      { platform: process.platform, exists: fs.existsSync }
    );
    const command = resolved.command;
    if (resolved.source === "bundled") ensureExecutable(command);
    // Once per setting per session. Every restart re-reads the config, and a
    // warning that repeats on each one trains the reader to dismiss it.
    if (resolved.ignoredConfig && this.warnedAbout !== resolved.ignoredConfig) {
      this.warnedAbout = resolved.ignoredConfig;
      vscode.window.showWarningMessage(`Code-AI: ${resolved.ignoredConfig}`);
      this.broadcast(syntheticNotice(resolved.ignoredConfig));
    }

    // The agent works on the folder the editor has open, not on whatever
    // workspace the config file last recorded - which is also where the
    // conversation archive lives, so getting this wrong loses the history too.
    // An explicit --workspace in `code-ai.args` still wins.
    const workspaceArgs = cwd && !extraArgs.includes("--workspace") ? ["--workspace", cwd] : [];

    let client: BridgeClient;
    try {
      client = new BridgeClient(command, [...workspaceArgs, ...extraArgs, "bridge"], cwd);
    } catch (err) {
      const detail = `failed to spawn "${command}": ${String(err)}`;
      vscode.window.showErrorMessage(`Code-AI: ${detail}`);
      this.broadcast(syntheticError(detail));
      return;
    }
    this.bridge = client;

    client.on("event", (event: EventEnvelope) => this.broadcast(event));
    client.on("stderr", (chunk: string) => console.error("[code-ai bridge]", chunk));
    client.on("error", (err: Error) => {
      const code = (err as NodeJS.ErrnoException).code;
      // EFTYPE is what Windows returns for a file it will not execute, such as
      // a .py pointed at directly.
      const hint =
        code === "ENOENT" || code === "EACCES" || code === "EFTYPE"
          ? ` — ${spawnFailureHint(resolved, process.platform)}`
          : "";
      vscode.window.showErrorMessage(`Code-AI bridge error: ${err.message}${hint}`);
      // A spawn that never starts emits "error" and no "exit", so the exit
      // handler below never runs. Without this the panel sits on STARTING for
      // ever and a dismissed toast is the only trace the failure ever left.
      this.broadcast(syntheticStatus("DISCONNECTED"));
      this.broadcast(syntheticError(`the backend did not start: ${err.message}${hint}`));
    });
    client.on("exit", (code: number | null) => {
      if (this.bridge !== client) return; // a restart already replaced this bridge
      this.bridge = undefined;
      // Leave the busy state so the "working" heartbeat stops, then show why.
      this.broadcast(syntheticStatus("DISCONNECTED"));
      this.broadcast(syntheticError(`bridge exited (${code})`));
    });
  }
}

/**
 * Make a bundled binary executable. A VSIX is a zip, and the bit does not
 * always survive the round trip (it never does when the zip was produced on
 * Windows), so the first launch on Linux would fail with EACCES.
 */
function ensureExecutable(file: string): void {
  if (process.platform === "win32") return;
  try {
    const mode = fs.statSync(file).mode;
    if (mode & 0o111) return;
    fs.chmodSync(file, mode | 0o755);
  } catch {
    // Read-only install or a vanished file: let the spawn report the real error.
  }
}

export function syntheticStatus(state: string): EventEnvelope {
  return { ...syntheticError(""), event_type: "status.changed", payload: { state } };
}

export function syntheticNotice(message: string): EventEnvelope {
  return { ...syntheticError(message), event_type: "warning" };
}

export function syntheticError(message: string): EventEnvelope {
  return {
    event_id: `synthetic-${Date.now()}`,
    event_type: "error",
    event_version: 1,
    session_id: "host",
    sequence: 0,
    timestamp: new Date().toISOString(),
    source: "extension",
    payload: { message },
  };
}
