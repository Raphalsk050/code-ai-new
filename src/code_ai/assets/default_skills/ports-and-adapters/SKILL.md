---
name: ports-and-adapters
description: Guia para implementar Ports & Adapters (Hexagonal): definir interfaces estáveis no core e conectar sistemas externos através de adapters.
---

# Ports & Adapters (Arquitetura Hexagonal)

## Propósito
Isolar o núcleo do domínio (core) de concerns externos (DB, HTTP, APIs, UI) através de interfaces bem definidas (ports) e implementações especializadas (adapters).

## Conceitos fundamentais

```
                    ┌─────────────────────────────┐
                    │      Driving Adapters       │
                    │  (HTTP, CLI, GraphQL, UI)   │
                    └──────────────┬──────────────┘
                                   │ Input Ports
                                   ▼
┌─────────────────────────────────────────────────────────┐
│                   APPLICATION CORE                       │
│  ┌─────────────┐         ┌─────────────────────────┐    │
│  │   Use Cases │         │      Domain Entities    │    │
│  │  (Services) │         │    (Business Logic)     │    │
│  └─────────────┘         └─────────────────────────┘    │
│                                                           │
│  ┌─────────────┐         ┌─────────────────────────┐    │
│  │ Input Ports │         │     Output Ports        │    │
│  │  (Traits)   │         │     (Traits)            │    │
│  └─────────────┘         └─────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
                                   │ Output Ports
                                   ▼
                    ┌─────────────────────────────┐
                    │    Driven Adapters          │
                    │  (DB, External APIs, FS)    │
                    └─────────────────────────────┘
```

## Terminologia

| Termo | Descrição | Exemplo |
|-------|-----------|---------|
| **Core** | Lógica de negócio pura, sem dependências externas | Entities, Use Cases |
| **Port** | Interface que define um contrato | `trait UserRepository` |
| **Driving Adapter** | Adapta entrada externa para o core | Controller HTTP, CLI command |
| **Driven Adapter** | Adapta o core para saída externa | Repository SQL, API client |
| **Input Port** | Como o core recebe comandos | `trait CreateUserUseCase` |
| **Output Port** | Como o core acessa infraestrutura | `trait NotificationGateway` |

## Como implementar

### Passo 1: Defina o Core (Domínio)

```rust
// domain/user.rs
pub struct User {
    id: UserId,
    email: Email,
    // ... sem dependências externas
}

// domain/user_repository.rs (Output Port)
pub trait UserRepository {
    fn find_by_id(&self, id: &UserId) -> Result<Option<User>>;
    fn save(&self, user: &User) -> Result<()>;
}

// domain/create_user.rs (Use Case / Input Port)
pub trait CreateUserUseCase {
    fn execute(&self, cmd: CreateUserCommand) -> Result<User>;
}
```

### Passo 2: Implemente Use Cases

```rust
// application/create_user.rs
pub struct CreateUserService<R: UserRepository> {
    repo: R,
}

impl<R: UserRepository> CreateUserUseCase for CreateUserService<R> {
    fn execute(&self, cmd: CreateUserCommand) -> Result<User> {
        // Regra de negócio pura
        let user = User::create(cmd.email)?;
        self.repo.save(&user)?;
        Ok(user)
    }
}
```

### Passo 3: Crie Adapters de Infraestrutura

```rust
// infrastructure/sqlx_user_repository.rs
pub struct SqlxUserRepository {
    pool: PgPool,
}

impl UserRepository for SqlxUserRepository {
    fn find_by_id(&self, id: &UserId) -> Result<Option<User>> {
        // Implementação com SQL
    }
}

// infrastructure/in_memory_user_repository.rs
pub struct InMemoryUserRepository {
    users: DashMap<UserId, User>,
}

impl UserRepository for InMemoryUserRepository {
    fn find_by_id(&self, id: &UserId) -> Result<Option<User>> {
        // Implementação em memória (para testes)
    }
}
```

### Passo 4: Crie Driving Adapters (API)

```rust
// api/http/user_controller.rs
pub struct UserController {
    use_case: Box<dyn CreateUserUseCase>,
}

async fn create_user(
    State(controller): State<UserController>,
    Json(req): Json<CreateUserRequest>,
) -> Json<UserResponse> {
    let cmd = CreateUserCommand { email: req.email };
    let user = controller.use_case.execute(cmd).await?;
    Json(user.into())
}
```

### Passo 5: Compose na raiz (main.rs)

```rust
#[tokio::main]
async fn main() {
    let pool = PgPool::connect(&config.db_url).await?;
    let repo = SqlxUserRepository::new(pool);
    let use_case = CreateUserService::new(repo);
    let controller = UserController::new(Box::new(use_case));
    
    // Start server com controller injetado
}
```

## Benefícios

| Benefício | Descrição |
|-----------|-----------|
| **Testabilidade** | Core testável sem DB/HTTP usando in-memory adapters |
| **Swap de infra** | Troca DB, API, UI sem tocar no core |
| **Parallel development** | Time de domínio e infra trabalham independentemente |
| **Legacy integration** | Adapters podem envolver sistemas legados |
| **Tecnologia agnóstico** | Core não sabe qual DB/framework está usando |

## Checklist de implementação

- [ ] Domínio não importa crates de infra (sqlx, reqwest, axum)
- [ ] Todos os acessos externos passam por traits (ports)
- [ ] Adapters vivem em crates/módulos separados
- [ ] Use cases são testáveis com mocks/fakes
- [ ] Composição (wiring) acontece apenas na raiz (main.rs)
- [ ] É possível rodar testes do core sem banco de dados

## Sinais de violação

- ❌ `use sqlx::` dentro de entities ou use cases
- ❌ Controllers chamando queries SQL diretamente
- ❌ Domínio conhecendo detalhes de HTTP/JSON
- ❌ Dificuldade de criar fake para testes
- ❌ Testes de unidade exigem banco de dados

## Padrões comuns de Ports

### Output Ports (Driven)
- `UserRepository` — persistência de usuários
- `NotificationGateway` — envio de emails/SMS
- `PaymentGateway` — integração com gateway de pagamento
- `FileSystem` — acesso a arquivos
- `Clock` — abstração de tempo (para testes determinísticos)

### Input Ports (Driving)
- `CreateUserUseCase` — caso de uso de criação
- `AuthenticateUserUseCase` — caso de uso de autenticação
- `ProcessOrderUseCase` — caso de uso de processamento
