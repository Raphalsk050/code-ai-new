import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

import { BridgeClient } from "./bridge-client";
import type {
  AppMode,
  EditorContext,
  EventEnvelope,
  RefactorImprovement,
  WebviewToHost,
} from "./protocol";

// AI calls (explain/refactor) go through the model, which the bridge bounds at
// `budgets.model_timeout()` (180s by default). The client must wait at least
// that long plus network/startup slack, so give these calls a generous ceiling.
const AI_REQUEST_TIMEOUT_MS = 300_000;

export function activate(context: vscode.ExtensionContext): void {
  const provider = new ChatViewProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("code-ai.chat", provider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.commands.registerCommand("code-ai.open", () =>
      vscode.commands.executeCommand("code-ai.chat.focus")
    ),
    // Explain mode surfaces its analysis as a native hover over the selection.
    vscode.languages.registerHoverProvider(
      { scheme: "file" },
      { provideHover: (doc, pos) => provider.provideExplainHover(doc, pos) }
    )
  );
}

export function deactivate(): void {
  // The view provider disposes its bridge in onDidDispose.
}

class ChatViewProvider implements vscode.WebviewViewProvider {
  constructor(private readonly extensionUri: vscode.Uri) {}

  // Shared with the hover provider and selection handlers (one bridge per view).
  private client?: BridgeClient;
  private webview?: vscode.Webview;
  private mode: AppMode = "agent";
  private autoRunRefactor = false;
  private explainTimer?: NodeJS.Timeout;
  // The current explain "job": a pre-warmed promise keyed by the exact selection
  // range. The hover provider returns this promise so VSCode shows a loading
  // hover that resolves into the explanation — and it survives the selection
  // collapsing (e.g. when the user clicks to position the mouse).
  private explainJob: {
    uri: string;
    rangeKey: string;
    range: vscode.Range;
    promise: Promise<vscode.MarkdownString>;
  } | null = null;
  private refactorTimer?: NodeJS.Timeout;
  private refactorSeq = 0;
  private refactorTarget: { code: string; path: string; language: string } | null = null;

  /**
   * Hover provider entry point. In explain mode, when the user hovers over the
   * selection we returned a (possibly still-pending) promise: VSCode renders a
   * loading hover and swaps in the explanation when it resolves. Returning the
   * promise — rather than a pre-popped hover — is what makes it reliable.
   */
  provideExplainHover(
    doc: vscode.TextDocument,
    pos: vscode.Position
  ): vscode.ProviderResult<vscode.Hover> {
    if (this.mode !== "explain") return undefined;
    const job = this.explainJob;
    if (!job || job.uri !== doc.uri.toString() || !job.range.contains(pos)) return undefined;
    return job.promise.then((md) => new vscode.Hover(md, job.range));
  }

  /** Debounced: pre-warm the explanation for the current selection. */
  private scheduleExplain(editor: vscode.TextEditor): void {
    if (this.explainTimer) clearTimeout(this.explainTimer);
    const sel = editor.selection;
    // Keep the last job when the selection collapses (e.g. a click), so an
    // already-computed explanation is still hoverable.
    if (!sel || sel.isEmpty) return;
    this.explainTimer = setTimeout(() => this.startExplain(editor, sel), 400);
  }

  private startExplain(editor: vscode.TextEditor, sel: vscode.Selection): void {
    if (!this.client || this.mode !== "explain") return;
    const doc = editor.document;
    const code = doc.getText(sel);
    if (!code.trim()) return;
    const range = new vscode.Range(sel.start, sel.end);
    const rangeKey = `${doc.uri.toString()}:${range.start.line}:${range.start.character}:${range.end.line}:${range.end.character}`;
    if (this.explainJob?.rangeKey === rangeKey) return; // already computing/cached
    const promise = this.computeExplain(code, doc);
    this.explainJob = { uri: doc.uri.toString(), rangeKey, range, promise };
    // Best-effort: pop the hover once ready, if the user is still on the editor.
    void promise.then(() => {
      if (this.explainJob?.rangeKey === rangeKey) {
        void vscode.commands.executeCommand("editor.action.showHover");
      }
    });
  }

  private async computeExplain(code: string, doc: vscode.TextDocument): Promise<vscode.MarkdownString> {
    try {
      const result = await this.client!.request<{ markdown: string }>(
        "explainCode",
        { code, path: vscode.workspace.asRelativePath(doc.uri, false), language: doc.languageId },
        AI_REQUEST_TIMEOUT_MS
      );
      const md = new vscode.MarkdownString(result.markdown || "_No explanation available._");
      md.supportThemeIcons = true;
      return md;
    } catch (err) {
      console.error("[code-ai] explainCode failed", err);
      const md = new vscode.MarkdownString(`**Code-AI** could not explain this selection.\n\n${String(err)}`);
      md.supportThemeIcons = true;
      return md;
    }
  }

  private scheduleRefactor(): void {
    if (this.refactorTimer) clearTimeout(this.refactorTimer);
    this.refactorTimer = setTimeout(() => void this.runAnalyzeRefactor(), 550);
  }

  /** Analyze the active selection for architectural improvements. */
  private async runAnalyzeRefactor(): Promise<void> {
    const client = this.client;
    const editor = vscode.window.activeTextEditor;
    const sel = editor?.selection;
    if (!client || !editor || !sel || sel.isEmpty) {
      if (this.mode === "refactor" && !this.autoRunRefactor) {
        this.webview?.postMessage({
          type: "refactorError",
          message: "Select code in the editor to analyze.",
        });
      }
      return;
    }
    const doc = editor.document;
    const target = {
      code: doc.getText(sel),
      path: vscode.workspace.asRelativePath(doc.uri, false),
      language: doc.languageId,
    };
    this.refactorTarget = target;
    const seq = ++this.refactorSeq;
    this.webview?.postMessage({ type: "refactorStatus", status: "analyzing" });
    try {
      const r = await client.request<{ improvements: RefactorImprovement[] }>(
        "analyzeRefactor",
        target,
        AI_REQUEST_TIMEOUT_MS
      );
      if (seq !== this.refactorSeq) return;
      this.webview?.postMessage({
        type: "refactorResult",
        improvements: r.improvements ?? [],
        path: target.path,
        language: target.language,
      });
    } catch (err) {
      if (seq === this.refactorSeq) {
        this.webview?.postMessage({ type: "refactorError", message: String(err) });
      }
    }
  }

  /** Generate a Markdown plan for the chosen improvements and open the preview. */
  private async runPlanRefactor(id: string, improvements: RefactorImprovement[]): Promise<void> {
    const client = this.client;
    const target = this.refactorTarget;
    if (!client || !target) {
      this.webview?.postMessage({ type: "refactorError", message: "Nothing to plan — analyze a selection first." });
      return;
    }
    this.webview?.postMessage({ type: "refactorPlanning", id });
    try {
      const r = await client.request<{ markdown: string }>(
        "planRefactor",
        { ...target, improvements },
        AI_REQUEST_TIMEOUT_MS
      );
      const markdown = r.markdown || "_The model returned an empty plan._";
      await openMarkdownPreview(target.path, markdown);
      this.webview?.postMessage({ type: "refactorPlanned", id, markdown });
    } catch (err) {
      this.webview?.postMessage({ type: "refactorError", message: String(err) });
    }
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "dist")],
    };
    // Render the UI first and unconditionally: the webview must never be blank,
    // even if the bridge fails to spawn. Bridge errors are surfaced as events
    // inside the already-rendered UI instead of leaving an empty panel.
    this.webview = view.webview;
    view.webview.html = renderHtml(view.webview, this.extensionUri);

    const config = vscode.workspace.getConfiguration("code-ai");
    const extraArgs = config.get<string[]>("args", []);
    const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const command = resolveCommand(config.get<string>("command", "code-ai"), this.extensionUri);

    let client: BridgeClient;
    try {
      client = new BridgeClient(command, [...extraArgs, "bridge"], cwd);
    } catch (err) {
      const detail = `failed to spawn "${command}": ${String(err)}`;
      vscode.window.showErrorMessage(`Code-AI: ${detail}`);
      void view.webview.postMessage({ type: "event", event: syntheticError(detail) });
      return;
    }
    this.client = client;

    client.on("event", (event: EventEnvelope) => {
      void view.webview.postMessage({ type: "event", event });
    });
    client.on("stderr", (chunk: string) => console.error("[code-ai bridge]", chunk));
    client.on("error", (err: Error) => {
      const hint =
        (err as NodeJS.ErrnoException).code === "ENOENT"
          ? ` — "${command}" was not found. Set "code-ai.command" to the absolute path of the binary` +
            ` (e.g. <project>/.venv/bin/code-ai).`
          : "";
      vscode.window.showErrorMessage(`Code-AI bridge error: ${err.message}${hint}`);
    });
    client.on("exit", (code: number | null) => {
      void view.webview.postMessage({ type: "event", event: syntheticError(`bridge exited (${code})`) });
    });

    view.webview.onDidReceiveMessage((message: WebviewToHost) => {
      switch (message.type) {
        case "submit": {
          const context =
            message.includeContext === false ? "" : formatEditorContext(currentEditorContext());
          client.send("submitUserMessage", { text: message.text, context });
          break;
        }
        case "newConversation":
          client.send("newConversation");
          break;
        case "getSettings":
          client
            .request("getSettings")
            .then((settings) => view.webview.postMessage({ type: "settings", settings }))
            .catch((err) => console.error("[code-ai] getSettings failed", err));
          break;
        case "updateSettings":
          client
            .request("updateSettings", { updates: message.updates })
            .then((result) => view.webview.postMessage({ type: "settingsUpdated", result }))
            .catch((err) =>
              vscode.window.showErrorMessage(`Code-AI: failed to save settings: ${String(err)}`)
            );
          break;
        case "cancel":
          client.send("cancel");
          break;
        case "compact":
          client.send("compact");
          break;
        case "resolveApproval":
          client.send("resolveApproval", {
            call_id: message.call_id,
            scope: message.scope,
            reason: message.reason ?? "",
          });
          break;
        case "setPermissionMode":
          client.send("setPermissionMode", { mode: message.mode });
          break;
        case "setMode":
          this.mode = message.mode;
          this.autoRunRefactor = message.autoRunRefactor;
          if (this.mode !== "explain") this.explainJob = null;
          break;
        case "analyzeRefactor":
          void this.runAnalyzeRefactor();
          break;
        case "planRefactor":
          void this.runPlanRefactor(message.id, message.improvements);
          break;
      }
    });

    // Keep the webview in sync with what the user is looking at, so it can show
    // a context chip and let the user opt out before sending.
    const pushContext = () =>
      void view.webview.postMessage({ type: "editorContext", context: currentEditorContext() });
    const editorSubs = [
      vscode.window.onDidChangeActiveTextEditor(pushContext),
      vscode.window.onDidChangeTextEditorSelection((e) => {
        if (e.textEditor !== vscode.window.activeTextEditor) return;
        pushContext();
        if (this.mode === "explain") this.scheduleExplain(e.textEditor);
        else if (this.mode === "refactor" && this.autoRunRefactor) this.scheduleRefactor();
      }),
      view.onDidChangeVisibility(() => {
        if (view.visible) pushContext();
      }),
    ];
    pushContext();

    view.onDidDispose(() => {
      editorSubs.forEach((d) => d.dispose());
      if (this.explainTimer) clearTimeout(this.explainTimer);
      if (this.refactorTimer) clearTimeout(this.refactorTimer);
      this.explainJob = null;
      this.refactorTarget = null;
      this.client = undefined;
      this.webview = undefined;
      client.dispose();
    });
  }
}

/** Build an {@link EditorContext} from the active editor, or `null` if none. */
function currentEditorContext(): EditorContext | null {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return null;
  const doc = editor.document;
  if (doc.uri.scheme !== "file") return null; // skip output/diff/webview docs
  const path = vscode.workspace.asRelativePath(doc.uri, false);
  const sel = editor.selection;
  const context: EditorContext = { path, language: doc.languageId };
  if (sel && !sel.isEmpty) {
    context.selection = doc.getText(sel);
    context.startLine = sel.start.line + 1;
    context.endLine = sel.end.line + 1;
  }
  return context;
}

/** Write the plan to a temp Markdown file and open VSCode's preview on it. */
async function openMarkdownPreview(sourcePath: string, markdown: string): Promise<void> {
  const base = sourcePath ? path.basename(sourcePath).replace(/\W+/g, "-") : "selection";
  const file = path.join(os.tmpdir(), `code-ai-refactor-${base}-${Date.now()}.md`);
  await fs.promises.writeFile(file, markdown, "utf8");
  const uri = vscode.Uri.file(file);
  await vscode.commands.executeCommand("markdown.showPreview", uri);
}

/** Render an {@link EditorContext} into the preamble the model receives. */
function formatEditorContext(context: EditorContext | null): string {
  if (!context) return "";
  const head = `[Editor context] Active file: ${context.path} (${context.language})`;
  if (context.selection && context.startLine && context.endLine) {
    return (
      `${head}\nUser has selected lines ${context.startLine}-${context.endLine}:\n` +
      "```" +
      `${context.language}\n${context.selection}\n` +
      "```"
    );
  }
  return `${head}\nThe user currently has this file open (no selection).`;
}

/**
 * Resolve the bridge executable. VSCode launched from the GUI does not inherit
 * the shell PATH, so the bare `code-ai` console script (which lives in a project
 * virtualenv) is usually not found. When the user left the default, probe
 * deterministic locations for a `.venv/bin/code-ai`: relative to the extension
 * itself (works regardless of the open folder) and each workspace folder.
 */
function resolveCommand(configured: string, extensionUri: vscode.Uri): string {
  if (configured !== "code-ai" || path.isAbsolute(configured)) {
    return configured;
  }
  const roots = [
    path.dirname(extensionUri.fsPath),
    ...(vscode.workspace.workspaceFolders ?? []).map((f) => f.uri.fsPath),
  ];
  for (const root of roots) {
    const candidate = path.join(root, ".venv", "bin", "code-ai");
    if (fs.existsSync(candidate)) return candidate;
  }
  return configured;
}

function syntheticError(message: string): EventEnvelope {
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

function renderHtml(webview: vscode.Webview, extensionUri: vscode.Uri): string {
  const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "dist", "webview.js"));
  const nonce = makeNonce();
  const csp =
    `default-src 'none'; ` +
    `img-src ${webview.cspSource} https: data:; ` +
    `style-src ${webview.cspSource} 'unsafe-inline'; ` +
    `script-src 'nonce-${nonce}';`;
  return `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta http-equiv="Content-Security-Policy" content="${csp}" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Code-AI</title>
  </head>
  <body>
    <div id="root"></div>
    <script nonce="${nonce}" src="${scriptUri}"></script>
  </body>
</html>`;
}

function makeNonce(): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let text = "";
  for (let i = 0; i < 32; i++) text += chars.charAt(Math.floor(Math.random() * chars.length));
  return text;
}
