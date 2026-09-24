import * as React from "react";

import { t, tf } from "./i18n";

import type { McpAction, McpList, McpServerInfo } from "../src/protocol";
import { IconRefresh } from "./icons";
import {
  EMPTY_FORM,
  STATUS_TEXT,
  ServerForm,
  declarationOf,
  formFromSpec,
  formProblem,
  parseImport,
} from "./mcp-form";

/** What the panel knows about the session's MCP servers. */
export interface McpViewState {
  list: McpList | null;
  error: string;
  /** The server an action is running on, so its buttons wait for it. */
  busy: string;
  /** The sign-in page each server sent the user to, to open it again. */
  authUrls: Record<string, string>;
}

export const INITIAL_MCP: McpViewState = { list: null, error: "", busy: "", authUrls: {} };

const FAILING = new Set(["failed", "invalid", "needs_auth"]);

type Editing = { form: ServerForm; previous: string } | null;

export function McpSection({
  mcp,
  enabled,
  disabledTools,
  onEnabledChange,
  onDisabledToolsChange,
  onAction,
}: {
  mcp: McpViewState;
  /** The draft's mcp_enabled: saved with the rest of the settings. */
  enabled: boolean;
  disabledTools: string[];
  onEnabledChange: (value: boolean) => void;
  onDisabledToolsChange: (next: string[]) => void;
  onAction: (action: McpAction) => void;
}): JSX.Element {
  const [editing, setEditing] = React.useState<Editing>(null);
  const [importing, setImporting] = React.useState(false);
  const servers = mcp.list?.servers ?? [];
  const names = servers.map((server) => server.name);

  const save = (form: ServerForm, previous: string) => {
    onAction({ action: "save", server: declarationOf(form), previous });
    setEditing(null);
  };

  return (
    <div className="settings-section mcp-section">
      <div className="settings-section-head">
        <h3>{t("MCP servers")}</h3>
        <span className="settings-section-hint">
          {t(
            "Tools from other programs: GitHub, databases, a browser. Servers a project declares (.mcp.json, .vscode/mcp.json, .cursor/mcp.json) start only after you approve them."
          )}
        </span>
      </div>
      <div className="settings-fields">
        <div className="settings-toggle" onClick={() => onEnabledChange(!enabled)}>
          <button className={`switch ${enabled ? "on" : ""}`} role="switch" aria-checked={enabled}>
            <span className="switch-knob" />
          </button>
          <div className="settings-toggle-text">
            <span className="settings-field-label">{t("Use MCP servers")}</span>
            <span className="settings-toggle-hint">
              {t("Off stops every server and takes their tools away.")}
            </span>
          </div>
        </div>

        {mcp.error && (
          <div className="mcp-alert" role="alert">
            {mcp.error}
          </div>
        )}
        {mcp.list === null && <div className="settings-note">{t("Loading MCP servers…")}</div>}
        {mcp.list !== null && servers.length === 0 && (
          <div className="settings-note">
            {t("No MCP server yet. Add one, or paste the JSON a server's README gives you.")}
          </div>
        )}

        {servers.map((server) =>
          editing && editing.previous === server.name ? (
            <ServerEditor
              key={server.name}
              initial={editing.form}
              taken={names.filter((name) => name !== server.name)}
              onSave={(form) => save(form, server.name)}
              onCancel={() => setEditing(null)}
            />
          ) : (
            <ServerCard
              key={server.name}
              server={server}
              authUrl={mcp.authUrls[server.name] ?? ""}
              busy={mcp.busy === server.name}
              disabledTools={disabledTools}
              onDisabledToolsChange={onDisabledToolsChange}
              onAction={onAction}
              onEdit={() => setEditing({ form: formFromSpec(server.spec), previous: server.name })}
            />
          )
        )}

        {editing && editing.previous === "" && (
          <ServerEditor
            initial={editing.form}
            taken={names}
            onSave={(form) => save(form, "")}
            onCancel={() => setEditing(null)}
          />
        )}
        {importing && (
          <ImportBox
            onImport={(declarations) => {
              for (const declaration of declarations) onAction({ action: "save", server: declaration });
              setImporting(false);
            }}
            onCancel={() => setImporting(false)}
          />
        )}
        {!editing && !importing && (
          <div className="mcp-add">
            <button
              className="btn-ghost"
              onClick={() => setEditing({ form: { ...EMPTY_FORM }, previous: "" })}
            >
              + {t("Add server")}
            </button>
            <button className="btn-ghost" onClick={() => setImporting(true)}>
              {t("Paste JSON")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function ServerCard({
  server,
  authUrl,
  busy,
  disabledTools,
  onDisabledToolsChange,
  onAction,
  onEdit,
}: {
  server: McpServerInfo;
  authUrl: string;
  busy: boolean;
  disabledTools: string[];
  onDisabledToolsChange: (next: string[]) => void;
  onAction: (action: McpAction) => void;
  onEdit: () => void;
}): JSX.Element {
  const [details, setDetails] = React.useState(false);
  const [confirming, setConfirming] = React.useState(false);
  const own = server.source === "config";
  // A project's server is switched by its approval; the user's own by its flag.
  const on = own ? server.spec.enabled : server.status !== "needs_approval";
  const off = new Set(disabledTools);
  const toolsOn = server.tools.filter((tool) => !off.has(tool.name)).length;
  const [headline, ...rest] = server.error.split("\n");
  const extra = [rest.join("\n"), server.diagnostics, server.logs.join("\n")]
    .map((part) => part.trim())
    .filter(Boolean)
    .join("\n\n");

  const toggleTool = (name: string) => {
    const next = new Set(off);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onDisabledToolsChange([...next].sort());
  };

  return (
    <div className="mcp-server" data-status={server.status}>
      <div className="mcp-server-head">
        <span className={`mcp-dot mcp-dot-${server.status}`} aria-hidden />
        <span className="mcp-server-name" title={server.server?.title || server.server?.name || ""}>
          {server.name}
        </span>
        <span className="mcp-server-meta">
          {server.status === "invalid" ? "" : `${server.transport} · `}
          {t(STATUS_TEXT[server.status] ?? server.status)}
          {server.status === "connected" &&
            ` · ${server.tools.length === 1 ? t("1 tool") : tf("{0} tools", server.tools.length)}`}
        </span>
        <div className="mcp-server-actions">
          {server.status === "needs_approval" && (
            <button
              className="btn-primary mcp-approve"
              disabled={busy}
              onClick={() => onAction({ action: "approve", name: server.name })}
            >
              {t("Approve")}
            </button>
          )}
          {server.status === "needs_auth" && (
            <button
              className="btn-primary mcp-approve"
              disabled={busy}
              onClick={() => onAction({ action: "signIn", name: server.name })}
            >
              {t("Sign in")}
            </button>
          )}
          {on && server.status !== "invalid" && (
            <button
              className="icon-btn"
              title={t("Reconnect")}
              disabled={busy}
              onClick={() => onAction({ action: "reconnect", name: server.name })}
            >
              {busy || server.status === "connecting" ? (
                <span className="spinner" />
              ) : (
                <IconRefresh size={13} />
              )}
            </button>
          )}
          <button
            className={`switch ${on ? "on" : ""}`}
            role="switch"
            aria-checked={on}
            title={on ? t("Turn off") : t("Turn on")}
            disabled={busy || server.status === "invalid"}
            onClick={() => onAction({ action: "setEnabled", name: server.name, enabled: !on })}
          >
            <span className="switch-knob" />
          </button>
        </div>
      </div>

      {!own && (
        <div className="mcp-server-source">
          {server.user_level
            ? tf("Set up for {0} in {1}", server.source, server.source_label || server.source_path)
            : tf("Declared by the project in {0}", server.source_label || server.source_path)}
        </div>
      )}
      {server.missing_env.length > 0 && (
        <div className="mcp-server-warn">
          {tf("Not set in the environment: {0}", server.missing_env.join(", "))}
        </div>
      )}
      {server.error && FAILING.has(server.status) && (
        <div className="mcp-server-error">
          {headline}
          {extra && (
            <button className="mcp-link" onClick={() => setDetails(!details)}>
              {details ? t("Hide details") : t("Details")}
            </button>
          )}
        </div>
      )}
      {/* Waiting for approval says so above; "off" is a note, not a failure. */}
      {server.error && server.status === "disabled" && (
        <div className="mcp-server-source">{t(headline)}</div>
      )}
      {details && extra && <pre className="mcp-details">{extra}</pre>}
      {server.status === "needs_auth" && authUrl && (
        <div className="mcp-server-source">
          <a href={authUrl}>{t("Open the sign-in page again")}</a>
        </div>
      )}

      {server.tools.length > 0 && (
        <details className="mcp-tools">
          <summary>
            {tf("{0} of {1} tools on", toolsOn, server.tools.length)}
            {server.deferred && ` · ${t("loaded when the agent asks for them")}`}
          </summary>
          <div className="tool-switch-list">
            {server.tools.map((tool) => (
              <label key={tool.name} className="tool-switch" title={tool.description}>
                <input
                  type="checkbox"
                  checked={!off.has(tool.name)}
                  onChange={() => toggleTool(tool.name)}
                />
                <span className="tool-switch-name">{tool.remote}</span>
                {tool.read_only && <span className="mcp-tag">{t("read-only")}</span>}
              </label>
            ))}
          </div>
        </details>
      )}
      {server.prompts.length > 0 && (
        <div className="mcp-server-source">
          {t("Prompts")}: {server.prompts.map((prompt) => `/mcp:${server.name}:${prompt.name}`).join("  ")}
        </div>
      )}

      {(own || server.signed_in) && (
        <div className="mcp-server-foot">
          {own && (
            <button className="mcp-link" onClick={onEdit} disabled={busy}>
              {t("Edit")}
            </button>
          )}
          {server.signed_in && (
            <button
              className="mcp-link"
              onClick={() => onAction({ action: "signOut", name: server.name })}
              disabled={busy}
            >
              {t("Sign out")}
            </button>
          )}
          {!own ? null : confirming ? (
            <span className="mcp-confirm">
              {t("Remove this server?")}
              <button
                className="mcp-link danger"
                onClick={() => onAction({ action: "remove", name: server.name })}
              >
                {t("Remove")}
              </button>
              <button className="mcp-link" onClick={() => setConfirming(false)}>
                {t("Keep")}
              </button>
            </span>
          ) : (
            <button className="mcp-link danger" onClick={() => setConfirming(true)} disabled={busy}>
              {t("Remove")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function ServerEditor({
  initial,
  taken,
  onSave,
  onCancel,
}: {
  initial: ServerForm;
  taken: string[];
  onSave: (form: ServerForm) => void;
  onCancel: () => void;
}): JSX.Element {
  const [form, setForm] = React.useState<ServerForm>(initial);
  const [tried, setTried] = React.useState(false);
  const problem = formProblem(form, taken);
  const set = <K extends keyof ServerForm>(key: K, value: ServerForm[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  return (
    <div className="mcp-editor">
      <Labeled label={t("Name")}>
        <input
          value={form.name}
          placeholder="github"
          onChange={(e) => set("name", e.target.value)}
        />
      </Labeled>
      <Labeled label={t("Transport")}>
        <select
          value={form.transport}
          onChange={(e) => set("transport", e.target.value as ServerForm["transport"])}
        >
          <option value="stdio">{t("stdio - a program on this machine")}</option>
          <option value="http">{t("http - a server at a URL")}</option>
          <option value="sse">{t("sse - older servers at a URL")}</option>
        </select>
      </Labeled>
      {form.transport === "stdio" ? (
        <>
          <Labeled label={t("Command")}>
            <input
              value={form.command}
              placeholder="npx"
              onChange={(e) => set("command", e.target.value)}
            />
          </Labeled>
          <Labeled label={t("Arguments, one per line")}>
            <textarea
              rows={3}
              value={form.args}
              placeholder={"-y\n@modelcontextprotocol/server-github"}
              onChange={(e) => set("args", e.target.value)}
            />
          </Labeled>
          <Labeled label={t("Environment, KEY=value per line")}>
            <textarea
              rows={2}
              value={form.env}
              placeholder="GITHUB_PERSONAL_ACCESS_TOKEN=${GITHUB_TOKEN}"
              onChange={(e) => set("env", e.target.value)}
            />
            <span className="settings-hint">
              {t("${NAME} reads a variable from your environment, so the secret stays out of the file.")}
            </span>
          </Labeled>
          <Labeled label={t("Working directory")}>
            <input
              value={form.cwd}
              placeholder={t("the workspace")}
              onChange={(e) => set("cwd", e.target.value)}
            />
          </Labeled>
        </>
      ) : (
        <>
          <Labeled label="URL">
            <input
              value={form.url}
              placeholder="https://mcp.example.com/mcp"
              onChange={(e) => set("url", e.target.value)}
            />
          </Labeled>
          <Labeled label={t("Headers, Name: value per line")}>
            <textarea
              rows={2}
              value={form.headers}
              placeholder="Authorization: Bearer ${API_TOKEN}"
              onChange={(e) => set("headers", e.target.value)}
            />
          </Labeled>
        </>
      )}
      <Labeled label={t("Its tools in the agent's list")}>
        <select
          value={form.defer}
          onChange={(e) => set("defer", e.target.value as ServerForm["defer"])}
        >
          <option value="auto">{t("all of them, or on demand past 12")}</option>
          <option value="always">{t("on demand: the agent loads them when it needs them")}</option>
          <option value="never">{t("always all of them")}</option>
        </select>
      </Labeled>
      <label className="tool-switch mcp-trust">
        <input type="checkbox" checked={form.trust} onChange={(e) => set("trust", e.target.checked)} />
        <span>{t("Run the tools it marks read-only without asking")}</span>
      </label>
      {tried && problem && <div className="mcp-server-error">{t(problem)}</div>}
      <div className="mcp-editor-actions">
        <button
          className="btn-primary"
          onClick={() => {
            setTried(true);
            if (!problem) onSave(form);
          }}
        >
          {t("Save server")}
        </button>
        <button className="btn-ghost" onClick={onCancel}>
          {t("Cancel")}
        </button>
      </div>
    </div>
  );
}

function ImportBox({
  onImport,
  onCancel,
}: {
  onImport: (declarations: Record<string, unknown>[]) => void;
  onCancel: () => void;
}): JSX.Element {
  const [text, setText] = React.useState("");
  const [error, setError] = React.useState("");

  return (
    <div className="mcp-editor">
      <Labeled label={t("Paste the server's JSON")}>
        <textarea
          rows={7}
          value={text}
          placeholder={'{\n  "mcpServers": {\n    "github": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"] }\n  }\n}'}
          onChange={(e) => {
            setText(e.target.value);
            setError("");
          }}
        />
        <span className="settings-hint">
          {t("The block from Claude, Cursor or VS Code works as it is; every server in it is added.")}
        </span>
      </Labeled>
      {error && <div className="mcp-server-error">{error}</div>}
      <div className="mcp-editor-actions">
        <button
          className="btn-primary"
          onClick={() => {
            const parsed = parseImport(text);
            if (parsed.error) {
              setError(t(parsed.error));
              return;
            }
            onImport(parsed.servers.map((server) => server.declaration));
          }}
        >
          {t("Add")}
        </button>
        <button className="btn-ghost" onClick={onCancel}>
          {t("Cancel")}
        </button>
      </div>
    </div>
  );
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="settings-field">
      <span className="settings-field-label">{label}</span>
      {children}
    </label>
  );
}
