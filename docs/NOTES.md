# NOTES

Decisões, trade-offs e pendências. Uma entrada por bloco de trabalho, mais
recente no topo.

## 2026-09-20 - Extensão VS Code: paridade fechada, testes e e2e com screenshot

Segunda leva, fechando o que a primeira deixou pendente. Tudo verificado com
`npm run test` (typecheck + unitários do webview + e2e em Chromium) e
`pytest tests/unit tests/integration`.

### O que foi feito

- **Resumo por conversa.** `ConversationStore` ganhou `summary`, `summary_at` e
  `total_tokens`; `CodeAIApplication.summarize_conversation()` escreve o resumo
  com uma chamada de modelo e não gasta outra enquanto a thread não andar
  (`summary_at >= updated_at`). O total de tokens é a distância até o
  `_usage_baseline` gravado quando a conversa virou a corrente, não o valor
  absoluto do ledger do processo, que conta todas as outras. A extensão pede o
  resumo quando você sai da conversa (nova, abrir outra, voltar ao histórico).
- **`@` no composer.** Ranking e inserção em `webview/mentions.ts` (nome vence
  caminho, mais curto desempata); a busca é do host, via
  `vscode.workspace.findFiles`, que já respeita `files.exclude` e não custa um
  hop de processo.
- **Esc alterna chat e terminal**, como na TUI. O listener é do documento
  porque o input do terminal vive no rail, que o composer não conhece; abrir a
  seção certa é um `reveal` com nonce.
- **Imagens coladas** viram chips no composer e viajam em `submitUserMessage`
  (`_parse_images` no bridge descarta o que chegou quebrado em vez de derrubar
  o turno).
- **`/skills` e `/workflows`** entram no catálogo que a ponte serve, marcados
  por `kind`, e rodam por `runAsset`; `resolve_asset` devolve o prompt e quem
  chama decide como rodar o turno. `/doctor`, `/status` e `/config` abrem as
  configurações, e `/config <campo> <valor>` aplica direto por `updateSettings`.
- **Doctor na extensão.** `get_settings` passou a expor `disabled_tools`,
  `subagent_models`, `max_concurrent_subagents` e a amostragem, mais o catálogo
  de ferramentas lido do registry vivo (não do config: uma ferramenta que a
  sessão nunca registrou não vira um interruptor que não faz nada).
  `update_settings` aceita os mesmos campos e chama `registry.set_disabled()`,
  então desligar uma ferramenta vale já na próxima requisição.
- **Aprovação virou cartão inline.** Era modal: numa sidebar estreita o overlay
  cobria justamente o diff, a saída do comando e o plano - o contexto de onde
  sai a decisão. Agora fica no fim da transcrição, com A/S/D no teclado, e só
  sai quando a ponte confirma (dois cliques não resolvem a mesma chamada duas
  vezes).
- **UI em português** nos componentes do webview. O painel de configurações
  também.
- **Testes.** 33 unitários do webview (`node scripts/test-webview.mjs`: esbuild
  bundla, o runner do próprio node roda - sem framework novo para instalar);
  40 verificações e2e em Chromium com screenshot de cada estado; no Python, o
  `SessionCommands`, o catálogo, o store e um `tests/integration/test_bridge_e2e.py`
  que dirige o `BridgeServer` por linhas JSON-RPC reais contra uma aplicação de
  verdade.

### Trade-offs

- **O e2e usa o bundle real, não o VS Code.** `harness.html` fabrica o que o
  editor daria ao webview (variáveis `--vscode-*` e `acquireVsCodeApi`). Cobre
  layout, estado e interação; não cobre o host nem a ponte - esses têm os
  testes de integração em Python. Rodar um VS Code de verdade (`@vscode/test-electron`)
  cobriria os dois lados e é a evolução natural quando o host ganhar mais lógica.
- **Screenshot não é comparado pixel a pixel.** Ele documenta o estado e mostra
  regressão a olho; um baseline de imagem em CI exigiria fixar fonte e
  renderização, o que é caro para o retorno neste tamanho de projeto.
- **A gaveta encosta na tira de ícones (sm), e cobre a sidebar (xs).** Abaixo de
  380px não cabem os dois, então lá o único caminho de volta é o ✕ da gaveta.
- **O resumo custa uma chamada de modelo por conversa encerrada.** É explícito e
  só quando a thread mudou; um resumo automático por turno sairia caro sem
  melhorar a lista.

### Pendências

- `webview/reducer.ts` ficou órfão e precisa ser apagado (`git rm`).
- O `/doctor` da TUI ainda tem telas que a extensão não tem (diagnóstico de
  provedor, experimentos); ferramentas, sub-agentes e amostragem já estão lá.
- Baseline de screenshot em CI, se o layout estabilizar.
- Imagens só entram por colagem; arrastar arquivo ainda não.

## 2026-09-20 - Extensão VS Code: chassi de painéis, store fatiado e movimento

### O que foi feito

- **Store fatiado no webview** (`webview/store/`). O reducer único virou dez
  fatias (`transcript`, `session`, `usage`, `approvals`, `plan`, `agents`,
  `terminal`, `questions`, `code`, `goal`). `applyEvent` chama todas e devolve
  o mesmo objeto quando nenhuma mudou, então um delta de streaming repinta a
  transcrição e mais nada. O antigo `webview/reducer.ts` foi substituído por
  `webview/store/transcript.ts`.
- **Rail de tarefas** (`webview/task-rail.tsx`). Sessão, Plano, Agentes,
  Terminal e Histórico como seções de um mesmo componente, com três layouts:
  coluna encaixada (>520px), gaveta sobreposta (380-520px) e gaveta cheia
  (<380px). A largura vem de `ResizeObserver` (`use-container-width.ts`), não
  de media query: o webview só conhece a própria caixa, não a janela.
- **Paridade com a TUI**: plano com passos e evidência, sub-agentes com
  circuito aberto, perguntas do `ask_user` inline no chat, código ao vivo
  durante o `write_file`, barra de contexto sempre visível, terminal
  interativo, loop de objetivo, índice com progresso.
- **Camada de comandos compartilhada** (`app/commands.py`). Terminal, objetivo,
  plano e índice saíram do `TerminalController` para `SessionCommands`, que
  devolve dados; a TUI formata suas strings e o bridge serializa o mesmo
  resultado. Antes, a ponte teria que duplicar o comportamento para ter
  `/term`.
- **Catálogo de slash commands compartilhado** (`app/slash_catalog.py`). Uma
  lista só; `bridge_supports()` marca o que a ponte executa hoje e a extensão
  mostra o resto apagado em vez de fingir que funciona.
- **Novos métodos JSON-RPC**: `replan`, `startPlan`, `planSnapshot`,
  `goalDefine/Start/Stop/Status`, `terminalStart/Write/Control/Kill/Status`,
  `indexRefresh`, `indexStatus`, `listSlashCommands`. Os que fazem chamada de
  modelo, andam o workspace ou cancelam o turno entram em
  `_CONCURRENT_METHODS` - segurar o read loop congelaria as aprovações que
  chegam pelo mesmo pipe.

### Trade-offs

- **Sem biblioteca de animação.** Web Animations API e CSS, nada no bundle. O
  VS Code 1.74 roda Chromium 102: sem `linear()`, sem View Transitions, sem
  `interpolate-size`. Mola é `cubic-bezier(.34,1.4,.52,1)`, altura de seção é
  medida e animada à mão (`useCollapseHeight`), reordenação é FLIP
  (`useFlipList`). Se o mínimo subir para 1.90+ (Chromium 124), dá para trocar
  por `linear()` e View Transitions.
- **Nada anima por evento.** Um turno emite centenas de eventos por segundo;
  só montagem, desmontagem, reordenação e interação direta animam. O cursor de
  streaming é CSS puro.
- **`prefers-reduced-motion` desliga tudo**, inclusive o `transform: scale` do
  clique e o ripple.
- **Diff grande sai da sidebar**: cartão compacto no chat e o diff nativo do
  editor para ler de verdade.
