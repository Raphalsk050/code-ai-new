# Plano de refactoring da orquestração — determinismo robusto para modelos fracos

> **Branch:** `refactor/orchestration-determinism`
> **Autor da análise:** sessão de 2026-07-31 (forense de 96 MB de logs reais + comparativo com Claude Code)
> **Como usar este doc:** cada frente (F1–F5) é independente e executável isolada. Leia as seções 1–3 primeiro (contexto + princípio), depois ataque as frentes na ordem da seção 5. Cada frente traz: motivação com evidência, âncoras `arquivo:linha`, mudança concreta, ADR com trade-offs, testes e riscos.

---

## 0. TL;DR

A camada determinística do planner (fases, ledger de evidência, gate de conclusão) foi a decisão **certa** para rodar modelos locais fracos — mas na prática ela **fica apagada**, por três furos independentes comprovados em logs reais:

1. **O classificador de superfície derruba a tarefa para `CONVERSATION`** e isso zera o bloco de estado do planner (presente em só **17%** dos steps; **0% em 3 de 5 sessões**).
2. **Os meta-tools que dirigem qualidade são opt-in** e o modelo fraco não os chama (`complete_task`=**0** em 241 steps) — logo a única trava dura **nunca dispara**.
3. **`execute_command` é o bypass universal**: 77% das mutações reais passam por shell, que **nunca** vira evidência `FILE_CHANGED`, então a verificação pós-mutação nunca é exigida.

**Princípio de correção:** engajar a disciplina por **evidência observada** (o que o modelo fez), não por keyword (o que ele disse); e cravar o checkpoint de qualidade **no ponto de parada natural do loop**, não em `complete_task`.

---

## 1. Mapa da arquitetura relevante

Camadas (de cima pra baixo): `ui/` → `app/` → `core/` → `providers/`. A orquestração de um turno vive em dois lugares:

| Componente | Arquivo | Papel |
|---|---|---|
| **`AgentOrchestrator`** | `src/code_ai/core/orchestration.py` (~2150 linhas) | O loop provider/tool de um turno. Determinístico, tolerante a falha. |
| **`PlannerService`** | `src/code_ai/core/planning/service.py` (~1650 linhas) | Progresso semântico, política de tools por fase, gate de conclusão por evidência. |
| **`PlannerToolPolicy`** | `src/code_ai/core/planning/policy.py` | Deriva tools permitidas/recomendadas da fase. Em modo `advisory` (default) **expõe tudo**. |
| **Classificador** | `src/code_ai/core/planning/models.py` — `TaskProfile.from_user_text` (:132) | Regex de keyword decide `intent` / `requires_workspace_mutation`. **Ponto único de falha.** |
| **Evidência** | `src/code_ai/core/planning/evidence.py` — `_records_from_payload` (:308) | Traduz payload de tool em `EvidenceRecord`. `execute_command` em :415. |
| **Gate de conclusão** | `src/code_ai/core/planning/completion.py` | Políticas Minimal/Standard/Strict sobre um `CompletionContext` imutável. |
| **Preconditions** | `src/code_ai/core/planning/preconditions.py` | Nudges advisory (1×, fail-open) antes de tools de ação. |

**Fluxo de um turno** (`orchestration.py`):
- `run_turn` (:405) → `_begin_planner` (:542) classifica e monta o esqueleto → `_run_model_loop` (:570).
- Loop: `_build_request` (:2014) anexa `_planner_context()` como mensagem *user* efêmera por step → `_run_model_step` → se sem tool call: `_handle_no_tool_response` (:635); se com: `_execute_tool_batch` (:1300) → `_note_tool_round` (:1066, guard de stall).
- Conclusão: só via `complete_task` → `_completion_rejection` (:1606) → `planner.evaluate_completion` (service.py:844).

**Restrição de runtime importante:** o app roda **um modelo por turno**; reflexão em background é cancelada quando um novo turno precisa do modelo (`_cancel_pending_learning`). Não introduza chamadas de modelo síncronas extras no caminho crítico.

---

## 2. Baseline forense (a evidência que motiva tudo)

Fonte: `~/.code-ai/logs/` — **241 model steps reais**, 5 sessões, modelo local `qwen3.6-35b-a3b`, workload de mutação intensiva no filesystem feita majoritariamente via shell. Método: parsear os blocos `REQUEST`/`PARSED RESPONSE` de cada `*.log`.

| Métrica | Valor | Implicação |
|---|---|---|
| Bloco "Runtime task state" presente | **42/241 (17%)**, 0% em 3/5 sessões | O planner fica mudo na maioria dos steps (furo #1) |
| `submit_plan` / `complete_plan_step` | 2 / 2 | Checklist do modelo é decorativa |
| `complete_task` | **0** | O gate de conclusão **nunca dispara** (furo #2) |
| `use_skill` | **0** | Skills nunca usadas proativamente |
| `execute_command` | **118/221 tool calls (53%)** | Shell é o canal dominante |
| — dos quais mutam o filesystem | **77%** (`python -c`, `>`, `cp`) | Mutações invisíveis ao planner (furo #3) |
| Batches com >1 write | **0** (191/221 batches = 1 call) | "Limitar writes por step" seria no-op — **não implementar** |
| Tools expostas por step | **43** (ou 0 em 19 steps) | Sem foco; superfície grande p/ modelo fraco |
| Tool calls truncados mid-stream | **44** numa única sessão | Robustez de streaming (F4) |
| Stall nudges distintos / precondition | 8 / 7 | Guards fail-open saudáveis e modestos |

**Confirmações diretas no código** (rodadas nesta análise):
```

"continue de onde paramos"   -> CONVERSATION, mut=False   (continuação vira chat)
"olhe o arquivo ..."         -> LOCAL_INSPECTION, mut=False (sem andaimes de mutação)
"implemente um endpoint"     -> IMPLEMENTATION, mut=True    (controle: funciona)
```
`execute_command` em `evidence.py:415` classifica shell como `VERIFICATION_*` (se for test/build) ou `COMMAND_SUCCEEDED/FAILED` — **nunca** `FILE_CHANGED`/`FILE_CREATED`. Logo `has_file_change` (service.py:927) fica `False` para trabalho feito via shell, e `changes_require_verification([])` retorna `False`.

**Reproduzir a forense:** os scripts foram descartados, mas o método é: para cada `~/.code-ai/logs/<sessão>/*.log`, split em `^===== (REQUEST|PARSED RESPONSE)`, `json.loads` de cada bloco, contar `tool_calls[].name` na resposta e `messages` no request. Nudges se contam na **última** request de cada sessão (a de histórico mais cheio), senão inflam por persistirem no histórico.

---

## 3. Princípio de design (o norte de todas as frentes)

> **Determinismo dirigido por evidência observada, não por classificação declarada; checkpoint de qualidade no ponto de parada natural do loop, não num meta-tool opt-in.**

Isto importa a boa lição do Claude Code — **o loop termina quando o modelo para de chamar tools** (docs oficiais: agent-loop; não há máquina de fases nem gate de conclusão lá; TodoWrite é rastreio, não trava; plan mode é permission mode) — **sem** abrir mão do determinismo que o modelo fraco exige. O Claude Code pode confiar no modelo porque roda Opus/Sonnet; nós não podemos, então o **runtime** precisa cravar as travas — mas cravá-las sobre o que o modelo **fez**, não sobre o que ele **disse** que ia fazer.

Corolário anti-over-engineering: **não** endurecer batches (dado #: batches já são de 1 call), **não** transformar guards fail-open em travas duras (eles estão saudáveis), **não** copiar o loop plano do Claude Code (ele depende de modelo forte).

---

## 4. As frentes

### F1 — Desacoplar o determinismo do classificador  *(causa-raiz #1 — maior impacto)*

**Motivação.** Hoje toda a disciplina de mutação está condicionada a `TaskProfile.requires_workspace_mutation`, que vem de regex de keyword sobre o **primeiro texto do turno**. Turnos de continuação ("continue", "siga") e instruções de baixo nível caem em `CONVERSATION` → `task_context_block` (service.py:625) retorna `""` → zero andaimes. Comprovado: 17% de presença do bloco.

**Duas mudanças (independentes entre si):**

**F1a — Continuação herda o profile anterior.**
- Âncora: `PlannerService.begin_turn` (service.py:226) — hoje sempre faz `self.profile = TaskProfile.from_user_text(text)`.
- Já existe o precedente `_resume_turn` (service.py:273) para o caso de `ask_user` pendente. Generalizar: se o texto do turno é uma **continuação pura** (regex curto: `^(continue|contin[úu]e|siga|prossiga|segue|vai|go on|keep going|continue)\b` sem novo objetivo substantivo) **e** já existe `self.profile`, chamar o caminho de resume em vez de re-classificar.
- **Onde detectar:** ou em `begin_turn` (cedo, antes de sobrescrever `self.profile`), ou subir a decisão para quem chama `run_turn(resume_plan=...)` em `app/service.py`. Preferir `begin_turn` para manter a regra num lugar só.

**F1b — Escalar para disciplina de mutação por evidência observada.**
- Âncora: `record_tool_result` (service.py:374) e `_effective_profile` (service.py:771).
- Quando o modelo chama uma tool de mutação (`write_file`/`edit_code`, ou shell mutante — ver F2) numa tarefa classificada como não-mutação, **promover** o profile em runtime: setar um flag `self._observed_mutation = True` e fazer `_task_produces_workspace_effects` (service.py:755) e o `task_context_block` passarem a tratar a tarefa como produtora de efeitos no workspace **a partir daí**.
- O gate de conclusão **já** faz isso parcialmente (`has_file_change and not verified` em completion.py:201 dispara independente do label) — F1b estende a mesma filosofia para o **bloco de contexto** e a **exigência de verificação**, não só para o gate final.

**ADR / trade-offs.**
- *Decisão:* engajar por observação, com o classificador virando só uma dica inicial.
- *Prós:* fecha o furo #1 na raiz; robusto a frase; reaproveita a filosofia já provada no gate.
- *Contras:* risco de re-injetar o bloco pesado (~2 KB) numa conversa genuína. *Mitigação:* F1b só liga **após a 1ª evidência de mutação** — conversa pura nunca chama tool de mutação, então nunca escala. F1a é conservador: só dispara em texto de continuação pura com profile pré-existente.
- *Alternativa descartada:* melhorar o regex de mutação (adicionar mais verbos ao dicionário). Rejeitada: é remendo perpétuo, não fecha a classe do furo, e não cobre continuação.

**Testes** (`tests/unit/test_planning.py`, 64 testes hoje):
- "continue" após um turno de implementação mantém `requires_workspace_mutation=True`.
- Turno CONVERSATION que chama `write_file` passa a exigir verificação para concluir.
- Conversa pura (sem tool de mutação) **não** re-injeta o bloco nem exige verificação.

**Riscos.** Não deixar F1a engolir um turno que *parece* continuação mas traz objetivo novo ("continue, mas agora faça X") — o regex deve exigir que o texto seja **essencialmente só** o marcador de continuação.

---

### F2 — Tornar mutações via shell visíveis ao planner  *(cobre 77% das mutações reais)*

**Motivação.** `execute_command` que muta o filesystem nunca vira `FILE_CHANGED` (evidence.py:415). Consequência em cadeia: `has_file_change=False` (service.py:927) → verificação pós-mutação nunca exigida → o gate de conclusão não tem o que cobrar. Para workloads shell-driven (geração/edição de arquivos via scripts, automação de filesystem), a disciplina inteira é cega.

**Mudança.**
- Âncora: bloco `execute_command` em `evidence.py:415`.
- Detectar shell mutante por forma do comando (não parsear paths — frágil): redirecionamentos `>`/`>>`, `Set-Content`/`Add-Content`/`Out-File`/`New-Item`, `cp`/`mv`/`Copy-Item`/`Move-Item`, `sed -i`, `tee`, `python -c` com escrita, `rm`/`Remove-Item`. Regex já esboçada na forense desta análise.
- Quando mutante **e** exit 0: além do `COMMAND_SUCCEEDED` atual, emitir um sinal **grosso** de mutação — nova `EvidenceType.WORKSPACE_MUTATED_BY_COMMAND` (ou setar um flag no ledger `command_mutated_workspace=True`) que faça `has_file_change`/`has_success(...)` retornarem `True` **sem** paths específicos.
- Consumir esse sinal em `_completion_context` (service.py:927): `has_file_change = ledger.has_success(FILE_CREATED, FILE_CHANGED) or ledger.command_mutated_workspace`.

**ADR / trade-offs.**
- *Decisão:* sinal coarse-grained ("workspace mudou via comando"), não extração de paths.
- *Prós:* engata a verificação para o canal dominante; simples; sem parser de shell arbitrário.
- *Contras:* sem paths, a verificação pós-mutação vira genérica ("rode o check do projeto") em vez de dirigida a arquivos. Aceitável — o objetivo é *exigir* verificação, não localizar o diff. E `changed_paths` fica vazio, então `changes_require_verification` precisa considerar o flag também (senão retorna `False` por lista vazia — cuidado com essa interação em completion.py:53).
- *Falso-positivo:* um `rm` de arquivo temporário conta como mutação. Impacto: pede verificação uma vez; fail-open cobre. Tolerável.

**Testes** (`tests/unit/test_completion_gate.py`, 28 testes; + evidência em `test_planning.py`):
- `execute_command` com `echo x > f.py` exit 0 → `has_file_change=True`, conclusão sem verificação é rejeitada 1×.
- `execute_command` de leitura (`cat`, `ls`) **não** marca mutação.
- Comando mutante + verificação depois → conclusão aceita.

**Riscos.** A interação `changes_require_verification([])` (completion.py:53) retorna `False` para lista vazia — se você marcar `has_file_change=True` mas `changed_paths` vazio, garanta que o caminho de verificação ainda engate (ajustar `verification_applies` em service.py:935 para considerar o flag de comando).

---

### F3 — Conclusão dirigida pelo runtime  *(destrava a trava morta #2)*

**Motivação.** `complete_task`=0 em 241 steps. O gate de conclusão por evidência — a única trava dura — é código morto no uso real, porque depende do modelo fraco chamar um meta-tool. Turnos terminam por prosa (`_handle_no_tool_response`) ou stall/wind-down, sem nenhum checkpoint de verificação.

**Mudança.** Mover o checkpoint para o **parada natural do loop** (quando o modelo responde sem tool call), keyed em **evidência observada**:
- Âncora: `_handle_no_tool_response` (orchestration.py:635) + `settle_agent_plan_on_final_answer` (service.py:1246).
- Antes de aceitar o fim em prosa: se a tarefa acumulou evidência de mutação (`has_file_change` incluindo o flag de F2) **e** não há verificação bem-sucedida para o change-set atual, injetar **um** nudge determinístico ("você mudou o workspace mas não rodou verificação; rode o check do projeto ou declare a limitação") e rodar mais um step. Bounded: **1 vez por turno**, depois fail-open (aceita a prosa e expõe a pendência como limitação — mesmo espírito do `CompletionGate` fail-open em completion.py:385).
- Reaproveitar a lógica de `_verification_status`/`_completion_context` já existente para decidir "falta verificação" — não duplicar.

**ADR / trade-offs.**
- *Decisão:* o runtime crava o checkpoint no ponto de parada, independente de `complete_task`.
- *Prós:* torna a trava efetiva para modelos que nunca chamam o meta-tool; 1 nudge, fail-open, nunca prende o turno.
- *Contras:* um round-trip extra ao fim de tarefas de mutação não verificadas. Aceitável e proporcional ao risco (mutação sem verificação é exatamente o que queremos pegar).
- *Interação:* NÃO alterar o caminho de `complete_task` — ele continua válido quando o modelo forte o chama. F3 é uma **segunda porta** para o mesmo gate, no parada natural. As duas convergem no mesmo `_completion_context`.
- *Alternativa descartada:* forçar `complete_task` via política (negar prosa até o modelo chamar). Rejeitada: prende modelo fraco em loop, e a sessão anterior já mostrou que "prosa como resposta final" é legítima em várias tarefas.

**Testes** (`tests/unit/test_orchestration_resilience.py`, 21 testes; `test_completion_gate.py`):
- Turno que edita arquivo e termina em prosa sem verificação → 1 nudge de verificação, depois aceita.
- Turno que edita + verifica + termina em prosa → aceita sem nudge.
- Tarefa read-only que termina em prosa → **nenhum** nudge (não regredir o comportamento de inspeção).
- O nudge é injetado no máximo 1×/turno.

**Riscos.** Não colidir com F1b (ambos mexem em "o que conta como tarefa de mutação"). Ordem sugerida: F1 e F2 primeiro (definem `has_file_change` corretamente), F3 depois (consome). Cuidado para o nudge não disparar em tarefa read-only — gate por `has_file_change`, não por label.

---

### F4 — Robustez de streaming  *(secundária, mas real)*

**Motivação.** Uma sessão teve **44 tool calls truncados mid-stream** (de 97 steps — quase metade). Já existe `_retry_interrupted_tool_call` (orchestration.py:788, máx. `_MAX_INTERRUPTED_CALL_RETRIES=2`), mas 44 num turno sugere que o retry não está segurando o caso, ou há instabilidade de provider/timeout.

**Investigação antes de mudar** (não presuma a causa):
- Ver quais tools truncam (provavelmente `write_file`/`edit_code` grandes) cruzando `tool.call.interrupted` com o tamanho do payload nos logs daquela sessão (`c7e68e6c605d`).
- Checar se é timeout de modelo (`model_timeout`) vs. corte real do provider. `config.json` do usuário tem `max_model_step_seconds=900` — generoso, então provavelmente é corte de stream, não timeout.

**Direções possíveis** (decidir após a investigação):
- Endurecer a instrução de `_interrupted_call_correction_text` (orchestration.py:844) para forçar chunking mais agressivo após o 1º corte.
- Se for write grande recorrente: fazer o `oversized_write_gap` (preconditions.py:121) disparar **antes** do corte, não depois.

**ADR / trade-offs.** *Baixa prioridade* — concentrado numa sessão, pode ser específico do provider/modelo. Não invista aqui antes de F1–F3 salvo se reproduzir facilmente.

**Testes.** `tests/unit/test_orchestration_resilience.py` já cobre o retry de call interrompida; estender se mudar a política.

---

### F5 — Superfície de ferramentas para modelo fraco  *(menor — só depois de F1)*

**Motivação.** Sempre 43 tools expostas + system prompt de ~18 KB. Para modelo fraco, conjunto focado por fase reduz erro mensuravelmente. Hoje `tool_policy="advisory"` (defaults.py:186) expõe tudo; o `_strict_allowed_tool_names` (policy.py:131) que focaria por fase é **código morto em produção**.

**Mudança.** Não ligar o strict cegamente (a sessão anterior documentou por que advisory existe: strict prende tarefa mal classificada, policy.py:106-118). **Só é seguro depois do F1** tornar a classificação confiável. Então: ou (a) ligar strict com o engajamento-por-evidência do F1 como rede, ou (b) manter tudo callable mas **destacar** os recomendados no `task_context_block` (já há a linha "Recommended tools now").

**ADR / trade-offs.** *Prós:* menos superfície = menos erro de modelo fraco. *Contras:* reintroduz o risco que o advisory resolveu. *Recomendação:* fazer (b) primeiro (baixo risco), medir, e só considerar (a) se o F1 provar a classificação robusta.

**Testes.** `tests/unit/test_planning.py` tem cobertura de política por fase — estender.

---

## 5. Ordem de execução recomendada

```
F1  (F1a + F1b)   ── causa-raiz; define corretamente "isto é tarefa de mutação"
  └─ F2           ── faz shell contar como mutação (alimenta has_file_change)
      └─ F3       ── consome has_file_change no parada natural do loop
F4                ── independente; fazer após investigar, ou adiar
F5                ── só depois de F1 provar classificação robusta
```

F1→F2→F3 são um **pacote coeso** (a "definição de mutação observada" flui entre eles) e valem um único ADR no histórico. F4 e F5 são independentes e adiáveis.

**Sugestão de commits:** um por frente (F1a, F1b, F2, F3), cada um com testes no mesmo commit. Mensagens no estilo do repo: `fix(orchestration): ...` / `feat(planning): ...`.

---

## 6. Estratégia de teste e verificação

- **Suíte:** `pytest` (config em `pyproject.toml`, `testpaths=["tests"]`, `pytest-asyncio`). Rodar o subconjunto relevante durante o desenvolvimento:
  ```bash
  python -m pytest tests/unit/test_planning.py tests/unit/test_completion_gate.py tests/unit/test_orchestration_resilience.py -q
  ```
- **Regressão-chave a não quebrar:** tarefa read-only que termina em prosa **não** deve exigir verificação nem escrever arquivo (é a razão de metade das salvaguardas atuais existirem — ver `_task_produces_workspace_effects` e o "READ-ONLY TASK" em service.py:741). Todo teste de F1b/F3 precisa provar que read-only continua limpo.
- **Validação end-to-end (opcional, alto valor):** re-rodar a forense da seção 2 depois das mudanças, num workload shell-driven, e confirmar: bloco do planner presente >90% dos steps de mutação; `has_file_change=True` quando só houve shell; ≥1 verificação exigida por tarefa de mutação.
- Rodar `ruff` (há `.ruff_cache`) para manter o estilo.

---

## 7. O que explicitamente NÃO fazer

- **Não** limitar writes por model step — os dados mostram batches de 1 call; seria no-op com custo de complexidade.
- **Não** transformar os guards fail-open (stall, precondition) em travas duras — estão saudáveis (8 e 7 fires distintos) e a filosofia "1 nudge, fail-open" é o que impede prender modelo fraco.
- **Não** copiar o loop plano do Claude Code — ele depende de modelo forte; nosso alvo é o oposto.
- **Não** adicionar RAG para skills — a sessão anterior já concluiu que o gargalo é enforcement/atenção, não recuperação (o candidato já está no contexto).
- **Não** introduzir chamada de modelo síncrona extra no caminho crítico do turno (a restrição de 1-modelo-por-turno; ver `_cancel_pending_learning`).
- **Não** re-classificar continuação do zero (é o bug do F1a).

---

## 8. Referências rápidas (âncoras)

| O quê | Local |
|---|---|
| Loop do turno | `orchestration.py:570` (`_run_model_loop`) |
| Parada natural (sem tool call) | `orchestration.py:635` (`_handle_no_tool_response`) |
| Batch de tools | `orchestration.py:1300` (`_execute_tool_batch`) |
| Classificador | `models.py:132` (`from_user_text`), `:636` (`_is_mutation_request`) |
| Início de turno / resume | `service.py:226` (`begin_turn`), `:273` (`_resume_turn`) |
| Bloco de contexto | `service.py:625` (`task_context_block`) |
| "Tarefa produz efeito no workspace?" | `service.py:755` (`_task_produces_workspace_effects`) |
| Snapshot de conclusão | `service.py:923` (`_completion_context`) |
| Settle em prosa | `service.py:1246` (`settle_agent_plan_on_final_answer`) |
| Evidência de `execute_command` | `evidence.py:415` |
| Políticas de conclusão | `completion.py` (`select_policy:330`, `changes_require_verification:53`) |
| Política de tools por fase | `policy.py:131` (`_strict_allowed_tool_names`, hoje morto) |
| Nudges advisory | `preconditions.py` (`oversized_write_gap:121`) |
| Defaults do planner | `defaults.py:186` (`tool_policy="advisory"`) |

Memórias relacionadas: `orchestration-forensics-2026-07`, `claude-code-orchestration-model`, `code-ai-project-architecture`, `plan-completion-on-prose-answer`.
