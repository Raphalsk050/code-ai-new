import * as path from "path";
import * as vscode from "vscode";

import { ChangeLedger, ChangeSummary } from "./change-ledger";
import { DIFF_TOOLS } from "./edit-plan";
import type { EventEnvelope } from "./protocol";

const SCHEME = "code-ai-original";

/**
 * Every file the agent changed in the conversation, against how it was before
 * the agent first touched it: the Changes section of the rail. Unlike the
 * per-turn diff tabs this spans the whole conversation and outlives the turn,
 * so the user can review or undo it later.
 */
export class SessionChanges implements vscode.FileDecorationProvider, vscode.Disposable {
  private readonly ledger = new ChangeLedger();
  private readonly originals = new Map<string, string>();
  private readonly emitter = new vscode.EventEmitter<ChangeSummary[]>();
  /** Fires with the new list whenever it may have changed. */
  readonly onDidChangeList = this.emitter.event;
  private readonly disposables: vscode.Disposable[];
  private pending?: ReturnType<typeof setTimeout>;
  /** Files the explorer marks, by fsPath: true for one the agent created. */
  private decorated = new Map<string, boolean>();
  private readonly decorationsChanged = new vscode.EventEmitter<vscode.Uri[]>();
  readonly onDidChangeFileDecorations = this.decorationsChanged.event;

  constructor() {
    this.disposables = [
      vscode.workspace.registerTextDocumentContentProvider(SCHEME, {
        provideTextDocumentContent: (uri) => this.originals.get(uri.toString()) ?? "",
      }),
      // An edit by hand moves the counts as much as one by the agent.
      vscode.workspace.onDidSaveTextDocument((doc) => {
        if (this.ledger.get(doc.uri.fsPath)) this.refresh();
      }),
      this.emitter,
      this.decorationsChanged,
    ];
  }

  /** A letter in the explorer on every file the agent changed in the conversation. */
  provideFileDecoration(uri: vscode.Uri): vscode.FileDecoration | undefined {
    const created = this.decorated.get(uri.fsPath);
    if (created === undefined) return undefined;
    return new vscode.FileDecoration(
      created ? "A" : "M",
      created ? "Created by Code-AI in this conversation" : "Changed by Code-AI in this conversation",
      new vscode.ThemeColor(created ? "gitDecoration.addedResourceForeground" : "gitDecoration.modifiedResourceForeground")
    );
  }

  dispose(): void {
    if (this.pending) clearTimeout(this.pending);
    for (const disposable of this.disposables) disposable.dispose();
  }

  handle(event: EventEnvelope): void {
    const p = event.payload ?? {};
    if (event.event_type === "file.write.before") {
      const place = this.resolve(String(p.path ?? ""));
      if (place) this.ledger.snapshot(place.path, place.relative, String(p.content ?? ""), Boolean(p.existed));
      return;
    }
    if (event.event_type === "tool.call.completed" && DIFF_TOOLS.has(String(p.name ?? ""))) {
      const result = p.result ?? {};
      if (result.location === "sandbox" || typeof result.path !== "string") return;
      const place = this.resolve(result.path);
      if (!place) return;
      this.ledger.touched(place.path, place.relative);
      this.refresh();
    }
  }

  /** A new or another conversation: its changes are its own. */
  reset(): void {
    this.ledger.clear();
    this.originals.clear();
    this.emitter.fire([]);
    this.redecorate([]);
  }

  async summaries(): Promise<ChangeSummary[]> {
    return this.ledger.summarize((file) => this.read(file));
  }

  /** The change to one file, original on the left, the file on disk on the right. */
  async open(relative: string): Promise<void> {
    const entry = this.ledger.byRelative(relative);
    if (!entry) return;
    const file = vscode.Uri.file(entry.path);
    if (entry.before === undefined) {
      await vscode.window.showTextDocument(file, { preview: true });
      return;
    }
    await vscode.commands.executeCommand(
      "vscode.diff",
      this.original(entry.relative, entry.before),
      file,
      `${entry.relative} (since the conversation started)`,
      { preview: true }
    );
  }

  /** All of them at once: the multi-file diff where the editor has one. */
  async reviewAll(): Promise<void> {
    const entries = this.ledger.list().filter((entry) => entry.before !== undefined);
    if (entries.length === 0) return;
    const resources = entries.map((entry) => [
      vscode.Uri.file(entry.path),
      this.original(entry.relative, entry.before ?? ""),
      vscode.Uri.file(entry.path),
    ]);
    try {
      await vscode.commands.executeCommand("vscode.changes", "Code-AI", resources);
    } catch {
      await this.open(entries[0].relative);
    }
  }

  /** Put a file back as it was before the agent touched it, after asking. */
  async revert(relative: string): Promise<void> {
    const entry = this.ledger.byRelative(relative);
    if (!entry || entry.before === undefined) return;
    const action = entry.existed ? "Revert" : "Delete";
    const question = entry.existed
      ? `Revert ${relative} to how it was before the agent changed it? Later edits to it are lost too.`
      : `Delete ${relative}? The agent created it in this conversation.`;
    const answer = await vscode.window.showWarningMessage(question, { modal: true }, action);
    if (answer !== action) return;
    const uri = vscode.Uri.file(entry.path);
    try {
      if (entry.existed) await vscode.workspace.fs.writeFile(uri, new TextEncoder().encode(entry.before));
      else await vscode.workspace.fs.delete(uri, { useTrash: true });
    } catch (err) {
      void vscode.window.showErrorMessage(`Could not revert ${relative}: ${(err as Error).message}`);
      return;
    }
    this.ledger.forget(entry.path);
    this.refresh();
  }

  /** Coalesced: a burst of writes reads the files once. */
  private refresh(): void {
    if (this.pending) return;
    this.pending = setTimeout(() => {
      this.pending = undefined;
      void this.summaries().then((list) => {
        this.emitter.fire(list);
        this.redecorate(list);
      });
    }, 120);
  }

  private redecorate(list: ChangeSummary[]): void {
    const next = new Map<string, boolean>();
    for (const item of list) {
      const entry = this.ledger.byRelative(item.path);
      if (entry) next.set(entry.path, item.created);
    }
    const touched = new Set([...this.decorated.keys(), ...next.keys()]);
    this.decorated = next;
    if (touched.size) this.decorationsChanged.fire([...touched].map((file) => vscode.Uri.file(file)));
  }

  private original(relative: string, text: string): vscode.Uri {
    const uri = vscode.Uri.from({ scheme: SCHEME, path: "/" + relative, query: `t${Date.now().toString(36)}` });
    this.originals.set(uri.toString(), text);
    return uri;
  }

  private async read(fsPath: string): Promise<string | null> {
    try {
      return new TextDecoder("utf-8").decode(await vscode.workspace.fs.readFile(vscode.Uri.file(fsPath)));
    } catch {
      return null;
    }
  }

  private resolve(value: string): { path: string; relative: string } | undefined {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri;
    if (!root || !value.trim()) return undefined;
    const uri = path.isAbsolute(value)
      ? vscode.Uri.file(value)
      : vscode.Uri.joinPath(root, ...value.split(/[\\/]+/).filter(Boolean));
    const relative = vscode.workspace.asRelativePath(uri, false);
    if (path.isAbsolute(relative)) return undefined;
    return { path: uri.fsPath, relative: relative.replace(/\\/g, "/") };
  }
}
