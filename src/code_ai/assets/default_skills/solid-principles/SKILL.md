---
name: solid-principles
description: Guia para aplicar os 5 princípios SOLID: SRP, OCP, LSP, ISP, DIP em design de código orientado a objetos ou baseado em traits.
---

# SOLID Principles

## Propósito
Aplicar os cinco princípios de design que tornam o código mais compreensível, flexível e manutenível.

---

## 1. SRP — Single Responsibility Principle

> Uma classe/módulo deve ter uma, e apenas uma, razão para mudar.

### Como identificar
- A classe faz mais de uma "coisa" conceitual?
- Diferentes times mudam o mesmo arquivo por motivos diferentes?
- É difícil explicar o propósito da classe em uma frase?

### ✅ Exemplo (Rust)
```rust
// ❌ ERRADO: Múltiplas responsabilidades
struct UserService {
    db: Database,
    emailer: EmailService,
    logger: Logger,
}

impl UserService {
    fn register_user(&self, data: UserData) -> Result<User> {
        // Valida, salva no DB, envia email, loga...
    }
}

// ✅ CERTO: Responsabilidades separadas
struct UserValidator { /* validação */ }
struct UserRepository { /* persistência */ }
struct UserNotifier { /* notificações */ }
```

### Checklist
- [ ] Cada função faz uma coisa bem definida
- [ ] Cada módulo tem um propósito claro
- [ ] Mudanças em uma regra não afetam outras

---

## 2. OCP — Open/Closed Principle

> Entidades devem estar abertas para extensão, mas fechadas para modificação.

### Como aplicar
- Use traits/interfaces para comportamento variável
- Prefira composição sobre herança
- Injete comportamentos, não os hard-code

### ✅ Exemplo (Rust)
```rust
// ❌ ERRADO: Modificação necessária para novo tipo
enum NotificationType { Email, SMS, Push }

fn send_notification(user: &User, notification_type: NotificationType) {
    match notification_type {
        NotificationType::Email => { /* ... */ }
        NotificationType::SMS => { /* ... */ }
        NotificationType::Push => { /* novo código modifica esta função */ }
    }
}

// ✅ CERTO: Extensão sem modificação
trait NotificationChannel {
    fn send(&self, user: &User, message: &str) -> Result<()>;
}

struct EmailChannel { /* impl NotificationChannel */ }
struct SMSChannel { /* impl NotificationChannel */ }
// Novo canal: apenas crie nova struct, não modifique o existente

fn send_notification(user: &User, channel: &dyn NotificationChannel) {
    channel.send(user, "message");
}
```

### Checklist
- [ ] Novo comportamento = novo arquivo, não modificação
- [ ] Código existente não precisa ser tocado para extensões
- [ ] Traits definem contratos estáveis

---

## 3. LSP — Liskov Substitution Principle

> Subtipos devem ser substituíveis por seus tipos base sem quebrar o programa.

### Como violar
- Subclasses que lançam exceptions não esperadas
- Implementações que ignoram contratos da interface
- Pré-condições mais fortes ou pós-condições mais fracas

### ✅ Exemplo (Rust)
```rust
// ❌ ERRADO: Violação do contrato
trait Repository {
    fn find(&self, id: Id) -> Result<Option<Entity>>;
}

struct CachedRepository {
    // Sempre retorna Some, nunca None (viola contrato!)
}

// ✅ CERTO: Respeita o contrato
struct CachedRepository {
    cache: Cache,
    inner: Box<dyn Repository>,
}
// Delega para o inner quando cache miss, mantendo o contrato
```

### Checklist
- [ ] Implementações respeitam pré-condições da interface
- [ ] Implementações garantem pós-condições da interface
- [ ] Invariantes são preservadas em todos os subtipos
- [ ] Exceptions/errors são consistentes com o contrato

---

## 4. ISP — Interface Segregation Principle

> Clientes não devem depender de métodos que não usam.

### Como identificar
- Interfaces "gordas" com muitos métodos
- Implementações com métodos `unimplemented!()` ou `panic!()`
- Classes que implementam interface mas não usam todos os métodos

### ✅ Exemplo (Rust)
```rust
// ❌ ERRADO: Interface gorda
trait Worker {
    fn work(&self);
    fn eat(&self);
    fn sleep(&self);
}

struct Robot;
impl Worker for Robot {
    fn work(&self) { /* ... */ }
    fn eat(&self) { unimplemented!() }  // ISP violation
    fn sleep(&self) { unimplemented!() }
}

// ✅ CERTO: Interfaces segregadas
trait Workable { fn work(&self); }
trait Eatable { fn eat(&self); }
trait Sleepable { fn sleep(&self); }

struct Robot;
impl Workable for Robot { /* ... */ }
// Robot não implementa Eatable ou Sleepable — e está tudo bem
```

### Checklist
- [ ] Interfaces são pequenas e focadas
- [ ] Clientes implementam apenas o que realmente usam
- [ ] Não há métodos `unimplemented!()` por má modelagem

---

## 5. DIP — Dependency Inversion Principle

> Dependa de abstrações, não de concretas.

### Como aplicar
- Defina traits/interfaces para dependências externas
- Injete implementações, não as instancie internamente
- Alta-level policy não depende de low-level details

### ✅ Exemplo (Rust)
```rust
// ❌ ERRADO: Dependendo de concreta
struct UserService {
    db: SqlxDatabase,  // Dependência concreta
}

// ✅ CERTO: Dependendo de abstração
trait UserRepository {
    fn save(&self, user: &User) -> Result<()>;
}

struct UserService {
    repo: Box<dyn UserRepository>,  // Abstração
}

// Infraestrutura depende do domínio
struct SqlxUserRepository { /* impl UserRepository */ }
```

### Checklist
- [ ] Módulos de alto nível não importam módulos de baixo nível
- [ ] Abstrações são definidas no domínio/core
- [ ] Implementações concretas são injetadas
- [ ] É possível swapar implementações sem mudar o core

---

## Revisão Rápida

| Princípio | Pergunta chave |
|-----------|----------------|
| SRP | Esta classe tem mais de um motivo para mudar? |
| OCP | Preciso modificar código existente para adicionar feature? |
| LSP | Posso trocar a implementação sem quebrar testes? |
| ISP | Esta interface tem métodos que alguns clientes não usam? |
| DIP | Estou dependendo de uma classe concreta? |
