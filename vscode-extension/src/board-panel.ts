import * as vscode from "vscode";

import type { BridgeSession } from "./bridge-session";
import type {
  BoardHostToWebview,
  BoardMove,
  BoardSnapshot,
  BoardStep,
  BoardWebviewToHost,
} from "./board-schema";
import type { EventEnvelope } from "./protocol";

/**
 * The task board: an editor tab of its own, as the graph editor is, over the
 * one board the backend keeps for the workspace.
 *
 * The tab holds nothing but a copy. Every edit goes to the backend as
 * operations, and every change comes back as `board.changed` with the whole
 * board, so the tab, the terminal and the agent can never disagree about it.
 */
export class BoardPanel {
  static readonly viewType = "code-ai.board";

  private panel?: vscode.WebviewPanel;
  private readonly subscriptions: vscode.Disposable[] = [];
  private language = "";

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly bridge: BridgeSession
  ) {
    this.subscriptions.push(
      bridge.subscribe({
        onEvent: (event) => this.onBridgeEvent(event),
        onRestart: () => void this.pushBoard(),
      })
    );
  }

  /** Open the tab, or bring it forward. */
  open(options: { preserveFocus?: boolean; beside?: boolean } = {}): void {
    if (this.panel) {
      this.panel.reveal(undefined, options.preserveFocus);
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      BoardPanel.viewType,
      "Code-AI Board",
      {
        // Beside the file the user is in, when there is one; alone, it takes the editor.
        viewColumn:
          options.beside && vscode.window.visibleTextEditors.length
            ? vscode.ViewColumn.Beside
            : vscode.ViewColumn.Active,
        preserveFocus: options.preserveFocus,
      },
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [this.distUri()] }
    );
    this.adopt(panel);
  }

  /** A tab VS Code brings back after a reload. */
  adopt(panel: vscode.WebviewPanel): void {
    this.panel = panel;
    panel.webview.options = { enableScripts: true, localResourceRoots: [this.distUri()] };
    panel.iconPath = vscode.Uri.joinPath(this.extensionUri, "media", "icon.svg");
    panel.webview.html = renderBoardHtml(panel.webview, this.extensionUri);
    const listener = panel.webview.onDidReceiveMessage((message: BoardWebviewToHost) =>
      this.onMessage(message)
    );
    panel.onDidDispose(() => {
      listener.dispose();
      if (this.panel === panel) this.panel = undefined;
    });
    this.bridge.ensureStarted();
  }

  dispose(): void {
    this.subscriptions.forEach((item) => item.dispose());
    this.panel?.dispose();
  }

  // -- backend -> tab ---------------------------------------------------

  private onBridgeEvent(event: EventEnvelope): void {
    const payload = (event.payload ?? {}) as Record<string, unknown>;
    switch (event.event_type) {
      case "board.changed":
        this.post({
          type: "boardSnapshot",
          board: payload.board as BoardSnapshot,
          moved: (payload.moved as BoardMove[]) ?? [],
        });
        break;
      case "board.activity":
        this.post({
          type: "boardActivity",
          step: {
            task: String(payload.task ?? ""),
            subtask: String(payload.subtask ?? ""),
            agent: String(payload.agent ?? ""),
            kind: (payload.kind as BoardStep["kind"]) ?? "tool",
            state: (payload.state as BoardStep["state"]) ?? "",
            tool: String(payload.tool ?? ""),
            target: String(payload.target ?? ""),
            call: String(payload.call ?? ""),
            text: String(payload.text ?? ""),
            at: Number(payload.at ?? Date.now() / 1000),
          },
        });
        break;
      case "board.review.requested":
        // The agent or the planners finished: the board comes forward for the
        // user to check it and press Play, beside the chat they are in.
        this.open({ preserveFocus: true, beside: !this.panel });
        this.post({
          type: "boardReview",
          tasks: (payload.tasks as string[]) ?? [],
          message: String(payload.message ?? ""),
        });
        break;
      case "board.run.completed":
        this.post({
          type: "boardRunDone",
          status: String(payload.status ?? ""),
          done: (payload.done as string[]) ?? [],
          failed: (payload.failed as string[]) ?? [],
        });
        break;
    }
  }

  private post(message: BoardHostToWebview): void {
    if (this.panel) void this.panel.webview.postMessage(message);
  }

  private notify(level: "info" | "warn" | "error", text: string): void {
    this.post({ type: "boardNotice", level, text });
  }

  private async pushBoard(): Promise<void> {
    if (!this.panel) return;
    try {
      if (!this.language) {
        const settings = await this.bridge.request<{ language?: string }>("getSettings", {}, 30000);
        this.language = String(settings?.language ?? "");
      }
      const result = await this.bridge.request<{ ok: boolean; board?: BoardSnapshot; message?: string }>(
        "boardGet",
        {},
        30000
      );
      if (!result?.ok || !result.board) {
        this.notify("error", result?.message ?? "The backend did not send the board.");
        return;
      }
      this.post({ type: "boardSnapshot", board: result.board, moved: [], language: this.language });
    } catch (err) {
      this.notify("error", `The board could not be read: ${String(err)}`);
    }
  }

  // -- tab -> backend ---------------------------------------------------

  private onMessage(message: BoardWebviewToHost): void {
    switch (message.type) {
      case "boardReady":
        void this.pushBoard();
        break;
      case "boardEdit":
        void this.call("boardEdit", { operations: message.operations });
        break;
      case "boardPlan":
        void this.call("boardPlan", { tasks: message.tasks });
        break;
      case "boardPlay":
        void this.call("boardPlay", { tasks: message.tasks });
        break;
      case "boardStop":
        void this.call("boardStop", {});
        break;
      case "boardAttach":
        void this.call("boardAttach", { task: message.task, name: message.name, data: message.data });
        break;
      case "boardAttachPick":
        void this.pickFiles(message.task);
        break;
      case "boardOpenFile":
        void vscode.commands.executeCommand("vscode.open", vscode.Uri.file(message.path));
        break;
    }
  }

  /** Attach files picked in the system's dialog: the backend copies each from where it is. */
  private async pickFiles(task: string): Promise<void> {
    const picked = await vscode.window.showOpenDialog({
      canSelectMany: true,
      openLabel: "Attach",
      title: `Attach files to ${task}`,
    });
    for (const uri of picked ?? []) {
      await this.call("boardAttach", { task, path: uri.fsPath });
    }
  }

  private async call(
    method: "boardEdit" | "boardPlan" | "boardPlay" | "boardStop" | "boardAttach",
    params: Record<string, unknown>
  ): Promise<void> {
    this.bridge.ensureStarted();
    try {
      const result = await this.bridge.request<{
        ok: boolean;
        message?: string;
        created?: string[];
        board?: BoardSnapshot;
      }>(method, params, method === "boardAttach" ? 120000 : 30000);
      if (!result?.ok) {
        this.notify("warn", result?.message ?? "The board refused that.");
        // A refused edit leaves the board as it was: put the tab back on it.
        if (result?.board) this.post({ type: "boardSnapshot", board: result.board, moved: [] });
        return;
      }
      if (result.created?.length) this.post({ type: "boardCreated", ids: result.created });
      if (result.message && method !== "boardEdit" && method !== "boardAttach") {
        this.notify("info", result.message);
      }
    } catch (err) {
      this.notify("error", String(err));
    }
  }

  private distUri(): vscode.Uri {
    return vscode.Uri.joinPath(this.extensionUri, "dist");
  }
}

function renderBoardHtml(webview: vscode.Webview, extensionUri: vscode.Uri): string {
  const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "dist", "board.js"));
  const codiconsUri = webview.asWebviewUri(
    vscode.Uri.joinPath(extensionUri, "dist", "codicons", "codicon.css")
  );
  const nonce = makeNonce();
  const csp =
    `default-src 'none'; ` +
    `img-src ${webview.cspSource} https: data:; ` +
    `font-src ${webview.cspSource} data:; ` +
    `style-src ${webview.cspSource} 'unsafe-inline'; ` +
    `script-src 'nonce-${nonce}';`;
  return `<!DOCTYPE html>
<html lang="pt-BR">
  <head>
    <meta charset="UTF-8" />
    <meta http-equiv="Content-Security-Policy" content="${csp}" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <link rel="stylesheet" href="${codiconsUri}" />
    <title>Code-AI Board</title>
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
