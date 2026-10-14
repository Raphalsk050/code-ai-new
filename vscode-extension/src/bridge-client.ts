import { ChildProcessWithoutNullStreams, spawn } from "child_process";
import { EventEmitter } from "events";

import type { BridgeMethod, EventEnvelope } from "./protocol";

/**
 * Thin JSON-RPC 2.0 client over a child process' stdio. One JSON object per
 * line in each direction. Emits:
 *   - "event"    (EventEnvelope)        bus events forwarded by the bridge
 *   - "response" (full JSON-RPC reply)  correlated by request id
 *   - "stderr"   (string)               diagnostics from the bridge
 *   - "exit"     (number | null)        the bridge process exited
 *   - "error"    (Error)                spawn failure
 */
/** How long a request waits for its answer once the bridge is talking. */
export const DEFAULT_TIMEOUT_MS = 20_000;
/** How long a bridge may take to say its first word before what waits on it gives up. */
export const START_ALLOWANCE_MS = 5 * 60_000;

interface Pending {
  settle: (err: Error | null, msg?: any) => void;
  method: BridgeMethod;
  timeoutMs: number;
  timer?: ReturnType<typeof setTimeout>;
}

export class BridgeClient extends EventEmitter {
  private readonly proc: ChildProcessWithoutNullStreams;
  private buffer = "";
  private nextId = 1;
  private dead: string | null = null;
  private started = false;
  private readonly pending = new Map<number, Pending>();

  constructor(
    command: string,
    args: string[],
    cwd?: string,
    private readonly startAllowanceMs = START_ALLOWANCE_MS
  ) {
    super();
    // windowsHide: the frozen binary is a console application, so without it
    // Windows pops a console window in front of the editor on every spawn.
    this.proc = spawn(command, args, {
      cwd,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.proc.stdout.setEncoding("utf8");
    this.proc.stdout.on("data", (chunk: string) => this.onStdout(chunk));
    this.proc.stderr.setEncoding("utf8");
    this.proc.stderr.on("data", (chunk: string) => this.emit("stderr", chunk));
    this.proc.on("exit", (code) => {
      this.fail(`bridge process exited (code ${code})`);
      this.emit("exit", code);
    });
    this.proc.on("error", (err) => {
      this.fail(`bridge process error: ${err.message}`);
      this.emit("error", err);
    });
  }

  /** Mark the bridge dead and reject every in-flight request right away. */
  private fail(reason: string): void {
    this.dead = reason;
    for (const [, waiting] of this.pending) {
      clearTimeout(waiting.timer);
      waiting.settle(new Error(reason));
    }
    this.pending.clear();
  }

  /** Give a request so long to be answered, counted from now. */
  private arm(id: number, waiting: Pending, delayMs: number): void {
    clearTimeout(waiting.timer);
    waiting.timer = setTimeout(() => {
      if (!this.pending.delete(id)) return;
      waiting.settle(
        new Error(`Bridge request "${waiting.method}" timed out after ${Math.round(delayMs / 1000)}s`)
      );
    }, delayMs);
  }

  private onStdout(chunk: string): void {
    this.buffer += chunk;
    let newline: number;
    while ((newline = this.buffer.indexOf("\n")) >= 0) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (!line) continue;
      let message: any;
      try {
        message = JSON.parse(line);
      } catch {
        continue; // ignore non-JSON noise on stdout
      }
      if (!this.started) {
        this.started = true;
        // What was asked while it started has its own time to be answered from here.
        for (const [id, waiting] of this.pending) this.arm(id, waiting, waiting.timeoutMs);
      }
      if (message.method === "event") {
        this.emit("event", message.params as EventEnvelope);
      } else if (message.id !== undefined) {
        const waiting = this.pending.get(message.id);
        if (waiting) {
          this.pending.delete(message.id);
          clearTimeout(waiting.timer);
          waiting.settle(null, message);
        }
        this.emit("response", message);
      }
    }
  }

  /** Send a request (carries an id; the bridge replies). Returns the id. */
  send(method: BridgeMethod, params: Record<string, unknown> = {}): number {
    const id = this.nextId++;
    this.write({ jsonrpc: "2.0", id, method, params });
    return id;
  }

  /**
   * Send a request and resolve with its `result` (or reject on error/timeout).
   * A bridge that has not said a word yet is still starting, and a frozen one
   * can take longer than any answer should: what is asked meanwhile counts its
   * time from that first word, so a slow start is not a lost answer.
   */
  request<T = any>(
    method: BridgeMethod,
    params: Record<string, unknown> = {},
    timeoutMs = DEFAULT_TIMEOUT_MS
  ): Promise<T> {
    if (this.dead) return Promise.reject(new Error(this.dead));
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      const waiting: Pending = {
        method,
        timeoutMs,
        settle: (err, msg) => {
          if (err) reject(err);
          else if (msg.error) reject(new Error(msg.error.message ?? "bridge error"));
          else resolve(msg.result as T);
        },
      };
      this.pending.set(id, waiting);
      this.arm(id, waiting, this.started ? timeoutMs : timeoutMs + this.startAllowanceMs);
      this.write({ jsonrpc: "2.0", id, method, params });
    });
  }

  private write(message: Record<string, unknown>): void {
    this.proc.stdin.write(JSON.stringify(message) + "\n");
  }

  dispose(): void {
    try {
      this.write({ jsonrpc: "2.0", method: "shutdown", params: {} });
    } catch {
      // stdin may already be closed; fall through to the kill below.
    }
    setTimeout(() => this.proc.kill(), 500);
  }
}
