package com.codeai.intellij.web

/**
 * The document a panel's bundle runs in: what VS Code builds around a webview,
 * rebuilt for the IDE's embedded browser.
 *
 * The bundle was written for VS Code and is shipped unchanged, so the page
 * gives it the three things it gets there: `acquireVsCodeApi()`, the default
 * stylesheet VS Code puts under every webview, and the theme variables.
 */
internal object WebviewPage {
    /** Where the page and its files are served from. Nothing is ever fetched from the network. */
    const val ORIGIN = "https://code-ai.localhost"
    const val URL = "$ORIGIN/index.html"

    /**
     * @param script the bundle to run: `webview.js` or `flow.js`
     * @param theme the stylesheet from [ThemeVariables.stylesheet]
     * @param themeKind the body class from [ThemeVariables.kind]
     * @param state the state a previous page saved with `setState`, as JSON, or null
     * @param toHost JavaScript that hands the string in the variable `payload` to the plugin
     * @param nonce a fresh random token: only scripts carrying it may run
     */
    fun html(script: String, theme: String, themeKind: String, state: String?, toHost: String, nonce: String): String =
        """
        <!DOCTYPE html>
        <html lang="en">
          <head>
            <meta charset="UTF-8" />
            <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src $ORIGIN https: data:; font-src $ORIGIN; style-src $ORIGIN 'unsafe-inline'; script-src 'nonce-$nonce';" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <style id="_defaultStyles">$DEFAULT_STYLES</style>
            <style id="code-ai-theme">$theme</style>
            <link rel="stylesheet" href="codicons/codicon.css" />
            <title>Code-AI</title>
          </head>
          <body class="$themeKind" data-vscode-theme-kind="$themeKind">
            <div id="root"></div>
            <script nonce="$nonce">${bootstrap(state, toHost)}</script>
            <script nonce="$nonce" src="$script"></script>
          </body>
        </html>
        """.trimIndent()

    /**
     * The host side of the page, in the page.
     *
     * `acquireVsCodeApi()` is what the bundle talks to its host through. State
     * is answered from memory, as VS Code answers it, and saved a moment
     * later: the panel sets it on every change while a reply streams in.
     * The plugin is told the page is ready when the bundle starts listening
     * for messages, not before: one posted earlier would be lost.
     */
    private fun bootstrap(state: String?, toHost: String): String =
        """
        (function () {
          var state = ${escapeForScript(state ?: "undefined")};
          var unsaved = false, saving = null;
          function toHost(kind, body) {
            var payload = JSON.stringify({ kind: kind, body: body === undefined ? null : body });
            $toHost
          }
          function saveState() {
            saving = null;
            if (!unsaved) return;
            unsaved = false;
            toHost("state", state);
          }
          var api = Object.freeze({
            postMessage: function (message) { toHost("message", message); },
            getState: function () { return state; },
            setState: function (next) {
              state = next;
              unsaved = true;
              if (saving === null) saving = setTimeout(saveState, $STATE_SAVE_DELAY_MS);
              return next;
            }
          });
          var acquired = false;
          window.acquireVsCodeApi = function () {
            if (acquired) throw new Error("An instance of the VS Code API has already been acquired");
            acquired = true;
            return api;
          };
          window.addEventListener("pagehide", saveState);

          var listen = window.addEventListener;
          window.addEventListener = function (type) {
            var result = listen.apply(this, arguments);
            if (type === "message") {
              window.addEventListener = listen;
              toHost("ready");
            }
            return result;
          };

          window.__codeAi = {
            receive: function (messages) {
              for (var i = 0; i < messages.length; i++) {
                window.dispatchEvent(new MessageEvent("message", { data: messages[i] }));
              }
            },
            theme: function (css, kind) {
              document.getElementById("code-ai-theme").textContent = css;
              document.body.className = kind;
              document.body.setAttribute("data-vscode-theme-kind", kind);
            }
          };
        })();
        """.trimIndent()

    /** JSON, safe inside a script element: nothing in it can close the element. */
    internal fun escapeForScript(json: String): String = json.replace("</", "<\\/").replace("<!--", "<\\!--")

    private const val STATE_SAVE_DELAY_MS = 400

    /**
     * The stylesheet VS Code puts under every webview, verbatim (its
     * `webview/browser/pre/index.html`). The panel's own styles are written on
     * top of it: without the body's padding, the scrollbars and the link and
     * `code` colours it sets, the same bundle would look like a different panel.
     */
    private val DEFAULT_STYLES = """
        @layer vscode-default {
          html {
            scrollbar-color: var(--vscode-scrollbarSlider-background) var(--vscode-editor-background);
          }
          body {
            overscroll-behavior-x: none;
            background-color: transparent;
            color: var(--vscode-editor-foreground);
            font-family: var(--vscode-font-family);
            font-weight: var(--vscode-font-weight);
            font-size: var(--vscode-font-size);
            margin: 0;
            padding: 0 20px;
          }
          img, video {
            max-width: 100%;
            max-height: 100%;
          }
          a, a code {
            color: var(--vscode-textLink-foreground);
          }
          p > a {
            text-decoration: var(--text-link-decoration);
          }
          a:hover {
            color: var(--vscode-textLink-activeForeground);
          }
          a:focus,
          input:focus,
          select:focus,
          textarea:focus {
            outline: 1px solid -webkit-focus-ring-color;
            outline-offset: -1px;
          }
          code {
            font-family: var(--monaco-monospace-font);
            color: var(--vscode-textPreformat-foreground);
            background-color: var(--vscode-textPreformat-background);
            padding: 1px 3px;
            border-radius: 4px;
          }
          pre code {
            padding: 0;
          }
          blockquote {
            background: var(--vscode-textBlockQuote-background);
            border-color: var(--vscode-textBlockQuote-border);
          }
          kbd {
            background-color: var(--vscode-keybindingLabel-background);
            color: var(--vscode-keybindingLabel-foreground);
            border-style: solid;
            border-width: 1px;
            border-radius: 3px;
            border-color: var(--vscode-keybindingLabel-border);
            border-bottom-color: var(--vscode-keybindingLabel-bottomBorder);
            box-shadow: inset 0 -1px 0 var(--vscode-widget-shadow);
            vertical-align: middle;
            padding: 1px 3px;
          }
          ::-webkit-scrollbar {
            width: 10px;
            height: 10px;
          }
          ::-webkit-scrollbar-corner {
            background-color: var(--vscode-editor-background);
          }
          ::-webkit-scrollbar-thumb {
            background-color: var(--vscode-scrollbarSlider-background);
          }
          ::-webkit-scrollbar-thumb:hover {
            background-color: var(--vscode-scrollbarSlider-hoverBackground);
          }
          ::-webkit-scrollbar-thumb:active {
            background-color: var(--vscode-scrollbarSlider-activeBackground);
          }
        }
    """.trimIndent()
}
