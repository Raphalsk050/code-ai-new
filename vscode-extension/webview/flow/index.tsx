import * as React from "react";
import { createRoot } from "react-dom/client";

import type {
  FlowGraph,
  FlowHostToWebview,
  FlowNode,
  FlowWebviewToHost,
} from "../../src/flow-schema";
import { Canvas, ContextTarget, frameAll, zoomAround } from "./canvas";
import {
  buildClip,
  fallbackClip,
  instantiate,
  isEditable,
  parseClip,
  writeClip,
} from "./clipboard";
import { setLanguage, t, tf } from "../i18n";
import { ContextMenu, MenuEntry } from "./context-menu";
import { Inspector } from "./inspector";
import { LogPanel } from "./log";
import { accentOf, isAnnotation, layoutGraph, NODE_W, nodesInside, pinsOf, Point } from "./model";
import { FLOW_STYLE } from "./styles";
import {
  Action,
  EditorState,
  hasErrors,
  initialState,
  pinTypeOf,
  reduce,
} from "./store";
import { Toolbar } from "./toolbar";

declare function acquireVsCodeApi(): {
  postMessage: (message: FlowWebviewToHost) => void;
  getState: () => unknown;
  setState: (state: unknown) => void;
};

const vscode = acquireVsCodeApi();
const send = (message: FlowWebviewToHost) => vscode.postMessage(message);

function App(): JSX.Element {
  const [state, dispatch] = React.useReducer(reduce, undefined, initialState);
  // The graph as the host last saw it, so an edit that only changed selection
  // does not write the document. Seeded with the placeholder the panel mounts
  // on: that graph is not the user's, and pushing it blanks the file.
  const pushed = React.useRef<string | null>(null);
  if (pushed.current === null) pushed.current = JSON.stringify(state.graph);
  const stateRef = React.useRef(state);
  stateRef.current = state;
  // The right-click menu is pure UI: it never belongs in the document, so it
  // lives here rather than in the reducer.
  const [menu, setMenu] = React.useState<ContextTarget | null>(null);
  const [minimap, setMinimap] = React.useState(true);

  // -- host traffic -----------------------------------------------------

  React.useEffect(() => {
    const onMessage = (event: MessageEvent<FlowHostToWebview | { type: string }>) => {
      const message = event.data as FlowHostToWebview;
      switch (message.type) {
        case "flowCatalog":
          // The backend owns the language the user picked, so the panel
          // follows it rather than guessing from the host.
          setLanguage(message.catalog.language ?? "en");
          dispatch({ type: "catalog", catalog: message.catalog });
          break;
        case "flowDocument":
          pushed.current = JSON.stringify(message.graph);
          dispatch({ type: "document", graph: message.graph, fileName: message.fileName });
          break;
        case "flowValidation":
          dispatch({ type: "validation", ok: message.ok, issues: message.issues });
          break;
        case "flowEvent":
          dispatch({
            type: "graphEvent",
            eventType: message.event.event_type,
            payload: message.event.payload ?? {},
          });
          break;
        case "flowRunState":
          dispatch({
            type: "runState",
            running: message.running,
            runId: message.runId,
            message: message.message,
          });
          break;
        case "flowModels":
          dispatch({
            type: "models",
            nodeId: message.nodeId,
            state: { loading: false, models: message.models, message: message.message },
          });
          break;
        case "flowNotice":
          dispatch({ type: "notice", level: message.level, text: message.text });
          break;
        case "flowSecretStored":
          dispatch({
            type: "notice",
            level: "info",
            text: tf("Key {0} stored.", message.name),
          });
          break;
        case "flowCommand":
          if (message.command === "run") runGraph();
          else stopGraph();
          break;
      }
    };
    window.addEventListener("message", onMessage);
    send({ type: "flowReady" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  // Every committed graph change goes back to the document, which is what makes
  // the tab dirty and Ctrl+S meaningful.
  React.useEffect(() => {
    const serialized = JSON.stringify(state.graph);
    if (serialized === pushed.current) return;
    pushed.current = serialized;
    send({ type: "flowEdit", graph: state.graph });
  }, [state.graph]);

  // -- actions ----------------------------------------------------------

  const runGraph = React.useCallback(() => {
    const current = stateRef.current;
    if (hasErrors(current.issues)) {
      dispatch({
        type: "notice",
        level: "warn",
        text: t("Fix the errors marked on the graph before running it."),
      });
      return;
    }
    const variables: Record<string, unknown> = {};
    for (const variable of current.graph.variables) {
      const override = current.overrides[variable.name];
      if (override !== undefined && override !== "") variables[variable.name] = override;
    }
    send({ type: "flowRun", graph: current.graph, variables });
  }, []);

  const stopGraph = React.useCallback(() => {
    send({ type: "flowStop" });
  }, []);

  const closeMenu = React.useCallback(() => setMenu(null), []);

  const frame = React.useCallback(() => {
    const current = stateRef.current;
    if (!current.catalog) return;
    const element = document.querySelector<SVGSVGElement>('[data-testid="flow-canvas"]');
    const rect = element?.getBoundingClientRect();
    const view = frameAll(
      layoutGraph(current.graph, current.catalog),
      rect?.width ?? 900,
      rect?.height ?? 600
    );
    if (view) dispatch({ type: "setView", pan: view.pan, zoom: view.zoom });
  }, []);

  // The toolbar's zoom keeps the middle of the canvas still, as the wheel keeps
  // the point under the cursor.
  const zoomTo = React.useCallback((next: (zoom: number) => number) => {
    const current = stateRef.current;
    const element = document.querySelector<SVGSVGElement>('[data-testid="flow-canvas"]');
    const rect = element?.getBoundingClientRect();
    const centre = { x: (rect?.width ?? 900) / 2, y: (rect?.height ?? 600) / 2 };
    const view = zoomAround(current.graph.view, centre, next(current.graph.view.zoom));
    dispatch({ type: "setView", pan: view.pan, zoom: view.zoom });
  }, []);

  // -- keyboard and clipboard -------------------------------------------

  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // Never steal keys from a field the user is typing in.
      if (isEditable(event.target)) return;
      const mod = event.ctrlKey || event.metaKey;
      const current = stateRef.current;

      if (mod && event.key.toLowerCase() === "z") {
        event.preventDefault();
        dispatch({ type: event.shiftKey ? "redo" : "undo" });
        return;
      }
      if (mod && event.key.toLowerCase() === "y") {
        event.preventDefault();
        dispatch({ type: "redo" });
        return;
      }
      if (mod && event.key.toLowerCase() === "s") {
        event.preventDefault();
        send({ type: "flowSave" });
        return;
      }
      if (mod && event.key.toLowerCase() === "a") {
        event.preventDefault();
        dispatch({ type: "select", ids: current.graph.nodes.map((node) => node.id) });
        return;
      }
      if (mod && (event.key === "c" || event.key === "x")) {
        const clip = buildClip(current.graph, current.selection);
        if (!clip) return;
        writeClip(clip);
        if (event.key === "x") dispatch({ type: "deleteSelection" });
        return;
      }
      // Ctrl+V is deliberately absent: the browser answers it with a `paste`
      // event, and handling both pasted twice.
      if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        dispatch({ type: "deleteSelection" });
        return;
      }
      if (event.key === "F5") {
        event.preventDefault();
        if (current.run.running) stopGraph();
        else runGraph();
        return;
      }
      if (event.key === "Escape" && current.run.running) {
        stopGraph();
        return;
      }
      if (event.key === "f" && !mod) {
        frame();
        return;
      }
      if (event.key.toLowerCase() === "c" && !mod && !event.altKey) {
        // Unreal's gesture: a comment around what is selected, or one where the view is.
        event.preventDefault();
        dispatch({
          type: "wrapInComment",
          ids: current.selection,
          at: current.selection.length === 0 ? viewCentre(current.graph.view) : undefined,
        });
        return;
      }
      if (event.key === "F2" && current.selection.length === 1) {
        event.preventDefault();
        const id = current.selection[0];
        const node = current.graph.nodes.find((item) => item.id === id);
        const spec = node ? current.catalog?.kinds.get(node.kind) : undefined;
        if (node) dispatch({ type: "edit", target: { id, what: isAnnotation(spec) ? "title" : "note" } });
      }
    };

    const onPaste = (event: ClipboardEvent) => {
      // Text pasted into a field is the field's, never a subgraph.
      if (isEditable(event.target)) return;
      const text = event.clipboardData?.getData("text/plain") ?? "";
      const clip = parseClip(text) ?? fallbackClip(text);
      if (!clip) return;
      event.preventDefault();
      const { nodes, edges } = instantiate(clip, stateRef.current.graph);
      dispatch({ type: "paste", nodes, edges });
    };

    window.addEventListener("keydown", onKey);
    window.addEventListener("paste", onPaste);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("paste", onPaste);
    };
  }, [frame, runGraph, stopGraph]);

  // -- render -----------------------------------------------------------

  const question = state.run.question;

  return (
    <div className="flow-app">
      <Toolbar
        state={state}
        dispatch={dispatch}
        onRun={runGraph}
        onStop={stopGraph}
        onSave={() => send({ type: "flowSave" })}
        onFrame={frame}
        onZoom={(factor) => zoomTo((zoom) => zoom * factor)}
        onZoomReset={() => zoomTo(() => 1)}
        minimap={minimap}
        onToggleMinimap={() => setMinimap((value) => !value)}
      />

      {state.notice && (
        <div className={`flow-notice ${state.notice.level}`} data-testid="notice">
          <span>{state.notice.text}</span>
          <span className="spacer" />
          <button onClick={() => dispatch({ type: "dismissNotice" })}>{t("Close")}</button>
        </div>
      )}

      <div className="flow-body">
        <div className="flow-main">
          <Canvas state={state} dispatch={dispatch} onContext={setMenu} minimap={minimap} />
          {question && <QuestionCard question={question} runId={state.run.runId} />}
          <LogPanel state={state} dispatch={dispatch} />
        </div>
        <Inspector
          state={state}
          dispatch={dispatch}
          onListModels={(node: FlowNode) => {
            dispatch({
              type: "models",
              nodeId: node.id,
              state: { loading: true, models: state.models[node.id]?.models ?? [], message: "" },
            });
            send({
              type: "flowListModels",
              nodeId: node.id,
              baseUrl: String(node.data.base_url ?? ""),
              apiMode: String(node.data.api_mode ?? ""),
              apiKeyRef: String(node.data.api_key_ref ?? "session"),
            });
          }}
          onStoreSecret={(name) => send({ type: "flowStoreSecret", name })}
        />
      </div>

      {menu && (
        <ContextMenu
          at={menu.screen}
          title={menuTitle(menu)}
          searchable={menu.kind === "canvas" || menu.kind === "pin"}
          entries={buildEntries(state, menu, dispatch)}
          onClose={closeMenu}
        />
      )}
    </div>
  );
}

// -- the right-click menu ------------------------------------------------

function menuTitle(target: ContextTarget): string {
  switch (target.kind) {
    case "node":
      return t("Node");
    case "edge":
      return t("Connection");
    case "pin":
      return tf("Connect from {0}", target.pin.pin.pin.label || target.pin.pin.pin.name);
    default:
      return t("Add node");
  }
}

/** The point in the middle of the canvas, in graph coordinates. */
function viewCentre(view: { pan: [number, number]; zoom: number }): Point {
  const element = document.querySelector<SVGSVGElement>('[data-testid="flow-canvas"]');
  const rect = element?.getBoundingClientRect();
  return {
    x: ((rect?.width ?? 900) / 2 - view.pan[0]) / view.zoom - 200,
    y: ((rect?.height ?? 600) / 2 - view.pan[1]) / view.zoom - 120,
  };
}

const CATEGORY_LABELS: Record<string, string> = {
  note: "Notes",
  flow: "Flow",
  agent: "Agents",
  data: "Data",
  debug: "Debug",
  math: "Math",
  logic: "Logic",
  text: "Text",
  convert: "Convert",
};

/**
 * What the menu offers, by what was right-clicked.
 *
 * Dragging a wire off a pin filters the list to the node kinds that actually
 * have a pin the wire can land on - and picking one creates the node already
 * connected, which is the whole point of the gesture. A pin nothing accepts
 * gets an open, empty menu rather than a silent no-op.
 */
export function buildEntries(
  state: EditorState,
  target: ContextTarget,
  dispatch: (action: Action) => void
): MenuEntry[] {
  switch (target.kind) {
    case "node":
      return nodeEntries(state, target.id, dispatch);
    case "edge":
      return [
        {
          id: "delete-edge",
          label: t("Delete connection"),
          hint: "Del",
          section: t("Connection"),
          colour: "var(--danger)",
          run: () => dispatch({ type: "deleteEdge", id: target.id }),
        },
      ];
    case "pin":
      return pinEntries(state, target, dispatch);
    default:
      return canvasEntries(state, target.at, dispatch);
  }
}

function nodeEntries(
  state: EditorState,
  nodeId: string,
  dispatch: (action: Action) => void
): MenuEntry[] {
  const node = state.graph.nodes.find((item) => item.id === nodeId);
  const spec = node ? state.catalog?.kinds.get(node.kind) : undefined;
  const section = spec?.title ?? t("Node");
  const ids = state.selection.includes(nodeId) ? state.selection : [nodeId];
  return [
    ...noteEntries(state, nodeId, ids, section, dispatch),
    {
      id: "duplicate",
      label: ids.length > 1 ? tf("Duplicate {0} nodes", ids.length) : t("Duplicate"),
      hint: "Ctrl+C, Ctrl+V",
      section,
      colour: "var(--accent)",
      run: () => {
        const clip = buildClip(state.graph, ids);
        if (!clip) return;
        writeClip(clip);
        const { nodes, edges } = instantiate(clip, state.graph);
        dispatch({ type: "paste", nodes, edges });
      },
    },
    {
      id: "delete-node",
      label: ids.length > 1 ? tf("Delete {0} nodes", ids.length) : t("Delete"),
      hint: "Del",
      section,
      colour: "var(--danger)",
      run: () => {
        dispatch({ type: "select", ids });
        dispatch({ type: "deleteSelection" });
      },
    },
  ];
}

/** What a node's menu offers about comments and notes. */
function noteEntries(
  state: EditorState,
  nodeId: string,
  ids: string[],
  section: string,
  dispatch: (action: Action) => void
): MenuEntry[] {
  const node = state.graph.nodes.find((item) => item.id === nodeId);
  if (!node || !state.catalog) return [];
  const colour = "var(--accent)";
  const entries: MenuEntry[] = [];
  if (isAnnotation(state.catalog.kinds.get(node.kind))) {
    entries.push(
      {
        id: "edit-comment",
        label: t("Edit the text"),
        hint: "F2",
        section,
        colour,
        run: () => dispatch({ type: "edit", target: { id: nodeId, what: "title" } }),
      },
      {
        id: "select-inside",
        label: t("Select what it holds"),
        hint: "",
        section,
        colour,
        run: () =>
          dispatch({ type: "select", ids: nodesInside(layoutGraph(state.graph, state.catalog!), nodeId) }),
      }
    );
  } else if (ids.length === 1) {
    const note = String(node.data?.note ?? "");
    const shown = node.data?.note_shown !== false;
    entries.push({
      id: "edit-note",
      label: note ? t("Edit the note") : t("Add a note"),
      hint: "F2",
      section,
      colour,
      run: () => dispatch({ type: "edit", target: { id: nodeId, what: "note" } }),
    });
    if (note) {
      entries.push(
        {
          id: "toggle-note",
          label: shown ? t("Hide the note") : t("Show the note"),
          hint: "",
          section,
          colour,
          run: () => dispatch({ type: "patchNodeData", id: nodeId, patch: { note_shown: !shown } }),
        },
        {
          id: "remove-note",
          label: t("Remove the note"),
          hint: "",
          section,
          colour: "var(--danger)",
          run: () =>
            dispatch({
              type: "patchNodeData",
              id: nodeId,
              patch: { note: undefined, note_shown: undefined },
            }),
        }
      );
    }
  }
  entries.push({
    id: "wrap-comment",
    label: ids.length > 1 ? tf("Wrap {0} nodes in a comment", ids.length) : t("Wrap in a comment"),
    hint: "C",
    section,
    colour,
    run: () => dispatch({ type: "wrapInComment", ids }),
  });
  return entries;
}

function canvasEntries(
  state: EditorState,
  at: Point,
  dispatch: (action: Action) => void
): MenuEntry[] {
  const entries: MenuEntry[] = [];
  for (const spec of state.catalog?.raw.node_kinds ?? []) {
    entries.push({
      id: `kind-${spec.kind}`,
      label: t(spec.title),
      hint: "",
      section: CATEGORY_LABELS[spec.category] ?? spec.category,
      colour: accentOf(spec),
      glyph: spec.kind,
      run: () => dispatch({ type: "addNode", kind: spec.kind, at }),
    });
  }
  entries.push(...variableEntries(state, at, dispatch));
  return entries;
}

/** Get/Set for the variables that exist, plus one entry per new type. */
function variableEntries(
  state: EditorState,
  at: Point,
  dispatch: (action: Action) => void,
  filter?: (type: string, mode: "get" | "set") => boolean
): MenuEntry[] {
  const entries: MenuEntry[] = [];
  for (const variable of state.graph.variables) {
    for (const mode of ["get", "set"] as const) {
      if (filter && !filter(variable.type, mode)) continue;
      entries.push({
        id: `var-${mode}-${variable.id}`,
        label: tf(mode === "get" ? "Get {0}" : "Set {0}", variable.name),
        hint: variable.type,
        section: t("Variables"),
        colour: state.catalog?.color(variable.type) ?? "#888",
        run: () =>
          dispatch({
            type: "addNode",
            kind: mode === "get" ? "var_get" : "var_set",
            at,
            data: { variable: variable.name },
          }),
      });
    }
  }
  for (const type of state.catalog?.raw.variable_types ?? []) {
    if (filter && !filter(type, "get")) continue;
    entries.push({
      id: `new-var-${type}`,
      label: tf("New {0} variable", type),
      hint: "+",
      section: t("New variable"),
      colour: state.catalog?.color(type) ?? "#888",
      run: () => dispatch({ type: "createVariableWithNode", varType: type, at, mode: "get" }),
    });
  }
  return entries;
}

function pinEntries(
  state: EditorState,
  target: Extract<ContextTarget, { kind: "pin" }>,
  dispatch: (action: Action) => void
): MenuEntry[] {
  const source = target.pin;
  const sourceType = pinTypeOf(state, source.nodeId, source.pin.pin.name, source.direction);
  const wanted = source.direction === "out" ? "in" : "out";
  const entries: MenuEntry[] = [];

  for (const spec of state.catalog?.raw.node_kinds ?? []) {
    // The pins a *fresh* node of this kind would have, so a Sequence offers the
    // two it is born with rather than only the one its declaration lists.
    const probe = {
      id: "__probe",
      kind: spec.kind,
      pos: [0, 0] as [number, number],
      data: { outputs: 2, inputs: 2 },
    };
    for (const pin of pinsOf(spec, probe)) {
      if (pin.direction !== wanted) continue;
      const fits =
        source.direction === "out"
          ? connectableTypes(sourceType, pin.type)
          : connectableTypes(pin.type, sourceType);
      if (!fits) continue;
      entries.push({
        id: `wire-${spec.kind}-${pin.name}`,
        label: `${t(spec.title)} - ${pin.label || pin.name}`,
        hint: pin.type === "exec" ? "" : pin.type,
        section: CATEGORY_LABELS[spec.category] ?? spec.category,
        colour: accentOf(spec),
        glyph: spec.kind,
        run: () =>
          dispatch({
            type: "addNode",
            kind: spec.kind,
            at: target.at,
            wire: {
              newPin: pin.name,
              kind: pin.type === "exec" ? "exec" : "data",
              other: [source.nodeId, source.pin.pin.name],
              otherIsSource: source.direction === "out",
            },
          }),
      });
    }
  }

  // A variable of a type this pin accepts is as good a source as a node.
  entries.push(
    ...variableEntries(state, target.at, dispatch, (type, mode) => {
      if (sourceType === "exec") return false;
      return source.direction === "out"
        ? mode === "set" && connectableTypes(sourceType, type)
        : mode === "get" && connectableTypes(type, sourceType);
    })
  );
  return entries;
}

/** Mirror of the schema rule, over the types the catalog reports. */
function connectableTypes(from: string, to: string): boolean {
  if (from === "exec" || to === "exec") return from === to;
  if (from === to) return true;
  if (from === "any" || to === "any") return true;
  if (from === "int" && to === "float") return true;
  if (to === "string" && (from === "int" || from === "float" || from === "bool")) return true;
  return false;
}

/** The card a `user_chat` node shows while the run waits for a human. */
function QuestionCard({
  question,
  runId,
}: {
  question: { nodeId: string; callId: string; prompt: string };
  runId: string;
}): JSX.Element {
  const [text, setText] = React.useState("");
  const inputRef = React.useRef<HTMLTextAreaElement | null>(null);

  React.useEffect(() => {
    setText("");
    inputRef.current?.focus();
  }, [question.callId]);

  const submit = () => {
    send({ type: "flowAnswer", runId, callId: question.callId, text });
    setText("");
  };

  return (
    <div className="flow-question" data-testid="question">
      <strong>{question.nodeId}</strong>
      <span className="prompt">
        {question.prompt || t("The graph is waiting for your answer.")}
      </span>
      <textarea
        ref={inputRef}
        data-testid="question-input"
        value={text}
        placeholder={t("Your answer…")}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="row">
        <button className="primary" data-testid="question-send" onClick={submit}>
          {t("Answer")}
        </button>
      </div>
    </div>
  );
}

/** Where a new node lands when it comes from the palette rather than a click. */
function centreOfView(state: EditorState): Point {
  const element = document.querySelector<SVGSVGElement>('[data-testid="flow-canvas"]');
  const rect = element?.getBoundingClientRect();
  const { pan, zoom } = state.graph.view;
  const width = rect?.width ?? 900;
  const height = rect?.height ?? 600;
  return {
    x: (width / 2 - pan[0]) / zoom - NODE_W / 2,
    y: (height / 2 - pan[1]) / zoom,
  };
}

const style = document.createElement("style");
style.textContent = FLOW_STYLE;
document.head.appendChild(style);

const host = document.getElementById("root");
if (host) createRoot(host).render(<App />);
