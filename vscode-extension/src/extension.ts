import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

import { BridgeClient } from "./bridge-client";
import { DEFAULT_COMMAND, resolveCommand, spawnFailureHint } from "./resolve-command";
import type {
  AppMode,
  BridgeMethod,
  EditorContext,
  EventEnvelope,
  ExplainTarget,
  RefactorImprovement,
  SlashCommandSpec,
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
    // Respawn the bridge so edited settings/config take effect without having to
    // reload the whole extension host.
    vscode.commands.registerCommand("code-ai.restart", () => provider.restartBridge()),
    vscode.commands.registerCommand("code-ai.toggleInlineHints", () => provider.toggleInlineHints()),
    vscode.commands.registerCommand("code-ai.inlineHintInfo", () => provider.showInlineHintInfo()),
    vscode.commands.registerCommand("code-ai.testInlineHint", () => provider.testInlineHint()),
    // Explain mode surfaces its analysis as a native hover over the selection.
    vscode.languages.registerHoverProvider(
      { scheme: "file" },
      { provideHover: (doc, pos) => provider.provideExplainHover(doc, pos) }
    ),
    // Inline code hints (ghost text), driven by the same bridge/model.
    vscode.languages.registerInlineCompletionItemProvider({ pattern: "**" }, provider)
  );

  // A clickable status-bar toggle so inline hints can be flipped without opening
  // settings; the provider keeps its label in sync with the backend state.
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = "code-ai.toggleInlineHints";
  context.subscriptions.push(statusBar);
  provider.bindInlineStatusBar(statusBar);
}

export function deactivate(): void {
  // The view provider disposes its bridge in onDidDispose.
}

class ChatViewProvider implements vscode.WebviewViewProvider {
  constructor(private readonly extensionUri: vscode.Uri) {}

  // Shared with the hover provider and selection handlers (one bridge per view).
  private client?: BridgeClient;
  /** Last `code-ai.command` warning shown, so a restart does not repeat it. */
  private warnedAbout?: string;
  private view?: vscode.WebviewView;
  private webview?: vscode.Webview;
  private mode: AppMode = "agent";
  private autoRunRefactor = false;
  // Inline code hints (editor ghost text). Enabled state + model are mirrored
  // from the backend settings; the provider no-ops while disabled.
  private inlineEnabled = false;
  private inlineModel = "";
  private inlineStatusBar?: vscode.StatusBarItem;
  // Diagnostics channel so the user can confirm a suggestion came from Code-AI.
  private inlineLog?: vscode.OutputChannel;
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

  // -- inline hints (editor ghost text) ----------------------------------

  bindInlineStatusBar(item: vscode.StatusBarItem): void {
    this.inlineStatusBar = item;
    this.renderInlineStatusBar();
  }

  private renderInlineStatusBar(busy = false): void {
    const item = this.inlineStatusBar;
    if (!item) return;
    if (!this.inlineEnabled) {
      item.text = "$(circle-slash) Code-AI Hints";
      item.tooltip = "Code-AI inline hints are off — click to enable";
    } else if (busy) {
      item.text = "$(sync~spin) Code-AI Hints";
      item.tooltip = `Code-AI is generating a hint${this.inlineModel ? ` (${this.inlineModel})` : ""}…`;
    } else {
      item.text = "$(sparkle) Code-AI Hints";
      item.tooltip =
        `Code-AI inline hints are on${this.inlineModel ? ` (model: ${this.inlineModel})` : ""}` +
        " — click to disable. Ghost text with this label in its toolbar is from Code-AI.";
    }
    item.show();
  }

  /** The label shown in the inline-suggestion toolbar, so the source is clear. */
  private inlineLabel(): string {
    return this.inlineModel ? `Code-AI · ${this.inlineModel}` : "Code-AI";
  }

  private logInline(message: string): void {
    if (!this.inlineLog) this.inlineLog = vscode.window.createOutputChannel("Code-AI Inline Hints");
    this.inlineLog.appendLine(`[${new Date().toLocaleTimeString()}] ${message}`);
  }

  /** Pull the inline-hints enabled state + model from the backend snapshot. */
  private refreshInlineSettings(): void {
    this.client
      ?.request<{ inline_hints_enabled?: boolean; inline_model?: string; model?: string }>("getSettings")
      .then((settings) => {
        this.inlineEnabled = !!settings.inline_hints_enabled;
        this.inlineModel = (settings.inline_model || settings.model || "").trim();
        this.renderInlineStatusBar();
      })
      .catch((err) => console.error("[code-ai] refreshInlineSettings failed", err));
  }

  private applyInlineSettings(settings?: {
    inline_hints_enabled?: boolean;
    inline_model?: string;
    model?: string;
  }): void {
    if (!settings) return;
    this.inlineEnabled = !!settings.inline_hints_enabled;
    this.inlineModel = (settings.inline_model || settings.model || "").trim();
    this.renderInlineStatusBar();
  }

  /** Flip inline hints on/off, persisting through the backend settings. */
  async toggleInlineHints(): Promise<void> {
    const client = this.client;
    if (!client) {
      vscode.window.showWarningMessage("Code-AI: the bridge is not running yet.");
      return;
    }
    const next = !this.inlineEnabled;
    try {
      const result = await client.request<{ settings?: { inline_hints_enabled?: boolean } }>(
        "updateSettings",
        { updates: { inline_hints_enabled: next } }
      );
      this.applyInlineSettings(result.settings);
      // Keep an open settings panel in sync.
      this.webview?.postMessage({ type: "settingsUpdated", result });
      vscode.window.setStatusBarMessage(
        `Code-AI inline hints ${this.inlineEnabled ? "enabled" : "disabled"}`,
        2000
      );
    } catch (err) {
      vscode.window.showErrorMessage(`Code-AI: could not toggle inline hints: ${String(err)}`);
    }
  }

  /** Invoked from the inline-suggestion toolbar button, confirming the source. */
  showInlineHintInfo(): void {
    const model = this.inlineModel || "the main model";
    vscode.window
      .showInformationMessage(
        `This inline suggestion is from Code-AI (model: ${model}).`,
        "Show log"
      )
      .then((choice) => {
        if (choice === "Show log") this.inlineLog?.show();
      });
  }

  /**
   * Inline-completion entry point. While enabled, sends the code around the
   * cursor to the bridge and returns the model's completion as ghost text. The
   * cancellation token doubles as a debounce: a keystroke cancels the in-flight
   * request before it reaches the model. Each item carries a "Code-AI" toolbar
   * label so its source is unambiguous even next to other providers.
   */
  async provideInlineCompletionItems(
    document: vscode.TextDocument,
    position: vscode.Position,
    _context: vscode.InlineCompletionContext,
    token: vscode.CancellationToken
  ): Promise<vscode.InlineCompletionItem[] | undefined> {
    const where = `${vscode.workspace.asRelativePath(document.uri, false)}:${position.line + 1}`;
    if (!this.inlineEnabled) {
      this.logInline(`skip ${where}: inline hints disabled (enable from the status bar)`);
      return undefined;
    }
    if (!this.client) {
      this.logInline(`skip ${where}: bridge not running`);
      return undefined;
    }
    if (document.uri.scheme !== "file") return undefined;
    const prefix = document.getText(new vscode.Range(new vscode.Position(0, 0), position));
    if (!prefix.trim()) return undefined;
    const lastLine = document.lineAt(document.lineCount - 1).range.end;
    const suffix = document.getText(new vscode.Range(position, lastLine));

    // Debounce: wait out a short pause; a new keystroke cancels this token.
    await new Promise((r) => setTimeout(r, 250));
    if (token.isCancellationRequested) return undefined;

    this.renderInlineStatusBar(true);
    const started = Date.now();
    try {
      const result = await this.client.request<{ completion: string }>(
        "inlineComplete",
        {
          prefix,
          suffix,
          path: vscode.workspace.asRelativePath(document.uri, false),
          language: document.languageId,
        },
        AI_REQUEST_TIMEOUT_MS
      );
      const ms = Date.now() - started;
      if (token.isCancellationRequested) {
        this.logInline(`cancelled ${where} after ${ms}ms (you kept typing)`);
        return undefined;
      }
      const text = result.completion ?? "";
      if (!text.trim()) {
        this.logInline(`empty ${where} after ${ms}ms — model returned no completion`);
        return undefined;
      }
      const item = new vscode.InlineCompletionItem(text, new vscode.Range(position, position));
      // Surfaces a "Code-AI" button in the inline-suggestion toolbar, so the
      // user can tell this ghost text apart from other providers'.
      item.command = { command: "code-ai.inlineHintInfo", title: this.inlineLabel() };
      this.logInline(`served ${where} in ${ms}ms → ${JSON.stringify(text.slice(0, 80))}`);
      return [item];
    } catch (err) {
      this.logInline(`error ${where} after ${Date.now() - started}ms: ${String(err)}`);
      return undefined;
    } finally {
      if (!token.isCancellationRequested) this.renderInlineStatusBar(false);
    }
  }

  /**
   * Diagnostic: force one inline completion at the cursor and report the raw
   * result in a message + the log. Isolates the model/bridge path from VSCode's
   * ghost-text rendering, so we can tell whether nothing shows because the model
   * returned nothing or because the suggestion is being suppressed.
   */
  async testInlineHint(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showWarningMessage("Code-AI: open a file and place the cursor first.");
      return;
    }
    if (!this.client) {
      vscode.window.showWarningMessage("Code-AI: the bridge is not running yet.");
      return;
    }
    const doc = editor.document;
    const position = editor.selection.active;
    const prefix = doc.getText(new vscode.Range(new vscode.Position(0, 0), position));
    const suffix = doc.getText(new vscode.Range(position, doc.lineAt(doc.lineCount - 1).range.end));
    this.inlineLog?.show(true);
    this.logInline(`TEST request at ${doc.languageId}:${position.line + 1} (model: ${this.inlineModel || "main"})`);
    const started = Date.now();
    try {
      const result = await this.client.request<{ completion: string }>(
        "inlineComplete",
        { prefix, suffix, path: vscode.workspace.asRelativePath(doc.uri, false), language: doc.languageId },
        AI_REQUEST_TIMEOUT_MS
      );
      const ms = Date.now() - started;
      const text = result.completion ?? "";
      this.logInline(`TEST result in ${ms}ms: ${JSON.stringify(text)}`);
      vscode.window.showInformationMessage(
        text.trim()
          ? `Code-AI inline (${ms}ms): ${JSON.stringify(text.slice(0, 120))}`
          : `Code-AI inline (${ms}ms): the model returned an empty completion. Try a faster/available inline model.`
      );
    } catch (err) {
      this.logInline(`TEST error after ${Date.now() - started}ms: ${String(err)}`);
      vscode.window.showErrorMessage(`Code-AI inline test failed: ${String(err)}`);
    }
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
    const target: ExplainTarget = {
      path: vscode.workspace.asRelativePath(doc.uri, false),
      language: doc.languageId,
      startLine: range.start.line + 1,
      endLine: range.end.line + 1,
    };
    // Mirror the analysis into the side panel immediately, so the user gets a
    // reliable, always-visible result even when the native hover is finicky.
    this.webview?.postMessage({ type: "explainStatus", status: "analyzing", target });
    const promise = this.computeExplain(code, doc, target);
    this.explainJob = { uri: doc.uri.toString(), rangeKey, range, promise };
    // Best-effort: pop the hover once ready, if the user is still on the editor.
    void promise.then(() => {
      if (this.explainJob?.rangeKey === rangeKey) {
        void vscode.commands.executeCommand("editor.action.showHover");
      }
    });
  }

  private async computeExplain(
    code: string,
    doc: vscode.TextDocument,
    target: ExplainTarget
  ): Promise<vscode.MarkdownString> {
    try {
      const result = await this.client!.request<{ markdown: string }>(
        "explainCode",
        { code, path: target.path, language: target.language },
        AI_REQUEST_TIMEOUT_MS
      );
      const markdown = result.markdown || "_No explanation available._";
      this.webview?.postMessage({ type: "explainResult", markdown, target });
      const md = new vscode.MarkdownString(markdown);
      md.supportThemeIcons = true;
      return md;
    } catch (err) {
      console.error("[code-ai] explainCode failed", err);
      this.webview?.postMessage({ type: "explainError", message: String(err) });
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

  /**
   * Answer the composer's `@` picker from the editor's own file index.
   *
   * The workspace search lives here rather than in the bridge because VS Code
   * already knows the workspace, honours `files.exclude` and search.exclude,
   * and does it without a process hop.
   */
  private async searchWorkspaceFiles(query: string): Promise<void> {
    const pattern = query ? `**/*${query.replace(/[*?{}[\]]/g, "")}*` : "**/*";
    try {
      const found = await vscode.workspace.findFiles(
        pattern,
        "**/{node_modules,.git,dist,out,build,.venv,__pycache__}/**",
        200
      );
      const paths = found
        .map((uri) => vscode.workspace.asRelativePath(uri, false).replace(/\\/g, "/"))
        .sort((a, b) => a.length - b.length);
      this.webview?.postMessage({ type: "fileMatches", query, paths });
    } catch (err) {
      console.error("[code-ai] searchFiles failed", err);
      this.webview?.postMessage({ type: "fileMatches", query, paths: [] });
    }
  }

  /**
   * Fire a bridge method whose only answer is a status line, and put that line
   * in the chat. Panel buttons (replan, goal, terminal, index) all go through
   * here so a failure is never silent.
   */
  private runCommand(
    method: BridgeMethod,
    params: Record<string, unknown>,
    client: BridgeClient | null | undefined
  ): void {
    if (!client) {
      this.webview?.postMessage({
        type: "commandResult",
        ok: false,
        text: "Code-AI: a bridge não está rodando.",
      });
      return;
    }
    client
      .request<{ message?: string; ok?: boolean }>(method, params, AI_REQUEST_TIMEOUT_MS)
      .then((result) => {
        const text = String(result?.message ?? "");
        if (text.trim()) {
          this.webview?.postMessage({ type: "commandResult", ok: result?.ok !== false, text });
        }
      })
      .catch((err) =>
        this.webview?.postMessage({ type: "commandResult", ok: false, text: String(err) })
      );
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "dist")],
    };
    // Render the UI first and unconditionally: the webview must never be blank,
    // even if the bridge fails to spawn. Bridge errors are surfaced as events
    // inside the already-rendered UI instead of leaving an empty panel.
    this.view = view;
    this.webview = view.webview;
    view.webview.html = renderHtml(view.webview, this.extensionUri);

    view.webview.onDidReceiveMessage((message: WebviewToHost) => {
      const client = this.client;
      switch (message.type) {
        case "submit": {
          const context =
            message.includeContext === false ? "" : formatEditorContext(currentEditorContext());
          client?.send("submitUserMessage", {
            text: message.text,
            context,
            images: message.images ?? [],
            // Default false: a second message is usually a second question, and
            // steering silently folds it into the answer to the first.
            steer: message.steer === true,
          });
          break;
        }
        case "newConversation":
          client?.send("newConversation", { conversation_id: message.id });
          break;
        case "listConversations":
          client
            ?.request<{ conversations: any[] }>("listConversations")
            .then((r) =>
              this.webview?.postMessage({ type: "conversationsList", conversations: r.conversations ?? [] })
            )
            .catch((err) => console.error("[code-ai] listConversations failed", err));
          break;
        case "loadConversation":
          client
            ?.request<{ id: string; messages: any[] }>("loadConversation", { conversation_id: message.id })
            .then((r) =>
              this.webview?.postMessage({
                type: "conversationLoaded",
                id: r.id ?? message.id,
                messages: r.messages ?? [],
              })
            )
            .catch((err) => this.recoverFromMissingConversation(client, message.id, err));
          break;
        case "deleteConversation":
          client?.send("deleteConversation", { conversation_id: message.id });
          break;
        case "getSettings":
          client
            ?.request("getSettings")
            .then((settings) => this.webview?.postMessage({ type: "settings", settings }))
            .catch((err) => console.error("[code-ai] getSettings failed", err));
          break;
        case "updateSettings":
          client
            ?.request<{ settings?: { inline_hints_enabled?: boolean; inline_model?: string; model?: string } }>(
              "updateSettings",
              { updates: message.updates }
            )
            .then((result) => {
              // Keep the inline-hints toggle/status bar in sync with edits made
              // from the settings panel.
              this.applyInlineSettings(result.settings);
              this.webview?.postMessage({ type: "settingsUpdated", result });
            })
            .catch((err) =>
              vscode.window.showErrorMessage(`Code-AI: failed to save settings: ${String(err)}`)
            );
          break;
        case "listModels":
          client
            ?.request<{ models: string[] }>("listModels", {}, AI_REQUEST_TIMEOUT_MS)
            .then((r) => this.webview?.postMessage({ type: "modelsListed", models: r.models ?? [] }))
            .catch((err) => this.webview?.postMessage({ type: "modelsError", message: String(err) }));
          break;
        case "cancel":
          client?.send("cancel");
          break;
        case "compact":
          client?.send("compact");
          break;
        case "resolveApproval":
          client?.send("resolveApproval", {
            call_id: message.call_id,
            scope: message.scope,
            reason: message.reason ?? "",
          });
          break;
        case "setPermissionMode":
          client?.send("setPermissionMode", { mode: message.mode });
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
        case "answerQuestions":
          client?.send("answerQuestion", { answer: message.text });
          break;
        case "setPlannerMode":
          client?.send("setPlannerMode", { mode: message.mode });
          break;
        case "replan":
          this.runCommand("replan", {}, client);
          break;
        case "startPlan":
          this.runCommand("startPlan", {}, client);
          break;
        case "goal": {
          const method =
            message.action === "define"
              ? "goalDefine"
              : message.action === "start"
                ? "goalStart"
                : message.action === "stop"
                  ? "goalStop"
                  : "goalStatus";
          this.runCommand(method, { objective: message.objective ?? "" }, client);
          break;
        }
        case "terminal": {
          const request = message.request;
          const method =
            request.action === "start"
              ? "terminalStart"
              : request.action === "write"
                ? "terminalWrite"
                : request.action === "ctrl"
                  ? "terminalControl"
                  : request.action === "kill"
                    ? "terminalKill"
                    : "terminalStatus";
          const params: Record<string, unknown> =
            request.action === "start"
              ? { command: request.command ?? "" }
              : request.action === "write"
                ? { text: request.text }
                : request.action === "ctrl"
                  ? { key: request.key }
                  : {};
          this.runCommand(method, params, client);
          break;
        }
        case "indexRefresh":
          this.runCommand("indexRefresh", { full: message.full, subtree: message.subtree ?? "" }, client);
          break;
        case "summarizeConversation":
          // Fire and forget: the card updates on the next list, and a summary
          // that fails is not worth interrupting the user for.
          client
            ?.request("summarizeConversation", { conversation_id: message.id }, AI_REQUEST_TIMEOUT_MS)
            .then(() =>
              client
                ?.request<{ conversations: any[] }>("listConversations")
                .then((r) =>
                  this.webview?.postMessage({
                    type: "conversationsList",
                    conversations: r.conversations ?? [],
                  })
                )
            )
            .catch((err) => console.error("[code-ai] summarizeConversation failed", err));
          break;
        case "runAsset":
          this.runCommand("runAsset", { token: message.token, argument: message.argument }, client);
          break;
        case "searchFiles":
          void this.searchWorkspaceFiles(message.query);
          break;
        case "listSlashCommands":
          client
            ?.request<{ commands: SlashCommandSpec[] }>("listSlashCommands")
            .then((r) =>
              this.webview?.postMessage({ type: "slashCommands", commands: r.commands ?? [] })
            )
            .catch((err) => console.error("[code-ai] listSlashCommands failed", err));
          break;
        case "restartBridge":
          this.restartBridge();
          break;
      }
    });

    // Keep the webview in sync with what the user is looking at, so it can show
    // a context chip and let the user opt out before sending.
    const pushContext = () =>
      void this.webview?.postMessage({ type: "editorContext", context: currentEditorContext() });
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
      this.client?.dispose();
      this.client = undefined;
      this.view = undefined;
      this.webview = undefined;
    });

    this.spawnBridge();
  }

  /**
   * Spawn the bridge child process and wire its lifecycle to the webview. Reads
   * the configuration fresh on every call, so a {@link restartBridge} picks up
   * edited `code-ai.*` settings. Failures are surfaced inside the already-
   * rendered UI rather than left as a blank panel.
   */
  private spawnBridge(): void {
    const config = vscode.workspace.getConfiguration("code-ai");
    const extraArgs = config.get<string[]>("args", []);
    const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const resolved = resolveCommand(
      config.get<string>("command", DEFAULT_COMMAND),
      this.extensionUri.fsPath,
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
      void this.webview?.postMessage({ type: "event", event: syntheticNotice(resolved.ignoredConfig) });
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
      void this.webview?.postMessage({ type: "event", event: syntheticError(detail) });
      return;
    }
    this.client = client;

    client.on("event", (event: EventEnvelope) => {
      void this.webview?.postMessage({ type: "event", event });
    });
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
    });
    client.on("exit", (code: number | null) => {
      if (this.client !== client) return; // a restart already replaced this bridge
      // Leave the busy state so the "working" heartbeat stops, then show why.
      void this.webview?.postMessage({ type: "event", event: syntheticStatus("DISCONNECTED") });
      void this.webview?.postMessage({ type: "event", event: syntheticError(`bridge exited (${code})`) });
    });

    // Learn whether inline hints are on so the provider and status bar are
    // correct straight away (and after a restart re-reads edited config).
    this.refreshInlineSettings();
  }

  /**
   * A conversation the user picked is not in the store. That is not an error
   * they can act on: the archive is per-workspace, so a webview that kept its
   * history across a folder change - or across a switch from a virtualenv to
   * the bundled backend, which reads a different store - lists entries this
   * process never had. Drop the dead entry, start a fresh conversation, and say
   * so in the panel rather than leaving a modal over an empty transcript.
   */
  private recoverFromMissingConversation(
    client: BridgeClient | undefined,
    id: string,
    err: unknown
  ): void {
    if (!/No saved conversation/i.test(String(err))) {
      vscode.window.showErrorMessage(`Code-AI: could not open conversation: ${String(err)}`);
      return;
    }
    client?.send("newConversation", {});
    void this.webview?.postMessage({
      type: "event",
      event: syntheticNotice(`That conversation is no longer saved (${id}). Started a new one.`),
    });
    // Re-read the archive so the entry the user just clicked stops being listed.
    client
      ?.request<{ conversations: unknown[] }>("listConversations", {})
      .then((r) =>
        this.webview?.postMessage({ type: "conversationsList", conversations: r.conversations ?? [] })
      )
      .catch(() => undefined);
  }

  /**
   * Tear down the running bridge and spawn a fresh one. This re-reads the
   * on-disk config and `code-ai.*` settings, so edits take effect without
   * reloading the whole extension host.
   */
  restartBridge(): void {
    if (!this.webview) return;
    const previous = this.client;
    this.client = undefined;
    this.explainJob = null;
    previous?.dispose();
    void this.webview.postMessage({ type: "event", event: syntheticStatus("RECONNECTING") });
    void this.webview.postMessage({
      type: "event",
      event: syntheticNotice("Restarting Code-AI to apply your settings…"),
    });
    this.spawnBridge();
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

function syntheticStatus(state: string): EventEnvelope {
  return { ...syntheticError(""), event_type: "status.changed", payload: { state } };
}

function syntheticNotice(message: string): EventEnvelope {
  return { ...syntheticError(message), event_type: "warning" };
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
