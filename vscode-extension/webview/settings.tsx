import * as React from "react";

import type { AppMode, Settings } from "../src/protocol";
import { ExtPrefs } from "./history";
import { IconBack, IconRefresh } from "./icons";

// Local, editable mirror of the backend settings. `null` until the bridge
// answers `getSettings`; the panel shows a loading state meanwhile.
type Draft = Record<string, string | number | boolean>;

/** State of the on-demand "list models" query for the model field. */
export interface ModelsState {
  status: "idle" | "loading" | "ready" | "error";
  list: string[];
  error?: string;
}

const RESTART_FIELDS = new Set(["api_mode", "base_url", "api_key", "workspace", "max_context_tokens"]);

export function SettingsScreen({
  settings,
  prefs,
  models,
  onBack,
  onSave,
  onPrefsChange,
  onRestart,
  onListModels,
}: {
  settings: Settings | null;
  prefs: ExtPrefs;
  models: ModelsState;
  onBack: () => void;
  onSave: (updates: Record<string, unknown>) => void;
  onPrefsChange: (next: Partial<ExtPrefs>) => void;
  onRestart: () => void;
  onListModels: () => void;
}): JSX.Element {
  const [draft, setDraft] = React.useState<Draft>({});
  const [apiKey, setApiKey] = React.useState("");
  const [saved, setSaved] = React.useState(false);

  // Reset the working copy whenever fresh settings arrive from the bridge.
  React.useEffect(() => {
    if (!settings) return;
    setDraft({
      model: settings.model,
      api_mode: settings.api_mode,
      base_url: settings.base_url,
      language: settings.language,
      permission_mode: settings.permission_mode,
      reasoning_effort: settings.reasoning_effort,
      learn: settings.learn,
      inline_hints_enabled: settings.inline_hints_enabled,
      inline_model: settings.inline_model,
      max_context_tokens: settings.max_context_tokens,
      workspace: settings.workspace,
    });
    setApiKey("");
    setSaved(false);
  }, [settings]);

  const set = (key: string, value: string | number | boolean) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setSaved(false);
  };

  const save = () => {
    if (!settings) return;
    const updates: Record<string, unknown> = { ...draft };
    if (apiKey.trim()) updates.api_key = apiKey.trim();
    onSave(updates);
    setSaved(true);
  };

  return (
    <div className="settings">
      <div className="settings-bar">
        <button className="icon-btn" title="Back" onClick={onBack}>
          <IconBack size={16} />
        </button>
        <span className="settings-title">Settings</span>
        <button className="btn-primary settings-save" onClick={save} disabled={!settings}>
          {saved ? "Saved" : "Save"}
        </button>
      </div>

      {!settings ? (
        <div className="settings-loading">Loading settings…</div>
      ) : (
        <div className="settings-body">
          <Section title="Modes & automation" hint="How the extension behaves in the editor.">
            <Field label="Default mode">
              <select
                value={prefs.mode}
                onChange={(e) => onPrefsChange({ mode: e.target.value as AppMode })}
              >
                <option value="agent">Agent</option>
                <option value="refactor">Refactor</option>
                <option value="explain">Explain</option>
              </select>
            </Field>
            <Toggle
              label="Run refactor automatically on selection"
              hint="In refactor mode, analyze a snippet as soon as you select it."
              checked={prefs.autoRunRefactor}
              onChange={(v) => onPrefsChange({ autoRunRefactor: v })}
            />
          </Section>

          <Section title="Provider & model" hint="Restart fields apply after reopening the panel.">
            <Field label="Model">
              <div className="model-row">
                <input
                  className="model-input"
                  value={String(draft.model ?? "")}
                  onChange={(e) => set("model", e.target.value)}
                />
                <button
                  type="button"
                  className="btn-ghost model-list-btn"
                  title="List the models the configured provider serves"
                  onClick={onListModels}
                  disabled={models.status === "loading"}
                >
                  {models.status === "loading" ? <span className="spinner" /> : <IconRefresh size={13} />}
                  <span>{models.status === "loading" ? "Listing…" : "List models"}</span>
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
                  <option value="">Pick from {models.list.length} available…</option>
                  {models.list.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              )}
              {models.status === "ready" && models.list.length === 0 && (
                <div className="model-note">The provider returned no models.</div>
              )}
              {models.status === "error" && <div className="model-error">{models.error}</div>}
            </Field>
            <Field label="API mode" restart>
              <select value={String(draft.api_mode ?? "")} onChange={(e) => set("api_mode", e.target.value)}>
                {settings.supported.api_mode.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label="Base URL" restart>
              <input value={String(draft.base_url ?? "")} onChange={(e) => set("base_url", e.target.value)} />
            </Field>
            <Field label="API key" restart>
              <input
                type="password"
                placeholder={settings.api_key_set ? "•••••••• (set — leave blank to keep)" : "not set"}
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  setSaved(false);
                }}
              />
            </Field>
          </Section>

          <Section
            title="Inline hints"
            hint="Editor ghost-text completions driven by the same provider."
          >
            <Toggle
              label="Enable inline code hints"
              hint="Suggest completions as you type. Toggle quickly from the status bar too."
              checked={Boolean(draft.inline_hints_enabled)}
              onChange={(v) => set("inline_hints_enabled", v)}
            />
            <Field label="Inline hints model">
              <input
                placeholder="Leave blank to use the main model"
                value={String(draft.inline_model ?? "")}
                onChange={(e) => set("inline_model", e.target.value)}
              />
            </Field>
          </Section>

          <Section title="Behavior">
            <Field label="Permission mode">
              <select value={String(draft.permission_mode ?? "")} onChange={(e) => set("permission_mode", e.target.value)}>
                {settings.supported.permission_mode.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label="Reasoning effort">
              <select value={String(draft.reasoning_effort ?? "")} onChange={(e) => set("reasoning_effort", e.target.value)}>
                {settings.supported.reasoning_effort.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label="Response language">
              <input value={String(draft.language ?? "")} onChange={(e) => set("language", e.target.value)} />
            </Field>
            <Toggle
              label="Learn mode"
              hint="Show the model's explanation of why each change is needed in approvals."
              checked={Boolean(draft.learn)}
              onChange={(v) => set("learn", v)}
            />
          </Section>

          <Section title="Context & workspace">
            <Field label="Max context tokens" restart>
              <input
                type="number"
                value={Number(draft.max_context_tokens ?? 0)}
                onChange={(e) => set("max_context_tokens", Number(e.target.value))}
              />
            </Field>
            <Field label="Workspace" restart>
              <input value={String(draft.workspace ?? "")} onChange={(e) => set("workspace", e.target.value)} />
            </Field>
          </Section>

          <div className="settings-restart">
            <div className="settings-note">
              Fields marked <span className="restart-tag">restart</span> are saved now but take effect
              after restarting Code-AI. This reloads the config without reloading the window.
            </div>
            <button className="btn-ghost settings-restart-btn" onClick={onRestart}>
              <IconRefresh size={13} /> Restart Code-AI
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
