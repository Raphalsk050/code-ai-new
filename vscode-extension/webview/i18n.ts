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
  "tab|Run": "Execução",
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

  // -- the node catalog, as the backend serves it -------------------------
  "Where the main line begins. Exactly one per graph.":
    "Onde a linha principal começa. Exatamente um por grafo.",
  "Ends the run. Whatever reaches `result` is the run's answer.":
    "Encerra a execução. O que chegar em `result` é a resposta dela.",
  "An endpoint plus a model name. Feed it into the agents that should run on it; agents with no model wired run on the session's own.":
    "Um endpoint e um nome de modelo. Ligue nos agentes que devem rodar nele; agente sem modelo ligado usa o da sessão.",
  "One isolated agent, hired for a profession, running one task to a report. Its profession decides what tools it may touch.":
    "Um agente isolado, contratado para uma profissão, que faz uma tarefa e devolve um relatório. A profissão decide em que ferramentas ele pode mexer.",
  "Runs each output in order, waiting for one branch to finish before the next.":
    "Dispara cada saída em ordem, esperando um ramo terminar antes de começar o próximo.",
  "Two ways forward. `bool` reads a wired condition; `judge` asks a model a yes/no question about the run so far.":
    "Dois caminhos. `bool` lê uma condição ligada; `judge` faz uma pergunta de sim/não ao modelo sobre o que aconteceu até aqui.",
  "Repeats `body` and then continues from `completed`. Always bounded: the iteration cap applies whatever the kind.":
    "Repete `body` e depois segue por `completed`. Sempre limitado: o teto de voltas vale para qualquer tipo.",
  "Pauses the run until the user types an answer on the node.":
    "Pausa a execução até o usuário responder no próprio nó.",
  "Writes a graph variable.": "Grava uma variável do grafo.",
  "Reads a graph variable.": "Lê uma variável do grafo.",
  "Writes a value to the run log. The debugger's best friend.":
    "Escreve um valor no log da execução. O melhor amigo de quem depura.",

  // Node titles are Blueprint vocabulary, so they stay as they are.

  // -- inspector fields ---------------------------------------------------
  "API mode": "Modo da API",
  "Empty follows the session setting.": "Vazio segue a sessão.",
  "API key": "Chave da API",
  "session (use the session key), env:NAME, or secret:NAME. A literal key is refused - this file goes into git.":
    "session (usa a chave da sessão), env:NOME ou secret:NOME. Chave literal é recusada - este arquivo vai para o git.",
  Model: "Modelo",
  Temperature: "Temperatura",
  Profession: "Profissão",
  Task: "Tarefa",
  "Extra instructions": "Instruções extras",
  "Appended to the profession's own role prompt.": "Entram junto com o papel da profissão.",
  "Max steps": "Passos no máximo",
  "0 keeps the profession's budget.": "0 mantém o orçamento da profissão.",
  "Timeout (s)": "Tempo limite (s)",
  Mode: "Modo",
  Condition: "Condição",
  Question: "Pergunta",
  "Answerable with yes or no.": "Deve dar para responder com sim ou não.",
  Kind: "Tipo",
  "foreach walks one line of `items` per lap.": "foreach anda uma linha de `items` por volta.",
  Count: "Voltas",
  "Max iterations": "Voltas no máximo",
  "0 uses the configured ceiling.": "0 usa o teto da configuração.",
  Title: "Título",
  Value: "Valor",
  Label: "Rótulo",
  "Base URL": "URL base",
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

/**
 * The English source string, translated if the user picked another language.
 *
 * `context` disambiguates two uses of the same English word that translate
 * differently - "Run" the button and "Run" the tab - without inventing a fake
 * English string that would leak into the panel when no table applies.
 */
export function t(text: string, context = ""): string {
  if (current === "en") return text;
  const table = TABLES[current];
  if (context) {
    const keyed = table[`${context}|${text}`];
    if (keyed) return keyed;
  }
  return table[text] ?? text;
}

/** `t` for a string with one `{0}` placeholder, so word order can move. */
export function tf(text: string, ...values: (string | number)[]): string {
  return values.reduce<string>(
    (out, value, index) => out.split(`{${index}}`).join(String(value)),
    t(text)
  );
}
