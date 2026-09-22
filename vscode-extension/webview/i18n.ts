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

  // -- the node kinds themselves ------------------------------------------
  Start: "Início",
  End: "Fim",
  Sequence: "Sequência",
  Decision: "Decisão",
  Loop: "Laço",
  Print: "Imprimir",
  "Ask the user": "Perguntar ao usuário",
  "Get variable": "Ler variável",
  "Set variable": "Escrever variável",
  "Top P": "Top P",
  "Top K": "Top K",

  // -- a profession written in the editor ---------------------------------
  Role: "Papel",
  Prefix: "Prefixo",
  "Read files": "Ler arquivos",
  "Edit files": "Editar arquivos",
  "Run commands": "Rodar comandos",
  "Search the web": "Buscar na web",
  "Add workspace framing": "Somar o enquadramento do workspace",
  "name this profession": "dê um nome a esta profissão",
  "wired profession": "profissão por fio",
  "A profession you write yourself: the role prompt is the job, the switches are what it may touch. Wire it into an Agent's profession pin.":
    "Uma profissão escrita por você: o papel é o trabalho, as chaves dizem no que ela pode mexer. Ligue no pino de profissão de um Agente.",
  "What to call it. Shows on the node and in the run log.":
    "Como chamá-la. Aparece no nó e no log da execução.",
  "Who this agent is and how it works - the same voice as the built-in professions. It is the whole job description.":
    "Quem é este agente e como ele trabalha - na mesma voz das profissões prontas. É a descrição inteira do cargo.",
  "How many tool round-trips it gets before it must report.":
    "Quantas idas e voltas de ferramenta ele tem antes de precisar responder.",
  "0 keeps the worker budget from settings.":
    "0 mantém o orçamento de trabalhador da configuração.",
  "Off, the role text is the entire system prompt. On, the usual sub-agent preamble is added: workspace path, rules, report-back instructions. Leave it off for agents that talk to each other.":
    "Desligado, o texto do papel é o prompt de sistema inteiro. Ligado, entra o preâmbulo de sub-agente: caminho do workspace, regras, instruções de resposta. Deixe desligado para agentes que conversam entre si.",
  "Ignored while a Profession node is wired to the profession pin.":
    "Ignorado enquanto um nó Profissão estiver ligado no pino de profissão.",
  "append joins onto what is already there, separated by a blank line - how a transcript grows across a loop.":
    "append emenda no que já está lá, separado por uma linha em branco - é assim que uma transcrição cresce ao longo de um laço.",
  "Put in front of the appended text, e.g. a speaker's name.":
    "Vai na frente do texto emendado, por exemplo o nome de quem fala.",

  // -- the rest of both panels --------------------------------------------
  " — type the name with / to force it.": " — digite o nome com / para forçar.",
  "(session default)": "(padrão da sessão)",
  "A allows · S allows for the session · D denies": "A permite · S permite na sessão · D nega",
  "Agent": "Agente",
  "Allow for the session": "Permitir na sessão",
  "Analyse the selection automatically": "Analisar a seleção automaticamente",
  "Apply this edit?": "Aplicar esta edição?",
  "Apply with the agent": "Aplicar com o agente",
  "Ask anything about this workspace. Tools, plan and":
    "Pergunte qualquer coisa sobre este workspace. Ferramentas, plano e",
  "Back to the history": "Voltar ao histórico",
  "Behaviour": "Comportamento",
  "Clear messages": "Limpar mensagens",
  "Code-AI did not answer, so there are no settings to show. The chat says why.":
    "O Code-AI não respondeu, então não há configurações para mostrar. O chat diz o porquê.",
  "Code-AI refused": "O Code-AI recusou",
  "Collapse the panel": "Recolher painel",
  "Collapse the task panel": "Recolher painel de tarefas",
  "Completes as you type. The status bar toggles it.":
    "Completa enquanto você digita. Dá para ligar e desligar pela barra de status.",
  "Concurrent sub-agents": "Sub-agentes simultâneos",
  "Context and workspace": "Contexto e workspace",
  "Default mode": "Modo padrão",
  "Delete conversation": "Apagar conversa",
  "Do not include the editor context": "Não incluir o contexto do editor",
  "Dock the panel": "Encaixar painel",
  "Enter answers later · Alt+Enter steers this turn · Esc terminal":
    "Enter responde depois · Alt+Enter guia o turno atual · Esc terminal",
  "Explain": "Explicar",
  "Fields tagged": "Campos marcados com",
  "Fields tagged restart take effect when the panel reopens.":
    "Campos marcados com restart valem ao reabrir o painel.",
  "Ghost text in the editor, from the same provider.":
    "Ghost text no editor, pelo mesmo provedor.",
  "History": "Histórico",
  "How the extension behaves in the editor.": "Como a extensão se comporta no editor.",
  "In refactor mode, analyses the snippet as soon as you select it.":
    "No modo refatorar, analisa o trecho assim que você seleciona.",
  "Inline suggestions": "Sugestões inline",
  "Inline suggestions in the editor": "Sugestões inline no editor",
  "Language": "Idioma",
  "Learn mode": "Modo learn",
  "List the models the configured provider serves":
    "Listar os modelos que o provedor configurado serve",
  "Listing…": "Listando…",
  "Loading settings…": "Carregando configurações…",
  "Log": "Log",
  "Max context tokens": "Máx. de tokens de contexto",
  "Message for Code-AI…  (/ for commands)": "Mensagem para o Code-AI…  (/ para comandos)",
  "Model for inline suggestions": "Modelo das sugestões inline",
  "Modes and automation": "Modos e automação",
  "New conversation": "Nova conversa",
  "Next": "Próxima",
  "No conversations yet.": "Nenhuma conversa ainda.",
  "No saved conversations yet.": "Sem conversas salvas ainda.",
  "Open a terminal": "Abrir terminal",
  "Permission mode": "Modo de permissão",
  "Permission mode for commands and edits": "Modo de permissão para comandos e edições",
  "Pick up a conversation or start another.": "Retome uma conversa ou comece outra.",
  "Provider and model": "Provedor e modelo",
  "Reasoning": "Raciocínio",
  "Reasoning effort": "Esforço de raciocínio",
  "Refactor": "Refatorar",
  "Resize the task panel": "Redimensionar painel de tarefas",
  "Run this command?": "Rodar este comando?",
  "Select a snippet in the editor and run the analysis.":
    "Selecione um trecho no editor e rode a análise.",
  "Select a snippet in the editor to see the suggested improvements.":
    "Selecione um trecho no editor para ver as melhorias sugeridas.",
  "Send (Alt to steer the running turn)": "Enviar (Alt para guiar o turno atual)",
  "Send and deny": "Enviar e negar",
  "Shows, on approvals, why the model thinks the change is needed.":
    "Mostra, nas aprovações, por que o modelo acha que a mudança é necessária.",
  "Stop the agent": "Parar o agente",
  "Sub-agent models": "Modelos dos sub-agentes",
  "Task panel": "Painel de tarefas",
  "Tell the agent why, or what to do instead (optional):":
    "Diga ao agente por quê, ou o que fazer em vez disso (opcional):",
  "The agent is working": "O agente está trabalhando",
  "The panel and the agent's answers both follow this.":
    "O painel e as respostas do agente seguem isto.",
  "The provider returned no models.": "O provedor não devolveu nenhum modelo.",
  "This session exposed no tool list.": "Esta sessão não expôs a lista de ferramentas.",
  "Tools": "Ferramentas",
  "Travels with the next message": "Vai junto com a próxima mensagem",
  "Try again": "Tentar de novo",
  "Waiting for your answer.": "Esperando sua resposta.",
  "What the TUI edits in /doctor: tools, sub-agents and sampling.":
    "O que a TUI edita em /doctor: ferramentas, sub-agentes e amostragem.",
  "Workspace": "Workspace",
  "Write a detailed plan": "Gerar um plano detalhado",
  "Write this file?": "Escrever este arquivo?",
  "You": "Você",
  "approval": "aprovação",
  "approvals show up here.": "aprovações aparecem aqui.",
  "are saved now but only take effect after restarting Code-AI, which reloads the config without reloading the window.":
    "são salvos agora mas só valem depois de reiniciar o Code-AI, o que recarrega a config sem recarregar a janela.",
  "ask the user": "pergunta ao usuário",
  "choose a profession": "escolha uma profissão",
  "choose a variable": "escolha uma variável",
  "e.g. do not delete that file — update the config instead":
    "ex.: não apague esse arquivo — atualize o config",
  "empty = every sub-agent inherits the session model": "vazio = todos herdam o modelo da sessão",
  "empty = uses the main model": "vazio = usa o modelo principal",
  "endpoint default": "padrão do endpoint",
  "not set": "não configurada",
  "open file": "arquivo aberto",
  "or answer in your own words": "ou responda com suas palavras",
  "queued at the provider": "na fila do provedor",
  "read-only": "somente leitura",
  "selection": "seleção",
  "session model": "modelo da sessão",
  "terminal only": "só no terminal",
  "the rest": "o resto",
  "Turn all on": "Ligar todas",
  "Turn all off": "Desligar todas",
  "these fields": "estes campos",
  "this field": "este campo",
  "was saved. What you typed is still here so you can fix it.":
    "foi salvo. O que você digitou continua aqui para corrigir.",
  "your answer": "sua resposta",
  "•••••••• (stored - leave empty to keep it)": "•••••••• (salva - deixe vazio para manter)",
  "{0} line(s)": "{0} linha(s)",
  "{0} nodes": "{0} nós",
  "{0} error(s)": "{0} erro(s)",
  "{0} messages": "{0} mensagens",
  "{0} models on this endpoint": "{0} modelos neste endpoint",
  "{0} nodes, {1} connections.": "{0} nós, {1} ligações.",
  "{0} of {1} on": "{0} de {1} ligadas",
  "{0} is not in the extension yet; use the terminal for now.":
    "{0} ainda não existe na extensão; use o terminal por enquanto.",
  "No {0} found in this workspace.": "Nenhum {0} encontrado neste workspace.",
  "Choose from {0} available…": "Escolher entre {0} disponíveis…",
  "Key {0} stored.": "Chave {0} guardada.",
  "Delete {0} nodes": "Excluir {0} nós",
  "Duplicate {0} nodes": "Duplicar {0} nós",
  "Connect from {0}": "Ligar de {0}",
  "Get {0}": "Ler {0}",
  "Set {0}": "Gravar {0}",
  "New {0} variable": "Nova variável {0}",
  "Resize {0}": "Redimensionar {0}",
  "A graph may hold only {0} {1} node(s).": "Só pode existir {0} nó {1} por grafo.",
  "A {0} value cannot feed a {1} pin.": "Um valor {0} não entra em um pino {1}.",
  "Run finished in {0}s ({1} nodes).": "Execução concluída em {0}s ({1} nós).",
  "Run {0}: {1}": "Execução {0}: {1}",
  "The loop stopped at its cap of {0} laps.": "O laço parou no limite de {0} voltas.",
  "decision ({0}): {1}": "decisão ({0}): {1}",
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
