---
name: dependency-rule
description: Guia para aplicar a Dependency Rule: dependências devem sempre apontar para dentro (do infraestrutura/frameworks para o domínio).
---

# Dependency Rule

## Propósito
Garantir que as dependências do sistema sempre apontem para dentro, do infraestrutura e frameworks em direção ao domínio core, nunca o contrário.

## Regra fundamental
```
❌ ERRADO: Domain → Infrastructure
✅ CERTO: Infrastructure → Domain
```

## Direção das dependências

```
          ┌──────────────┐
          │   Frameworks │  ← Pode depender de tudo abaixo
          │   & Drivers  │
          ├──────────────┤
          │  Interfaces  │  ← Depende apenas da camada interna
          │   (Adapters) │
          ├──────────────┤
          │  Application │  ← Depende apenas do Domain
          │   Services   │
          ├──────────────┤
          │    Domain    │  ← NÃO DEPENDE de nada externo
          │   Entities   │
          └──────────────┘
```

## Como aplicar

### 1. Identifique o core do domínio
- Quais são as entities e regras que nunca mudariam mesmo se você trocasse o banco, o framework web, ou a UI?
- Esse é seu domínio core — ele não deve ter dependências externas.

### 2. Defina interfaces no core
```rust
// ✅ CERTO: Interface definida no domínio
pub trait UserRepository {
    fn find_by_id(&self, id: UserId) -> Result<Option<User>>;
    fn save(&self, user: &User) -> Result<()>;
}

// ❌ ERRADO: Domain dependendo de infraestrutura
use sqlx::PgPool;
pub struct User {
    // ...
}
impl User {
    pub async fn save(&self, pool: &PgPool) { }  // Domain → Infra
}
```

### 3. Implemente adapters na infraestrutura
```rust
// ✅ Infraestrutura dependendo do domínio
pub struct SqlxUserRepository {
    pool: PgPool,
}

impl UserRepository for SqlxUserRepository {
    // Implementação com detalhes de DB
}
```

## Verificação

### Perguntas de validação
- [ ] O domínio importa algum pacote de banco de dados?
- [ ] O domínio conhece detalhes de HTTP/API?
- [ ] É possível rodar testes do domínio sem infraestrutura?
- [ ] Se trocar o banco de dados, o domínio precisa mudar?

### Sinais de violação
- `use sqlx::` ou `use diesel::` dentro de entities
- `use reqwest::` ou `use axum::` dentro de regras de negócio
- Entities com atributos de ORM (`#[table]`, `#[column]`)
- Dificuldade de criar fakes/stubs para testes

## Ação corretiva

1. **Identifique a dependência invertida**: O que no domínio está importando infraestrutura?
2. **Extraia para interface**: Crie um trait/interface no domínio
3. **Mova implementação**: A implementação concreta fica na camada de infraestrutura
4. **Injete dependência**: Use injeção de dependência para conectar

## Benefícios
- **Testabilidade**: Domain testável sem DB/HTTP
- **Swap de infraestrutura**: Troca DB, API, UI sem tocar no core
- **Evolução independente**: Camadas mudam em ritmos diferentes
- **Clareza arquitetural**: Fácil entender onde cada coisa vive
