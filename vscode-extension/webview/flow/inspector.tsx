import * as React from "react";

import type { FieldSpec, FlowNode } from "../../src/flow-schema";
import type { Action, EditorState } from "./store";
import { defaultFor } from "./store";

export interface InspectorProps {
  state: EditorState;
  dispatch(action: Action): void;
  onListModels(node: FlowNode): void;
  onStoreSecret(name: string): void;
}

export function Inspector(props: InspectorProps): JSX.Element {
  const { state, dispatch } = props;
  const tab = state.inspectorTab;
  return (
    <aside className="flow-inspector" data-testid="inspector">
      <div className="flow-tabs">
        {(["node", "variables", "run"] as const).map((name) => (
          <button
            key={name}
            className={tab === name ? "active" : ""}
            data-testid={`tab-${name}`}
            onClick={() => dispatch({ type: "inspectorTab", tab: name })}
          >
            {name === "node" ? "Nó" : name === "variables" ? "Variáveis" : "Execução"}
          </button>
        ))}
      </div>
      <div className="flow-inspector-body">
        {tab === "node" && <NodePanel {...props} />}
        {tab === "variables" && <VariablesPanel state={state} dispatch={dispatch} />}
        {tab === "run" && <RunPanel state={state} />}
      </div>
    </aside>
  );
}

// -- the selected node ---------------------------------------------------

function NodePanel({ state, dispatch, onListModels, onStoreSecret }: InspectorProps) {
  const selected = state.selection[0];
  const node = state.graph.nodes.find((item) => item.id === selected);
  const spec = node ? state.catalog?.kinds.get(node.kind) : undefined;

  // More than one node selected: editing fields makes no sense, but acting on
  // the whole selection does - and showing only the first one's fields would
  // quietly edit a node the user is not looking at.
  if (state.selection.length > 1) {
    return <MultiSelection state={state} dispatch={dispatch} />;
  }

  if (!node || !spec) {
    return (
      <>
        <div className="flow-section-title">
          <span>Grafo</span>
          <span className="line" />
        </div>
        <div className="flow-field">
          <label htmlFor="flow-name">Nome</label>
          <input
            id="flow-name"
            data-testid="graph-name"
            value={state.graph.name}
            onChange={(event) => dispatch({ type: "rename", name: event.target.value })}
          />
        </div>
        <p className="flow-hint">
          Selecione um nó para editar. {state.graph.nodes.length} nós, {state.graph.edges.length}{" "}
          ligações.
        </p>
        <IssueList state={state} />
      </>
    );
  }

  const nodeIssues = state.issues.filter((issue) => issue.node_id === node.id);

  return (
    <>
      <div className="flow-section-title">
        <span>{spec.title}</span>
        <span className="line" />
        <span>{node.id}</span>
      </div>
      <p className="flow-hint">{spec.description}</p>

      {spec.fields.map((field) => (
        <Field
          key={field.name}
          field={field}
          node={node}
          state={state}
          dispatch={dispatch}
          onListModels={onListModels}
          onStoreSecret={onStoreSecret}
        />
      ))}

      {node.kind === "sequence" && (
        <div className="flow-field">
          <label>Saídas</label>
          <div className="row">
            <button
              data-testid="seq-remove"
              onClick={() =>
                dispatch({
                  type: "setNodeData",
                  id: node.id,
                  key: "outputs",
                  value: Math.max(1, Number(node.data.outputs ?? 2) - 1),
                })
              }
            >
              −
            </button>
            <span data-testid="seq-count">{Number(node.data.outputs ?? 2)}</span>
            <button
              data-testid="seq-add"
              onClick={() =>
                dispatch({
                  type: "setNodeData",
                  id: node.id,
                  key: "outputs",
                  value: Math.min(12, Number(node.data.outputs ?? 2) + 1),
                })
              }
            >
              + Adicionar pino
            </button>
          </div>
        </div>
      )}

      {nodeIssues.length > 0 && (
        <div className="flow-field">
          <label>Problemas</label>
          {nodeIssues.map((issue, index) => (
            <div key={index} className={`flow-issue ${issue.severity}`}>
              <span className="marker" />
              <span>{issue.message}</span>
            </div>
          ))}
        </div>
      )}

      <button
        className="danger"
        data-testid="delete-node"
        onClick={() => dispatch({ type: "deleteSelection" })}
      >
        Excluir nó
      </button>
    </>
  );
}

/** What the inspector offers when several nodes are selected at once. */
function MultiSelection({
  state,
  dispatch,
}: {
  state: EditorState;
  dispatch(action: Action): void;
}): JSX.Element {
  const counts = new Map<string, number>();
  for (const id of state.selection) {
    const node = state.graph.nodes.find((item) => item.id === id);
    if (!node) continue;
    const title = state.catalog?.kinds.get(node.kind)?.title ?? node.kind;
    counts.set(title, (counts.get(title) ?? 0) + 1);
  }
  return (
    <>
      <div className="flow-section-title">
        <span>Seleção</span>
        <span className="line" />
        <span data-testid="selection-count">{state.selection.length} nós</span>
      </div>
      {[...counts].map(([title, count]) => (
        <div key={title} className="flow-issue">
          <span className="marker" style={{ background: "var(--accent)" }} />
          <span>
            {count} × {title}
          </span>
        </div>
      ))}
      <p className="flow-hint">
        Botão direito em um nó para duplicar a seleção inteira.
      </p>
      <button
        className="danger"
        data-testid="delete-selection"
        onClick={() => dispatch({ type: "deleteSelection" })}
      >
        Excluir {state.selection.length} nós
      </button>
    </>
  );
}

interface FieldProps {
  field: FieldSpec;
  node: FlowNode;
  state: EditorState;
  dispatch(action: Action): void;
  onListModels(node: FlowNode): void;
  onStoreSecret(name: string): void;
}

function Field({ field, node, state, dispatch, onListModels, onStoreSecret }: FieldProps) {
  const value = node.data[field.name];
  const set = (next: unknown) =>
    dispatch({ type: "setNodeData", id: node.id, key: field.name, value: next });
  const id = `field-${node.id}-${field.name}`;
  const testId = `field-${field.name}`;

  const control = (() => {
    switch (field.kind) {
      case "select":
        return (
          <select id={id} data-testid={testId} value={String(value ?? "")} onChange={(e) => set(e.target.value)}>
            {field.options.map((option) => (
              <option key={option} value={option}>
                {option || "(padrão da sessão)"}
              </option>
            ))}
          </select>
        );
      case "textarea":
        return (
          <textarea
            id={id}
            data-testid={testId}
            value={String(value ?? "")}
            onChange={(e) => set(e.target.value)}
          />
        );
      case "number":
        return (
          <input
            id={id}
            data-testid={testId}
            type="number"
            value={value === null || value === undefined || value === "" ? "" : String(value)}
            onChange={(e) => set(e.target.value === "" ? null : Number(e.target.value))}
          />
        );
      case "bool":
        return (
          <input
            id={id}
            data-testid={testId}
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => set(e.target.checked)}
          />
        );
      case "variable":
        return (
          <select id={id} data-testid={testId} value={String(value ?? "")} onChange={(e) => set(e.target.value)}>
            <option value="">(escolha)</option>
            {state.graph.variables.map((variable) => (
              <option key={variable.id} value={variable.name}>
                {variable.name} : {variable.type}
              </option>
            ))}
          </select>
        );
      case "profession":
        return (
          <select id={id} data-testid={testId} value={String(value ?? "")} onChange={(e) => set(e.target.value)}>
            <option value="">(escolha)</option>
            {(state.catalog?.raw.professions ?? []).map((profession) => (
              <option key={profession.name} value={profession.name}>
                {profession.title}
                {profession.writes ? " ✎" : ""}
              </option>
            ))}
          </select>
        );
      case "model": {
        const list = state.models[node.id];
        return (
          <>
            <div className="row">
              <input
                id={id}
                data-testid={testId}
                value={String(value ?? "")}
                list={`models-${node.id}`}
                onChange={(e) => set(e.target.value)}
              />
              <button
                data-testid="list-models"
                disabled={list?.loading}
                onClick={() => onListModels(node)}
              >
                {list?.loading ? "…" : "Listar"}
              </button>
            </div>
            <datalist id={`models-${node.id}`}>
              {(list?.models ?? []).map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>
            {list?.message && <span className="help">{list.message}</span>}
            {list && !list.loading && list.models.length > 0 && (
              <span className="help" data-testid="models-count">
                {list.models.length} modelos neste endpoint
              </span>
            )}
          </>
        );
      }
      default:
        return (
          <input
            id={id}
            data-testid={testId}
            value={String(value ?? "")}
            onChange={(e) => set(e.target.value)}
          />
        );
    }
  })();

  const isSecretRef = field.name === "api_key_ref" && String(value ?? "").startsWith("secret:");

  return (
    <div className="flow-field">
      <label htmlFor={id}>{field.label}</label>
      {control}
      {isSecretRef && (
        <button
          data-testid="store-secret"
          onClick={() => onStoreSecret(String(value).slice(7))}
        >
          Guardar a chave no cofre do VSCode
        </button>
      )}
      {field.help && <span className="help">{field.help}</span>}
      {field.kind === "profession" && <ProfessionHint state={state} name={String(value ?? "")} />}
    </div>
  );
}

function ProfessionHint({ state, name }: { state: EditorState; name: string }) {
  const profession = (state.catalog?.raw.professions ?? []).find((item) => item.name === name);
  if (!profession) return null;
  return (
    <span className="help">
      {profession.description} — {profession.read_only ? "somente leitura" : "pode editar arquivos"}
    </span>
  );
}

// -- variables -----------------------------------------------------------

function VariablesPanel({
  state,
  dispatch,
}: {
  state: EditorState;
  dispatch(action: Action): void;
}) {
  const types = state.catalog?.raw.variable_types ?? ["string", "int", "float", "bool"];
  return (
    <>
      <div className="flow-section-title">
        <span>Variáveis</span>
        <span className="line" />
      </div>
      <div className="row" style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {types.map((type) => (
          <button
            key={type}
            data-testid={`add-var-${type}`}
            onClick={() => dispatch({ type: "addVariable", varType: type })}
          >
            <span
              className="chip-swatch"
              style={{ background: state.catalog?.color(type) ?? "#888" }}
            />
            + {type}
          </button>
        ))}
      </div>

      {state.graph.variables.length === 0 && (
        <p className="flow-hint">
          Nenhuma variável. Elas guardam valores entre os nós e aparecem com a cor do seu tipo.
        </p>
      )}

      {state.graph.variables.map((variable) => (
        <div key={variable.id} className="flow-var-row" data-testid={`var-${variable.name}`}>
          <span
            className="swatch"
            style={{ background: state.catalog?.color(variable.type) ?? "#888" }}
          />
          <input
            className="name"
            data-testid={`var-name-${variable.id}`}
            value={variable.name}
            onChange={(event) =>
              dispatch({
                type: "setVariable",
                id: variable.id,
                patch: { name: event.target.value },
              })
            }
          />
          <select
            data-testid={`var-type-${variable.id}`}
            value={variable.type}
            onChange={(event) =>
              dispatch({
                type: "setVariable",
                id: variable.id,
                patch: { type: event.target.value, default: defaultFor(event.target.value) },
              })
            }
          >
            {types.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
          <button
            className="danger"
            data-testid={`var-delete-${variable.id}`}
            onClick={() => dispatch({ type: "deleteVariable", id: variable.id })}
          >
            ×
          </button>
        </div>
      ))}

      {state.graph.variables.length > 0 && (
        <>
          <div className="flow-section-title">
            <span>Valores iniciais</span>
            <span className="line" />
          </div>
          {state.graph.variables.map((variable) => (
            <div className="flow-field" key={`default-${variable.id}`}>
              <label>{variable.name}</label>
              {variable.type === "bool" ? (
                <input
                  type="checkbox"
                  data-testid={`var-default-${variable.id}`}
                  checked={Boolean(variable.default)}
                  onChange={(event) =>
                    dispatch({
                      type: "setVariable",
                      id: variable.id,
                      patch: { default: event.target.checked },
                    })
                  }
                />
              ) : (
                <input
                  data-testid={`var-default-${variable.id}`}
                  type={variable.type === "string" ? "text" : "number"}
                  value={String(variable.default ?? "")}
                  onChange={(event) =>
                    dispatch({
                      type: "setVariable",
                      id: variable.id,
                      patch: {
                        default:
                          variable.type === "string"
                            ? event.target.value
                            : Number(event.target.value),
                      },
                    })
                  }
                />
              )}
            </div>
          ))}
        </>
      )}
      {state.graph.variables.length > 0 && (
        <>
          <div className="flow-section-title">
            <span>Valores desta execução</span>
            <span className="line" />
          </div>
          <p className="flow-hint">
            Vale só para a próxima execução: nada disso entra no arquivo.
          </p>
          {state.graph.variables.map((variable) => (
            <div className="flow-field" key={`override-${variable.id}`}>
              <label htmlFor={`override-${variable.name}`}>{variable.name}</label>
              <input
                id={`override-${variable.name}`}
                data-testid={`override-${variable.name}`}
                placeholder={String(variable.default ?? variable.type)}
                value={state.overrides[variable.name] ?? ""}
                onChange={(event) =>
                  dispatch({
                    type: "setOverride",
                    name: variable.name,
                    value: event.target.value,
                  })
                }
              />
            </div>
          ))}
        </>
      )}
    </>
  );
}

// -- the run -------------------------------------------------------------

function RunPanel({ state }: { state: EditorState }) {
  const { run } = state;
  const variables = Object.entries(run.variables);
  const agents = Object.entries(run.agents);
  return (
    <>
      <div className="flow-section-title">
        <span>Execução</span>
        <span className="line" />
        <span>{run.running ? "rodando" : run.status || "parada"}</span>
      </div>
      {!run.runId && <p className="flow-hint">Nada executado ainda nesta sessão.</p>}
      {run.runId && (
        <p className="flow-hint">
          run {run.runId} · {run.nodesExecuted} nós
        </p>
      )}

      {agents.length > 0 && (
        <>
          <div className="flow-section-title">
            <span>Agentes</span>
            <span className="line" />
          </div>
          {agents.map(([nodeId, agent]) => (
            <div key={nodeId} className="flow-var-row">
              <span
                className="swatch"
                style={{
                  background:
                    run.nodes[nodeId] === "failed"
                      ? "var(--danger)"
                      : run.nodes[nodeId] === "running"
                        ? "var(--accent)"
                        : "var(--ok)",
                }}
              />
              <div style={{ flex: "1 1 auto", minWidth: 0 }}>
                <div>
                  {agent.name} · {agent.profession}
                </div>
                {agent.detail && <div className="flow-hint">{agent.detail}</div>}
              </div>
            </div>
          ))}
        </>
      )}

      {variables.length > 0 && (
        <>
          <div className="flow-section-title">
            <span>Valores agora</span>
            <span className="line" />
          </div>
          {variables.map(([name, value]) => (
            <div key={name} className="flow-field">
              <label>{name}</label>
              <div data-testid={`run-var-${name}`}>{String(value) || "(vazio)"}</div>
            </div>
          ))}
        </>
      )}

      <IssueList state={state} />
    </>
  );
}

function IssueList({ state }: { state: EditorState }) {
  if (state.issues.length === 0) return null;
  return (
    <>
      <div className="flow-section-title">
        <span>Validação</span>
        <span className="line" />
      </div>
      {state.issues.map((issue, index) => (
        <div key={index} className={`flow-issue ${issue.severity}`} data-testid="issue">
          <span className="marker" />
          <span>
            {issue.node_id ? `${issue.node_id}: ` : ""}
            {issue.message}
          </span>
        </div>
      ))}
    </>
  );
}
