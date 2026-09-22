// User-facing text for both panels.
//
// English is the language of the product: every string in the source is the
// English one, and `t()` returns it untouched unless the user picked another
// language in the settings. A translation that is missing falls back to the
// English text rather than to a key, so a half-finished table degrades into a
// readable panel instead of into `settings.save.button`.
//
// Keying by the English string is deliberate: there is no key registry to keep
// in sync, and a string that is edited in place simply stops matching and
// shows its (new) English form until the table catches up.

export type Lang = "en" | "pt";

/** Portuguese (Brazil). Every entry is keyed by the English source string. */
const PT: Record<string, string> = {
  // -- chrome, shared -----------------------------------------------------
  Settings: "Configurações",
  Save: "Salvar",
  Saving: "Salvando",
  Saved: "Salvo",
  "Not saved": "Não salvou",
  Back: "Voltar",
  Close: "Fechar",
  Clear: "Limpar",
  Cancel: "Cancelar",
  Stop: "Parar",
  Run: "Executar",
  Delete: "Excluir",
  Duplicate: "Duplicar",
  Search: "Buscar",
  Undo: "Undo",
  Redo: "Redo",
  ready: "pronto",
  running: "rodando",
  stopped: "parada",
  "(none)": "(nenhum)",
  "(choose)": "(escolha)",
  "(empty)": "(vazio)",

  // -- orchestrator graph -------------------------------------------------
  Orchestrator: "Orquestrador",
  unnamed: "sem nome",
  Node: "Nó",
  Nodes: "Nós",
  Variables: "Variáveis",
  Variable: "Variável",
  Run_tab: "Execução",
  "Add node": "Adicionar nó",
  Connection: "Ligação",
  "Delete connection": "Excluir ligação",
  "Delete node": "Excluir nó",
  Flow: "Fluxo",
  Agents: "Agentes",
  Data: "Dados",
  Debug: "Depuração",
  "New variable": "Nova variável",
  Graph: "Grafo",
  Name: "Nome",
  Problems: "Problemas",
  Validation: "Validação",
  Selection: "Seleção",
  Outputs: "Saídas",
  "Add pin": "Adicionar pino",
  "List models": "Listar",
  Answer: "Responder",
  "Your answer…": "Sua resposta…",
  "Starting values": "Valores iniciais",
  "Values for this run": "Valores desta execução",
  "Live values": "Valores agora",
  "Nothing ran in this session yet.": "Nada foi executado nesta sessão ainda.",
  "Select a node to edit it.": "Selecione um nó para editar.",
  "Empty graph. Right-click the canvas to create the first node.":
    "Grafo vazio. Clique com o botão direito na tela para criar o primeiro nó.",
  "Nothing yet. A Print node writes here, and that is how a graph gets debugged.":
    "Nada ainda. Um nó Print escreve aqui, e é assim que se depura um grafo.",
  "Nothing with that name.": "Nada com esse nome.",
  "Fix the errors marked on the graph before running it.":
    "Corrija os erros marcados antes de executar.",
  "Connect an output to an input.": "Ligue uma saída em uma entrada.",
  "A node cannot connect to itself.": "Um nó não pode se ligar a si mesmo.",
  "One of the nodes is gone.": "Um dos nós não existe mais.",
  "Unknown node kind.": "Tipo de nó desconhecido.",
  "That pin does not exist.": "Esse pino não existe.",
  "An exec pin does not connect to a data pin.":
    "Um pino de execução não conecta em um pino de dados.",
  "That connection already exists.": "Essa ligação já existe.",
  "The node catalog has not loaded yet.": "O catálogo de nós ainda não carregou.",
  "Only read-only": "Somente leitura",
  "can edit files": "pode editar arquivos",
  "Right-click a node to duplicate the whole selection.":
    "Botão direito em um nó para duplicar a seleção inteira.",
  "Only applies to the next run: none of it goes into the file.":
    "Vale só para a próxima execução: nada disso entra no arquivo.",
  "Store the key in the VSCode vault": "Guardar a chave no cofre do VSCode",
  "The graph is waiting for your answer.": "O grafo está esperando sua resposta.",
  "Fit everything": "Enquadrar tudo",
  "Fix the graph errors before running it.": "Corrija os erros do grafo antes de executar.",
  "No variables yet. They carry values between nodes and take their type's colour.":
    "Nenhuma variável. Elas guardam valores entre os nós e aparecem com a cor do seu tipo.",
};

const TABLES: Record<Lang, Record<string, string>> = { en: {}, pt: PT };

let current: Lang = "en";

/** Pick the table from a backend language tag such as `pt-BR` or `en`. */
export function setLanguage(tag: string): Lang {
  current = /^pt/i.test(String(tag ?? "").trim()) ? "pt" : "en";
  return current;
}

export function language(): Lang {
  return current;
}

/** The English source string, translated if the user picked another language. */
export function t(text: string): string {
  if (current === "en") return text;
  return TABLES[current][text] ?? text;
}

/** `t` for a string with one `{0}` placeholder, so word order can move. */
export function tf(text: string, ...values: (string | number)[]): string {
  return values.reduce<string>(
    (out, value, index) => out.split(`{${index}}`).join(String(value)),
    t(text)
  );
}
