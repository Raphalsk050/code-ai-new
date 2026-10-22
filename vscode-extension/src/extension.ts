import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

import { NO_TIMEOUT, type BridgeClient } from "./bridge-client";
import { EditDiffs } from "./edit-diff";
import { formatEditorContext, otherOpenFiles } from "./editor-context";
import { SessionChanges } from "./session-changes";
import { TranscriptStore } from "./transcript-store";
import { FIRST_CHECK_MS, nextCheckDelay } from "./update-schedule";
import { initialAgentStatus, reduceAgentStatus, statusView } from "./agent-status";
import {
  BridgeSession,
  StartReason,
  syntheticError,
  syntheticEvent,
  syntheticNotice,
} from "./bridge-session";
import { BoardPanel } from "./board-panel";
import { createFlowFile, FlowEditorProvider } from "./flow-editor";
import type {
  AppMode,
  BridgeMethod,
  EditorContext,
  EditorSettings,
  EventEnvelope,
  ExplainTarget,
  McpAction,
  McpList,
  RefactorImprovement,
  SlashCommandSpec,
  WebviewToHost,
} from "./protocol";

// Stopping a stdio server waits on its process; approving one starts it.
const MCP_REQUEST_TIMEOUT_MS = 60_000;

// AI calls (explain, refactor, a plan, a summary) wait on the model, which may
// think for as long as it needs: no clock here. The bridge dying still fails them.
const AI_REQUEST_TIMEOUT_MS = NO_TIMEOUT;
// A suggestion that arrives this late is about code the user has moved past.
const INLINE_REQUEST_TIMEOUT_MS = 300_000;
// The bridge gives a model catalog a minute (providers/model_listing.py).
const LIST_MODELS_TIMEOUT_MS = 90_000;

// A turn cut short by a dying backend is sent again, like any failed step: up
// to 20 times, counted afresh whenever a model response lands.
const MAX_TURN_RESENDS = 20;
// Backend states that end a turn (synthetic DISCONNECTED does not: that is the
// crash the turn has to survive).
const TURN_ENDED = new Set(["READY", "FAILED", "CLOSED"]);
// A new release is looked for shortly after start; update-schedule.ts says when again.
const UPDATE_FIRST_CHECK_MS = FIRST_CHECK_MS;

/** What getProfile and signInForPicture answer (see updates.Profile.describe). */
interface ProfileAnswer {
  login?: string;
  name?: string;
  avatar?: string;
  problem?: string;
  signIn?: boolean;
}

interface UpdateInfo {
  version?: string;
  current?: string;
  notes?: string;
  url?: string;
  /** False when no release repository is set; ``message`` says where to set it. */
  configured?: boolean;
  message?: string;
  /** The releases need a login on ``host`` this machine does not have yet. */
  needsLogin?: boolean;
  host?: string;
}

export function activate(context: vscode.ExtensionContext): void {
  // One backend for the window. Both the chat panel and the orchestrator-graph
  // editor subscribe to it, so either can be the first thing the user opens.
  const bridge = new BridgeSession(context.extensionUri.fsPath);
  // The agent's file edits, shown in the editor's diff view as they are made.
  // Subscribed here rather than by the chat view, so they show with it closed.
  const diffs = new EditDiffs();
  // Everything the agent changed in the conversation, for the rail's Changes.
  const changes = new SessionChanges();
  // Per workspace, as the backend keeps its own record of the conversations.
  const transcripts = new TranscriptStore(
    path.join((context.storageUri ?? context.globalStorageUri).fsPath, "transcripts")
  );
  context.subscriptions.push({ dispose: () => void transcripts.flush() });
  const provider = new ChatViewProvider(context.extensionUri, bridge, diffs, changes, transcripts);
  const flowEditor = new FlowEditorProvider(context.extensionUri, bridge, context.secrets);
  const board = new BoardPanel(context.extensionUri, bridge);
  provider.onOpenBoard = () => board.open();
  provider.onSettingsSaved = () => flowEditor.refreshCatalog();
  context.subscriptions.push(
    bridge,
    diffs,
    changes,
    vscode.window.registerFileDecorationProvider(changes),
    bridge.subscribe({
      onEvent: (event) => {
        diffs.handle(event);
        changes.handle(event);
        showAgentStatus(event);
      },
    }),
    vscode.window.registerWebviewViewProvider("code-ai.chat", provider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    // A chat left in its own window comes back in it after a reload.
    vscode.window.registerWebviewPanelSerializer("code-ai.window", {
      deserializeWebviewPanel: async (panel, state) => provider.adoptWindow(panel, state),
    }),
    vscode.window.registerCustomEditorProvider(FlowEditorProvider.viewType, flowEditor, {
      webviewOptions: { retainContextWhenHidden: true },
      supportsMultipleEditorsPerDocument: false,
    }),
    vscode.commands.registerCommand("code-ai.open", () =>
      vscode.commands.executeCommand("code-ai.chat.focus")
    ),
    vscode.commands.registerCommand("code-ai.popOut", () => provider.requestPopOut()),
    // Respawn the bridge so edited settings/config take effect without having to
    // reload the whole extension host.
    vscode.commands.registerCommand("code-ai.restart", () => bridge.restart()),
    board,
    // A board tab left open comes back after a reload, like a graph's.
    vscode.window.registerWebviewPanelSerializer(BoardPanel.viewType, {
      deserializeWebviewPanel: async (panel) => board.adopt(panel),
    }),
    vscode.commands.registerCommand("code-ai.openBoard", () => board.open()),
    vscode.commands.registerCommand("code-ai.newFlow", () => createFlowFile()),
    vscode.commands.registerCommand("code-ai.runFlow", () => flowEditor.runActive()),
    vscode.commands.registerCommand("code-ai.stopFlow", () => flowEditor.stopActive()),
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

  // The agent at a glance with the sidebar closed: context left, and whether
  // it is working or waiting on an approval.
  const agentItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 101);
  agentItem.command = "code-ai.open";
  context.subscriptions.push(agentItem);
  let agentStatus = initialAgentStatus;
  const paint = () => {
    const view = statusView(agentStatus);
    agentItem.text = view.text;
    agentItem.tooltip = view.tooltip;
    agentItem.backgroundColor = view.warn ? new vscode.ThemeColor("statusBarItem.warningBackground") : undefined;
    agentItem.show();
  };
  function showAgentStatus(event: EventEnvelope): void {
    const next = reduceAgentStatus(agentStatus, event);
    if (next === agentStatus) return;
    agentStatus = next;
    paint();
  }
  paint();
}

export function deactivate(): void {
  // The view provider disposes its bridge in onDidDispose.
}

class ChatViewProvider implements vscode.WebviewViewProvider {
  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly bridge: BridgeSession,
    private readonly diffs: EditDiffs,
    private readonly changes: SessionChanges,
    private readonly transcripts: TranscriptStore
  ) {}

  /** The extension's own settings, as the panel's settings screen edits them. */
  private postEditorSettings(): void {
    const config = vscode.workspace.getConfiguration("code-ai");
    const settings: EditorSettings = {
      diffInEditor: config.get<boolean>("diff.openInEditor", true),
      command: config.get<string>("command", "") ?? "",
      args: config.get<string[]>("args", []) ?? [],
    };
    void this.webview?.postMessage({ type: "editorSettings", settings });
  }

  private async updateEditorSettings(updates: Partial<EditorSettings>): Promise<void> {
    const config = vscode.workspace.getConfiguration("code-ai");
    const target = vscode.ConfigurationTarget.Global;
    try {
      if (updates.diffInEditor !== undefined) await config.update("diff.openInEditor", updates.diffInEditor, target);
      if (updates.command !== undefined) await config.update("command", updates.command, target);
      if (updates.args !== undefined) await config.update("args", updates.args, target);
    } catch (err) {
      void vscode.window.showErrorMessage(`Code-AI could not save its settings: ${(err as Error).message}`);
    }
    this.postEditorSettings();
  }

  /** The window's backend, shared with the graph editor. */
  private get client(): BridgeClient | undefined {
    return this.bridge.client;
  }

  /** Called after a settings save lands, so other views can refresh. */
  onSettingsSaved?: () => void;
  /** The chat header's board button. */
  onOpenBoard?: () => void;
  private view?: vscode.WebviewView;
  /** The chat in a window of its own, while it is popped out of the sidebar. */
  private window?: vscode.WebviewPanel;
  /** The surface holding the chat: the window while there is one, else the sidebar. */
  private webview?: vscode.Webview;
  /** What the surfaces share while any is open (see attach). */
  private attached?: vscode.Disposable[];
  /** The backend's last word on its state, for a surface that opens later. */
  private lastStatus?: EventEnvelope;
  /** The window's latest saved state, handed back to the sidebar when it closes. */
  private windowState?: unknown;
  /** What the next surface to load takes over: the list, preferences and open conversation. */
  private handOver?: { state?: unknown; conversation?: string };
  /** The thread on screen, so a backend that restarts can pick it up again. */
  private activeConversation?: string;
  /** Messages sent while the backend was down, delivered once it is back. */
  private pendingSubmits: Record<string, unknown>[] = [];
  /** Messages whose turn has not ended. A backend that dies takes its turn
   * with it, so these go out again once the replacement is up. */
  private inFlight: Record<string, unknown>[] = [];
  /** Times the running turn was sent again since a model response last landed. */
  private resends = 0;
  /** The newer release the backend found, once it has found one. */
  private update?: UpdateInfo;
  private updateProblem?: string;
  private updateTimer?: ReturnType<typeof setTimeout>;
  /** Looks in a row that got no answer: what the next one is timed by. */
  private updateFailures = 0;
  private updating = false;
  private signingIn = false;
  /** Set while the releases host wants a login; the button offers it. */
  private loginHost?: string;
  /** Who is signed in on the releases host: the chat's user avatar. */
  private profile?: Required<ProfileAnswer>;
  /** A picture sign-in window is open; a second click waits for it. */
  private pictureSignIn = false;
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
        INLINE_REQUEST_TIMEOUT_MS
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
    client: BridgeClient | null | undefined,
    reply: "commandResult" | "report" = "commandResult"
  ): void {
    if (!client) {
      this.webview?.postMessage({
        type: reply,
        ok: false,
        text: "Code-AI: the bridge is not running.",
      });
      return;
    }
    client
      .request<{ message?: string; ok?: boolean }>(method, params, AI_REQUEST_TIMEOUT_MS)
      .then((result) => {
        const text = String(result?.message ?? "");
        if (text.trim()) {
          this.webview?.postMessage({ type: reply, ok: result?.ok !== false, text });
        }
      })
      .catch((err) => this.webview?.postMessage({ type: reply, ok: false, text: String(err) }));
  }

  /** Run one MCP action, then hand the panel the list as it stands afterwards. */
  private async runMcp(action: McpAction, client: BridgeClient | null | undefined): Promise<void> {
    if (!client) {
      this.webview?.postMessage({ type: "mcpError", message: "Code-AI: the bridge is not running." });
      return;
    }
    try {
      if (action.action === "reconnect") {
        await client.request("mcpReconnect", { name: action.name }, MCP_REQUEST_TIMEOUT_MS);
      } else if (action.action === "approve") {
        await client.request("mcpApprove", { name: action.name }, MCP_REQUEST_TIMEOUT_MS);
      } else if (action.action === "setEnabled") {
        await client.request(
          "mcpSetEnabled",
          { name: action.name, enabled: action.enabled },
          MCP_REQUEST_TIMEOUT_MS
        );
      } else if (action.action === "save") {
        await client.request(
          "mcpSave",
          { server: action.server, previous: action.previous ?? "" },
          MCP_REQUEST_TIMEOUT_MS
        );
      } else if (action.action === "remove") {
        await client.request("mcpRemove", { name: action.name }, MCP_REQUEST_TIMEOUT_MS);
      } else if (action.action === "signIn") {
        await client.request("mcpSignIn", { name: action.name }, MCP_REQUEST_TIMEOUT_MS);
      } else if (action.action === "signOut") {
        await client.request("mcpSignOut", { name: action.name }, MCP_REQUEST_TIMEOUT_MS);
      }
    } catch (err) {
      this.webview?.postMessage({ type: "mcpError", message: String(err).replace(/^Error: /, "") });
    }
    // The list either way: a refused edit still leaves the panel showing what is true.
    try {
      const list = await client.request<McpList>("mcpList");
      this.webview?.postMessage({ type: "mcpServers", list });
    } catch (err) {
      this.webview?.postMessage({ type: "mcpError", message: String(err).replace(/^Error: /, "") });
    }
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    view.webview.options = this.webviewOptions();
    // Render the UI first and unconditionally: the webview must never be blank,
    // even if the bridge fails to spawn. Bridge errors are surfaced as events
    // inside the already-rendered UI instead of leaving an empty panel.
    this.view = view;
    view.webview.html = renderHtml(view.webview, this.extensionUri);
    view.webview.onDidReceiveMessage((message: WebviewToHost) => this.onMessage(message, view.webview));
    view.onDidChangeVisibility(() => {
      if (view.visible) this.pushContext();
    });
    view.onDidDispose(() => {
      this.view = undefined;
      if (this.webview === view.webview) this.webview = undefined;
      this.detachIfAlone();
    });
    // While the chat is in its own window, the sidebar only says where it went.
    if (!this.window) this.webview = view.webview;
    this.attach();
  }

  private webviewOptions(): vscode.WebviewOptions {
    return {
      enableScripts: true,
      // The workspace too: pictures in answers and documents are its files.
      localResourceRoots: [
        vscode.Uri.joinPath(this.extensionUri, "dist"),
        ...(vscode.workspace.workspaceFolders ?? []).map((folder) => folder.uri),
      ],
    };
  }

  /**
   * A message from a surface. Only the one holding the chat is answered: the
   * other is the sidebar while the chat is in its own window, and all it can
   * do is say so and call the chat back.
   */
  private onMessage(message: WebviewToHost, from: vscode.Webview): void {
    if (from !== this.webview) {
      if (message.type === "popIn") this.popIn();
      else if (message.type === "getSettings") void from.postMessage({ type: "surface", active: false });
      return;
    }
    const client = this.client;
    switch (message.type) {
      case "submit": {
        const context =
          message.includeContext === false ? "" : formatEditorContext(currentEditorContext());
        const params = {
          text: message.text,
          context,
          images: message.images ?? [],
          // Default false: a second message is usually a second question, and
          // steering silently folds it into the answer to the first.
          steer: message.steer === true,
        };
        if (client) {
          this.inFlight.push(params);
          client.send("submitUserMessage", params);
          break;
        }
        // Dropping it here is what made one crash look like a dead panel.
        this.pendingSubmits.push(params);
        void this.webview?.postMessage({
          type: "event",
          event: syntheticNotice(
            "The Code-AI backend is not running; starting it, and your message goes " +
              "out as soon as it is up."
          ),
        });
        this.bridge.ensureStarted();
        break;
      }
      case "newConversation":
        this.activeConversation = message.id;
        this.changes.reset();
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
        this.activeConversation = message.id;
        this.changes.reset();
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
        if (message.id === this.activeConversation) this.activeConversation = undefined;
        client?.send("deleteConversation", { conversation_id: message.id });
        void this.transcripts.remove(message.id);
        break;
      case "saveTranscript": {
        const id = message.id;
        void this.transcripts.save(id, message.from, message.items).then((kept) => {
          if (!kept) void this.webview?.postMessage({ type: "transcriptResend", id });
        });
        break;
      }
      case "loadTranscript": {
        const id = message.id;
        void this.transcripts.load(id).then((items) => this.webview?.postMessage({ type: "transcript", id, items }));
        break;
      }
      case "adoptTranscripts":
        void this.transcripts.adopt(message.conversations);
        break;
      case "answerNow":
        client?.request("answerNow", { text: message.text }).catch((err) =>
          this.webview?.postMessage({
            type: "event",
            event: syntheticError(`could not hand the message over: ${String(err)}`),
          })
        );
        break;
      case "deleteMessage":
        client
          ?.request<{ deleted?: boolean }>("deleteMessage", {
            text: message.text,
            from_end: message.fromEnd,
            count: message.count,
          })
          .then((answer) => {
            if (answer?.deleted) return;
            // Off the screen either way; the agent's memory of it is another matter.
            void this.webview?.postMessage({
              type: "event",
              event: syntheticNotice(
                "That message had already been folded into the conversation's summary: " +
                  "it is off the list, but the summary still holds what it said."
              ),
            });
          })
          .catch((err) =>
            this.webview?.postMessage({
              type: "event",
              event: syntheticError(`could not delete the message: ${String(err)}`),
            })
          );
        break;
      case "getSettings":
        // The panel asks this as it mounts, so it is listening by now: what
        // was posted while it was still loading may never have reached it.
        this.greet(true);
        client
          ?.request("getSettings")
          .then((settings) => this.webview?.postMessage({ type: "settings", settings }))
          .catch((err) => {
            console.error("[code-ai] getSettings failed", err);
            // The settings screen renders nothing until this answer lands, so
            // a rejection swallowed here is a screen that loads for ever.
            void this.webview?.postMessage({
              type: "event",
              event: syntheticError(`could not read the settings: ${String(err)}`),
            });
          });
        break;
      case "desktopAvatarSeat":
        client?.request("desktopAvatarSeat", { seat: message.seat }).catch((err) =>
          console.error("[code-ai] desktopAvatarSeat failed", err)
        );
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
            // The graph editor caches the catalog, and the catalog carries the
            // language: without this, changing it there leaves that tab in the
            // old one until the window reloads.
            this.onSettingsSaved?.();
          })
          .catch((err) =>
            vscode.window.showErrorMessage(`Code-AI: failed to save settings: ${String(err)}`)
          );
        break;
      case "listModels":
        client
          ?.request<{ models: string[] }>("listModels", {}, LIST_MODELS_TIMEOUT_MS)
          .then((r) => this.webview?.postMessage({ type: "modelsListed", models: r.models ?? [] }))
          .catch((err) => this.webview?.postMessage({ type: "modelsError", message: String(err) }));
        break;
      case "mcp":
        void this.runMcp(message, client);
        break;
      case "mcpElicitation":
        client
          ?.request<{ ok: boolean; message: string }>("mcpElicitationAnswer", {
            id: message.id,
            action: message.action,
            content: message.content ?? {},
          })
          .then((r) =>
            this.webview?.postMessage({
              type: "mcpElicitationResult",
              id: message.id,
              ok: r.ok,
              message: r.message ?? "",
            })
          )
          .catch((err) =>
            this.webview?.postMessage({
              type: "mcpElicitationResult",
              id: message.id,
              ok: false,
              message: String(err).replace(/^Error: /, ""),
            })
          );
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
      case "report":
        this.runCommand(message.method, {}, client, "report");
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
        this.bridge.restart();
        break;
      case "openDiff":
        void this.diffs.open(message.path);
        break;
      case "openChange":
        void this.changes.open(message.path);
        break;
      case "revertChange":
        void this.changes.revert(message.path);
        break;
      case "reviewChanges":
        void this.changes.reviewAll();
        break;
      case "updateEditorSettings":
        void this.updateEditorSettings(message.updates);
        break;
      case "installUpdate":
        void this.installUpdate();
        break;
      case "signInForUpdates":
        void this.signInForUpdates();
        break;
      case "avatarSignIn":
        void this.signInForPicture();
        break;
      case "reloadWindow":
        void vscode.commands.executeCommand("workbench.action.reloadWindow");
        break;
      case "popOut":
        void this.popOut(message.state, message.conversation);
        break;
      case "popIn":
        this.popIn();
        break;
      case "openBoard":
        this.onOpenBoard?.();
        break;
      case "boardCommand":
        this.runCommand("boardCommand", { text: message.text }, client);
        break;
      case "surfaceState":
        if (this.window) this.windowState = message.state;
        break;
    }
  }

  /**
   * What every surface shares, set up when the first one appears and kept
   * until the last one goes: the backend's events and the editor's context
   * reach whichever surface holds the chat.
   */
  private attach(): void {
    if (this.attached) return;
    this.attached = [
      // Keep the webview in sync with what the user is looking at, so it can
      // show a context chip and let the user opt out before sending.
      vscode.window.onDidChangeActiveTextEditor(() => this.pushContext()),
      // A tab opened, closed or moved: the list of open files changed.
      vscode.window.tabGroups.onDidChangeTabs(() => this.pushContext()),
      vscode.window.onDidChangeTextEditorSelection((e) => {
        if (e.textEditor !== vscode.window.activeTextEditor) return;
        this.pushContext();
        if (this.mode === "explain") this.scheduleExplain(e.textEditor);
        else if (this.mode === "refactor" && this.autoRunRefactor) this.scheduleRefactor();
      }),
      // Events reach the panel through the session, so the backend keeps
      // running when the panel is closed and re-attaches when it is reopened.
      this.bridge.subscribe({
        onEvent: (event) => {
          this.trackTurn(event);
          if (event.event_type === "status.changed") this.lastStatus = event;
          // An MCP sign-in: the page opens in the user's browser, the VS Code way,
          // so a remote window still reaches the machine the user sits at.
          // Web pages only: a command: or file: link here would run in the editor.
          const url = event.event_type === "mcp.auth.url" ? String(event.payload?.url ?? "") : "";
          if (/^https?:\/\//i.test(url)) void vscode.env.openExternal(vscode.Uri.parse(url));
          void this.webview?.postMessage({ type: "event", event });
        },
        onRestart: () => {
          this.explainJob = null;
          // The restart re-read the config, so the hint state may have changed.
          this.refreshInlineSettings();
        },
        onStarted: (client, reason) => void this.resume(client, reason),
      }),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("code-ai.diff.openInEditor")) this.postEditorDiff();
        if (["code-ai.diff.openInEditor", "code-ai.command", "code-ai.args"].some((key) => e.affectsConfiguration(key))) {
          this.postEditorSettings();
        }
      }),
      this.changes.onDidChangeList((files) => void this.webview?.postMessage({ type: "sessionChanges", files })),
    ];
    this.greet(false);

    this.bridge.ensureStarted();
    // A backend the graph editor started before this view existed never told
    // it so: without this, that window would not look for updates at all.
    if (!this.updateTimer) this.scheduleUpdateChecks();
    // Learn whether inline hints are on so the provider and status bar are
    // correct straight away (and after a restart re-reads edited config).
    this.refreshInlineSettings();
  }

  private detachIfAlone(): void {
    if (this.view || this.window || !this.attached) return;
    this.attached.forEach((d) => d.dispose());
    this.attached = undefined;
    if (this.explainTimer) clearTimeout(this.explainTimer);
    if (this.refactorTimer) clearTimeout(this.refactorTimer);
    this.explainJob = null;
    this.refactorTarget = null;
  }

  /**
   * Everything a surface draws from the host, for one that has just loaded.
   * The hand-over waits for a surface that is `listening`: posted to one still
   * loading, it would be lost.
   */
  private greet(listening: boolean): void {
    this.pushContext();
    this.postEditorDiff();
    this.postEditorSettings();
    void this.changes.summaries().then((files) => this.webview?.postMessage({ type: "sessionChanges", files }));
    this.postUpdate();
    this.postProfile();
    this.postResourceRoots();
    // Where the backend is, for a surface that missed it saying so.
    if (this.lastStatus) void this.webview?.postMessage({ type: "event", event: this.lastStatus });
    // A surface taking the chat over: the list, the preferences and the open
    // conversation the other one had.
    if (listening && this.handOver) {
      void this.webview?.postMessage({ type: "surface", active: true, ...this.handOver });
      this.handOver = undefined;
    }
  }

  private pushContext(): void {
    void this.webview?.postMessage({ type: "editorContext", context: currentEditorContext() });
  }

  /** Where edits are reviewed: the editor's diff, or the panel's own view. */
  private postEditorDiff(): void {
    void this.webview?.postMessage({ type: "editorDiff", enabled: this.diffs.enabled });
  }

  /**
   * The chat in an editor tab of its own, moved into a window of its own: one
   * that can go to another monitor. The conversation goes with it, a turn in
   * progress included, and the sidebar says where it went.
   */
  async popOut(state?: unknown, conversation?: string): Promise<void> {
    if (this.window) {
      this.window.reveal();
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "code-ai.window",
      "Code-AI",
      { viewColumn: vscode.ViewColumn.Active, preserveFocus: false },
      { ...this.webviewOptions(), retainContextWhenHidden: true }
    );
    this.handOver = { state, conversation: conversation ?? this.activeConversation };
    this.adoptWindow(panel, state);
    // VS Code 1.85 and later move an editor into a window of its own; before,
    // the chat stays a tab the user can place where they like.
    await vscode.commands.executeCommand("workbench.action.moveEditorToNewWindow").then(undefined, () => undefined);
  }

  /** The chat moves into `panel`: one just made, or one VS Code restored after a reload with its saved state. */
  adoptWindow(panel: vscode.WebviewPanel, state?: unknown): void {
    if (this.window && this.window !== panel) this.window.dispose();
    panel.webview.options = this.webviewOptions();
    panel.iconPath = vscode.Uri.joinPath(this.extensionUri, "media", "icon.svg");
    this.window = panel;
    this.windowState = state;
    this.webview = panel.webview;
    panel.webview.html = renderHtml(panel.webview, this.extensionUri, { window: true });
    panel.webview.onDidReceiveMessage((message: WebviewToHost) => this.onMessage(message, panel.webview));
    panel.onDidDispose(() => this.popIn(panel));
    void this.view?.webview.postMessage({ type: "surface", active: false });
    this.attach();
  }

  /** Back to the sidebar, with whatever the window had: the window closes. */
  popIn(closed?: vscode.WebviewPanel): void {
    const panel = this.window;
    if (!panel || (closed && closed !== panel)) return;
    this.window = undefined;
    this.handOver = { state: this.windowState, conversation: this.activeConversation };
    this.windowState = undefined;
    if (this.view) {
      // Still loaded, showing where the chat went: it is listening.
      this.webview = this.view.webview;
      this.greet(true);
      this.view.show?.(true);
    } else {
      this.webview = undefined;
      // The sidebar was never opened, or was closed: opening it takes the chat back.
      void vscode.commands.executeCommand("code-ai.chat.focus");
    }
    if (!closed) panel.dispose();
    this.detachIfAlone();
  }

  /** Asked from the command palette or the sidebar's title bar: the sidebar hands its state over. */
  requestPopOut(): void {
    if (this.window) {
      this.window.reveal();
    } else if (this.view && this.webview === this.view.webview) {
      void this.view.webview.postMessage({ type: "popOutRequested" });
    } else {
      void this.popOut();
    }
  }

  /**
   * Look for a newer release soon, and keep looking: one timer, re-armed by
   * each look. A look that got no answer - the backend still starting, the
   * network not up yet - is tried again soon instead of hours later.
   */
  private scheduleUpdateChecks(delayMs = UPDATE_FIRST_CHECK_MS): void {
    if (this.updateTimer) clearTimeout(this.updateTimer);
    this.updateTimer = setTimeout(() => {
      this.updateTimer = undefined;
      void this.checkForUpdate().finally(() => {
        // Unless something armed it meanwhile (a backend that just restarted).
        if (!this.updateTimer) this.scheduleUpdateChecks(nextCheckDelay(this.updateFailures));
      });
    }, delayMs);
  }

  private async checkForUpdate(): Promise<UpdateInfo | undefined> {
    const client = this.client;
    if (!client) {
      this.updateFailures += 1;
      return undefined;
    }
    try {
      // The platform goes along, so a release still missing this one's package is not offered.
      const target = `${process.platform}-${process.arch}`;
      const found = await client.request<UpdateInfo>("checkForUpdate", { target }, 60_000);
      this.updateFailures = 0;
      this.updateProblem = found?.configured === false ? found.message || "Updates are off." : undefined;
      // Only while no sign-in is under way: its own answer is the one to show.
      if (!this.signingIn) {
        this.loginHost = found?.needsLogin ? found.host || "GitHub" : undefined;
        if (found?.needsLogin) this.postUpdate();
      }
      // Silent when there is none, or GitHub is out of reach.
      if (found?.version) {
        this.update = found;
        this.postUpdate();
      }
      return found?.version ? found : undefined;
    } catch (err) {
      // Kept for /update to show; a background check stays quiet.
      this.updateFailures += 1;
      this.updateProblem = String(err).replace(/^Error: /, "");
      console.debug("[code-ai] update check failed", err);
      return undefined;
    }
  }

  /**
   * The user's picture on the releases host, through the backend: the same
   * login and host the update check uses. Without one the icon stays.
   */
  private async loadProfile(): Promise<void> {
    const client = this.client;
    if (!client) return;
    try {
      this.takeProfile(await client.request<ProfileAnswer>("getProfile", {}, 60_000));
    } catch (err) {
      // An older backend has no getProfile: the icon stays, and that is all.
      console.warn("[code-ai] profile lookup failed", err);
    }
  }

  /**
   * A host in private mode shows the picture to browser sessions only: open a
   * browser window the user signs in to. On a click, never on its own.
   */
  private async signInForPicture(): Promise<void> {
    const client = this.client;
    if (!client || this.pictureSignIn) return;
    this.pictureSignIn = true;
    try {
      // Long enough to get through a company sign-in by hand.
      this.takeProfile(await client.request<ProfileAnswer>("signInForPicture", {}, 6 * 60_000));
    } catch (err) {
      const reason = String(err).replace(/^Error: /, "");
      this.takeProfile({ ...this.profile, problem: reason, signIn: true });
    } finally {
      this.pictureSignIn = false;
    }
  }

  private takeProfile(found?: ProfileAnswer): void {
    if (!found) return;
    this.profile = {
      login: found.login ?? "",
      name: found.name ?? "",
      avatar: found.avatar ?? "",
      problem: found.problem ?? "",
      signIn: found.signIn ?? false,
    };
    // The backend log has it too; this is for whoever opens the dev tools.
    if (this.profile.problem) console.warn(`[code-ai] no GitHub picture: ${this.profile.problem}`);
    this.postProfile();
  }

  /** Where each workspace folder's files are served, for the pictures in answers and documents. */
  private postResourceRoots(): void {
    const webview = this.webview;
    if (!webview) return;
    const roots = (vscode.workspace.workspaceFolders ?? []).map((folder) => ({
      path: folder.uri.fsPath,
      base: webview.asWebviewUri(folder.uri).toString().replace(/\/+$/, ""),
    }));
    void webview.postMessage({ type: "resourceRoots", roots });
  }

  private postProfile(): void {
    if (this.profile) void this.webview?.postMessage({ type: "profile", ...this.profile });
  }

  private postUpdate(): void {
    const found = this.update;
    if (!found?.version) {
      if (this.loginHost) void this.webview?.postMessage({ type: "updateLogin", host: this.loginHost });
      return;
    }
    void this.webview?.postMessage({
      type: "updateAvailable",
      version: found.version,
      current: found.current ?? "",
      notes: found.notes ?? "",
      url: found.url ?? "",
    });
  }

  /**
   * Sign in to the releases host in git's own window (the one a clone opens), then
   * look again. The login stays with git, so later checks find it by themselves.
   */
  private async signInForUpdates(): Promise<UpdateInfo | undefined> {
    const client = this.client;
    if (!client || this.signingIn) return undefined;
    const post = (state: string, message = "") =>
      void this.webview?.postMessage({ type: "updateProgress", state, message });
    this.signingIn = true;
    post("signingIn");
    try {
      const found = await client.request<UpdateInfo>("signInForUpdates", {}, 10 * 60_000);
      this.loginHost = undefined;
      this.updateProblem = undefined;
      // A first login on this machine: the picture can be had now.
      if (!this.profile?.avatar) void this.loadProfile();
      if (found?.version) {
        this.update = found;
        this.postUpdate();
        return found;
      }
      void this.webview?.postMessage({ type: "updateCleared" });
      void vscode.window.showInformationMessage("Signed in. Code-AI is up to date.");
      return undefined;
    } catch (err) {
      const reason = String(err).replace(/^Error: /, "");
      post("login", reason);
      void vscode.window.showWarningMessage(reason);
      return undefined;
    } finally {
      this.signingIn = false;
    }
  }

  /**
   * Download the release's package for this platform through the backend (which
   * knows the proxy and where releases live), install it, and offer a reload.
   */
  private async installUpdate(): Promise<void> {
    if (this.updating) return;
    const client = this.client;
    if (!client) return;
    let found = this.update ?? (await this.checkForUpdate());
    if (!found?.version && this.loginHost) {
      // /update was asked for: sign in here instead of explaining how.
      found = await this.signInForUpdates();
      if (!found?.version) return; // it has said why
    }
    if (!found?.version) {
      if (this.updateProblem) void vscode.window.showWarningMessage(this.updateProblem);
      else void vscode.window.showInformationMessage("Code-AI is up to date.");
      return;
    }
    const post = (state: string, message = "") =>
      void this.webview?.postMessage({ type: "updateProgress", state, message });
    this.updating = true;
    post("downloading");
    try {
      const target = `${process.platform}-${process.arch}`;
      const { path: vsix } = await client.request<{ path: string }>(
        "downloadUpdate",
        { target },
        15 * 60_000
      );
      post("installing");
      await vscode.commands.executeCommand("workbench.extensions.installExtension", vscode.Uri.file(vsix));
      post("installed");
      const choice = await vscode.window.showInformationMessage(
        `Code-AI v${found.version} is installed. Reload the window to start using it.`,
        "Reload"
      );
      if (choice === "Reload") await vscode.commands.executeCommand("workbench.action.reloadWindow");
    } catch (err) {
      const reason = String(err).replace(/^Error: /, "");
      post("failed", reason);
      void vscode.window.showErrorMessage(`Code-AI update failed: ${reason}`);
    } finally {
      this.updating = false;
    }
  }

  /**
   * A new backend process knows nothing of the thread on screen. Point it back
   * at that thread first, then send what was typed while it was down, in that
   * order, so the message lands in the conversation it was written for.
   */
  private async resume(client: BridgeClient, reason: StartReason): Promise<void> {
    this.scheduleUpdateChecks();
    // Alongside the first update check, not ahead of the conversation's reload.
    if (!this.profile?.avatar) setTimeout(() => void this.loadProfile(), UPDATE_FIRST_CHECK_MS);
    // Taken now: the new backend announces READY as it starts, which would
    // otherwise clear the turn this is about to send again.
    const interrupted = reason === "crash" ? this.inFlight.splice(0) : [];
    this.inFlight = [];
    const id = this.activeConversation;
    if (id) {
      try {
        await client.request("loadConversation", { conversation_id: id, adopt: true });
      } catch (err) {
        console.error("[code-ai] could not resume the conversation", err);
      }
    }
    if (interrupted.length && this.resends < MAX_TURN_RESENDS) {
      this.resends += 1;
      for (const params of interrupted) {
        void this.webview?.postMessage({
          type: "event",
          event: syntheticEvent("turn.resending", {
            text: params.text,
            attempt: this.resends,
            max_attempts: MAX_TURN_RESENDS,
          }),
        });
        this.inFlight.push(params);
        client.send("submitUserMessage", params);
      }
    } else if (interrupted.length) {
      void this.webview?.postMessage({
        type: "event",
        event: syntheticError(
          `the backend restarted mid-answer ${MAX_TURN_RESENDS} times in a row; the message was not sent again`
        ),
      });
    }
    const pending = this.pendingSubmits.splice(0);
    for (const params of pending) {
      this.inFlight.push(params);
      client.send("submitUserMessage", params);
    }
  }

  /** Follow whether a turn is still running, for {@link resume}. */
  private trackTurn(event: EventEnvelope): void {
    if (event.source === "extension") return;
    if (event.event_type === "model.response.completed") {
      this.resends = 0;
    } else if (
      event.event_type === "status.changed" &&
      TURN_ENDED.has(String(event.payload?.state ?? ""))
    ) {
      this.inFlight = [];
    }
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

}

/** The files open in editor tabs, workspace-relative, in tab order. */
function openFilePaths(): string[] {
  const paths: string[] = [];
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      const input = tab.input;
      // A file shown as text, in a custom editor (a graph) or as a notebook.
      // Diffs are left out: the agent's own show up there as it works.
      const uri =
        input instanceof vscode.TabInputText ||
        input instanceof vscode.TabInputCustom ||
        input instanceof vscode.TabInputNotebook
          ? input.uri
          : undefined;
      if (uri?.scheme === "file") paths.push(vscode.workspace.asRelativePath(uri, false));
    }
  }
  return paths;
}

/**
 * Build an {@link EditorContext} from the editor: the active file and its
 * selection, and the other files open beside it. `null` when nothing is open.
 */
function currentEditorContext(): EditorContext | null {
  const editor = vscode.window.activeTextEditor;
  // Output, diff and webview documents are not files the user is working on.
  const doc = editor?.document.uri.scheme === "file" ? editor.document : undefined;
  const path = doc ? vscode.workspace.asRelativePath(doc.uri, false) : "";
  const openFiles = otherOpenFiles(openFilePaths(), path || undefined);
  if (!doc || !editor) return openFiles.length ? { path: "", language: "", openFiles } : null;
  const sel = editor.selection;
  const context: EditorContext = { path, language: doc.languageId };
  if (sel && !sel.isEmpty) {
    context.selection = doc.getText(sel);
    context.startLine = sel.start.line + 1;
    context.endLine = sel.end.line + 1;
  }
  if (openFiles.length) context.openFiles = openFiles;
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

function renderHtml(webview: vscode.Webview, extensionUri: vscode.Uri, options: { window?: boolean } = {}): string {
  const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "dist", "webview.js"));
  const codiconsUri = webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "dist", "codicons", "codicon.css"));
  const nonce = makeNonce();
  const csp =
    `default-src 'none'; ` +
    `img-src ${webview.cspSource} https: data:; ` +
    // data: for the formula fonts, which travel inside the bundle.
    `font-src ${webview.cspSource} data:; ` +
    `style-src ${webview.cspSource} 'unsafe-inline'; ` +
    `script-src 'nonce-${nonce}';`;
  return `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta http-equiv="Content-Security-Policy" content="${csp}" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="stylesheet" href="${codiconsUri}" />
    <title>Code-AI</title>
  </head>
  <body data-board="1"${options.window ? ' data-surface="window"' : ""}>
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
