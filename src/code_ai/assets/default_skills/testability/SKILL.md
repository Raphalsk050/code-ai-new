---
name: testability
description: Guia para projetar código testável: isolar testes unitários, injetar dependências via interfaces, e criar fakes/stubs sem scaffolding elaborado.
---

# Testability (Testabilidade)

## Propósito
Projetar código que possa ser testado em isolamento, sem scaffolding elaborado, permitindo testes unitários rápidos, determinísticos e focados em uma única intenção.

## Princípios fundamentais

### 1. Testes unitários testam UMA coisa
```rust
// ❌ ERRADO: Teste com múltiplas intenções
#[test]
fn test_user_creation_and_email_and_logging() {
    // Cria usuário
    // Envia email
    // Loga ação
    // Atualiza estatísticas
    // ... 50 linhas de asserts
}

// ✅ CERTO: Um teste por intenção
#[test]
fn test_user_creation_validates_email() { }
#[test]
fn test_user_creation_persists_user() { }
#[test]
fn test_user_creation_sends_welcome_email() { }
#[test]
fn test_user_creation_logs_audit_event() { }
```

### 2. Dependências são injetadas, não criadas internamente
```rust
// ❌ ERRADO: Dependência hard-coded
pub struct UserService {
    // Difícil de testar sem DB real
}

impl UserService {
    pub fn new() -> Self {
        let pool = PgPool::connect("postgres://...").await?;
        Self { db: SqlxRepository::new(pool) }
    }
}

// ✅ CERTO: Dependência injetada
pub struct UserService<R: UserRepository> {
    repo: R,
}

impl<R: UserRepository> UserService<R> {
    pub fn new(repo: R) -> Self {
        Self { repo }
    }
}

// Teste com fake
#[test]
fn test_create_user() {
    let fake_repo = InMemoryUserRepository::new();
    let service = UserService::new(fake_repo);
    // Testa sem DB
}
```

### 3. Interfaces definem contratos testáveis
```rust
// Trait define o contrato
pub trait UserRepository {
    fn find_by_id(&self, id: &UserId) -> Result<Option<User>>;
    fn save(&self, user: &User) -> Result<()>;
}

// Implementação real (infraestrutura)
pub struct SqlxUserRepository { /* ... */ }
impl UserRepository for SqlxUserRepository { /* ... */ }

// Fake para testes
pub struct FakeUserRepository {
    users: HashMap<UserId, User>,
}
impl UserRepository for FakeUserRepository {
    fn find_by_id(&self, id: &UserId) -> Result<Option<User>> {
        Ok(self.users.get(id).cloned())
    }
    fn save(&self, user: &User) -> Result<()> {
        // Simples, sem DB
        Ok(())
    }
}
```

## Padrões de testes

### Arrange-Act-Assert (AAA)
```rust
#[test]
fn test_create_user_with_invalid_email() {
    // Arrange
    let fake_repo = InMemoryUserRepository::new();
    let service = UserService::new(fake_repo);
    let cmd = CreateUserCommand {
        email: "invalid-email".to_string(),
    };

    // Act
    let result = service.create_user(cmd);

    // Assert
    assert!(result.is_err());
    assert!(matches!(result.unwrap_err(), Error::InvalidEmail));
}
```

### Test Doubles (Fakes, Mocks, Stubs)

| Tipo | Propósito | Exemplo |
|------|-----------|---------|
| **Fake** | Implementação simplificada mas funcional | `InMemoryRepository` |
| **Stub** | Retorna dados pré-definidos | `AlwaysReturnsUserStub` |
| **Mock** | Verifica interações/chamadas | `MockEmailSender` |
| **Spy** | Registra chamadas para verificação posterior | `LoggingEmailSender` |

### Exemplo: Fake Repository
```rust
pub struct FakeUserRepository {
    users: DashMap<UserId, User>,
    next_id: AtomicU64,
}

impl FakeUserRepository {
    pub fn new() -> Self {
        Self {
            users: DashMap::new(),
            next_id: AtomicU64::new(1),
        }
    }
    
    pub fn with_user(mut self, user: User) -> Self {
        self.users.insert(user.id.clone(), user);
        self
    }
}

impl UserRepository for FakeUserRepository {
    fn find_by_id(&self, id: &UserId) -> Result<Option<User>> {
        Ok(self.users.get(id).map(|r| r.clone()))
    }
    
    fn save(&self, user: &User) -> Result<()> {
        self.users.insert(user.id.clone(), user.clone());
        Ok(())
    }
}
```

### Exemplo: Mock para verificação de interações
```rust
pub struct MockEmailSender {
    sent_emails: Mutex<Vec<Email>>,
}

impl MockEmailSender {
    pub fn new() -> Self {
        Self { sent_emails: Mutex::new(Vec::new()) }
    }
    
    pub fn assert_email_sent_to(&self, expected: &str) {
        let emails = self.sent_emails.lock().unwrap();
        assert!(emails.iter().any(|e| e.to == expected));
    }
}

impl EmailSender for MockEmailSender {
    fn send(&self, email: Email) -> Result<()> {
        self.sent_emails.lock().unwrap().push(email);
        Ok(())
    }
}

#[test]
fn test_welcome_email_sent() {
    let mock_sender = MockEmailSender::new();
    let service = UserService::new(mock_sender.clone());
    
    service.register_user("test@example.com").unwrap();
    
    mock_sender.assert_email_sent_to("test@example.com");
}
```

## Isolando testes de infraestrutura

### ❌ ERRADO: Teste que precisa de DB
```rust
#[tokio::test]
async fn test_create_user() {
    // Precisa de banco rodando
    let pool = PgPool::connect("postgres://localhost/test").await?;
    
    // Lento, frágil, não isolado
    let user = service.create_user(cmd).await?;
    
    let found = sqlx::query!("SELECT * FROM users WHERE id = ?", user.id)
        .fetch_one(&pool)
        .await?;
    
    assert_eq!(found.email, cmd.email);
}
```

### ✅ CERTO: Teste puramente em memória
```rust
#[test]
fn test_create_user() {
    let fake_repo = FakeUserRepository::new();
    let fake_email = FakeEmailSender::new();
    let service = UserService::new(fake_repo, fake_email);
    
    let user = service.create_user(cmd)?;
    
    assert_eq!(user.email, cmd.email);
    assert!(user.id.is_some());
    // Rápido, determinístico, isolado
}
```

### Testes de integração (quando necessário)
```rust
// Teste de integração: marcado separadamente
#[tokio::test]
#[ignore] // Só roda quando explicitamente pedido
async fn integration_test_create_user_with_real_db() {
    // Usa Testcontainers ou DB de teste
    // Valida queries SQL, migrations, etc.
}
```

## Checklist de testabilidade

- [ ] Cada teste verifica UMA coisa apenas
- [ ] Tests não dependem de infraestrutura (DB, HTTP, FS)
- [ ] Dependências externas são injetadas via traits
- [ ] É possível criar fakes/stubs sem scaffolding complexo
- [ ] Tests são rápidos (< 100ms para unitários)
- [ ] Tests são determinísticos (mesmo input = mesmo output)
- [ ] Tests não dependem de ordem de execução
- [ ] Tests não compartilham estado mutável entre si

## Sinais de código não-testável

- ❌ `new()` que conecta a DB/API
- ❌ Funções que acessam `std::env::var()` diretamente
- ❌ Uso de `std::time::SystemTime::now()` sem abstração
- ❌ Singleton global mutável
- ❌ Testes que precisam rodar em ordem específica
- ❌ Testes que falham intermitentemente ("flaky")
- ❌ Dificuldade de testar sem subir Docker containers

## Abstrações para testabilidade

### Clock abstraction (tempo)
```rust
pub trait Clock {
    fn now(&self) -> DateTime<Utc>;
}

pub struct SystemClock;
impl Clock for SystemClock {
    fn now(&self) -> DateTime<Utc> { Utc::now() }
}

pub struct FakeClock {
    current: Mutex<DateTime<Utc>>,
}
impl Clock for FakeClock {
    fn now(&self) -> DateTime<Utc> { *self.current.lock().unwrap() }
}

// Teste determinístico
#[test]
fn test_expires_in_24h() {
    let fake_clock = FakeClock::new(DateTime::parse("2024-01-01T00:00:00Z"));
    let token = Token::create(fake_clock);
    assert_eq!(token.expires_at, DateTime::parse("2024-01-02T00:00:00Z"));
}
```

### Random abstraction (aleatoriedade)
```rust
pub trait RandomGenerator {
    fn generate_uuid(&self) -> Uuid;
}

pub struct SystemRandom;
impl RandomGenerator for SystemRandom {
    fn generate_uuid(&self) -> Uuid { Uuid::new_v4() }
}

pub struct FakeRandom {
    next_uuid: Uuid,
}
impl RandomGenerator for FakeRandom {
    fn generate_uuid(&self) -> Uuid { self.next_uuid }
}
```

## Pirâmide de testes

```
              /\
             /  \      E2E Tests (lentos, caros, poucos)
            /----\
           /      \    Integration Tests (médio, alguns)
          /--------\
         /          \   Unit Tests (rápidos, baratos, muitos)
        /------------\
```

| Tipo | Velocidade | Custo | Quantidade |
|------|------------|-------|------------|
| Unit | < 100ms | Baixo | 70-80% |
| Integration | 100ms - 1s | Médio | 15-25% |
| E2E | > 1s | Alto | 5-10% |

## Benefícios

| Benefício | Descrição |
|-----------|-----------|
| **Feedback rápido** | Tests unitários rodam em segundos |
| **Refatoração segura** | Tests pegam regressões antes de deploy |
| **Documentação viva** | Tests mostram como o código deve ser usado |
| **Design melhor** | Código testável é naturalmente mais desacoplado |
| **Confiança** | Deploy com confiança sabendo que tests cobrem o critical path |
