import * as React from "react";

import { t, tf } from "./i18n";

import type { AppMode, Settings, ToolSpec } from "../src/protocol";
import { ExtPrefs } from "./history";
import { IconBack, IconRefresh } from "./icons";
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

const SAVE_LABEL: Record<SaveState["status"], string> = {
  idle: "Save",
  saving: "Saving…",
  saved: "Saved",
  error: "Not saved",
};

/** Session states that mean no answer is coming, so waiting is pointless. */
const DEAD = new Set(["DISCONNECTED", "ERROR"]);

export function SettingsScreen({
  settings,
  sessionStatus,
  prefs,
  models,
  save,
  onBack,
  onSave,
  onPrefsChange,
  onRestart,
  onListModels,
}: {
  settings: Settings | null;
  sessionStatus: string;
  prefs: ExtPrefs;
  models: ModelsState;
  save: SaveState;
  onBack: () => void;
  onSave: (updates: Record<string, unknown>) => void;
  onPrefsChange: (next: Partial<ExtPrefs>) => void;
  onRestart: () => void;
  onListModels: () => void;
}): JSX.Element {
  const [draft, setDraft] = React.useState<Draft>({});
  const [apiKey, setApiKey] = React.useState("");
  const [dirty, setDirty] = React.useState(false);
  // What the last submit put on the wire, so a refused field can be handed
  // back to the user instead of the value the backend went on holding.
  const submittedRef = React.useRef<Draft | null>(null);
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
    setDraft(keepRefusedEdits(draftFromSettings(settings), submitted, errors));
    if (!errors.api_key) setApiKey("");
    setDirty(false);
  }, [settings]);

  const set = (key: string, value: string | number | boolean | string[] | null) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setDirty(true);
  };

  const submit = () => {
    if (!settings) return;
    const updates: Record<string, unknown> = { ...draft };
    if (apiKey.trim()) updates.api_key = apiKey.trim();
    submittedRef.current = updates as Draft;
    onSave(updates);
  };

  const failures = Object.entries(save.errors);

  return (
    <div className="settings">
      <div className="settings-bar">
        <button className="icon-btn" title={t("Back")} onClick={onBack}>
          <IconBack size={16} />
        </button>
        <span className="settings-title">{t("Settings")}</span>
        <button
          className="btn-primary settings-save"
          onClick={submit}
          disabled={!settings || save.status === "saving"}
          data-status={dirty ? "idle" : save.status}
        >
          {t(dirty ? SAVE_LABEL.idle : SAVE_LABEL[save.status])}
        </button>
      </div>

      {!settings ? (
        // The settings live in the backend, so a backend that never answered
        // leaves this screen empty. Saying so beats spinning for ever.
        <div className="settings-loading">
          {DEAD.has(sessionStatus) ? (
            <>
              <span>
                {t(
                  "Code-AI did not answer, so there are no settings to show. The chat says why."
                )}
              </span>
              <button className="btn-primary" onClick={onRestart}>
                {t("Try again")}
              </button>
            </>
          ) : (
            t("Loading settings…")
          )}
        </div>
      ) : (
        <div className="settings-body">
          {failures.length > 0 && (
            <div className="settings-errors" role="alert">
              <span className="settings-errors-head">
                {t("Code-AI refused")}{" "}
                {failures.length === 1 ? t("this field") : t("these fields")}; {t("the rest")}
                {t("was saved. What you typed is still here so you can fix it.")}
              </span>
              {failures.map(([field, message]) => (
                <div key={field} className="settings-error" data-field={field}>
                  <code>{field}</code> {message}
                </div>
              ))}
            </div>
          )}
          <Section
            title={t("Modes and automation")}
            hint={t("How the extension behaves in the editor.")}
          >
            <Field label={t("Default mode")}>
              <select
                value={prefs.mode}
                onChange={(e) => onPrefsChange({ mode: e.target.value as AppMode })}
              >
                <option value="agent">{t("Agent")}</option>
                <option value="refactor">{t("Refactor")}</option>
                <option value="explain">{t("Explain")}</option>
              </select>
            </Field>
            <Toggle
              label={t("Analyse the selection automatically")}
              hint={t("In refactor mode, analyses the snippet as soon as you select it.")}
              checked={prefs.autoRunRefactor}
              onChange={(v) => onPrefsChange({ autoRunRefactor: v })}
            />
          </Section>

          <Section
            title={t("Provider and model")}
            hint={t("Fields tagged restart take effect when the panel reopens.")}
          >
            <Field label={t("Model")}>
              <div className="model-row">
                <input
                  className="model-input"
                  value={String(draft.model ?? "")}
                  onChange={(e) => set("model", e.target.value)}
                />
                <button
                  type="button"
                  className="btn-ghost model-list-btn"
                  title={t("List the models the configured provider serves")}
                  onClick={onListModels}
                  disabled={models.status === "loading"}
                >
                  {models.status === "loading" ? <span className="spinner" /> : <IconRefresh size={13} />}
                  <span>{models.status === "loading" ? t("Listing…") : t("List models")}</span>
                </button>
              </div>
              {models.status === "ready" && models.list.length > 0 && (
                <select
                  className="model-picker"
                  value=""
                  onChange={(e) => {
                    if (e.target.value) set("model", e.target.value);
                  }}
                >
                  <option value="">{tf("Choose from {0} available…", models.list.length)}</option>
                  {models.list.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              )}
              {models.status === "ready" && models.list.length === 0 && (
                <div className="model-note">{t("The provider returned no models.")}</div>
              )}
              {models.status === "error" && <div className="model-error">{models.error}</div>}
            </Field>
            <Field label={t("API mode")} restart>
              <select value={String(draft.api_mode ?? "")} onChange={(e) => set("api_mode", e.target.value)}>
                {settings.supported.api_mode.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label={t("Base URL")} restart>
              <input value={String(draft.base_url ?? "")} onChange={(e) => set("base_url", e.target.value)} />
            </Field>
            <Field label={t("API key")} restart>
              <input
                type="password"
                placeholder={
                  settings.api_key_set
                    ? t("•••••••• (stored - leave empty to keep it)")
                    : t("not set")
                }
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  setDirty(true);
                }}
              />
            </Field>
          </Section>

          <Section
            title={t("Inline suggestions")}
            hint={t("Ghost text in the editor, from the same provider.")}
          >
            <Toggle
              label={t("Inline suggestions in the editor")}
              hint={t("Completes as you type. The status bar toggles it.")}
              checked={Boolean(draft.inline_hints_enabled)}
              onChange={(v) => set("inline_hints_enabled", v)}
            />
            <Field label={t("Model for inline suggestions")}>
              <input
                placeholder={t("empty = uses the main model")}
                value={String(draft.inline_model ?? "")}
                onChange={(e) => set("inline_model", e.target.value)}
              />
            </Field>
          </Section>

          <Section title={t("Behaviour")}>
            <Field label={t("Permission mode")}>
              <select value={String(draft.permission_mode ?? "")} onChange={(e) => set("permission_mode", e.target.value)}>
                {settings.supported.permission_mode.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label={t("Reasoning effort")}>
              <select value={String(draft.reasoning_effort ?? "")} onChange={(e) => set("reasoning_effort", e.target.value)}>
                {settings.supported.reasoning_effort.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label={t("Language")}>
              <select
                className="language-select"
                value={String(draft.language ?? "en")}
                onChange={(e) => set("language", e.target.value)}
              >
                {languageOptions(String(draft.language ?? "en")).map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <span className="settings-hint">
                {t("The panel and the agent's answers both follow this.")}
              </span>
            </Field>
            <Toggle
              label={t("Learn mode")}
              hint={t("Shows, on approvals, why the model thinks the change is needed.")}
              checked={Boolean(draft.learn)}
              onChange={(v) => set("learn", v)}
            />
          </Section>

          <Section title={t("Context and workspace")}>
            <Field label={t("Max context tokens")} restart>
              <input
                type="number"
                value={Number(draft.max_context_tokens ?? 0)}
                onChange={(e) => set("max_context_tokens", Number(e.target.value))}
              />
            </Field>
            <Field label={t("Workspace")} restart>
              <input value={String(draft.workspace ?? "")} onChange={(e) => set("workspace", e.target.value)} />
            </Field>
          </Section>

          <Section
            title="Doctor"
            hint={t("What the TUI edits in /doctor: tools, sub-agents and sampling.")}
          >
            <ToolSwitches
              tools={settings.supported?.tools ?? []}
              disabled={(draft.disabled_tools as string[]) ?? []}
              onChange={(next) => set("disabled_tools", next)}
            />
            <Field label={t("Sub-agent models")}>
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
            </Field>
            <Field label={t("Concurrent sub-agents")}>
              <input
                type="number"
                min={1}
                value={Number(draft.max_concurrent_subagents ?? 1)}
                onChange={(e) => set("max_concurrent_subagents", Number(e.target.value))}
              />
            </Field>
            <Field label="temperature">
              <input
                type="number"
                step="0.05"
                value={draft.temperature == null ? "" : String(draft.temperature)}
                placeholder={t("endpoint default")}
                onChange={(e) =>
                  set("temperature", e.target.value === "" ? null : Number(e.target.value))
                }
              />
            </Field>
            <Field label="top_p">
              <input
                type="number"
                step="0.05"
                value={draft.top_p == null ? "" : String(draft.top_p)}
                placeholder={t("endpoint default")}
                onChange={(e) => set("top_p", e.target.value === "" ? null : Number(e.target.value))}
              />
            </Field>
            <Field label="top_k">
              <input
                type="number"
                value={draft.top_k == null ? "" : String(draft.top_k)}
                placeholder={t("endpoint default")}
                onChange={(e) => set("top_k", e.target.value === "" ? null : Number(e.target.value))}
              />
            </Field>
          </Section>

          <div className="settings-restart">
            <div className="settings-note">
              {t("Fields tagged")} <span className="restart-tag">restart</span>{" "}
              {t(
                "are saved now but only take effect after restarting Code-AI, which reloads the config without reloading the window."
              )}
            </div>
            <button className="btn-ghost settings-restart-btn" onClick={onRestart}>
              <IconRefresh size={13} /> Reiniciar Code-AI
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="settings-section">
      <div className="settings-section-head">
        <h3>{title}</h3>
        {hint && <span className="settings-section-hint">{hint}</span>}
      </div>
      <div className="settings-fields">{children}</div>
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

function Field({ label, restart, children }: { label: string; restart?: boolean; children: React.ReactNode }) {
  return (
    <label className="settings-field">
      <span className="settings-field-label">
        {label}
        {restart && <span className="restart-tag">restart</span>}
      </span>
      {children}
    </label>
  );
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="settings-toggle" onClick={() => onChange(!checked)}>
      <button className={`switch ${checked ? "on" : ""}`} role="switch" aria-checked={checked}>
        <span className="switch-knob" />
      </button>
      <div className="settings-toggle-text">
        <span className="settings-field-label">{label}</span>
        {hint && <span className="settings-toggle-hint">{hint}</span>}
      </div>
    </div>
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
  const off = new Set(disabled);
  const toggle = (name: string) => {
    const next = new Set(off);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onChange([...next].sort());
  };
  return (
    <div className="tool-switches">
      <div className="settings-field-label">
        {t("Tools")} · {tf("{0} of {1} on", tools.length - off.size, tools.length)}
        <button
          className="btn-ghost tool-switch-all"
          onClick={() => onChange(off.size ? [] : tools.map((t) => t.name).sort())}
        >
          {off.size ? t("Turn all on") : t("Turn all off")}
        </button>
      </div>
      <div className="tool-switch-list">
        {tools.map((tool) => (
          <label key={tool.name} className="tool-switch" title={tool.description}>
            <input
              type="checkbox"
              checked={!off.has(tool.name)}
              onChange={() => toggle(tool.name)}
            />
            <span className="tool-switch-name">{tool.name}</span>
          </label>
        ))}
      </div>
    </div>
  );
}
