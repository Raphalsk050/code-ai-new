---
name: separation-of-concerns
description: Guia para aplicar separação de concerns: isolar lógica de negócio de concerns técnicos e garantir que cada módulo tenha uma única responsabilidade.
---

# Separation of Concerns (SoC)

## Propósito
Garantir que cada módulo, classe ou função tenha uma única responsabilidade e um único motivo para mudar, isolando lógica de negócio de concerns técnicos.

## Quando usar
- Ao criar novos módulos ou componentes
- Ao refatorar código com lógica misturada
- Ao revisar arquitetura de um sistema

## Princípios

### 1. Identifique os concerns
- **Business logic**: regras de domínio, validações de negócio
- **Technical concerns**: banco de dados, HTTP, UI, logging, autenticação

### 2. Isole em camadas
```
┌─────────────────────────┐
│   UI / API Layer        │  ← Concern técnico de entrega
├─────────────────────────┤
│   Application Layer     │  ← Orquestração de casos de uso
├─────────────────────────┤
│   Domain Layer          │  ← Lógica de negócio pura
├─────────────────────────┤
│   Infrastructure Layer  │  ← DB, HTTP, external services
└─────────────────────────┘
```

### 3. Verifique os limites
- [ ] Módulos técnicos não contêm regras de negócio
- [ ] Lógica de negócio não depende de frameworks
- [ ] Cada função faz uma coisa bem feita
- [ ] Mudanças em um concern não afetam outros

## Sinais de violação
- Funções que acessam DB e aplicam regras de negócio no mesmo lugar
- Controllers com lógica complexa de domínio
- Entities que conhecem detalhes de persistência
- Dificuldade de testar lógica de negócio isoladamente

## Ação corretiva
1. Identifique o concern misturado
2. Extraia a lógica para um módulo dedicado
3. Defina interfaces claras entre os concerns
4. Garanta que dependências apontem para o domínio

## Checklist de revisão
- [ ] Cada módulo tem um propósito claro e único
- [ ] É possível explicar o que cada camada faz em uma frase
- [ ] Testes unitários podem isolar a lógica de negócio
- [ ] Mudanças técnicas não exigem alterar regras de negócio
