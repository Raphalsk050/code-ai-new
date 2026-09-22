import * as path from "path";
import * as vscode from "vscode";

import { BridgeSession } from "./bridge-session";
import {
  emptyFlow,
  FLOW_EXTENSION,
  FlowGraph,
  FlowHostToWebview,
  FlowWebviewToHost,
  GraphCatalog,
  parseFlow,
  serializeFlow,
  ValidationIssue,
} from "./flow-schema";
import type { EventEnvelope } from "./protocol";

/**
 * The orchestrator-graph editor: a custom editor over `*.flow.json`.
 *
 * Binding it to a real file is what makes save and load disappear as features.
 * Ctrl+S, the dirty dot, tab reuse, reload-on-external-change and `git diff`
 * all come from VSCode owning the document; the toolbar's Save button is a
 * one-line wrapper over it.
 *
 * Undo is deliberately *not* delegated to the document. The canvas keeps its
 * own command stack and pushes the resulting graph after each committed
 * command - two undo stacks over one document fight each other and lose work.
 */
export class FlowEditorProvider implements vscode.CustomTextEditorProvider {
  static readonly viewType = "code-ai.flow";

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly bridge: BridgeSession,
    private readonly secrets: vscode.SecretStorage
  ) {}

  /** The panel the user is looking at, for the palette commands. */
  private active?: FlowSession;
  private catalog?: GraphCatalog;

  async resolveCustomTextEditor(
    document: vscode.TextDocument,
    panel: vscode.WebviewPanel,
    _token: vscode.CancellationToken
  ): Promise<void> {
    panel.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "dist")],
    };
    panel.webview.html = renderFlowHtml(panel.webview, this.extensionUri);

    const session = new FlowSession(
      document,
      panel,
      this.bridge,
      this.secrets,
      () => this.loadCatalog(),
      () => {
        // A restarted backend may serve a different catalog (a profession
        // added, a node kind shipped), so the cached one is dropped.
        this.catalog = undefined;
      }
    );
    this.active = session;
    panel.onDidChangeViewState(() => {
      if (panel.active) this.active = session;
      else if (this.active === session) this.active = undefined;
    });
    panel.onDidDispose(() => {
      if (this.active === session) this.active = undefined;
      session.dispose();
    });
    session.start();
  }

  runActive(): void {
    if (!this.active) {
      void vscode.window.showInformationMessage("Abra um grafo (.flow.json) para executar.");
      return;
    }
    this.active.runFromCommand();
  }

  stopActive(): void {
    this.active?.stopFromCommand();
  }

  /**
   * Fetch the node/profession catalog once per window. It is served by the
   * backend rather than compiled into the webview so a new profession does not
   * need a new VSIX.
   */
  private async loadCatalog(): Promise<GraphCatalog | undefined> {
    if (this.catalog) return this.catalog;
    this.bridge.ensureStarted();
    try {
      this.catalog = await this.bridge.request<GraphCatalog>("graphCatalog", {}, 30000);
    } catch (err) {
      console.error("[code-ai] graphCatalog failed", err);
      return undefined;
    }
    return this.catalog;
  }
}

/** One open graph tab. */
class FlowSession {
  constructor(
    private readonly document: vscode.TextDocument,
    private readonly panel: vscode.WebviewPanel,
    private readonly bridge: BridgeSession,
    private readonly secrets: vscode.SecretStorage,
    private readonly catalog: () => Promise<GraphCatalog | undefined>,
    private readonly invalidateCatalog: () => void
  ) {}

  private readonly subscriptions: vscode.Disposable[] = [];
  /** The text this editor last wrote, so its own change event is ignored. */
  private lastWritten = "";
  private runId = "";
  private running = false;
  private validateTimer?: NodeJS.Timeout;
  private pendingValidation?: FlowGraph;

  start(): void {
    this.subscriptions.push(
      this.panel.webview.onDidReceiveMessage((message: FlowWebviewToHost) =>
        this.onMessage(message)
      ),
      vscode.workspace.onDidChangeTextDocument((event) => {
        if (event.document.uri.toString() !== this.document.uri.toString()) return;
        // Our own write comes back as a change; re-pushing it would fight the
        // user's cursor and, mid-drag, undo the drag.
        if (event.document.getText() === this.lastWritten) return;
        this.pushDocument();
      }),
      this.bridge.subscribe({
        onEvent: (event) => this.onBridgeEvent(event),
        onRestart: () => {
          this.running = false;
          this.post({ type: "flowRunState", running: false, runId: "", message: "" });
          this.invalidateCatalog();
          void this.sendCatalog();
        },
      })
    );
    this.bridge.ensureStarted();
    void this.sendCatalog();
    void this.adoptRunningGraph();
    this.pushDocument();
  }

  /**
   * Re-attach to a run that is already going.
   *
   * A tab closed and reopened mid-run would otherwise show an idle toolbar over
   * a backend that is still hiring agents - and its Play button would be
   * refused with "a graph is already running" for no visible reason.
   */
  private async adoptRunningGraph(): Promise<void> {
    try {
      const status = await this.bridge.request<{
        running: boolean;
        run_id?: string;
      }>("graphStatus", {}, 10000);
      if (!status?.running) return;
      this.running = true;
      this.runId = String(status.run_id ?? "");
      this.post({ type: "flowRunState", running: true, runId: this.runId, message: "" });
    } catch (err) {
      console.error("[code-ai] graphStatus failed", err);
    }
  }

  dispose(): void {
    if (this.validateTimer) clearTimeout(this.validateTimer);
    // A tab closed mid-run leaves the backend running the graph; stopping it is
    // what the user means by closing the editor.
    if (this.running) this.bridge.send("graphStop", { run_id: this.runId });
    this.subscriptions.forEach((item) => item.dispose());
    this.subscriptions.length = 0;
  }

  // -- host -> webview --------------------------------------------------

  private post(message: FlowHostToWebview): void {
    void this.panel.webview.postMessage(message);
  }

  private async sendCatalog(): Promise<void> {
    const catalog = await this.catalog();
    if (catalog) this.post({ type: "flowCatalog", catalog });
    else {
      this.post({
        type: "flowNotice",
        level: "error",
        text: "O backend do Code-AI não respondeu ao catálogo de nós. Use Code-AI: Restart.",
      });
    }
  }

  private pushDocument(): void {
    const parsed = parseFlow(this.document.getText());
    if (parsed.error || !parsed.graph) {
      this.post({
        type: "flowNotice",
        level: "error",
        text: parsed.error ?? "Não foi possível ler este arquivo.",
      });
      return;
    }
    this.post({
      type: "flowDocument",
      graph: parsed.graph,
      fileName: path.basename(this.document.uri.fsPath),
      readOnly: false,
    });
    this.scheduleValidation(parsed.graph);
  }

  private onBridgeEvent(event: EventEnvelope): void {
    // Only graph traffic and the bridge's own notices concern this editor; the
    // chat panel handles the rest.
    if (!event.event_type.startsWith("graph.") && event.event_type !== "error") return;
    this.post({ type: "flowEvent", event });
    if (event.event_type === "graph.run.started") {
      this.running = true;
      this.runId = String(event.payload?.run_id ?? "");
      this.post({ type: "flowRunState", running: true, runId: this.runId, message: "" });
    }
    if (event.event_type === "graph.run.completed") {
      this.running = false;
      this.post({
        type: "flowRunState",
        running: false,
        runId: String(event.payload?.run_id ?? ""),
        message: String(event.payload?.reason ?? event.payload?.status ?? ""),
      });
    }
  }

  // -- webview -> host --------------------------------------------------

  private onMessage(message: FlowWebviewToHost): void {
    switch (message.type) {
      case "flowReady":
        void this.sendCatalog();
        void this.adoptRunningGraph();
        this.pushDocument();
        break;
      case "flowEdit":
        void this.write(message.graph);
        this.scheduleValidation(message.graph);
        break;
      case "flowSave":
        void this.document.save();
        break;
      case "flowValidate":
        this.scheduleValidation(message.graph, 0);
        break;
      case "flowRun":
        void this.run(message.graph, message.variables);
        break;
      case "flowStop":
        void this.stop();
        break;
      case "flowAnswer":
        this.bridge.send("graphAnswer", {
          run_id: message.runId || this.runId,
          call_id: message.callId,
          text: message.text,
        });
        break;
      case "flowListModels":
        void this.listModels(message.nodeId, message.baseUrl, message.apiMode, message.apiKeyRef);
        break;
      case "flowStoreSecret":
        void this.storeSecret(message.name);
        break;
      case "flowNotice":
        this.notify(message.level, message.text);
        break;
    }
  }

  /** Replace the whole document text. VSCode then owns dirty state and Ctrl+S. */
  private async write(graph: FlowGraph): Promise<void> {
    const text = serializeFlow(graph);
    if (text === this.document.getText()) return;
    this.lastWritten = text;
    const edit = new vscode.WorkspaceEdit();
    edit.replace(
      this.document.uri,
      new vscode.Range(0, 0, this.document.lineCount, 0),
      text
    );
    await vscode.workspace.applyEdit(edit);
  }

  private scheduleValidation(graph: FlowGraph, delay = 400): void {
    this.pendingValidation = graph;
    if (this.validateTimer) clearTimeout(this.validateTimer);
    this.validateTimer = setTimeout(() => void this.validate(), delay);
  }

  private async validate(): Promise<void> {
    const graph = this.pendingValidation;
    if (!graph) return;
    try {
      const result = await this.bridge.request<{ ok: boolean; issues: ValidationIssue[] }>(
        "graphValidate",
        { graph },
        20000
      );
      this.post({
        type: "flowValidation",
        ok: Boolean(result?.ok),
        issues: result?.issues ?? [],
      });
    } catch (err) {
      // A backend that is not up yet must not paint the graph red.
      console.error("[code-ai] graphValidate failed", err);
    }
  }

  private async run(graph: FlowGraph, variables: Record<string, unknown>): Promise<void> {
    this.bridge.ensureStarted();
    let secrets: Record<string, string> = {};
    try {
      secrets = await this.collectSecrets(graph);
    } catch (err) {
      this.notify("error", String((err as Error).message ?? err));
      return;
    }
    try {
      const result = await this.bridge.request<{
        ok: boolean;
        run_id?: string;
        message?: string;
        issues?: ValidationIssue[];
      }>("graphRun", { graph, variables, secrets }, 30000);
      if (!result?.ok) {
        this.notify("warn", result?.message ?? "O grafo não pôde ser executado.");
        if (result?.issues?.length) {
          this.post({ type: "flowValidation", ok: false, issues: result.issues });
        }
        this.post({ type: "flowRunState", running: false, runId: "", message: "" });
        return;
      }
      this.runId = String(result.run_id ?? "");
      this.running = true;
      this.post({ type: "flowRunState", running: true, runId: this.runId, message: "" });
    } catch (err) {
      this.notify("error", `Falha ao iniciar: ${String(err)}`);
      this.post({ type: "flowRunState", running: false, runId: "", message: "" });
    }
  }

  private async stop(): Promise<void> {
    try {
      await this.bridge.request("graphStop", { run_id: this.runId }, 20000);
    } catch (err) {
      console.error("[code-ai] graphStop failed", err);
    }
  }

  private async listModels(
    nodeId: string,
    baseUrl: string,
    apiMode: string,
    apiKeyRef: string
  ): Promise<void> {
    this.bridge.ensureStarted();
    try {
      const result = await this.bridge.request<{
        ok: boolean;
        models: string[];
        message?: string;
      }>(
        "graphListModels",
        { base_url: baseUrl, api_mode: apiMode, api_key_ref: apiKeyRef },
        60000
      );
      this.post({
        type: "flowModels",
        nodeId,
        ok: Boolean(result?.ok),
        models: result?.models ?? [],
        message: result?.message ?? "",
      });
    } catch (err) {
      this.post({
        type: "flowModels",
        nodeId,
        ok: false,
        models: [],
        message: String(err),
      });
    }
  }

  /**
   * Resolve every `secret:NAME` the graph references.
   *
   * Keys never live in the file (it is committed), so a Model node stores a
   * reference and the real value comes from VSCode's own secret storage at run
   * time. A missing one is asked for once and remembered.
   */
  private async collectSecrets(graph: FlowGraph): Promise<Record<string, string>> {
    const names = new Set<string>();
    for (const node of graph.nodes) {
      const reference = String(node.data?.api_key_ref ?? "");
      if (reference.startsWith("secret:")) names.add(reference.slice(7));
    }
    const resolved: Record<string, string> = {};
    for (const name of names) {
      let value = await this.secrets.get(secretKey(name));
      if (!value) {
        value = await vscode.window.showInputBox({
          title: `Chave de API: ${name}`,
          prompt: "Guardada no cofre do VSCode, nunca no arquivo do grafo.",
          password: true,
          ignoreFocusOut: true,
        });
        if (!value) throw new Error(`A execução precisa da chave ${name}.`);
        await this.secrets.store(secretKey(name), value);
      }
      resolved[name] = value;
    }
    return resolved;
  }

  private async storeSecret(name: string): Promise<void> {
    const clean = name.trim();
    if (!clean) return;
    const value = await vscode.window.showInputBox({
      title: `Chave de API: ${clean}`,
      prompt: "Guardada no cofre do VSCode, nunca no arquivo do grafo.",
      password: true,
      ignoreFocusOut: true,
    });
    if (!value) return;
    await this.secrets.store(secretKey(clean), value);
    this.post({ type: "flowSecretStored", name: clean });
  }

  private notify(level: "info" | "warn" | "error", text: string): void {
    this.post({ type: "flowNotice", level, text });
  }

  /** The palette commands drive the toolbar rather than the bridge, so the
   * webview stays the single place that decides what "run" means (it holds the
   * variable overrides and the validation state). */
  runFromCommand(): void {
    this.post({ type: "flowCommand", command: "run" });
  }

  stopFromCommand(): void {
    this.post({ type: "flowCommand", command: "stop" });
  }
}

function secretKey(name: string): string {
  return `code-ai.flow.secret.${name}`;
}

/**
 * Create a graph file and open it. Lands in `.code-ai/flows/` so graphs have an
 * obvious home and travel with the repository.
 */
export async function createFlowFile(): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    void vscode.window.showErrorMessage("Abra uma pasta antes de criar um grafo.");
    return;
  }
  const name = await vscode.window.showInputBox({
    title: "Novo grafo de orquestração",
    prompt: "Nome do arquivo (sem extensão)",
    value: "pipeline",
    validateInput: (value) =>
      /^[\w.-]+$/.test(value.trim()) ? undefined : "Use letras, números, ponto, hífen ou _.",
  });
  if (!name) return;
  const target = vscode.Uri.joinPath(
    folder.uri,
    ".code-ai",
    "flows",
    `${name.trim()}${FLOW_EXTENSION}`
  );
  try {
    await vscode.workspace.fs.stat(target);
    void vscode.window.showWarningMessage("Já existe um grafo com esse nome; abrindo o existente.");
  } catch {
    const content = Buffer.from(serializeFlow(emptyFlow(name.trim())), "utf8");
    await vscode.workspace.fs.writeFile(target, content);
  }
  await vscode.commands.executeCommand("vscode.openWith", target, FlowEditorProvider.viewType);
}

function renderFlowHtml(webview: vscode.Webview, extensionUri: vscode.Uri): string {
  const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "dist", "flow.js"));
  const nonce = makeNonce();
  const csp =
    `default-src 'none'; ` +
    `img-src ${webview.cspSource} https: data:; ` +
    `style-src ${webview.cspSource} 'unsafe-inline'; ` +
    `script-src 'nonce-${nonce}';`;
  return `<!DOCTYPE html>
<html lang="pt-BR">
  <head>
    <meta charset="UTF-8" />
    <meta http-equiv="Content-Security-Policy" content="${csp}" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Code-AI Orchestrator</title>
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
