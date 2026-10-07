import * as React from "react";

import { t, tf } from "./i18n";

import type { AppMode, EditorSettings, McpAction, Settings, ToolSpec } from "../src/protocol";
import { AVATAR_COLOR_VALUE, AVATAR_COLORS, AVATAR_SKINS, type AvatarColor } from "./avatar-skin";
import { Codicon } from "./codicon";
import { Dropdown } from "./dropdown";
import { ExtPrefs } from "./history";
import { McpSection, McpViewState } from "./mcp-settings";
import { Draft, draftFromSettings, keepRefusedEdits } from "./settings-draft";

/** State of the on-demand "list models" query for the model field. */
export interface ModelsState {
  status: "idle" | "loading" | "ready" | "error";
  list: string[];
  error?: string;
}

/** What the last save did, keyed by the field the backend complained about. */
export interface SaveState {
  status: "idle" | "saving" | "saved" | "error";
  errors: Record<string, string>;
}

/** Session states that mean no answer is coming, so waiting is pointless. */
const DEAD = new Set(["DISCONNECTED", "ERROR"]);

type Category = "model" | "agent" | "editor" | "context" | "tools" | "subagents" | "advanced";

const CATEGORIES: { id: Category; title: string; icon: string }[] = [
  { id: "model", title: "Model", icon: "sparkle" },
  { id: "agent", title: "Agent", icon: "hubot" },
  { id: "editor", title: "Editor", icon: "edit" },
  { id: "context", title: "Context", icon: "database" },
  { id: "tools", title: "Tools and MCP", icon: "tools" },
  { id: "subagents", title: "Sub-agents", icon: "organization" },
  { id: "advanced", title: "Advanced", icon: "settings" },
];

/** Settings that only take effect once the backend restarts. */
const RESTART_KEYS = new Set(["api_mode", "base_url", "api_key", "max_context_tokens", "workspace", "command", "args"]);

interface Row {
  id: string;
  category: Category;
  label: string;
  hint?: string;
  /** Draft keys this row edits: what makes it "changed". */
  keys: string[];
  restart?: boolean;
  /** Exposes secrets or acts without asking. */
  risk?: boolean;
  /** The control takes the row's whole width, under the label. */
  wide?: boolean;
  control: React.ReactNode;
}

function same(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) || Array.isArray(b)) return JSON.stringify(a ?? []) === JSON.stringify(b ?? []);
  return a === b;
}

const EDITOR_DEFAULT: EditorSettings = { diffInEditor: true, command: "", args: [] };

export function SettingsScreen({
  settings,
  editor,
  sessionStatus,
  prefs,
  models,
  save,
  narrow,
  onBack,
  onSave,
  onSaveEditor,
  onPrefsChange,
  onRestart,
  onListModels,
  mcp,
  onMcpAction,
}: {
  settings: Settings | null;
  /** The extension's own settings (VS Code's settings.json), not the backend's. */
  editor: EditorSettings | null;
  sessionStatus: string;
  prefs: ExtPrefs;
  models: ModelsState;
  save: SaveState;
  narrow: boolean;
  onBack: () => void;
  onSave: (updates: Record<string, unknown>) => void;
  onSaveEditor: (updates: Partial<EditorSettings>) => void;
  onPrefsChange: (next: Partial<ExtPrefs>) => void;
  onRestart: () => void;
  onListModels: () => void;
  mcp: McpViewState;
  onMcpAction: (action: McpAction) => void;
}): JSX.Element {
  const [draft, setDraft] = React.useState<Draft>({});
  const [apiKey, setApiKey] = React.useState("");
  const [changingKey, setChangingKey] = React.useState(false);
  const [hostDraft, setHostDraft] = React.useState<EditorSettings>(editor ?? EDITOR_DEFAULT);
  const [category, setCategory] = React.useState<Category>("model");
  const [query, setQuery] = React.useState("");
  const [pendingRestart, setPendingRestart] = React.useState(false);
  const [probe, setProbe] = React.useState<{ started: number; result?: string; ok?: boolean } | null>(null);
  // What the last submit put on the wire, so a refused field can be handed
  // back to the user instead of the value the backend went on holding.
  const submittedRef = React.useRef<Draft | null>(null);
  // The last submit touched a setting that only applies after a restart.
  const restartRef = React.useRef(false);
  // The snapshot and the save result land in the same render, so by the time
  // the effect below runs this already holds the result that goes with it.
  const saveRef = React.useRef(save);
  saveRef.current = save;

  // Reset the working copy whenever fresh settings arrive from the bridge.
  React.useEffect(() => {
    if (!settings) return;
    const { errors } = saveRef.current;
    const submitted = submittedRef.current;
    submittedRef.current = null;
    if (submitted && restartRef.current && Object.keys(errors).length === 0) setPendingRestart(true);
    restartRef.current = false;
    setDraft(keepRefusedEdits(draftFromSettings(settings), submitted, errors));
    if (!errors.api_key) {
      setApiKey("");
      setChangingKey(false);
    }
  }, [settings]);

  React.useEffect(() => {
    if (editor) setHostDraft(editor);
  }, [editor]);

  // The connection test is a model listing with a stopwatch on it.
  React.useEffect(() => {
    if (!probe || probe.result !== undefined) return;
    if (models.status === "ready") {
      const ms = Math.round(performance.now() - probe.started);
      setProbe({ ...probe, ok: true, result: tf("Connected in {0} ms · {1} models", ms, models.list.length) });
    } else if (models.status === "error") {
      setProbe({ ...probe, ok: false, result: models.error ?? t("The provider did not answer.") });
    }
  }, [models, probe]);

  const saved = settings ? draftFromSettings(settings) : {};
  const changed = (key: string): boolean => {
    if (key === "api_key") return apiKey.trim() !== "";
    if (key === "diffInEditor" || key === "command" || key === "args") {
      const base = editor ?? EDITOR_DEFAULT;
      return !same(hostDraft[key], base[key]);
    }
    return key in draft && !same(draft[key], saved[key]);
  };

  const set = (key: string, value: string | number | boolean | string[] | null) =>
    setDraft((d) => ({ ...d, [key]: value }));
  const setHost = (patch: Partial<EditorSettings>) => setHostDraft((d) => ({ ...d, ...patch }));

  const backendDirty = Object.keys(draft).filter((key) => changed(key));
  if (changed("api_key")) backendDirty.push("api_key");
  const hostDirty = (["diffInEditor", "command", "args"] as const).filter((key) => changed(key));
  const pending = backendDirty.length + hostDirty.length;
  const restartCount = [...backendDirty, ...hostDirty].filter((key) => RESTART_KEYS.has(key)).length;

  const submit = () => {
    if (!settings || pending === 0) return;
    if (backendDirty.length) {
      const updates: Record<string, unknown> = { ...draft };
      if (apiKey.trim()) updates.api_key = apiKey.trim();
      submittedRef.current = updates as Draft;
      restartRef.current = backendDirty.some((key) => RESTART_KEYS.has(key));
      onSave(updates);
    }
    if (hostDirty.length) {
      const updates: Partial<EditorSettings> = {};
      for (const key of hostDirty) (updates as any)[key] = hostDraft[key];
      onSaveEditor(updates);
      if (hostDirty.some((key) => RESTART_KEYS.has(key))) setPendingRestart(true);
    }
  };
  const discard = () => {
    if (settings) setDraft(draftFromSettings(settings));
    setApiKey("");
    setChangingKey(false);
    setHostDraft(editor ?? EDITOR_DEFAULT);
  };

  const submitRef = React.useRef(submit);
  submitRef.current = submit;
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        submitRef.current();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const failures = Object.entries(save.errors);
  const status =
    pending > 0
      ? tf("{0} unsaved", pending)
      : save.status === "saving"
        ? t("Saving…")
        : save.status === "error"
          ? t("Not saved")
          : t("Saved");

  const rows = settings ? buildRows() : [];
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? rows.filter((row) => `${row.label} ${row.hint ?? ""} ${row.id}`.toLowerCase().includes(needle))
    : rows.filter((row) => row.category === category);
  const dirtyCategories = new Set(rows.filter((row) => row.keys.some(changed)).map((row) => row.category));

  function buildRows(): Row[] {
    const s = settings as Settings;
    const tools = (s.supported?.tools ?? []).filter((tool) => !tool.name.startsWith("mcp__"));
    const modelQuery = String(draft.model ?? "");
    const showAll = modelQuery === s.model || !modelQuery;
    const listed = models.list.filter((m) => showAll || m.toLowerCase().includes(modelQuery.toLowerCase()));
    return [
      {
        id: "api_mode",
        category: "model",
        label: t("API mode"),
        hint: t("The protocol the provider speaks."),
        keys: ["api_mode"],
        restart: true,
        control: (
          <Choice
            value={String(draft.api_mode ?? "")}
            options={s.supported.api_mode}
            onChange={(v) => set("api_mode", v)}
          />
        ),
      },
      {
        id: "base_url",
        category: "model",
        label: t("Base URL"),
        hint: t("Where the provider listens."),
        keys: ["base_url"],
        restart: true,
        control: <input value={String(draft.base_url ?? "")} onChange={(e) => set("base_url", e.target.value)} />,
      },
      {
        id: "api_key",
        category: "model",
        label: t("API key"),
        hint: t("Kept by Code-AI, never shown again once stored."),
        keys: ["api_key"],
        restart: true,
        control:
          s.api_key_set && !changingKey && !apiKey ? (
            <span className="key-stored">
              <Codicon name="lock" size={14} /> {t("stored")}
              <button className="btn-ghost btn-sm" onClick={() => setChangingKey(true)}>
                {t("Change")}
              </button>
            </span>
          ) : (
            <input
              type="password"
              autoFocus={changingKey}
              placeholder={s.api_key_set ? t("type the new key") : t("not set")}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          ),
      },
      {
        id: "model",
        category: "model",
        label: t("Model"),
        hint: t("Type a name, or pick one the provider serves."),
        keys: ["model"],
        wide: true,
        control: (
          <div className="model-field">
            <div className="model-row">
              <input
                className="model-input"
                value={modelQuery}
                spellCheck={false}
                onChange={(e) => set("model", e.target.value)}
              />
              <button
                type="button"
                className="btn-ghost model-list-btn"
                title={t("List the models the configured provider serves")}
                onClick={onListModels}
                disabled={models.status === "loading"}
              >
                {models.status === "loading" ? <span className="spinner" /> : <Codicon name="refresh" size={14} />}
                <span>{models.status === "loading" ? t("Listing…") : t("List models")}</span>
              </button>
            </div>
            {models.status === "ready" && listed.length > 0 && (
              <ul className="model-options" role="listbox" aria-label={t("Models")}>
                {listed.map((m) => (
                  <li
                    key={m}
                    role="option"
                    aria-selected={m === modelQuery}
                    className={`model-option ${m === modelQuery ? "current" : ""}`}
                    onClick={() => set("model", m)}
                  >
                    <span className="model-check">{m === modelQuery && <Codicon name="check" size={14} />}</span>
                    {m}
                  </li>
                ))}
              </ul>
            )}
            {models.status === "ready" && models.list.length === 0 && (
              <div className="model-note">{t("The provider returned no models.")}</div>
            )}
            {models.status === "error" && !probe && <div className="model-error">{models.error}</div>}
          </div>
        ),
      },
      {
        id: "connection",
        category: "model",
        label: t("Test the connection"),
        hint: t("Asks the provider for its models and times the answer."),
        keys: [],
        control: (
          <span className="probe">
            {probe?.result && (
              <span className={`probe-result ${probe.ok ? "ok" : "bad"}`}>
                <Codicon name={probe.ok ? "pass" : "error"} size={14} /> {probe.result}
              </span>
            )}
            <button
              className="btn-ghost btn-sm"
              disabled={models.status === "loading"}
              onClick={() => {
                setProbe({ started: performance.now() });
                onListModels();
              }}
            >
              {models.status === "loading" && probe && probe.result === undefined ? <span className="spinner" /> : <Codicon name="plug" size={14} />}{" "}
              {t("Test")}
            </button>
          </span>
        ),
      },
      {
        id: "reasoning_effort",
        category: "model",
        label: t("Reasoning effort"),
        hint: t("How hard the model thinks before answering; none turns thinking off."),
        keys: ["reasoning_effort"],
        control: (
          <Choice
            value={String(draft.reasoning_effort ?? "")}
            options={s.supported.reasoning_effort}
            onChange={(v) => set("reasoning_effort", v)}
          />
        ),
      },
      {
        id: "followup_reasoning_effort",
        category: "model",
        label: t("Follow-up effort"),
        hint: t("Tool steps after a clean result think at this level; none keeps the reasoning effort."),
        keys: ["followup_reasoning_effort"],
        control: (
          <Choice
            value={String(draft.followup_reasoning_effort ?? "none")}
            options={s.supported.reasoning_effort}
            onChange={(v) => set("followup_reasoning_effort", v)}
          />
        ),
      },
      {
        id: "permission_mode",
        category: "agent",
        label: t("Permission mode"),
        hint: t("What the agent may do without asking."),
        keys: ["permission_mode"],
        control: (
          <Choice
            value={String(draft.permission_mode ?? "")}
            options={s.supported.permission_mode}
            onChange={(v) => set("permission_mode", v)}
          />
        ),
      },
      {
        id: "default_mode",
        category: "agent",
        label: t("Default mode"),
        hint: t("What a new conversation opens in."),
        keys: [],
        control: (
          <Choice
            value={prefs.mode}
            options={["agent", "refactor", "explain"]}
            labels={{ agent: t("Agent"), refactor: t("Refactor"), explain: t("Explain") }}
            onChange={(v) => onPrefsChange({ mode: v as AppMode })}
          />
        ),
      },
      {
        id: "language",
        category: "agent",
        label: t("Language"),
        hint: t("The panel and the agent's answers both follow this."),
        keys: ["language"],
        control: (
          <Dropdown
            className="language-select"
            label={t("Language")}
            value={String(draft.language ?? "en")}
            options={languageOptions(String(draft.language ?? "en"))}
            onChange={(v) => set("language", v)}
          />
        ),
      },
      {
        id: "learn",
        category: "agent",
        label: t("Learn mode"),
        hint: t("Shows, on approvals, why the model thinks the change is needed."),
        keys: ["learn"],
        control: <Switch checked={Boolean(draft.learn)} onChange={(v) => set("learn", v)} label={t("Learn mode")} />,
      },
      {
        id: "floating_avatar",
        category: "agent",
        label: t("Floating avatar"),
        hint: t("The agent loose in the panel: drag it anywhere, double-click sends it back to its corner."),
        keys: [],
        control: (
          <Switch
            checked={prefs.floatingAvatar}
            onChange={(v) => onPrefsChange({ floatingAvatar: v })}
            label={t("Floating avatar")}
          />
        ),
      },
      {
        id: "avatar_watches",
        category: "agent",
        label: t("Avatar looks at the pointer"),
        hint: t("While it is idle, its eyes follow your mouse over the panel."),
        keys: [],
        control: (
          <Switch
            checked={prefs.avatarWatches}
            onChange={(v) => onPrefsChange({ avatarWatches: v })}
            label={t("Avatar looks at the pointer")}
          />
        ),
      },
      {
        id: "avatar_skin",
        category: "agent",
        label: t("Avatar costume"),
        hint: t("By the calendar it dresses up for Halloween, Christmas and Easter."),
        keys: [],
        control: (
          <Choice
            value={prefs.avatarSkin}
            options={["auto", ...AVATAR_SKINS]}
            labels={{
              auto: t("By the calendar"),
              default: t("No costume"),
              halloween: t("Halloween"),
              christmas: t("Christmas"),
              easter: t("Easter"),
            }}
            onChange={(v) => onPrefsChange({ avatarSkin: v as ExtPrefs["avatarSkin"] })}
          />
        ),
      },
      {
        id: "avatar_color",
        category: "agent",
        label: t("Avatar colour"),
        hint: t("The theme's accent, or a colour of your own."),
        keys: [],
        control: (
          <Swatches
            value={prefs.avatarColor}
            options={AVATAR_COLORS}
            colors={AVATAR_COLOR_VALUE}
            labels={{
              theme: t("Theme's accent"),
              blue: t("Blue"),
              green: t("Green"),
              purple: t("Purple"),
              pink: t("Pink"),
              orange: t("Orange"),
              red: t("Red"),
              teal: t("Teal"),
              yellow: t("Yellow"),
              grey: t("Grey"),
            }}
            onChange={(v) => onPrefsChange({ avatarColor: v })}
          />
        ),
      },
      {
        id: "inline_hints_enabled",
        category: "editor",
        label: t("Inline suggestions in the editor"),
        hint: t("Completes as you type. The status bar toggles it."),
        keys: ["inline_hints_enabled"],
        control: (
          <Switch
            checked={Boolean(draft.inline_hints_enabled)}
            onChange={(v) => set("inline_hints_enabled", v)}
            label={t("Inline suggestions in the editor")}
          />
        ),
      },
      {
        id: "inline_model",
        category: "editor",
        label: t("Model for inline suggestions"),
        keys: ["inline_model"],
        control: (
          <input
            placeholder={t("empty = uses the main model")}
            value={String(draft.inline_model ?? "")}
            onChange={(e) => set("inline_model", e.target.value)}
          />
        ),
      },
      {
        id: "auto_refactor",
        category: "editor",
        label: t("Analyse the selection automatically"),
        hint: t("In refactor mode, analyses the snippet as soon as you select it."),
        keys: [],
        control: (
          <Switch
            checked={prefs.autoRunRefactor}
            onChange={(v) => onPrefsChange({ autoRunRefactor: v })}
            label={t("Analyse the selection automatically")}
          />
        ),
      },
      {
        id: "diff_in_editor",
        category: "editor",
        label: t("Show edits in the editor's diff"),
        hint: t("Each change the agent makes opens as a diff tab while it is written."),
        keys: ["diffInEditor"],
        control: (
          <Switch
            checked={hostDraft.diffInEditor}
            onChange={(v) => setHost({ diffInEditor: v })}
            label={t("Show edits in the editor's diff")}
          />
        ),
      },
      {
        id: "max_context_tokens",
        category: "context",
        label: t("Max context tokens"),
        hint: t("The budget the conversation is compacted to stay under."),
        keys: ["max_context_tokens"],
        restart: true,
        control: (
          <input
            type="number"
            className="number"
            value={Number(draft.max_context_tokens ?? 0)}
            onChange={(e) => set("max_context_tokens", Number(e.target.value))}
          />
        ),
      },
      {
        id: "workspace",
        category: "context",
        label: t("Workspace"),
        hint: t("The folder the agent works in."),
        keys: ["workspace"],
        restart: true,
        control: <input value={String(draft.workspace ?? "")} onChange={(e) => set("workspace", e.target.value)} />,
      },
      {
        id: "mcp",
        category: "tools",
        label: t("MCP servers"),
        keys: ["mcp_enabled"],
        wide: true,
        control: (
          <McpSection
            mcp={mcp}
            enabled={draft.mcp_enabled !== false}
            disabledTools={(draft.disabled_tools as string[]) ?? []}
            onEnabledChange={(value) => set("mcp_enabled", value)}
            onDisabledToolsChange={(next) => set("disabled_tools", next)}
            onAction={onMcpAction}
          />
        ),
      },
      {
        id: "tools",
        category: "tools",
        label: t("Built-in tools"),
        hint: t("Switched off, a tool is not offered to the model at all."),
        keys: ["disabled_tools"],
        wide: true,
        control: (
          <ToolSwitches
            tools={tools}
            disabled={(draft.disabled_tools as string[]) ?? []}
            onChange={(next) => set("disabled_tools", next)}
          />
        ),
      },
      {
        id: "subagent_models",
        category: "subagents",
        label: t("Sub-agent models"),
        hint: t("Comma-separated; a sub-agent takes the next one in turn."),
        keys: ["subagent_models"],
        control: (
          <input
            value={((draft.subagent_models as string[]) ?? []).join(", ")}
            placeholder={t("empty = every sub-agent inherits the session model")}
            onChange={(e) =>
              set(
                "subagent_models",
                e.target.value
                  .split(",")
                  .map((name) => name.trim())
                  .filter(Boolean)
              )
            }
          />
        ),
      },
      {
        id: "max_concurrent_subagents",
        category: "subagents",
        label: t("Concurrent sub-agents"),
        hint: t("How many run at once."),
        keys: ["max_concurrent_subagents"],
        control: (
          <input
            type="number"
            className="number"
            min={1}
            value={Number(draft.max_concurrent_subagents ?? 1)}
            onChange={(e) => set("max_concurrent_subagents", Number(e.target.value))}
          />
        ),
      },
      ...(["temperature", "top_p", "top_k", "presence_penalty"] as const).map(
        (key): Row => ({
          id: key,
          category: "advanced",
          label: key,
          hint:
            key === "presence_penalty"
              ? t("Raise it towards 1 when a thinking model keeps going back over the same point.")
              : t("Sampling; empty uses the endpoint default."),
          keys: [key],
          control: (
            <input
              type="number"
              className="number"
              step={key === "top_k" ? 1 : key === "presence_penalty" ? 0.1 : 0.05}
              value={draft[key] == null ? "" : String(draft[key])}
              placeholder={t("endpoint default")}
              onChange={(e) => set(key, e.target.value === "" ? null : Number(e.target.value))}
            />
          ),
        })
      ),
      {
        id: "commands_see_secrets",
        category: "advanced",
        label: t("Commands see secret variables"),
        hint: t(
          "Commands get your environment variables. Keys and tokens (GITHUB_TOKEN, OPENAI_API_KEY) stay out unless this is on."
        ),
        keys: ["commands_see_secrets"],
        risk: true,
        control: (
          <Switch
            checked={Boolean(draft.commands_see_secrets)}
            onChange={(v) => set("commands_see_secrets", v)}
            label={t("Commands see secret variables")}
          />
        ),
      },
      {
        id: "command",
        category: "advanced",
        label: t("Backend command"),
        hint: t("The program the extension starts; empty finds it by itself."),
        keys: ["command"],
        restart: true,
        control: (
          <input
            value={hostDraft.command}
            placeholder={t("found automatically")}
            spellCheck={false}
            onChange={(e) => setHost({ command: e.target.value })}
          />
        ),
      },
      {
        id: "args",
        category: "advanced",
        label: t("Backend arguments"),
        hint: t("Space-separated, passed to the backend command."),
        keys: ["args"],
        restart: true,
        control: (
          <input
            value={hostDraft.args.join(" ")}
            spellCheck={false}
            onChange={(e) => setHost({ args: e.target.value.split(/\s+/).filter(Boolean) })}
          />
        ),
      },
      {
        id: "restart",
        category: "advanced",
        label: t("Restart Code-AI"),
        hint: t("Reloads the configuration without reloading the window."),
        keys: [],
        control: (
          <button className="btn-ghost btn-sm settings-restart-btn" onClick={onRestart}>
            <Codicon name="debug-restart" size={14} /> {t("Restart")}
          </button>
        ),
      },
    ];
  }

  const renderRow = (row: Row) => {
    const dirty = row.keys.some(changed);
    return (
      <div
        key={row.id}
        className={`settings-field setting-row ${row.wide ? "wide" : ""} ${row.risk ? "risk" : ""} ${dirty ? "dirty" : ""}`}
        data-setting={row.id}
      >
        <span className="settings-field-label">
          {dirty && <span className="dirty-dot" aria-label={t("changed")} />}
          {row.label}
          {row.restart && <span className="restart-tag">{t("reopens panel")}</span>}
          {row.risk && <span className="risk-tag">{t("risk")}</span>}
        </span>
        {row.hint && <span className="setting-hint">{row.hint}</span>}
        <div className="setting-control">{row.control}</div>
      </div>
    );
  };

  const groups = needle
    ? CATEGORIES.map((c) => ({ c, rows: shown.filter((row) => row.category === c.id) })).filter((g) => g.rows.length)
    : [{ c: CATEGORIES.find((c) => c.id === category)!, rows: shown }];

  return (
    <div className={`settings ${narrow ? "narrow" : ""}`}>
      <div className="settings-bar">
        <button className="icon-btn" title={t("Back")} onClick={onBack}>
          <Codicon name="arrow-left" />
        </button>
        <span className="settings-title">{t("Settings")}</span>
        <label className="settings-search">
          <Codicon name="search" size={14} />
          <input
            type="search"
            value={query}
            placeholder={t("Search settings")}
            aria-label={t("Search settings")}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <span className={`settings-status ${pending ? "pending" : save.status}`} role="status">
          {pending > 0 && <span className="dirty-dot" />}
          {status}
        </span>
      </div>

      {!settings ? (
        // The settings live in the backend, so a backend that never answered
        // leaves this screen empty. Saying so beats spinning for ever.
        <div className="settings-loading">
          {DEAD.has(sessionStatus) ? (
            <>
              <span>{t("Code-AI did not answer, so there are no settings to show. The chat says why.")}</span>
              <button className="btn-primary" onClick={onRestart}>
                {t("Try again")}
              </button>
            </>
          ) : (
            t("Loading settings…")
          )}
        </div>
      ) : (
        <div className="settings-main">
          <nav className="settings-nav" aria-label={t("Categories")}>
            {CATEGORIES.map((c) => (
              <button
                key={c.id}
                className={`settings-nav-item ${!needle && category === c.id ? "active" : ""}`}
                aria-current={!needle && category === c.id ? "page" : undefined}
                onClick={() => {
                  setCategory(c.id);
                  setQuery("");
                }}
              >
                <Codicon name={c.icon} size={14} />
                <span>{t(c.title)}</span>
                {dirtyCategories.has(c.id) && <span className="dirty-dot" />}
              </button>
            ))}
          </nav>
          <div className="settings-body">
            {failures.length > 0 && (
              <div className="settings-errors" role="alert">
                <span className="settings-errors-head">
                  {t("Code-AI refused")} {failures.length === 1 ? t("this field") : t("these fields")}; {t("the rest")}{" "}
                  {t("was saved. What you typed is still here so you can fix it.")}
                </span>
                {failures.map(([field, message]) => (
                  <div key={field} className="settings-error" data-field={field}>
                    <code>{field}</code> {message}
                  </div>
                ))}
              </div>
            )}
            {pendingRestart && pending === 0 && (
              <div className="settings-restart-note" role="status">
                <Codicon name="info" size={14} />
                <span>{t("Some saved changes apply once Code-AI restarts.")}</span>
                <button
                  className="btn-ghost btn-sm"
                  onClick={() => {
                    setPendingRestart(false);
                    onRestart();
                  }}
                >
                  {t("Restart now")}
                </button>
              </div>
            )}
            {groups.length === 0 && <div className="settings-note">{tf("Nothing matches “{0}”.", query.trim())}</div>}
            {groups.map(({ c, rows: list }) => (
              <section key={c.id} className="settings-group">
                <h3 className="settings-group-title">{t(c.title)}</h3>
                <div className="settings-rows">{list.map(renderRow)}</div>
              </section>
            ))}
          </div>
        </div>
      )}

      {settings && (pending > 0 || save.status === "saving") && (
        <div className="save-bar" role="region" aria-label={t("Unsaved changes")}>
          <span className="save-bar-text">
            {pending === 1 ? t("1 unsaved change") : tf("{0} unsaved changes", pending)}
            {restartCount > 0 && (
              <span className="save-bar-note">
                {" "}
                · {restartCount === 1 ? t("1 reopens the panel") : tf("{0} reopen the panel", restartCount)}
              </span>
            )}
          </span>
          <button className="btn-ghost btn-sm" onClick={discard} disabled={save.status === "saving"}>
            {t("Discard")}
          </button>
          <button
            className="btn-primary settings-save"
            onClick={submit}
            disabled={save.status === "saving" || pending === 0}
            title={t("Save (Ctrl+S)")}
          >
            {save.status === "saving" && pending === 0 ? t("Saving…") : t("Save")}
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * The languages the panel ships a translation for.
 *
 * A config holding something else (set by hand, or by a build that knew more
 * languages) keeps its value as an extra option rather than being silently
 * rewritten to English the first time the panel is opened.
 */
export const LANGUAGES: { value: string; label: string }[] = [
  { value: "en", label: "English" },
  { value: "pt-BR", label: "Português (Brasil)" },
];

export function languageOptions(current: string): { value: string; label: string }[] {
  if (LANGUAGES.some((item) => item.value === current)) return LANGUAGES;
  return [...LANGUAGES, { value: current, label: current || "(unset)" }];
}

/** Up to four choices side by side; a longer or open list is a select. */
function Choice({
  value,
  options,
  labels,
  onChange,
}: {
  value: string;
  options: string[];
  labels?: Record<string, string>;
  onChange: (value: string) => void;
}): JSX.Element {
  const all = options.includes(value) || !value ? options : [...options, value];
  if (all.length > 4) {
    return (
      <Dropdown
        value={value}
        options={all.map((option) => ({ value: option, label: labels?.[option] ?? option }))}
        onChange={onChange}
      />
    );
  }
  return (
    <div className="segmented" role="radiogroup">
      {all.map((option) => (
        <button
          key={option}
          type="button"
          role="radio"
          aria-checked={option === value}
          className={option === value ? "on" : ""}
          onClick={() => onChange(option)}
        >
          {labels?.[option] ?? option}
        </button>
      ))}
    </div>
  );
}

/** A few colours to pick one from, each shown as what it is. */
function Swatches<T extends string>({
  value,
  options,
  colors,
  labels,
  onChange,
}: {
  value: T;
  options: readonly T[];
  colors: Record<T, string>;
  labels: Record<T, string>;
  onChange: (value: T) => void;
}): JSX.Element {
  return (
    <div className="swatches" role="radiogroup">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          role="radio"
          className="swatch"
          aria-checked={option === value}
          aria-label={labels[option]}
          title={labels[option]}
          style={{ "--swatch": colors[option] } as React.CSSProperties}
          onClick={() => onChange(option)}
        />
      ))}
    </div>
  );
}

function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      className={`switch ${checked ? "on" : ""}`}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
    >
      <span className="switch-knob" />
    </button>
  );
}

/**
 * The agent's tools, switched on and off.
 *
 * The list is the registry's, not the config's: a tool this session never
 * registered (an optional dependency that is not installed) must not show up
 * as a switch that does nothing.
 */
function ToolSwitches({
  tools,
  disabled,
  onChange,
}: {
  tools: ToolSpec[];
  disabled: string[];
  onChange: (next: string[]) => void;
}): JSX.Element {
  if (tools.length === 0) {
    return <div className="settings-note">{t("This session exposed no tool list.")}</div>;
  }
  // Names this list does not show (an MCP server's tools, a server that is
  // down) keep their switch position whatever happens here.
  const listed = new Set(tools.map((tool) => tool.name));
  const others = disabled.filter((name) => !listed.has(name));
  const off = new Set(disabled.filter((name) => listed.has(name)));
  const toggle = (name: string) => {
    const next = new Set(off);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onChange([...others, ...next].sort());
  };
  return (
    <div className="tool-switches">
      <div className="settings-field-label">
        {t("Tools")} · {tf("{0} of {1} on", tools.length - off.size, tools.length)}
        <button
          className="btn-ghost tool-switch-all"
          onClick={() =>
            onChange(off.size ? [...others].sort() : [...others, ...tools.map((t) => t.name)].sort())
          }
        >
          {off.size ? t("Turn all on") : t("Turn all off")}
        </button>
      </div>
      <div className="tool-switch-list">
        {tools.map((tool) => (
          <label key={tool.name} className="tool-switch" title={tool.description}>
            <input type="checkbox" checked={!off.has(tool.name)} onChange={() => toggle(tool.name)} />
            <span className="tool-switch-name">{tool.name}</span>
          </label>
        ))}
      </div>
    </div>
  );
}
