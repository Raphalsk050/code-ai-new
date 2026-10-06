// The avatar on the real panel, away from VS Code.
//
// Importing ../webview/index mounts the panel exactly as the extension does;
// this file then plays the host (a conversation, a turn in each state) and
// puts every skin, state and size beside it.

import "../webview/index";

import * as React from "react";
import { createRoot } from "react-dom/client";

import { AgentAvatar } from "../webview/agent-avatar";
import type { AvatarPose } from "../webview/avatar-pose";
import { AVATAR_SKINS, type AvatarSkin, seasonalSkin, setAvatarSkin } from "../webview/avatar-skin";

const SKIN_NAME: Record<AvatarSkin, string> = {
  default: "Padrão",
  halloween: "Halloween",
  christmas: "Natal",
  easter: "Páscoa",
};
const SKIN_WHEN: Record<AvatarSkin, string> = {
  default: "o resto do ano",
  halloween: "25 a 31 de outubro",
  christmas: "1 a 25 de dezembro",
  easter: "Semana Santa, até o domingo",
};
const POSES: [AvatarPose, string][] = [
  ["idle", "Parado"],
  ["think", "Pensando"],
  ["search", "Buscando"],
  ["type", "Escrevendo"],
];

let sequence = 0;
function toPanel(data: unknown): void {
  window.dispatchEvent(new MessageEvent("message", { data }));
}
function event(event_type: string, payload: Record<string, unknown> = {}): void {
  sequence += 1;
  toPanel({
    type: "event",
    event: {
      event_id: `lab${sequence}`, event_type, event_version: 1, session_id: "lab",
      sequence, timestamp: "", source: "lab", payload,
    },
  });
}

const DONE = [
  { kind: "user", id: "u1", text: "Por que o parser estoura o tempo em arquivo grande?" },
  { kind: "thinking", id: "t1", text: "Vou olhar o parser primeiro.", streaming: false },
  {
    kind: "tool", id: "c1", toolId: "c1", name: "read_file", target: "src/parser.py", path: "src/parser.py",
    status: "done", detail: "",
  },
  {
    kind: "assistant", id: "a1", streaming: false,
    text:
      "O `parse_config` relê o arquivo inteiro a cada bloco, então o custo cresce com o quadrado do tamanho.\n\n" +
      "Dá para ler uma vez e fatiar: fica linear, e o limite de 30 s deixa de ser atingido.",
  },
];
const ASKED = [...DONE, { kind: "user", id: "u2", text: "Pode corrigir e rodar os testes?" }];

/** Put the panel's turn in the state that shows `pose`. */
function showTurn(pose: AvatarPose): void {
  event("client.load", { items: pose === "idle" ? DONE : ASKED });
  event("status.changed", { state: "READY" });
  if (pose === "idle") return;
  event("status.changed", { state: "CALLING_MODEL" });
  if (pose === "type") {
    event("model.stream.delta", { channel: "answer", text: "Corrigindo: o arquivo passa a ser lido uma vez só, e" });
  }
  if (pose === "search") {
    event("status.changed", { state: "EXECUTING_TOOL" });
    event("tool.call.started", { tool_call_id: "c2", name: "search_code", target: "parse_config" });
  }
}

function openPanel(): void {
  toPanel({
    type: "settings",
    settings: {
      model: "gemma4:31b-cloud", api_mode: "ollama", base_url: "http://localhost:11434/v1", api_key_set: false,
      language: "pt-BR", permission_mode: "ask", reasoning_effort: "medium", followup_reasoning_effort: "none",
      learn: false, inline_hints_enabled: false, inline_model: "", max_context_tokens: 32768,
      workspace: "C:/projetos/code-ai", disabled_tools: [], subagent_models: [], max_concurrent_subagents: 1,
      temperature: null, top_p: null, top_k: null,
      supported: { api_mode: ["ollama"], permission_mode: ["ask"], reasoning_effort: ["medium"], tools: [] },
    },
  });
  document.querySelector<HTMLButtonElement>(".home-new")?.click();
}

function Choice<T extends string>({
  label,
  value,
  options,
  onPick,
}: {
  label: string;
  value: T;
  options: readonly (readonly [T, string])[];
  onPick: (value: T) => void;
}): JSX.Element {
  return (
    <div className="lab-choice">
      <span className="lab-label">{label}</span>
      <div className="lab-seg" role="group" aria-label={label}>
        {options.map(([option, name]) => (
          <button key={option} type="button" aria-pressed={option === value} onClick={() => onPick(option)}>
            {name}
          </button>
        ))}
      </div>
    </div>
  );
}

function Lab(): JSX.Element {
  const [skin, setSkin] = React.useState<AvatarSkin>("default");
  const [pose, setPose] = React.useState<AvatarPose>("idle");
  const [theme, setTheme] = React.useState("dark");

  React.useEffect(() => setAvatarSkin(skin), [skin]);
  React.useEffect(() => showTurn(pose), [pose]);
  React.useEffect(() => {
    document.documentElement.classList.toggle("lab-light", theme === "light");
    document.body.classList.toggle("vscode-light", theme === "light");
    document.body.classList.toggle("vscode-dark", theme !== "light");
  }, [theme]);

  const today = seasonalSkin(new Date());
  return (
    <>
      <header className="lab-head">
        <h1>Avatar</h1>
        <p>O painel é o de verdade, com uma conversa de mentira. O que se vê aqui é o que a extensão mostra.</p>
      </header>

      <section className="lab-card">
        <Choice label="Skin" value={skin} options={AVATAR_SKINS.map((s) => [s, SKIN_NAME[s]] as const)} onPick={setSkin} />
        <Choice label="Estado" value={pose} options={POSES} onPick={setPose} />
        <Choice label="Tema" value={theme} options={[["dark", "Escuro"], ["light", "Claro"]]} onPick={setTheme} />
      </section>

      <section className="lab-card">
        <h2>Flutuando na ferramenta</h2>
        <p>
          Arraste o avatar no painel e solte em qualquer lugar. Solte em movimento para ver a inércia: ele desliza,
          bate de leve na borda e a folha chega atrasada. Setas empurram, duplo clique manda de volta ao canto. Só o
          rosto pega o clique; em volta dele o painel continua clicável. Desliga em Configurações, Agente.
        </p>
      </section>

      <section className="lab-card">
        <h2>Skins</h2>
        <p>
          O rosto, os olhos e o preenchimento chapado não mudam: é a marca. A skin troca o que vai na cabeça e usa
          no máximo duas cores a mais, tiradas do tema quando o tema tem. Hoje a data pediria:{" "}
          <strong>{SKIN_NAME[today]}</strong>.
        </p>
        <div className="lab-sheet">
          {AVATAR_SKINS.map((each) => (
            <button
              key={each}
              type="button"
              className="lab-row"
              aria-pressed={each === skin}
              onClick={() => setSkin(each)}
            >
              <span className="lab-row-name">
                {SKIN_NAME[each]}
                <small>{SKIN_WHEN[each]}</small>
              </span>
              <span className="lab-row-sizes">
                <AgentAvatar pose={null} size={14} skin={each} />
                <AgentAvatar pose={pose} size={22} skin={each} />
                <AgentAvatar pose={pose} size={30} fx skin={each} />
                <AgentAvatar pose={pose} size={52} fx skin={each} />
                <AgentAvatar pose={pose} size={96} fx skin={each} />
              </span>
            </button>
          ))}
        </div>
        <p className="lab-note">Da esquerda: 14 px (caixas antigas do chat), 22, 30 (cabeçalho), 52 e 96.</p>
      </section>
    </>
  );
}


// The panel hears its host only once its effects have run: a frame after it paints.
requestAnimationFrame(() =>
  window.setTimeout(() => {
    openPanel();
    createRoot(document.getElementById("lab")!).render(<Lab />);
  }, 30)
);
