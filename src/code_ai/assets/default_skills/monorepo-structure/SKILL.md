---
name: monorepo-structure
description: Guia para estruturar monorepos com workspaces: definir boundaries explícitos entre packages, evitar ciclos e manter direção correta de dependências.
---

# Monorepo Structure com Workspaces

## Propósito
Organizar múltiplos projetos relacionados em um único repositório com boundaries explícitos entre packages, evitando ciclos e mantendo a direção correta das dependências.

## Estrutura fundamental

```
monorepo/
├── packages/
│   ├── shared/          # Contratos, schemas, tipos compartilhados
│   │   ├── src/
│   │   │   ├── types.ts      # Tipos TypeScript
│   │   │   ├── schemas.ts    # Zod schemas (validação)
│   │   │   └── events.ts     # Definições de eventos
│   │   └── package.json
│   │
│   ├── server/          # Backend (API, DB, business logic)
│   │   ├── src/
│   │   │   ├── api/          # Controllers, routes
│   │   │   ├── domain/       # Entidades, regras de negócio
│   │   │   ├── application/  # Use cases, services
│   │   │   └── infrastructure/ # DB, external APIs
│   │   └── package.json
│   │
│   └── client/          # Frontend (UI, state management)
│       ├── src/
│       │   ├── components/
│       │   ├── pages/
│       │   ├── hooks/
│       │   └── store/
│       └── package.json
│
├── package.json         # Root: define workspaces
└── tsconfig.json        # Configuração TypeScript compartilhada
```

## Regra de ouro das dependências

```
┌─────────────────────────────────────────────────────┐
│                  shared (contracts)                 │
│  - Tipos, schemas, interfaces, eventos              │
│  - NÃO depende de server ou client                  │
│  - server e client dependem de shared               │
└─────────────────────────────────────────────────────┘
           ▲                        ▲
           │                        │
           │                        │
    ┌──────┴──────┐          ┌──────┴──────┐
    │   server    │          │   client    │
    │  (backend)  │          │ (frontend)  │
    │             │          │             │
    │ - API       │          │ - UI        │
    │ - DB        │          │ - State     │
    │ - Business  │          │ - Routing   │
    └─────────────┘          └─────────────┘
    
    ❌ NUNCA: server ↔ client (dependência cruzada)
    ✅ SEMPRE: server → shared, client → shared
```

## Configuração de Workspaces

### package.json (root)
```json
{
  "name": "my-monorepo",
  "private": true,
  "workspaces": [
    "packages/*"
  ]
}
```

### packages/shared/package.json
```json
{
  "name": "@myapp/shared",
  "version": "1.0.0",
  "main": "./src/index.ts",
  "types": "./src/index.ts",
  "dependencies": {
    "zod": "^3.22.0"
  }
}
```

### packages/server/package.json
```json
{
  "name": "@myapp/server",
  "version": "1.0.0",
  "main": "./src/index.ts",
  "dependencies": {
    "@myapp/shared": "1.0.0",
    "express": "^4.18.0",
    "prisma": "^5.0.0"
  }
}
```

### packages/client/package.json
```json
{
  "name": "@myapp/client",
  "version": "1.0.0",
  "main": "./src/index.ts",
  "dependencies": {
    "@myapp/shared": "1.0.0",
    "react": "^18.2.0",
    "react-router-dom": "^6.0.0"
  }
}
```

## O que vai em cada package

### `shared` (contracts)
**Responsabilidade**: Definir contratos que server e client compartilham

| Inclui | Não inclui |
|--------|------------|
| Tipos TypeScript/interfaces | Lógica de negócio |
| Zod schemas de validação | Código de banco de dados |
| Definições de eventos | Código de UI |
| Enums constantes | Implementações de API |
| Tipos de comandos/queries | State management |

**Exemplo**:
```typescript
// packages/shared/src/user.ts
import { z } from 'zod';

export const CreateUserSchema = z.object({
  email: z.string().email(),
  name: z.string().min(2),
});

export type CreateUserCommand = z.infer<typeof CreateUserSchema>;

export interface User {
  id: string;
  email: string;
  name: string;
  createdAt: Date;
}
```

### `server` (backend)
**Responsabilidade**: Implementar lógica de negócio, API, persistência

| Inclui | Não inclui |
|--------|------------|
| Controllers/routes | Componentes de UI |
| Services/use cases | Lógica de renderização |
| Repositories/DB | Hooks do React |
| External API clients |  |
| Autenticação/autorização |  |

**Exemplo**:
```typescript
// packages/server/src/user.service.ts
import { CreateUserCommand, User } from '@myapp/shared';
import { UserRepository } from './user.repository';

export class UserService {
  constructor(private repo: UserRepository) {}
  
  async createUser(cmd: CreateUserCommand): Promise<User> {
    // Validação, regras de negócio, persistência
    return this.repo.save(cmd);
  }
}
```

### `client` (frontend)
**Responsabilidade**: UI, state management, interação com usuário

| Inclui | Não inclui |
|--------|------------|
| Componentes React/Vue | Queries SQL |
| Pages/routes | Lógica de negócio complexa |
| Hooks customizados | Acesso direto ao banco |
| API clients (fetch/axios) |  |
| State (Zustand, Redux) |  |

**Exemplo**:
```typescript
// packages/client/src/hooks/useUser.ts
import { User, CreateUserCommand } from '@myapp/shared';
import { apiClient } from '../lib/api';

export function useUser() {
  const createUser = async (cmd: CreateUserCommand) => {
    const res = await apiClient.post('/users', cmd);
    return res.data as User;
  };
  
  return { createUser };
}
```

## Como evitar ciclos

### ❌ ERRADO: Ciclo server → client → server
```typescript
// server importa algo do client
import { SomeComponent } from '@myapp/client';  // 🚫 CICLO!

// client importa algo do server
import { SomeService } from '@myapp/server';    // 🚫 CICLO!
```

### ✅ CERTO: Ambos dependem apenas de shared
```typescript
// server
import { User, CreateUserSchema } from '@myapp/shared';

// client
import { User, CreateUserSchema } from '@myapp/shared';
```

### Quando server e client precisam se comunicar?

1. **Defina o contrato em shared**
```typescript
// packages/shared/src/user.api.ts
export interface CreateUserRequest {
  email: string;
  name: string;
}

export interface CreateUserResponse {
  user: User;
  success: boolean;
}
```

2. **Server implementa**
```typescript
// packages/server/src/user.controller.ts
app.post('/users', async (req, res) => {
  const cmd: CreateUserRequest = req.body;
  const user = await service.createUser(cmd);
  res.json({ user, success: true });
});
```

3. **Client consome**
```typescript
// packages/client/src/api/user.api.ts
export async function createUser(cmd: CreateUserRequest) {
  const res = await fetch('/users', {
    method: 'POST',
    body: JSON.stringify(cmd),
  });
  return res.json() as CreateUserResponse;
}
```

## Ferramentas recomendadas

| Ferramenta | Propósito |
|------------|-----------|
| **pnpm workspaces** | Gerenciamento de dependências |
| **Turborepo** | Build caching, pipelines |
| **Nx** | Build system, graph de dependências |
| **Changesets** | Versionamento e changelog |
| **TypeScript project refs** | Type-checking entre packages |

### tsconfig.json (root)
```json
{
  "compilerOptions": {
    "composite": true,
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true,
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "references": [
    { "path": "packages/shared" },
    { "path": "packages/server" },
    { "path": "packages/client" }
  ]
}
```

## Checklist de validação

- [ ] `shared` não depende de `server` ou `client`
- [ ] `server` e `client` não dependem um do outro
- [ ] Cada package tem um `package.json` com nome escopado (`@myapp/*`)
- [ ] Tipos/contratos compartilhados estão em `shared`
- [ ] Lógica de negócio está apenas em `server`
- [ ] UI está apenas em `client`
- [ ] É possível rodar testes de um package sem rodar os outros
- [ ] O graph de dependências não tem ciclos (`pnpm why` ou `nx graph`)

## Sinais de violação

- ❌ Import `from '@myapp/client'` dentro de `server`
- ❌ Import `from '@myapp/server'` dentro de `client`
- ❌ Lógica de negócio duplicada em server e client
- ❌ Tipos definidos em server e redefinidos em client
- ❌ `shared` crescendo demais e virando "lixeira"
- ❌ Dificuldade de buildar um package isoladamente

## Benefícios

| Benefício | Descrição |
|-----------|-----------|
| **Code sharing seguro** | Shared define contratos claros, sem acoplamento |
| **Builds mais rápidos** | Caching entre packages, build incremental |
| **Refatoração segura** | Tipos compartilhados pegam erros em compile |
| **Deploy independente** | Server e client podem versionar separadamente |
| **Onboarding** | Estrutura previsível, fácil entender onde cada coisa vive |
