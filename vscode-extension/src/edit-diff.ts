import * as path from "path";
import * as vscode from "vscode";

import { DIFF_TOOLS, diffTitle, proposedContent, writesToProject } from "./edit-plan";
import type { EventEnvelope } from "./protocol";

const SCHEME = "code-ai-diff";
// A streaming write repaints its proposal at most this often.
const REPAINT_MS = 150;

type Payload = Record<string, any>;

/** A file the agent is changing this turn. */
interface FileEdit {
  /** Absolute, as VS Code spells it: the key. */
  path: string;
  /** Workspace-relative with forward slashes: what titles and virtual URIs show. */
  relative: string;
  /** The file as it was before the agent first touched it this turn. */
  before?: string;
  /** Settles once a read of `before` from disk has finished, if one started. */
  ready: Promise<void>;
  reading: boolean;
  beforeUri: vscode.Uri;
  proposedUri: vscode.Uri;
  /** The call the proposal on screen belongs to. */
  callId: string;
  proposing: boolean;
  written: boolean;
  repaint?: ReturnType<typeof setTimeout>;
}

const nonce = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;

/**
 * The agent's file edits, in the editor's own diff view.
 *
 * While a write streams in, or waits for approval, the diff shows the file as
 * it was when the turn started against the proposal, repainting as the model
 * types. Once the tool has written the file the right side becomes the file
 * itself, so what is on screen is what is on disk and can be edited there. A
 * refused or failed call takes its proposal away again. Focus stays in the chat.
 */
export class EditDiffs implements vscode.TextDocumentContentProvider, vscode.Disposable {
  private readonly contents = new Map<string, string>();
  private readonly changed = new vscode.EventEmitter<vscode.Uri>();
  readonly onDidChange = this.changed.event;
  private files = new Map<string, FileEdit>();
  /** write_file calls streaming in, by their index in the model's response. */
  private readonly streams = new Map<number, { file: FileEdit; text: string }>();
  private readonly disposables: vscode.Disposable[];

  constructor() {
    this.disposables = [
      vscode.workspace.registerTextDocumentContentProvider(SCHEME, this),
      vscode.workspace.onDidCloseTextDocument((doc) => {
        if (doc.uri.scheme === SCHEME) this.contents.delete(doc.uri.toString());
      }),
      this.changed,
    ];
  }

  dispose(): void {
    for (const file of this.files.values()) if (file.repaint) clearTimeout(file.repaint);
    for (const disposable of this.disposables) disposable.dispose();
  }

  get enabled(): boolean {
    return vscode.workspace.getConfiguration("code-ai").get<boolean>("diff.openInEditor", true);
  }

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.contents.get(uri.toString()) ?? "";
  }

  handle(event: EventEnvelope): void {
    if (!this.enabled) return;
    const p: Payload = event.payload ?? {};
    try {
      switch (event.event_type) {
        case "user.message":
          // A new turn: the next change to a file is measured from what it is now.
          this.files = new Map();
          this.streams.clear();
          break;
        case "model.request.started":
          this.streams.clear();
          break;
        case "tool.call.progress":
          this.onProgress(p);
          break;
        case "tool.approval.requested":
          this.onApproval(p);
          break;
        case "tool.call.requested":
          this.onRequested(p);
          break;
        case "file.write.before":
          this.onSnapshot(p);
          break;
        case "tool.call.completed":
          this.onCompleted(p);
          break;
        case "tool.call.failed":
          this.onFailed(p);
          break;
      }
    } catch (err) {
      console.error("[code-ai] edit diff failed", err);
    }
  }

  /** Show the change to a file the agent touched, or the file itself when there is none. */
  async open(value: string): Promise<void> {
    const place = this.resolve(value);
    if (!place) return;
    const file = this.files.get(place.path);
    if (file?.before !== undefined) {
      await this.show(file, file.proposing ? "proposed" : "written");
      return;
    }
    await vscode.window.showTextDocument(vscode.Uri.file(place.path), { preview: true });
  }

  // --- events ------------------------------------------------------------

  private onProgress(p: Payload): void {
    if (!p.writes || String(p.name ?? "") !== "write_file") return;
    const index = Number(p.index ?? 0);
    if (p.call_started) this.streams.delete(index);
    let stream = this.streams.get(index);
    if (!stream) {
      // Arguments are still arriving, so the file is untouched yet: safe to read.
      const file = typeof p.path === "string" ? this.file(p.path, true) : undefined;
      if (!file) return;
      stream = { file, text: "" };
      this.streams.set(index, stream);
    }
    if (p.code_key !== "content" || typeof p.code_delta !== "string") return;
    const offset = Number(p.code_offset ?? 0);
    if (offset === 0) stream.text = p.code_delta;
    else if (offset === stream.text.length) stream.text += p.code_delta;
    else return; // an update went missing: hold the last coherent text
    this.propose(stream.file, stream.text);
  }

  private onApproval(p: Payload): void {
    const tool = String(p.tool_name ?? "");
    const args: Payload = p.arguments ?? {};
    if (!DIFF_TOOLS.has(tool) || !writesToProject(args) || typeof args.path !== "string") return;
    // The call waits for the user, so the file cannot change under the read.
    const file = this.file(args.path, true);
    if (!file) return;
    file.callId = String(p.call_id ?? "");
    void file.ready.then(async () => {
      const next = proposedContent(tool, args, await this.readDisk(file.path));
      if (next !== null) this.propose(file, next);
    });
  }

  private onRequested(p: Payload): void {
    const tool = String(p.name ?? "");
    const args: Payload = p.arguments ?? {};
    if (!DIFF_TOOLS.has(tool) || !writesToProject(args) || typeof args.path !== "string") return;
    // No read here: without an approval to wait for, the tool may already be
    // writing. The backend's own snapshot (file.write.before) covers it.
    const file = this.file(args.path, false);
    if (!file) return;
    file.callId = String(p.tool_call_id ?? "");
    // A streamed write may have been throttled short of its last characters.
    if (tool === "write_file" && file.proposing && typeof args.content === "string") {
      this.propose(file, args.content);
    }
  }

  private onSnapshot(p: Payload): void {
    const file = this.file(String(p.path ?? ""), false);
    // The turn's first snapshot of a file is its "before"; later ones are
    // the agent's own earlier edits.
    if (file && file.before === undefined) this.setBefore(file, String(p.content ?? ""));
  }

  private onCompleted(p: Payload): void {
    if (!DIFF_TOOLS.has(String(p.name ?? ""))) return;
    const result: Payload = p.result ?? {};
    if (result.location === "sandbox" || typeof result.path !== "string") return;
    const file = this.file(result.path, false);
    // Without knowing what was there, a diff would claim the whole file is new.
    if (!file || file.before === undefined) return;
    file.written = true;
    void this.settle(file, true);
  }

  private onFailed(p: Payload): void {
    if (!DIFF_TOOLS.has(String(p.name ?? ""))) return;
    const id = String(p.tool_call_id ?? "");
    for (const file of this.files.values()) {
      if (file.proposing && file.callId === id) void this.settle(file, file.written);
    }
  }

  // --- files ---------------------------------------------------------------

  private resolve(value: string): { path: string; relative: string } | undefined {
    const root = vscode.workspace.workspaceFolders?.[0]?.uri;
    if (!root || !value.trim()) return undefined;
    const uri = path.isAbsolute(value)
      ? vscode.Uri.file(value)
      : vscode.Uri.joinPath(root, ...value.split(/[\\/]+/).filter(Boolean));
    const relative = vscode.workspace.asRelativePath(uri, false);
    // Outside the workspace: not a change the editor should put in front of anyone.
    if (path.isAbsolute(relative)) return undefined;
    return { path: uri.fsPath, relative: relative.replace(/\\/g, "/") };
  }

  private file(value: string, readNow: boolean): FileEdit | undefined {
    const place = this.resolve(value);
    if (!place) return undefined;
    let file = this.files.get(place.path);
    if (!file) {
      file = {
        ...place,
        ready: Promise.resolve(),
        reading: false,
        beforeUri: this.virtual(place.relative, "before"),
        proposedUri: this.virtual(place.relative, "proposed"),
        callId: "",
        proposing: false,
        written: false,
      };
      this.files.set(place.path, file);
    }
    if (readNow && file.before === undefined && !file.reading) {
      const target = file;
      target.reading = true;
      target.ready = this.readDisk(target.path).then((text) => {
        if (target.before === undefined) this.setBefore(target, text);
      });
    }
    return file;
  }

  /** A URI whose path keeps the file's extension, so both sides get its highlighting. */
  private virtual(relative: string, side: string): vscode.Uri {
    return vscode.Uri.from({ scheme: SCHEME, path: "/" + relative, query: `${side}-${nonce()}` });
  }

  private setBefore(file: FileEdit, text: string): void {
    file.before = text;
    this.contents.set(file.beforeUri.toString(), text);
    this.changed.fire(file.beforeUri);
  }

  private async readDisk(fsPath: string): Promise<string> {
    try {
      return new TextDecoder("utf-8").decode(await vscode.workspace.fs.readFile(vscode.Uri.file(fsPath)));
    } catch {
      return ""; // not there yet: the write creates it
    }
  }

  // --- the diff tabs ---------------------------------------------------------

  private propose(file: FileEdit, text: string): void {
    const key = file.proposedUri.toString();
    const opening = !file.proposing;
    file.proposing = true;
    this.contents.set(key, text);
    if (opening) {
      void file.ready.then(() => this.show(file, "proposed"));
      return;
    }
    if (file.repaint) return;
    file.repaint = setTimeout(() => {
      file.repaint = undefined;
      this.changed.fire(file.proposedUri);
    }, REPAINT_MS);
  }

  private async show(file: FileEdit, phase: "proposed" | "written"): Promise<void> {
    if (file.before === undefined) return;
    this.contents.set(file.beforeUri.toString(), file.before);
    const right = phase === "proposed" ? file.proposedUri : vscode.Uri.file(file.path);
    await vscode.commands.executeCommand("vscode.diff", file.beforeUri, right, diffTitle(file.relative, phase), {
      preview: true,
      preserveFocus: true,
    });
  }

  /**
   * The proposal is over: the file was written (show it as it is on disk) or
   * the call went nowhere (take the proposal off the screen).
   */
  private async settle(file: FileEdit, showWritten: boolean): Promise<void> {
    if (file.repaint) {
      clearTimeout(file.repaint);
      file.repaint = undefined;
    }
    const stale = file.proposedUri.toString();
    const wasProposing = file.proposing;
    file.proposing = false;
    // A fresh name for the next proposal, so a tab left over can never show it.
    file.proposedUri = this.virtual(file.relative, "proposed");
    if (showWritten) await this.show(file, "written");
    if (!wasProposing) return;
    const tabs = vscode.window.tabGroups.all
      .flatMap((group) => group.tabs)
      .filter((tab) => tab.input instanceof vscode.TabInputTextDiff && tab.input.modified.toString() === stale);
    if (tabs.length) await vscode.window.tabGroups.close(tabs, true);
    this.contents.delete(stale);
  }
}
