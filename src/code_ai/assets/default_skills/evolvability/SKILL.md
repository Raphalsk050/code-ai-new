---
name: evolvability
description: Guia para projetar sistemas evolutivos: absorver mudanças (novos tipos, providers, transports) atrás de interfaces estáveis; preferir composição sobre herança.
---

# Evolvability - Projetando Sistemas Evolutivos

## Propósito

Garantir que o código possa absorver mudanças futuras sem reescrita significativa, mantendo o acoplamento baixo e a coesão alta.

## Princípios Fundamentais

### 1. Interfaces Estáveis nos Boundaries

- Defina interfaces claras nos pontos de extensão provável
- Exemplos comuns: novos tipos de nós, novos providers de LLM, novos transportes
- O core do sistema depende apenas das interfaces, não das implementações

### 2. Composição sobre Herança

- Prefira composição para estender comportamento
- Herança tende a criar acoplamento rígido e dificultar mudanças
- Use traits/interfaces para definir capacidades, não hierarquias profundas

### 3. Baixo Acoplamento

- Módulos devem conhecer o mínimo possível sobre outros módulos
- Dependencies apontam para dentro (em direção ao domínio)
- Mudanças em um módulo não devem exigir mudanças em outros

### 4. Alta Coesão

- Lógica relacionada fica junta
- Cada módulo tem uma razão clara para existir e uma razão para mudar
- Evite "god modules" que fazem tudo

## Aplicação Prática

### Ao criar um novo módulo:

1. **Identifique pontos de variação**: O que pode mudar no futuro?
2. **Defina interfaces antes de implementar**: Quais contratos são necessários?
3. **Isole detalhes técnicos**: DB, HTTP, APIs externas ficam em adapters
4. **Injete dependências**: Não instancie concretos diretamente no core

### Sinais de Baixa Evolvabilidade:

- [ ] Duplicação de lógica similar em vários lugares
- [ ] Condicionais espalhados verificando tipos/providers
- [ ] Dificuldade para adicionar novo tipo sem modificar código existente
- [ ] Testes exigem setup elaborado de infraestrutura
- [ ] Mudança em um módulo quebra outros não relacionados

## Exemplo de Estrutura Evolutiva

```
core/           # Domínio puro, interfaces estáveis
  entities/
  ports/        # Interfaces que adapters implementam
  services/     # Lógica de negócio

adapters/       # Implementações concretas, fáceis de trocar
  database/
  llm_providers/
  transports/
```

## Quando Usar

- Sistemas que devem suportar múltiplos providers/backends
- Domínios com regras de negócio complexas e mutáveis
- Projetos que serão mantidos/expandidos por longo prazo
- Quando há incerteza sobre requisitos futuros

## Relacionado

- `ports-and-adapters`: Padrão estrutural para implementar evolvability
- `solid-principles`: Princípios que sustentam design evolutivo
- `separation-of-concerns`: Base para coesão e acoplamento adequados
