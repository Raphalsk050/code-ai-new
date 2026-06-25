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
export class BridgeClient extends EventEmitter {
  private readonly proc: ChildProcessWithoutNullStreams;
  private buffer = "";
  private nextId = 1;
  private readonly pending = new Map<number, (msg: any) => void>();

  constructor(command: string, args: string[], cwd?: string) {
    super();
    this.proc = spawn(command, args, { cwd, stdio: ["pipe", "pipe", "pipe"] });
    this.proc.stdout.setEncoding("utf8");
    this.proc.stdout.on("data", (chunk: string) => this.onStdout(chunk));
    this.proc.stderr.setEncoding("utf8");
    this.proc.stderr.on("data", (chunk: string) => this.emit("stderr", chunk));
    this.proc.on("exit", (code) => this.emit("exit", code));
    this.proc.on("error", (err) => this.emit("error", err));
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
      if (message.method === "event") {
        this.emit("event", message.params as EventEnvelope);
      } else if (message.id !== undefined) {
        const resolve = this.pending.get(message.id);
        if (resolve) {
          this.pending.delete(message.id);
          resolve(message);
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

  /** Send a request and resolve with its `result` (or reject on error/timeout). */
  request<T = any>(method: BridgeMethod, params: Record<string, unknown> = {}, timeoutMs = 20000): Promise<T> {
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Bridge request "${method}" timed out`));
      }, timeoutMs);
      this.pending.set(id, (msg) => {
        clearTimeout(timer);
        if (msg.error) reject(new Error(msg.error.message ?? "bridge error"));
        else resolve(msg.result as T);
      });
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
