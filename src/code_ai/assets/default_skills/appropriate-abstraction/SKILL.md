---
name: appropriate-abstraction
description: Guia para aplicar appropriate abstraction: evitar over-engineering (indireção desnecessária) e under-structuring (lógica duplicada, god objects).
---

# Appropriate Abstraction

## Propósito
Encontrar o nível correto de abstração para cada problema: nem over-engineering (indireção desnecessária, generalização prematura) nem under-structuring (lógica duplicada, god objects, estado vazado).

## Princípio fundamental

> **Abstrações devem ser descobertas, não antecipadas.**
> 
> A abstração correta emerge após você entender o padrão de mudanças, não antes.

---

## Over-engineering (excesso de abstração)

### Sinais de alerta

- ❌ Interfaces criadas "caso um dia precise"
- ❌ Múltiplas camadas de indirection sem necessidade atual
- ❌ Generics excessivos que dificultam leitura
- ❌ Patterns aplicados sem problema real para resolver
- ❌ Código que tenta prever todos os cenários futuros

### ❌ Exemplo: Abstração prematura

```rust
// ❌ ERRADO: Generalização sem necessidade
pub trait Entity<T: Identifier> {
    fn id(&self) -> &T;
    fn created_at(&self) -> DateTime<Utc>;
    fn updated_at(&self) -> DateTime<Utc>;
}

pub trait Repository<T: Entity<I>, I: Identifier> {
    fn find(&self, id: &I) -> Result<Option<T>>;
    fn save(&self, entity: &T) -> Result<()>;
    fn delete(&self, id: &I) -> Result<()>;
}

pub trait Service<T: Entity<I>, I: Identifier, R: Repository<T, I>> {
    fn create(&self, cmd: CreateCommand) -> Result<T>;
    fn update(&self, id: &I, cmd: UpdateCommand) -> Result<T>;
    fn delete(&self, id: &I) -> Result<()>;
}

// Tudo isso para uma entidade User que nunca vai ter "siblings"
```

### ✅ Exemplo: Abstração quando necessário

```rust
// ✅ CERTO: Comece simples
pub struct User {
    pub id: UserId,
    pub email: String,
    pub created_at: DateTime<Utc>,
}

pub trait UserRepository {
    fn find_by_id(&self, id: &UserId) -> Result<Option<User>>;
    fn save(&self, user: &User) -> Result<()>;
}

// Quando surgir uma segunda entidade com padrão similar,
// AÍ você considera extrair um trait genérico
```

### Regra: Three Times Rule

> **Abrace a duplicação até o terceiro exemplo.**

1. **Primeira vez**: Implemente concretamente
2. **Segunda vez**: Note o padrão, mas ainda copie
3. **Terceira vez**: Agora extraia a abstração

```rust
// 1º: UserService com UserRepository
pub struct UserService {
    repo: UserRepo,
}

// 2º: ProductService com ProductRepository
pub struct ProductService {
    repo: ProductRepo,
}

// Note o padrão...

// 3º: OrderService com OrderRepository
// AGORA sim extraia:
pub trait Service<Repo> {
    fn new(repo: Repo) -> Self;
}
```

---

## Under-structuring (falta de estrutura)

### Sinais de alerta

- ❌ God objects (classes/módulos que fazem tudo)
- ❌ Lógica duplicada em vários lugares
- ❌ Estado mutável vazado através de APIs
- ❌ Funções com 50+ linhas fazendo múltiplas coisas
- ❌ Dificuldade de encontrar onde uma regra está implementada

### ❌ Exemplo: God object

```rust
// ❌ ERRADO: UserFacade faz tudo
pub struct UserFacade {
    db: PgPool,
    redis: RedisClient,
    email: EmailClient,
    s3: S3Client,
    logger: Logger,
    metrics: Metrics,
}

impl UserFacade {
    pub async fn register_user(&self, data: UserData) -> Result<User> {
        // Valida email
        // Hash password
        // Salva no DB
        // Cria cache no Redis
        // Envia email de boas-vindas
        // Upload de avatar no S3
        // Loga auditoria
        // Envia métricas
        // ... 200 linhas depois
    }
}
```

### ✅ Exemplo: Separação adequada

```rust
// ✅ CERTO: Responsabilidades separadas
pub struct UserValidator;
impl UserValidator {
    pub fn validate_email(email: &str) -> Result<Email> { }
    pub fn validate_password(password: &str) -> Result<Password> { }
}

pub trait UserRepository {
    fn save(&self, user: &User) -> Result<()>;
}

pub trait EmailSender {
    fn send_welcome(&self, user: &User) -> Result<()>;
}

pub struct UserService<R: UserRepository, E: EmailSender> {
    repo: R,
    email: E,
}

impl<R, E> UserService<R, E> {
    pub fn register(&self, cmd: RegisterCommand) -> Result<User> {
        let email = UserValidator::validate_email(&cmd.email)?;
        let password = UserValidator::validate_password(&cmd.password)?;
        let user = User::new(email, password);
        
        self.repo.save(&user)?;
        self.email.send_welcome(&user)?;
        
        Ok(user)
    }
}
```

---

## Guia de decisão: Quando abstrair?

### ✅ Abstraia quando...

| Situação | Ação |
|----------|------|
| Duplicação em 3+ lugares | Extraia função/módulo compartilhado |
| Troca de implementação é necessária | Crie trait/interface |
| Teste requer setup complexo | Injete dependência via interface |
| Múltiplos times esperam contrato estável | Defina interface pública clara |
| Implementação pode variar por contexto | Use estratégia via trait |

### ❌ Não abstraia quando...

| Situação | Ação |
|----------|------|
| "Pode ser útil no futuro" | Espere a necessidade real |
| Seguir "best practice" cegamente | Avalie o contexto real |
| Tornar código "mais clean" sem benefício | Simplicidade > elegância |
| Adicionar generics "porque sim" | Concretude > generalidade |

---

## Níveis de abstração em Rust

### Nível 1: Concreto (comece aqui)
```rust
pub struct EmailService {
    smtp_host: String,
    smtp_port: u16,
}

impl EmailService {
    pub fn send(&self, to: &str, body: &str) -> Result<()> {
        // Implementação direta
    }
}
```

### Nível 2: Trait para testabilidade (quando precisar testar)
```rust
pub trait EmailSender {
    fn send(&self, to: &str, body: &str) -> Result<()>;
}

pub struct SmtpEmailService { /* impl EmailSender */ }
pub struct FakeEmailService { /* impl EmailSender */ }
```

### Nível 3: Trait genérico (apenas se houver padrão real)
```rust
pub trait NotificationChannel {
    type Target;
    fn send(&self, to: Self::Target, body: &str) -> Result<()>;
}

impl NotificationChannel for EmailChannel { /* ... */ }
impl NotificationChannel for SMSChannel { /* ... */ }
impl NotificationChannel for PushChannel { /* ... */ }
```

---

## Checklist de appropriate abstraction

### Evitando over-engineering
- [ ] Cada abstração resolve um problema atual, não hipotético
- [ ] Não há traits/interfaces sem múltiplas implementações reais
- [ ] Generics são usados apenas quando há benefício real
- [ ] O código é legível sem precisar navegar 5 arquivos
- [ ] Novos desenvolvedores entendem o fluxo sem mapa mental complexo

### Evitando under-structuring
- [ ] Não há duplicação de lógica de negócio
- [ ] Módulos têm tamanho razoável (< 300 linhas)
- [ ] Estado não é exposto desnecessariamente
- [ ] Funções fazem uma coisa bem definida
- [ ] É fácil encontrar onde uma regra está implementada

---

## Refatoração: Ajustando o nível de abstração

### De over para appropriate
```rust
// Antes: Over-engineered
pub trait Identifiable<I: Clone> {
    fn identifier(&self) -> I;
}

pub trait Auditable<T: Identifiable<I>, I: Clone> {
    fn entity(&self) -> &T;
    fn action(&self) -> AuditAction;
    fn timestamp(&self) -> DateTime<Utc>;
}

// Depois: Simplificado
pub struct AuditLog {
    pub user_id: UserId,
    pub action: String,
    pub timestamp: DateTime<Utc>,
}
```

### De under para appropriate
```rust
// Antes: God function
pub async fn process_order(
    order: Order,
    db: &mut PgConnection,
    email: &EmailClient,
    payment: &PaymentGateway,
    inventory: &InventoryService,
    notification: &NotificationService,
) -> Result<Order> {
    // 150 linhas fazendo tudo...
}

// Depois: Funções coesas
pub fn validate_order(order: &Order) -> Result<()> { }
pub fn reserve_inventory(order: &Order, inventory: &InventoryService) -> Result<()> { }
pub fn process_payment(order: &Order, payment: &PaymentGateway) -> Result<Payment> { }
pub fn send_confirmation(order: &Order, email: &EmailClient) -> Result<()> { }

pub async fn process_order(/* ... */) -> Result<Order> {
    validate_order(&order)?;
    reserve_inventory(&order, inventory)?;
    process_payment(&order, payment)?;
    send_confirmation(&order, email)?;
    Ok(order)
}
```

---

## Benefícios

| Benefício | Descrição |
|-----------|-----------|
| **Manutenibilidade** | Código no nível certo de abstração é fácil de mudar |
| **Legibilidade** | Desenvolvedores entendem o código sem esforço excessivo |
| **Evolução** | Abstrações corretas permitem mudanças locais sem efeito cascata |
| **Velocidade** | Não se perde tempo mantendo abstrações desnecessárias |
| **Clareza** | Intenção do código é evidente, não escondida em camadas |
