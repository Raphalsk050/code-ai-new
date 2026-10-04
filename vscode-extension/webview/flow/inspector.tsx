import * as React from "react";

import type { FieldSpec, FlowNode } from "../../src/flow-schema";
import { t, tf } from "../i18n";
import {
  grantedTools,
  groupTools,
  permissionsOf,
  toolNames,
  ToolGroupView,
  wiredToolsets,
} from "./access";
import { GlyphBadge } from "./context-menu";
import { accentOf, dynamicCount, pinsOf } from "./model";
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
  // Held here so a folded section stays folded from one node to the next.
  const [collapsed, setCollapsed] = React.useState<Record<string, boolean>>({});
  const onCollapse = React.useCallback(
    (key: string, closed: boolean) => setCollapsed((value) => ({ ...value, [key]: closed })),
    []
  );
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
            {name === "node" ? t("Node") : name === "variables" ? t("Variables") : t("Run", "tab")}
          </button>
        ))}
      </div>
      <div className="flow-inspector-body">
        {tab === "node" && <NodePanel {...props} collapsed={collapsed} onCollapse={onCollapse} />}
        {tab === "variables" && <VariablesPanel state={state} dispatch={dispatch} />}
        {tab === "run" && <RunPanel state={state} />}
      </div>
    </aside>
  );
}

// -- the selected node ---------------------------------------------------

// Rarely touched, so they start folded; the header still says they are there.
const CLOSED_BY_DEFAULT = new Set(["Limits", "Sampling"]);

/** Consecutive fields that share a group, in catalog order. */
function sectionsOf(fields: FieldSpec[]): { group: string; fields: FieldSpec[] }[] {
  const sections: { group: string; fields: FieldSpec[] }[] = [];
  for (const field of fields) {
    const group = field.group ?? "";
    const last = sections[sections.length - 1];
    if (last && last.group === group) last.fields.push(field);
    else sections.push({ group, fields: [field] });
  }
  return sections;
}

interface NodePanelProps extends InspectorProps {
  collapsed: Record<string, boolean>;
  onCollapse(key: string, closed: boolean): void;
}

function NodePanel({
  state,
  dispatch,
  onListModels,
  onStoreSecret,
  collapsed,
  onCollapse,
}: NodePanelProps) {
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
          <span>{t("Graph")}</span>
          <span className="line" />
        </div>
        <div className="flow-field">
          <label htmlFor="flow-name">{t("Name")}</label>
          <input
            id="flow-name"
            data-testid="graph-name"
            value={state.graph.name}
            onChange={(event) => dispatch({ type: "rename", name: event.target.value })}
          />
        </div>
        <p className="flow-hint">
          {t("Select a node to edit it.")}{" "}
          {tf("{0} nodes, {1} connections.", state.graph.nodes.length, state.graph.edges.length)}
        </p>
        <IssueList state={state} />
      </>
    );
  }

  const nodeIssues = state.issues.filter((issue) => issue.node_id === node.id);
  // The switches read as one control, so they render together where the first is.
  const firstSwitch = spec.fields.find((field) => field.kind === "permission");
  const accent = accentOf(spec);

  const renderField = (field: FieldSpec) => {
    if (field.kind === "permission") {
      return field === firstSwitch ? (
        <PermissionGroup key="permissions" node={node} state={state} dispatch={dispatch} />
      ) : null;
    }
    return (
      <Field
        key={`${node.id}-${field.name}`}
        field={field}
        node={node}
        state={state}
        dispatch={dispatch}
        onListModels={onListModels}
        onStoreSecret={onStoreSecret}
      />
    );
  };

  return (
    <>
      {/* The title leads the markup so it reads first; the glyph is moved in front by CSS. */}
      <div className="flow-section-title flow-node-card" style={{ borderLeftColor: accent }}>
        <span className="title">{spec.title}</span>
        <GlyphBadge kind={spec.kind} colour={accent} size={24} />
        <span className="line" />
        <span className="id">{node.id}</span>
      </div>
      <p className="flow-hint">{t(spec.description)}</p>

      {sectionsOf(spec.fields).map(({ group, fields }) => {
        if (!group) return <React.Fragment key="fields">{fields.map(renderField)}</React.Fragment>;
        const key = `${spec.kind}:${group}`;
        const closed = collapsed[key] ?? CLOSED_BY_DEFAULT.has(group);
        return (
          <section key={group} className="flow-group" data-testid={`section-${group}`}>
            <button
              className="flow-group-head"
              aria-expanded={!closed}
              data-testid={`section-toggle-${group}`}
              onClick={() => onCollapse(key, !closed)}
            >
              <span className="caret">{closed ? "▸" : "▾"}</span>
              <span>{t(group)}</span>
              <span className="line" />
            </button>
            {!closed && <div className="flow-group-body">{fields.map(renderField)}</div>}
          </section>
        );
      })}

      {spec.pins.some((pin) => pin.dynamic) && (
        <DynamicPins node={node} state={state} dispatch={dispatch} />
      )}

      {nodeIssues.length > 0 && (
        <div className="flow-field">
          <label>{t("Problems")}</label>
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
        {t("Delete node")}
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
        <span>{t("Selection")}</span>
        <span className="line" />
        <span data-testid="selection-count">
          {tf("{0} nodes", state.selection.length)}
        </span>
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
        {t("Right-click a node to duplicate the whole selection.")}
      </p>
      <button
        className="danger"
        data-testid="delete-selection"
        onClick={() => dispatch({ type: "deleteSelection" })}
      >
        {tf("Delete {0} nodes", state.selection.length)}
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
                {option ? t(option.replace(/_/g, " ")) : t("(session default)")}
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
            <option value="">{t("(choose)")}</option>
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
            <option value="">{t("Custom")}</option>
            {(state.catalog?.raw.professions ?? []).map((profession) => (
              <option key={profession.name} value={profession.name}>
                {profession.title}
                {profession.writes ? " ✎" : ""}
              </option>
            ))}
          </select>
        );
      case "tools":
        return <ToolPicker key={node.id} field={field} node={node} state={state} dispatch={dispatch} />;
      case "permission":
        return (
          <input
            id={id}
            data-testid={testId}
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => set(e.target.checked)}
          />
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
                {list?.loading ? "…" : t("List models")}
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
                {tf("{0} models on this endpoint", list.models.length)}
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
      <label htmlFor={id}>{t(field.label)}</label>
      {control}
      {isSecretRef && (
        <button
          data-testid="store-secret"
          onClick={() => onStoreSecret(String(value).slice(7))}
        >
          {t("Store the key in the VSCode vault")}
        </button>
      )}
      {field.help && <span className="help">{t(field.help)}</span>}
      {field.kind === "profession" && <ProfessionHint state={state} name={String(value ?? "")} />}
    </div>
  );
}

function ProfessionHint({ state, name }: { state: EditorState; name: string }) {
  if (!name) {
    return <span className="help">{t("Custom: the role is what you write under Instructions.")}</span>;
  }
  const profession = (state.catalog?.raw.professions ?? []).find((item) => item.name === name);
  if (!profession) return null;
  return (
    <span className="help">
      {t(profession.description)} —{" "}
      {profession.read_only ? t("read-only") : t("can edit files")}
    </span>
  );
}

// -- "+ Add pin" -------------------------------------------------------------

/**
 * The count of a node's numbered pins, and a text box for each input copy
 * nothing is wired into - the only way to give an Append part a literal.
 */
function DynamicPins({
  node,
  state,
  dispatch,
}: {
  node: FlowNode;
  state: EditorState;
  dispatch(action: Action): void;
}): JSX.Element | null {
  const spec = state.catalog?.kinds.get(node.kind);
  if (!spec) return null;
  const direction = spec.pins.some((pin) => pin.dynamic && pin.direction === "out") ? "out" : "in";
  const key = direction === "out" ? "outputs" : "inputs";
  const count = dynamicCount(node, direction);
  // Sequence keeps its old test ids; everything else says which side it grows.
  const ids =
    node.kind === "sequence"
      ? { add: "seq-add", remove: "seq-remove", count: "seq-count" }
      : { add: "pins-add", remove: "pins-remove", count: "pins-count" };
  const set = (next: number) =>
    dispatch({ type: "setNodeData", id: node.id, key, value: Math.max(1, Math.min(12, next)) });
  const copies =
    direction === "in"
      ? pinsOf(spec, node).filter((pin) => pin.dynamic && pin.direction === "in")
      : [];
  const wired = new Set(
    state.graph.edges.filter((edge) => edge.to[0] === node.id).map((edge) => edge.to[1])
  );

  return (
    <div className="flow-field" data-testid="dynamic-pins">
      <label>{direction === "out" ? t("Outputs") : t("Inputs")}</label>
      <div className="row">
        <button data-testid={ids.remove} disabled={count <= 1} onClick={() => set(count - 1)}>
          −
        </button>
        <span data-testid={ids.count}>{count}</span>
        <button data-testid={ids.add} disabled={count >= 12} onClick={() => set(count + 1)}>
          + {t("Add pin")}
        </button>
      </div>
      {copies.map((pin) => (
        <div key={pin.name} className="row flow-pin-literal">
          <span className="flow-tag">{pin.label}</span>
          {wired.has(pin.name) ? (
            <span className="help">{t("wired")}</span>
          ) : (
            <input
              data-testid={`literal-${pin.name}`}
              value={String(node.data[pin.name] ?? "")}
              placeholder={t("text")}
              onChange={(event) =>
                dispatch({
                  type: "setNodeData",
                  id: node.id,
                  key: pin.name,
                  value: event.target.value,
                })
              }
            />
          )}
        </div>
      ))}
    </div>
  );
}

// -- what an agent may touch ----------------------------------------------

interface AccessProps {
  node: FlowNode;
  state: EditorState;
  dispatch(action: Action): void;
}

/**
 * The permission switches. On an Agent an unset switch follows the profession, and
 * wired Tools nodes replace them outright, so both states are drawn as such.
 */
function PermissionGroup({ node, state, dispatch }: AccessProps): JSX.Element {
  const isAgent = node.kind === "profession";
  const permissions = permissionsOf(state.catalog, state.graph, node);
  const locked = isAgent && wiredToolsets(state.graph, node.id).length > 0;
  const set = (field: string, value: unknown) =>
    dispatch({ type: "setNodeData", id: node.id, key: field, value });

  return (
    <div className="flow-field flow-permissions" data-testid="permissions">
      <label>{t("Permissions")}</label>
      {locked && (
        <span className="help" data-testid="permissions-locked">
          {t("Tools are wired in: this agent gets exactly those, and the switches do not apply.")}
        </span>
      )}
      <div className={`flow-switches${locked ? " locked" : ""}`}>
        {permissions.map(({ spec, on, explicit }) => (
          <div key={spec.field} className={`flow-switch-row${explicit ? " explicit" : ""}`}>
            <label className="flow-switch">
              <input
                type="checkbox"
                role="switch"
                data-testid={`field-${spec.field}`}
                checked={on}
                disabled={locked}
                onChange={(event) => set(spec.field, event.target.checked)}
              />
              <span className="track" aria-hidden="true">
                <span className="thumb" />
              </span>
              <span className="text">{t(spec.label)}</span>
            </label>
            {isAgent && !locked && explicit && (
              <button
                className="flow-link"
                title={t("Back to the profession's")}
                data-testid={`reset-${spec.field}`}
                onClick={() => set(spec.field, undefined)}
              >
                ↺
              </button>
            )}
            {isAgent && !locked && !explicit && (
              <span className="flow-tag">{t("profession")}</span>
            )}
          </div>
        ))}
      </div>
      {isAgent && <GrantedTools names={grantedTools(state.catalog, state.graph, node)} />}
    </div>
  );
}

/** What the agent ends up with, so the switches have a visible consequence. */
function GrantedTools({ names }: { names: string[] }): JSX.Element {
  const [open, setOpen] = React.useState(false);
  return (
    <div className="flow-granted" data-testid="granted-tools">
      <button
        className="flow-granted-head"
        data-testid="granted-toggle"
        disabled={names.length === 0}
        onClick={() => setOpen(!open)}
      >
        {names.length > 0 && (open ? "▾ " : "▸ ")}
        {names.length === 0 ? t("No tools: it can only talk.") : tf("Gets {0} tools", names.length)}
      </button>
      {open && names.length > 0 && (
        <div className="flow-chips">
          {names.map((name) => (
            <span key={name} className="flow-chip">
              {name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** A searchable, grouped checklist of tools, stored as a list of names. */
function ToolPicker({
  field,
  node,
  state,
  dispatch,
}: AccessProps & { field: FieldSpec }): JSX.Element {
  // A block list is usually empty, so its long checklist waits to be asked for.
  const compact = field.name === "blocked_tools";
  const [listOpen, setListOpen] = React.useState(!compact);
  const picked = toolNames(node, field.name);
  const known = new Set((state.catalog?.raw.tools ?? []).map((tool) => tool.name));
  const save = (names: string[]) =>
    dispatch({ type: "setNodeData", id: node.id, key: field.name, value: names });

  return (
    <div className="flow-tool-picker" data-testid={`picker-${field.name}`}>
      <div className="flow-chips">
        {picked.length === 0 && <span className="help">{t("None picked.")}</span>}
        {picked.map((name) => (
          <span
            key={name}
            className={`flow-chip${known.has(name) ? "" : " unknown"}`}
            data-testid={`chip-${field.name}-${name}`}
          >
            {name}
            <button
              className="flow-chip-x"
              title={t("Remove")}
              onClick={() => save(picked.filter((item) => item !== name))}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      {compact && (
        <button
          className="flow-link flow-picker-toggle"
          data-testid={`picker-toggle-${field.name}`}
          onClick={() => setListOpen(!listOpen)}
        >
          {listOpen ? t("Done") : picked.length > 0 ? t("Change…") : t("Choose tools…")}
        </button>
      )}
      {listOpen && <ToolChecklist field={field} picked={picked} state={state} onSave={save} />}
    </div>
  );
}

function ToolChecklist({
  field,
  picked,
  state,
  onSave,
}: {
  field: FieldSpec;
  picked: string[];
  state: EditorState;
  onSave(names: string[]): void;
}): JSX.Element {
  const [query, setQuery] = React.useState("");
  const [open, setOpen] = React.useState<Record<string, boolean>>({});
  const pickedSet = new Set(picked);
  const groups = groupTools(state.catalog, query);
  const searching = query.trim() !== "";

  const toggle = (name: string, on: boolean) =>
    onSave(on ? [...picked, name] : picked.filter((item) => item !== name));
  const setGroup = (group: ToolGroupView, on: boolean) => {
    const names = group.tools.map((tool) => tool.name);
    onSave(
      on
        ? [...picked, ...names.filter((name) => !pickedSet.has(name))]
        : picked.filter((name) => !names.includes(name))
    );
  };

  return (
    <>
      <input
        className="flow-tool-search"
        data-testid={`search-${field.name}`}
        placeholder={`${t("Search tools")}…`}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <div className="flow-tool-groups">
        {groups.length === 0 && <span className="help">{t("Nothing with that name.")}</span>}
        {groups.map((group) => {
          const count = group.tools.filter((tool) => pickedSet.has(tool.name)).length;
          const expanded = searching || Boolean(open[group.name]);
          const all = count === group.tools.length;
          return (
            <div
              key={group.name}
              className="flow-tool-group"
              data-testid={`group-${field.name}-${group.name}`}
            >
              <div className="flow-tool-group-head">
                <button
                  className="flow-tool-group-toggle"
                  data-testid={`group-toggle-${field.name}-${group.name}`}
                  onClick={() => setOpen({ ...open, [group.name]: !open[group.name] })}
                >
                  <span className="caret">{expanded ? "▾" : "▸"}</span>
                  <span className="title">{t(group.title)}</span>
                  <span className={`count${count > 0 ? " some" : ""}`}>
                    {count}/{group.tools.length}
                  </span>
                </button>
                <button
                  className="flow-link"
                  data-testid={`group-all-${field.name}-${group.name}`}
                  onClick={() => setGroup(group, !all)}
                >
                  {all ? t("None") : t("All")}
                </button>
              </div>
              {expanded &&
                group.tools.map((tool) => (
                  <label
                    key={tool.name}
                    className={`flow-tool-row${tool.enabled ? "" : " off"}`}
                    title={tool.description}
                  >
                    <input
                      type="checkbox"
                      data-testid={`tool-${field.name}-${tool.name}`}
                      checked={pickedSet.has(tool.name)}
                      onChange={(event) => toggle(tool.name, event.target.checked)}
                    />
                    <span className="name">{tool.name}</span>
                    {!tool.enabled && <span className="flow-tag">{t("off in settings")}</span>}
                  </label>
                ))}
            </div>
          );
        })}
      </div>
    </>
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
        <span>{t("Variables")}</span>
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
          {t(
            "No variables yet. They carry values between nodes and take their type's colour."
          )}
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
            <span>{t("Starting values")}</span>
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
            <span>{t("Values for this run")}</span>
            <span className="line" />
          </div>
          <p className="flow-hint">
            {t("Only applies to the next run: none of it goes into the file.")}
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
        <span>{t("Run", "tab")}</span>
        <span className="line" />
        <span>{run.running ? t("running") : run.status || t("stopped")}</span>
      </div>
      {!run.runId && (
        <p className="flow-hint">{t("Nothing ran in this session yet.")}</p>
      )}
      {run.runId && (
        <p className="flow-hint">
          {tf("run {0}", run.runId)} · {tf("{0} nodes", run.nodesExecuted)}
        </p>
      )}

      {agents.length > 0 && (
        <>
          <div className="flow-section-title">
            <span>{t("Agents")}</span>
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
                  {agent.tools !== undefined && (
                    <span className="flow-tag" style={{ marginLeft: 6 }}>
                      {tf("{0} tools", agent.tools)}
                    </span>
                  )}
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
            <span>{t("Live values")}</span>
            <span className="line" />
          </div>
          {variables.map(([name, value]) => (
            <div key={name} className="flow-field">
              <label>{name}</label>
              <div data-testid={`run-var-${name}`}>{String(value) || t("(empty)")}</div>
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
        <span>{t("Validation")}</span>
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
