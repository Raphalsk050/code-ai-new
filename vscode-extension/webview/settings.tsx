import * as React from "react";

import type { AppMode, Settings, ToolSpec } from "../src/protocol";
import { ExtPrefs } from "./history";
import { IconBack, IconRefresh } from "./icons";

// Local, editable mirror of the backend settings. `null` until the bridge
// answers `getSettings`; the panel shows a loading state meanwhile.
type Draft = Record<string, string | number | boolean | string[] | null>;

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
      disabled_tools: settings.disabled_tools ?? [],
      subagent_models: settings.subagent_models ?? [],
      max_concurrent_subagents: settings.max_concurrent_subagents ?? 1,
      temperature: settings.temperature,
      top_p: settings.top_p,
      top_k: settings.top_k,
    });
    setApiKey("");
    setSaved(false);
  }, [settings]);

  const set = (key: string, value: string | number | boolean | string[] | null) => {
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
        <button className="icon-btn" title="Voltar" onClick={onBack}>
          <IconBack size={16} />
        </button>
        <span className="settings-title">Configurações</span>
        <button className="btn-primary settings-save" onClick={save} disabled={!settings}>
          {saved ? "Salvo" : "Salvar"}
        </button>
      </div>

      {!settings ? (
        <div className="settings-loading">Carregando configurações…</div>
      ) : (
        <div className="settings-body">
          <Section title="Modos e automação" hint="Como a extensão se comporta no editor.">
            <Field label="Modo padrão">
              <select
                value={prefs.mode}
                onChange={(e) => onPrefsChange({ mode: e.target.value as AppMode })}
              >
                <option value="agent">Agente</option>
                <option value="refactor">Refatorar</option>
                <option value="explain">Explicar</option>
              </select>
            </Field>
            <Toggle
              label="Analisar a seleção automaticamente"
              hint="No modo refatorar, analisa o trecho assim que você seleciona."
              checked={prefs.autoRunRefactor}
              onChange={(v) => onPrefsChange({ autoRunRefactor: v })}
            />
          </Section>

          <Section title="Provedor e modelo" hint="Campos restart valem ao reabrir o painel.">
            <Field label="Modelo">
              <div className="model-row">
                <input
                  className="model-input"
                  value={String(draft.model ?? "")}
                  onChange={(e) => set("model", e.target.value)}
                />
                <button
                  type="button"
                  className="btn-ghost model-list-btn"
                  title="Listar os modelos que o provedor configurado serve"
                  onClick={onListModels}
                  disabled={models.status === "loading"}
                >
                  {models.status === "loading" ? <span className="spinner" /> : <IconRefresh size={13} />}
                  <span>{models.status === "loading" ? "Listando…" : "Listar modelos"}</span>
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
                  <option value="">Escolher entre {models.list.length} disponíveis…</option>
                  {models.list.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              )}
              {models.status === "ready" && models.list.length === 0 && (
                <div className="model-note">O provedor não devolveu nenhum modelo.</div>
              )}
              {models.status === "error" && <div className="model-error">{models.error}</div>}
            </Field>
            <Field label="Modo da API" restart>
              <select value={String(draft.api_mode ?? "")} onChange={(e) => set("api_mode", e.target.value)}>
                {settings.supported.api_mode.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label="URL base" restart>
              <input value={String(draft.base_url ?? "")} onChange={(e) => set("base_url", e.target.value)} />
            </Field>
            <Field label="Chave da API" restart>
              <input
                type="password"
                placeholder={settings.api_key_set ? "•••••••• (salva - deixe vazio para manter)" : "não configurada"}
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  setSaved(false);
                }}
              />
            </Field>
          </Section>

          <Section
            title="Sugestões inline"
            hint="Ghost text no editor, pelo mesmo provedor."
          >
            <Toggle
              label="Sugestões inline no editor"
              hint="Completa enquanto você digita. Dá para ligar e desligar pela barra de status."
              checked={Boolean(draft.inline_hints_enabled)}
              onChange={(v) => set("inline_hints_enabled", v)}
            />
            <Field label="Modelo das sugestões inline">
              <input
                placeholder="vazio = usa o modelo principal"
                value={String(draft.inline_model ?? "")}
                onChange={(e) => set("inline_model", e.target.value)}
              />
            </Field>
          </Section>

          <Section title="Comportamento">
            <Field label="Modo de permissão">
              <select value={String(draft.permission_mode ?? "")} onChange={(e) => set("permission_mode", e.target.value)}>
                {settings.supported.permission_mode.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label="Esforço de raciocínio">
              <select value={String(draft.reasoning_effort ?? "")} onChange={(e) => set("reasoning_effort", e.target.value)}>
                {settings.supported.reasoning_effort.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </Field>
            <Field label="Idioma das respostas">
              <input value={String(draft.language ?? "")} onChange={(e) => set("language", e.target.value)} />
            </Field>
            <Toggle
              label="Modo learn"
              hint="Mostra, nas aprovações, por que o modelo acha que a mudança é necessária."
              checked={Boolean(draft.learn)}
              onChange={(v) => set("learn", v)}
            />
          </Section>

          <Section title="Contexto e workspace">
            <Field label="Máx. de tokens de contexto" restart>
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

          <Section
            title="Doctor"
            hint="O que a TUI edita em /doctor: ferramentas, sub-agentes e amostragem."
          >
            <ToolSwitches
              tools={settings.supported?.tools ?? []}
              disabled={(draft.disabled_tools as string[]) ?? []}
              onChange={(next) => set("disabled_tools", next)}
            />
            <Field label="Modelos dos sub-agentes">
              <input
                value={((draft.subagent_models as string[]) ?? []).join(", ")}
                placeholder="vazio = todos herdam o modelo da sessão"
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
            <Field label="Sub-agentes simultâneos">
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
                placeholder="padrão do endpoint"
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
                placeholder="padrão do endpoint"
                onChange={(e) => set("top_p", e.target.value === "" ? null : Number(e.target.value))}
              />
            </Field>
            <Field label="top_k">
              <input
                type="number"
                value={draft.top_k == null ? "" : String(draft.top_k)}
                placeholder="padrão do endpoint"
                onChange={(e) => set("top_k", e.target.value === "" ? null : Number(e.target.value))}
              />
            </Field>
          </Section>

          <div className="settings-restart">
            <div className="settings-note">
              Campos marcados com <span className="restart-tag">restart</span> são salvos agora mas
              só valem depois de reiniciar o Code-AI, o que recarrega a config sem recarregar a janela.
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
    return <div className="settings-note">Esta sessão não expôs a lista de ferramentas.</div>;
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
        Ferramentas · {tools.length - off.size} de {tools.length} ligadas
        <button
          className="btn-ghost tool-switch-all"
          onClick={() => onChange(off.size ? [] : tools.map((t) => t.name).sort())}
        >
          {off.size ? "Ligar todas" : "Desligar todas"}
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
