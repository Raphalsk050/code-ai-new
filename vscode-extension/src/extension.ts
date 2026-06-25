import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

import { BridgeClient } from "./bridge-client";
import type { EditorContext, EventEnvelope, WebviewToHost } from "./protocol";

export function activate(context: vscode.ExtensionContext): void {
  const provider = new ChatViewProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("code-ai.chat", provider, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.commands.registerCommand("code-ai.open", () =>
      vscode.commands.executeCommand("code-ai.chat.focus")
    )
  );
}

export function deactivate(): void {
  // The view provider disposes its bridge in onDidDispose.
}

class ChatViewProvider implements vscode.WebviewViewProvider {
  constructor(private readonly extensionUri: vscode.Uri) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "dist")],
    };

    const config = vscode.workspace.getConfiguration("code-ai");
    const extraArgs = config.get<string[]>("args", []);
    const cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const command = resolveCommand(config.get<string>("command", "code-ai"), this.extensionUri);

    let client: BridgeClient;
    try {
      client = new BridgeClient(command, [...extraArgs, "bridge"], cwd);
    } catch (err) {
      vscode.window.showErrorMessage(`Code-AI: failed to spawn "${command}": ${String(err)}`);
      return;
    }

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
      }
    });

    // Keep the webview in sync with what the user is looking at, so it can show
    // a context chip and let the user opt out before sending.
    const pushContext = () =>
      void view.webview.postMessage({ type: "editorContext", context: currentEditorContext() });
    const editorSubs = [
      vscode.window.onDidChangeActiveTextEditor(pushContext),
      vscode.window.onDidChangeTextEditorSelection((e) => {
        if (e.textEditor === vscode.window.activeTextEditor) pushContext();
      }),
      view.onDidChangeVisibility(() => {
        if (view.visible) pushContext();
      }),
    ];
    pushContext();

    view.onDidDispose(() => {
      editorSubs.forEach((d) => d.dispose());
      client.dispose();
    });
    view.webview.html = renderHtml(view.webview, this.extensionUri);
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
