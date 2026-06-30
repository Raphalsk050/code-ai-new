---
name: bounded-contexts-ddd
description: Guia para aplicar Bounded Contexts do DDD: dividir sistemas grandes em contextos de domínio coesos com linguagens e boundaries explícitos.
---

# Bounded Contexts (Domain-Driven Design)

## Propósito
Dividir sistemas grandes e complexos em contextos de domínio menores, coesos e independentes, cada um com sua própria linguagem ubíqua, entidades e regras de negócio.

## Conceitos fundamentais

### O que é um Bounded Context?
Um **limite conceitual** dentro do qual um modelo de domínio específico é válido e consistente. Diferentes contextos podem ter:
- Diferentes significados para o mesmo termo
- Diferentes modelos para o mesmo conceito
- Diferentes linguagens ubíquas

### Exemplo clássico: "User" em diferentes contextos

```
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│   Identity Context  │    │   Billing Context   │    │  Shipping Context   │
├─────────────────────┤    ├─────────────────────┤    ├─────────────────────┤
│ User = Credentials  │    │ User = Payer        │    │ User = Recipient    │
│ - email             │    │ - payment_methods   │    │ - addresses         │
│ - password_hash     │    │ - billing_address   │    │ - shipping_prefs    │
│ - roles             │    │ - invoices          │    │ - delivery_history  │
└─────────────────────┘    └─────────────────────┘    └─────────────────────┘
```

## Como identificar Bounded Contexts

### 1. Analise a linguagem ubíqua
- Times diferentes usam os mesmos termos com significados diferentes?
- Há ambiguidades em reuniões entre áreas?
- O mesmo conceito tem atributos diferentes dependendo de quem fala?

### 2. Analise as mudanças
- Grupos de funcionalidades que sempre mudam juntos?
- Times que trabalham independentemente na maioria do tempo?
- Regras de negócio que são ortogonais entre si?

### 3. Analise os dados
- Dados que são lidos juntos, escritos juntos?
- Transações que envolvem sempre os mesmos aggregates?
- Consistência necessária apenas dentro de um grupo?

## Padrões de integração entre Contextos

### 1. Shared Kernel (Kernel Compartilhado)
```
Contexto A ←→ Shared Kernel ←→ Contexto B
```
- **Quando usar**: Contextos que compartilham um subconjunto pequeno e estável de modelo
- **Cuidado**: O kernel deve ser mínimo e muito estável
- **Exemplo**: Tipos básicos como `UserId`, `Money`, `DateTime`

### 2. Customer-Supplier (Cliente-Fornecedor)
```
Contexto Cliente ←→ Upstream (Supplier) ←→ Downstream (Customer)
```
- **Quando usar**: Relação clara de dependência entre contextos
- **Upstream**: Define o modelo e a API
- **Downstream**: Consome, pode fazer adaptações locais
- **Exemplo**: Identity (upstream) → Billing (downstream)

### 3. Conformist (Conformista)
```
Contexto A (dominante) → Contexto B (conformista)
```
- **Quando usar**: Downstream não tem poder de mudar o upstream
- **Conformista**: Adapta-se completamente ao modelo do upstream
- **Exemplo**: Sistema legado → Novo sistema (novo se adapta ao legado)

### 4. Anti-Corruption Layer (ACL)
```
Contexto A ←→ [ ACL ] ←→ Contexto B (legado/externo)
```
- **Quando usar**: Integrar com sistema legado ou externo sem se corromper
- **ACL**: Traduz entre modelos, protege o domínio interno
- **Exemplo**: Novo domínio ←→ API de terceiros ←→ Sistema legado

### 5. Open Host Service (Serviço Aberto)
```
Contexto A → Protocolo/API Pública → Múltiplos Contextos B, C, D
```
- **Quando usar**: Upstream serve múltiplos downstreams
- **Define**: Protocolo ou API bem documentada
- **Exemplo**: Identity service com API REST/GraphQL para todos os outros

### 6. Published Language (Linguagem Publicada)
```
Contexto A ←→ [ Schema/Contract ] ←→ Contexto B
```
- **Quando usar**: Integração baseada em schemas compartilhados
- **Formato**: JSON Schema, Protobuf, OpenAPI, Avro
- **Exemplo**: Eventos de domínio em formato padronizado

## Como implementar em Rust

### Estrutura de diretórios sugerida
```
src/
├── identity/
│   ├── domain/
│   │   ├── user.rs
│   │   └── credentials.rs
│   ├── application/
│   │   └── authenticate.rs
│   └── infrastructure/
│       └── repository.rs
├── billing/
│   ├── domain/
│   │   ├── customer.rs
│   │   └── invoice.rs
│   ├── application/
│   │   └── process_payment.rs
│   └── infrastructure/
│       └── repository.rs
├── shipping/
│   └── ...
└── shared/
    ├── kernel/
    │   ├── user_id.rs
    │   └── money.rs
    └── events/
        └── domain_event.rs
```

### Exemplo: Anti-Corruption Layer

```rust
// shipping/domain/model.rs (nosso modelo limpo)
pub struct Customer {
    id: CustomerId,
    delivery_addresses: Vec<DeliveryAddress>,
}

// shipping/infrastructure/legacy_adapter.rs (ACL)
pub struct LegacyCustomerAdapter {
    legacy_client: LegacyApiClient,
}

impl CustomerRepository for LegacyCustomerAdapter {
    async fn find_by_id(&self, id: &CustomerId) -> Result<Option<Customer>> {
        // Traduz do modelo legado para nosso modelo
        let legacy = self.legacy_client.get_customer(id).await?;
        Ok(Some(Customer {
            id: legacy.customer_id.into(),
            delivery_addresses: legacy.addresses
                .into_iter()
                .map(|a| DeliveryAddress {
                    street: a.street_name,
                    number: a.house_number,
                    // ... mapeamento
                })
                .collect(),
        }))
    }
}
```

### Exemplo: Domain Events entre Contextos

```rust
// shared/events/mod.rs
pub trait DomainEvent: Send + Sync {
    fn event_type(&self) -> &'static str;
    fn occurred_at(&self) -> DateTime<Utc>;
}

// identity: publica evento
pub struct UserRegistered {
    pub user_id: UserId,
    pub email: String,
    pub occurred_at: DateTime<Utc>,
}

// billing: consome evento
pub struct BillingEventHandler {
    customer_repo: Box<dyn CustomerRepository>,
}

impl DomainEventHandler<UserRegistered> for BillingEventHandler {
    async fn handle(&self, event: &UserRegistered) -> Result<()> {
        // Cria customer no contexto de billing
        let customer = Customer::new(event.user_id.clone(), event.email.clone());
        self.customer_repo.save(&customer).await?;
        Ok(())
    }
}
```

## Checklist de definição de boundaries

- [ ] Cada contexto tem uma linguagem ubíqua clara e documentada
- [ ] Entidades de um contexto não são compartilhadas diretamente com outros
- [ ] Comunicação entre contextos é explícita (APIs, eventos, adapters)
- [ ] Cada contexto pode evoluir independentemente na maioria dos casos
- [ ] Há clareza sobre quem é upstream/downstream em cada relação
- [ ] Contextos são pequenos o suficiente para serem compreendidos por um time

## Sinais de violação

- ❌ Entidades do mesmo tipo em contextos diferentes com campos diferentes
- ❌ Um contexto acessando banco de dados de outro contexto diretamente
- ❌ Mudanças em um contexto quebram compilação de outros contextos
- ❌ Reuniões constantes para "alinhar" significados de termos
- ❌ Modelo de domínio com muitas condicionais do tipo `if context == "billing"`

## Benefícios

| Benefício | Descrição |
|-----------|-----------|
| **Escala de times** | Times diferentes trabalham em contextos diferentes com mínimo acoplamento |
| **Clareza conceitual** | Cada modelo é consistente dentro do seu boundary |
| **Evolução independente** | Contextos mudam em ritmos diferentes sem se quebrar |
| **Legacy isolation** | ACL protege novo domínio de modelos legados |
| **Foco** | Desenvolvedores pensam em um problema de cada vez |
