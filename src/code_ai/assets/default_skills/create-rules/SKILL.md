---
name: create-rules
description: Guia para escrever boas rules (regras obrigatórias que o agente sempre segue) e gravá-las com o tool create_rule, escolhendo entre escopo global e de projeto.
---

# Create Rules

## Propósito

Ajudar o desenvolvedor a escrever **rules**: regras curtas e obrigatórias que o
agente sempre carrega e nunca esquece.
Diferente de uma skill (que é carregada sob demanda com `use_skill`), uma rule é
injetada no system prompt em toda sessão e tratada como vinculante.

Use esta skill quando o dev pedir algo como "crie uma regra", "o agente deve sempre
X", "nunca faça Y", ou quando você perceber uma convenção durável que precisa valer
em todas as interações.

## Onde a rule vive (escopo)

| Escopo | Local | Quando usar |
|--------|-------|-------------|
| `project` | `<workspace>/.code-ai/rules/` | Convenções daquele repositório, versionadas no git e compartilhadas com o time (ex.: "use pytest -q", "nunca edite arquivos em generated/"). É o padrão. |
| `global` | `~/.code-ai/rules/` | Preferências pessoais do dev que valem em todo projeto (ex.: "responda em pt-BR", "nunca adicione co-author de IA em commits"). |

Na dúvida, prefira `project`: a regra viaja com o código e beneficia quem clonar o repo.

## Como escrever uma boa rule

- **Imperativa e direta.** Escreva como uma instrução ("Sempre rode os testes antes
  de concluir", "Nunca faça commit direto na main"), não como uma observação.
- **Uma responsabilidade por arquivo.** Cada rule cobre um tema. Várias regras pequenas
  e nomeadas são melhores que um arquivo gigante.
- **Concisa.** Poucas linhas. Se precisar de muito contexto e exemplos, provavelmente é
  uma skill, não uma rule.
- **Verificável.** O dev (e o agente) deve conseguir dizer objetivamente se a regra foi
  ou não seguida.
- **Explique o porquê quando não for óbvio.** Uma linha de motivação ajuda o agente a
  aplicar a regra em situações novas.
- **Não duplique.** Antes de criar, verifique as rules existentes; se já houver uma
  parecida, edite-a em vez de criar outra.

## Formato

Cada rule é um arquivo markdown com frontmatter mínimo:

```markdown
---
name: run-tests-before-done
description: Sempre verificar antes de concluir mudanças de código.
---

Sempre rode os testes do projeto e confirme que passam antes de marcar uma tarefa
de código como concluída. Se não houver testes, diga isso explicitamente.
```

## Passo a passo

1. Esclareça com o dev **qual** comportamento deve ser obrigatório e **por quê**.
2. Decida o **escopo** (project vs global) pela tabela acima.
3. Escolha um **nome-slug** curto e descritivo (letras minúsculas, dígitos, `-`/`_`).
4. Chame o tool `create_rule` com `name`, `description` (uma linha), `content` (o corpo
   da regra) e `scope`. Use `overwrite=true` apenas para substituir uma regra existente.
5. Confirme ao dev onde a rule foi gravada e que ela passará a valer nas próximas sessões.

## Anti-padrões

- ❌ Regras vagas ("escreva código bom") - não são verificáveis.
- ❌ Despejar um tutorial inteiro numa rule - vire skill.
- ❌ Duplicar uma preferência que já existe como memória ou rule.
- ❌ Colocar segredos, tokens ou caminhos absolutos sensíveis numa rule versionada.
