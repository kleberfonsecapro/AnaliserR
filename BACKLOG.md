# Backlog — AnaliseR

Documento gerado a partir da análise do código em 2026-09-28.
Escopo analisado: `backend/` (bot, api, auth, database, init.sql), `frontend/` (src, nginx, Dockerfile), `docker-compose.yml`, `.env`.

**Legenda de severidade**

| Nível | Significado |
|---|---|
| **P0** | Quebra o sistema em produção ou é risco de segurança ativo. Corrigir antes de qualquer deployment. |
| **P1** | Degradação relevante de segurança, confiabilidade ou operação. Corrigir no próximo ciclo. |
| **P2** | Qualidade, manutenibilidade, escala. Backlog normal. |
| **P3** | Melhoria de conveniência / limpeza. |

**Legenda de esforço:** `S` (< 1 dia) · `M` (1–3 dias) · `L` (> 3 dias)

---

## 1. Resumo do sistema

**Nome:** AnaliseR — análise automática de requisitos a partir de áudio de conversa com cliente.

**Fluxo principal**

1. Usuário envia áudio (voice/audio) ao bot no Telegram.
2. `ao_receber_audio` (`backend/bot.py:121`) consulta `pode_usar_bot` para o `telegram_user_id`.
3. Se autorizado: baixa o arquivo, transcreve via Groq/Whisper (`transcrever`, `backend/bot.py:71`).
4. `analisar` (`backend/bot.py:83`) chama `llama-3.3-70b-versatile` com `SYSTEM_PROMPT` e devolve relatório Markdown.
5. Grava em `analises_mvp` (`inserir_analise`, `backend/database.py:65`).
6. `enviar_relatorio` fatia em blocos de 4096 caracteres e envia (`backend/bot.py:99`).

**Painel web (admin)**

- Login com e-mail + senha → JWT de 5 minutos (`backend/auth.py`).
- CRUD de usuários com 3 papéis: `admin`, `gestor`, `usuario` (`backend/api.py`).
- Controle de `telegram_user_id` e da flag `pode_usar_bot`.
- React 18 + Vite, servido por nginx que faz proxy de `/api/` para o backend.

**Arquitetura**

```
Telegram ──► [backend: bot.py + api.py + auth.py + database.py] ◄──► PostgreSQL
                                        │
                                        └──────────► Groq (Whisper + LLM)
                                        
Browser ──► [frontend: nginx :80] ──/api/──► backend :8000
```

**Infraestrutura (`docker-compose.yml`)**

| Serviço | Porta host | Imagem |
|---|---|---|
| `db` | — | `postgres:15-alpine` (volume `pgdata`, init via `init.sql`) |
| `backend` | `8092` | build de `backend/Dockerfile` (`python:3.11-slim`) |
| `frontend` | `8091` | build de `frontend/Dockerfile` (build Vite → `nginx:1.27-alpine`) |

**Modelo de dados (`backend/init.sql`)**

- `analises_mvp(id, telegram_user_id, relatorio_gerado, data_criacao)` — índice em `telegram_user_id`.
- `usuarios(id, email UNIQUE, senha_hash, papel CHECK, telegram_user_id UNIQUE, pode_usar_bot, data_criacao)`.

**Matriz de permissão atual**

| Ação | admin | gestor | usuario |
|---|---|---|---|
| Entrar no painel | sim | sim | não (`_ENTRA_NO_PAINEL`, `backend/api.py:19`) |
| Listar usuários | sim | sim | não |
| Criar usuário | qualquer nível | só `usuario` | não |
| Editar usuário | qualquer | só `usuario` | não |
| Remover usuário | qualquer, exceto a si mesmo e o último admin | só `usuario` | não |
| Alterar o próprio nível | bloqueado (`backend/api.py:185`) | bloqueado | — |
| Usar o bot | depende de `pode_usar_bot` | depende de `pode_usar_bot` | depende de `pode_usar_bot` |

---

## 2. P0 — Bloqueadores

### BUG-001 · `garantir_admin` não promove o admin e reseta a senha a cada boot

- **Arquivo:** `backend/database.py:123-135`
- **Severidade:** P0 · **Esforço:** S

```python
INSERT INTO usuarios (email, senha_hash, papel, pode_usar_bot)
VALUES ($1, $2, 'admin', FALSE)
ON CONFLICT (email) DO UPDATE SET senha_hash = EXCLUDED.senha_hash
```

Dois defeitos no mesmo statement:

1. **`papel` não é elevado no conflito.** Se o e-mail já existir com `papel='usuario'` (ou `gestor`), apenas a senha é trocada e o papel permanece. O admin configurado em `ADMIN_EMAIL` nunca passa de `usuario` e **não consegue entrar no painel** — a aplicação sobe com zero administradores funcionais e sem erro visível.
2. **A senha é sobrescrita a cada reinício.** `garantir_admin` roda no lifespan (`backend/bot.py:178-181`). Qualquer troca de senha feita por um admin é anulada no próximo deploy, restart ou `docker compose restart`.

**Correção:** separar as duas responsabilidades — subir com `ON CONFLICT DO NOTHING` e adicionar um comando explícito de reset de senha, ou elevar o papel condicionalmente.

**Aceite:**
- [ ] Com `ADMIN_EMAIL` apontando para e-mail existente com `papel='usuario'`, o papel passa a `admin` na subida.
- [ ] Com `ADMIN_EMAIL` inexistente, o usuário é criado com `papel='admin'`.
- [ ] Reiniciar o container 2× não altera a senha de nenhum usuário.
- [ ] Teste automatizado cobrindo os dois cenários de conflito.

---

### BUG-002 · `download_to_drive` bloqueia o event loop

- **Arquivo:** `backend/bot.py:147`
- **Severidade:** P0 · **Esforço:** S

```python
await arquivo.download_to_drive(custom_path=str(caminho))
```

`File.download_to_drive` é **sincrono** no python-telegram-bot (bloco `>=21.6,<23`). Chamado dentro de um handler `async def`, ele executa o download HTTP de forma bloqueante no event loop, travando **toda** a aplicação — inclusive as rotas do FastAPI no mesmo processo. Um áudio de grande porte derruba o painel administrativo.

**Correção:** usar a variante assíncrona (`download_as_bytearray`) ou despachar para thread com `asyncio.to_thread`.

**Aceite:**
- [ ] Durante o download de um áudio, `GET /health` responde em menos de 100 ms.
- [ ] Teste de integração que dispare download e requisição concorrente.

---

### SEC-001 · Backend exposto na internet sem TLS

- **Arquivo:** `docker-compose.yml:33`
- **Severidade:** P0 · **Esforço:** M

```yaml
ports:
  - "8092:8000"
```

A API (login, CRUD de usuários) fica acessível diretamente, contornando o nginx, sem TLS, sem rate limit e sem os headers de segurança que o nginx deveria aplicar. O painel em `8091` também não tem TLS.

**Correção:** remover a publicação de `8092` (o frontend fala com o backend pela rede interna do Compose), e terminar TLS no nginx com proxy para o backend.

**Aceite:**
- [ ] `docker compose ps` não expõe `8092` no host.
- [ ] Acesso externo ao painel usa HTTPS com certificado válido.
- [ ] HTTP redireciona para HTTPS.

---

### SEC-002 · Sem rate limit no login — brute force

- **Arquivo:** `backend/api.py:132-146`
- **Severidade:** P0 · **Esforço:** M

`POST /auth/login` não tem limite de tentativas. Com painel em IP público, permite enumeração de e-mails e força bruta de senha. Não há também `fail2ban` nem registro de tentativas falhas.

**Correção:** rate limit por IP e por e-mail (ex.: `slowapi` ou middleware próprio com backend Redis/in-memory), com bloqueio progressivo e log de eventos.

**Aceite:**
- [ ] Número de tentativas excedente no intervalo devolve `429`.
- [ ] Tentativas malsucedidas são registradas com IP, e-mail e horário.
- [ ] Teste automatizado cobrindo o bloqueio.

---
## 3. P1 — Segurança, confiabilidade e operação

### SEC-003 · Sem cota nem limite de áudio no bot

- **Arquivos:** `backend/bot.py:121-164`
- **Severidade:** P1 · **Esforço:** M

Não existe teto de duração, tamanho ou frequência por usuário. Um usuário com `pode_usar_bot` pode enviar áudios de horas em sequência, consumindo cota e custo da Groq e reentrando na fila de processamento. Não há cooldown por usuário nem controle de concorrência.

**Correção:** validar `audio.file_size` e duração antes de processar, aplicar cooldown por `telegram_user_id`, e limitar quantos áudios são processados simultaneamente (semaphore).

**Aceite:**
- [ ] Áudio acima do limite recebe resposta explicando o motivo.
- [ ] Usuário bloqueado por cooldown recebe aviso com tempo restante.
- [ ] Nenhum caso ultrapassa o teto de transcrição.

---

### SEC-004 · Logout inexistente; JWT não revogável

- **Arquivos:** `backend/auth.py`, `frontend/src/api.js:14-18`, `frontend/src/App.jsx:51-55`
- **Severidade:** P1 · **Esforço:** M

`encerrarSessao()` só limpa o estado no navegador. O JWT continua válido até expirar e pode ser reutilizado. Não há refresh token, nem revogação (`jti` + blacklist), nem cookie `httpOnly` com `Secure`/`SameSite`.

**Correção:** decidir o modelo de sessão (refresh token rotativo com revogação, ou cookie `httpOnly`). Recomenda-se `httpOnly` + `SameSite=Strict` + `Secure`, o que também elimina a dependência de JS para o token.

**Aceite:**
- [ ] Token emitido antes do logout é rejeitado pela API.
- [ ] Refresh token rotaciona a cada uso e revoga a família em caso de reuso.

---

### SEC-005 · Container rodando como root

- **Arquivos:** `backend/Dockerfile:1-15`, `frontend/Dockerfile:11-16`
- **Severidade:** P1 · **Esforço:** S

Nenhum dos serviços define `USER`. O processo do backend e o worker do nginx rodam como `root`. Em caso de comprometimento, o atacante herda privilégio máximo no container.

**Correção:** criar usuário não privilegiado no `python:3.11-slim` e rodar o nginx com o usuário `nginx` (com ajuste de `pid` e portas).

**Aceite:**
- [ ] `docker compose exec backend whoami` retorna usuário não-root.
- [ ] Containers sobem normalmente com as permissões corretas.

---

### SEC-006 · Headers de segurança e compressão ausentes no nginx

- **Arquivo:** `frontend/nginx.conf`
- **Severidade:** P1 · **Esforço:** S

Sem `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy`; sem `gzip`; sem `Cache-Control: immutable` nos assets com hash do Vite; sem `client_max_body_size` definido.

**Correção:** adicionar bloco de headers, `gzip on` com tipos compressíveis, cache longo para `/assets/*` e `no-cache` para `index.html`.

**Aceite:**
- [ ] `curl -I` em `/` retorna todos os headers de segurança.
- [ ] Resposta do JS principal tem `Cache-Control: immutable` e vem com `Content-Encoding: gzip`.

---

### OPS-001 · Polling do Telegram acoplado ao lifespan do FastAPI

- **Arquivo:** `backend/bot.py:174-206`
- **Severidade:** P1 · **Esforço:** L

Bot e API compartilham o mesmo processo/event loop. Se o polling morrer, nada o reinicia — a API segue respondendo `200` em `/health` e o sistema parece saudável enquanto o bot está morto. Se a API ficar lenta, o bot atrasa. O mesmo processo ainda disputa GIL e conexão de pool sob contenção de CPU.

**Correção:** separar em dois processos/serviços (`bot` e `api`), com healthcheck real do bot. Alternativa mínima: `asyncio.TaskGroup` com supervisão e política de reconexão com backoff exponencial.

**Aceite:**
- [ ] Derrubar a conexão com o Telegram faz o bot reconectar sozinho com backoff.
- [ ] `/health` reflete o estado real do bot, não só do processo.
- [ ] CPU intensiva na API não atrasa o processamento de áudio.

---

### OPS-002 · `_garantir_niveis()` executa DDL a cada boot

- **Arquivo:** `backend/database.py:42-63`
- **Severidade:** P1 · **Esforço:** M

Na **cada** subida o código faz `DROP CONSTRAINT` em todas as constraints de check de `usuarios` (via `pg_constraint`) e recria a `usuarios_papel_check`. Com um `f-string` no nome da constraint. Efeito colateral: se alguém adicionar futuramente outra constraint de check na tabela, ela é **removida silenciosamente** a cada restart. Não há controle de versão de schema.

**Correção:** migrações versionadas e idempotentes (Alembic ou tabela `schema_migrations`), aplicadas uma única vez. Se a decisão for manter SQL puro, fazer a verificação antes de dropar e usar `format()` com identificador validado.

**Aceite:**
- [ ] Subir duas vezes seguidas não executa DDL na segunda.
- [ ] Existe controle de versão; aplicar uma migração já aplicada é no-op.
- [ ] Constraint adicional de check sobrevive a um restart.

---

### OPS-003 · Sem healthcheck no backend e no frontend

- **Arquivo:** `docker-compose.yml:18-41`
- **Severidade:** P1 · **Esforço:** S

Só o `db` tem `healthcheck`. `frontend` usa `depends_on: [backend]` sem condição, e o backend não é aguardado. Na primeira subida, o nginx pode começar a fazer proxy para um backend que ainda está conectando ao Postgres.

**Correção:** adicionar healthcheck no backend (`GET /health`) e no frontend, e usar `condition: service_healthy` em `depends_on`.

**Aceite:**
- [ ] `docker compose up` em volume limpo chega a um estado pronto sem erro de proxy 502.
- [ ] `restart: unless-stopped` de um dependente unhealthy não trava o Compose.

---

### OPS-004 · `analises_mvp` é tabela morta, com dado sensível e sem retenção

- **Arquivos:** `backend/init.sql:1-9`, `backend/database.py:65`
- **Severidade:** P1 · **Esforço:** M

A tabela recebe o relatório de **todo** áudio processado, mas **nada a lê**: não há endpoint de consulta, não há tela de histórico, e `relatorio_gerado` contém conteúdo derivado de conversa com cliente (possivelmente dados pessoais). Não há FK para `usuarios`, nem política de retenção, nem LGPD Considerada. Cresce sem limite.

**Correção:** decidir o destino do dado — ou expor histórico no painel (com paginação e escopo por usuário), ou remover a gravação. Em qualquer caso: FK para `usuarios`, política de retenção configurável e registro de consentimento/base legal.

**Aceite:**
- [ ] A tabela tem destino definido e documentado.
- [ ] Existe rotina de expurgo com prazo configurável.
- [ ] Dado de análise está vinculado a um usuário válido.

---

### FEAT-001 · Sessão de 5 minutos com mensagem hardcoded

- **Arquivos:** `frontend/src/App.jsx:29`, `backend/auth.py:21-22`, `.env`
- **Severidade:** P1 · **Esforço:** S

`JWT_EXPIRE_MINUTES=5` com mensagem `"A sessão de 5 minutos acabou. Entre de novo."` fixa no componente. Se a variável mudar, a mensagem mente. Sem refresh, o admin é expulso no meio do trabalho a cada 5 minutos.

**Correção:** derivar a duração da resposta (`expires_in` já vem do backend) e usar na mensagem; alongar a duração padrão para algo utilizável (30–60 min) combinado com o modelo de sessão de SEC-004.

**Aceite:**
- [ ] A mensagem reflete o valor real de expiração.
- [ ] Alterar `JWT_EXPIRE_MINUTES` não gera texto incorreto na UI.

---

### FEAT-002 · Sem paginação em `GET /users`

- **Arquivo:** `backend/api.py:148-150`
- **Severidade:** P1 · **Esforço:** S

`listar_usuarios()` retorna a tabela inteira e o frontend carrega tudo em memória e renderiza todos os `<form>`. Escala mal e o DOM pesa.

**Correção:** paginação com `limit`/`offset` (ou cursor) e renderização progressiva.

**Aceite:**
- [ ] Endpoint aceita `limit`/`offset` com teto máximo.
- [ ] A UI não renderiza a lista inteira de uma vez.

---

### AUD-001 · Sem log de auditoria de mudanças de permissão

- **Arquivos:** `backend/api.py:152-197`
- **Severidade:** P1 · **Esforço:** M

Criação, edição e remoção de usuários só geram log de nível `INFO` com o `id` (`backend/api.py:169`). Não há registro de **quem** alterou **o quê** — elevação de `papel`, liberação de `pode_usar_bot`, vínculo de `telegram_user_id`. Para um sistema de controle de acesso, isso é uma lacuna de rastreabilidade relevante.

**Correção:** tabela `auditoria` com ator, alvo, campo alterado, valor anterior e novo, timestamp, IP. Registrar também tentativas de acesso negado.

**Aceite:**
- [ ] Toda mudança de permissão gera um registro consultável.
- [ ] Tentativas negadas (permissão insuficiente) também são registradas.

---

## 4. P2 — Qualidade, manutenibilidade, escala

### SEC-007 · Backend sem `TrustedHostMiddleware` nem CORS explícito

- **Arquivo:** `backend/bot.py:209-210`
- **Severidade:** P2 · **Esforço:** S

`FastAPI` é criado sem `CORSMiddleware` e sem `TrustedHostMiddleware`. Hoje o navegador é salvo pelo nginx não emitir CORS, mas o backend respondendo direto em `8092` (SEC-001) permite requisição de qualquer origem com credenciais do usuário. Resolver junto com SEC-001.

**Aceite:**
- [ ] Middleware de host confiável ativo.
- [ ] CORS restrito explicitamente à origem do painel (ou desabilitado por completo).

---

### SEC-008 · SQL montado com `f-string` (whitelist, porém frágil)

- **Arquivos:** `backend/database.py:199-204`, `backend/database.py:54`
- **Severidade:** P2 · **Esforço:** S

```python
sql = f"""UPDATE usuarios SET {", ".join(atribuicoes)} WHERE id = ${len(valores)} ..."""
```

Os **valores** são parametrizados corretamente e as chaves vêm de um `if` explícito — não há injeção hoje. Mas qualquer refatoração que adicione um campo ao `atualizar_usuario` sem estender a whitelist introduz SQL dinâmico silenciosamente. O mesmo padrão em `ALTER TABLE usuarios DROP CONSTRAINT "{conname}"` usa `pg_constraint` como fonte (interno, mas ainda assim concatenado).

**Correção:** manter a whitelist, mas torná-la **declarativa** (mapa campo → coluna), opcionalmente com teste garantindo que todo campo aceito pelo Pydantic (`UsuarioPatch`) tem entrada no mapa. Para o DDL, usar `psycopg`/asyncpg identifier quoting em vez de f-string.

**Aceite:**
- [ ] Teste garante que adicionar um campo a `UsuarioPatch` sem mapear falha.
- [ ] Nenhum SQL dinâmico concatenando valor de usuário.

---

### OPS-005 · `passlib` arquivado

- **Arquivos:** `backend/requirements.txt:7-8`
- **Severidade:** P2 · **Esforço:** M

`passlib==1.7.4` está em Maintenance Mode desde 2020 e não recebe correções de segurança. O pin em `bcrypt==4.0.1` existe só para contornar o bug do `__about__` removido em bcrypt 4.1+.

**Correção:** usar a biblioteca `bcrypt` diretamente (elimina o pin e o wrapper morto), ou migrar para Argon2id com `argon2-cffi`.

**Aceite:**
- [ ] `passlib` removido das dependências.
- [ ] Hashes existentes continuam verificáveis (ou há rotina de migração no login).

---

### OPS-006 · Sem testes, lint, CI ou README

- **Arquivo:** repositório inteiro
- **Severidade:** P2 · **Esforço:** L

Zero testes automatizados. Nenhum linter, formatter ou type checker. Nenhum workflow de CI. Nenhum README com instruções de setup, variáveis de ambiente e deploy. O código tem comentários bons e nomes claros em pt-BR, o que é uma boa base — mas qualquer refatoração é por olho.

Prioridade de cobertura: (a) matriz de RBAC em `backend/api.py` (10 regras, incluindo autoexclusão e último admin), (b) `garantir_admin` (BUG-001), (c) `fatiar_texto` em `backend/bot.py:52` (limite de 4096, quebras de linha, texto vazio), (d) `_pode_atribuir` / `_pode_gerenciar`.

**Aceite:**
- [ ] `pytest` com cobertura dos RBAC e `garantir_admin`.
- [ ] Lint (ruff) e type check (mypy) rodando no CI.
- [ ] README com setup, variáveis de ambiente e comandos.

---

### OPS-007 · Ausência de `.gitignore` com segredos reais no diretório

- **Arquivo:** raiz do projeto
- **Severidade:** P2 · **Esforço:** S

Não existe `.gitignore` e o `.env` contém credenciais reais de produção (token do Telegram, chave da Groq, senha do Postgres, `JWT_SECRET`, `ADMIN_PASSWORD`). O projeto não é um repositório git, então não vazou — mas o primeiro `git init` já expõe tudo. O `.dockerignore` de cada serviço exclui `.env`, o que é correto para a imagem mas irrelevante para o versionamento.

**Correção:** criar `.gitignore` cobrindo `.env`, `node_modules/`, `dist/`, `__pycache__/`, `*.pyc`, `.venv/`. Criar `.env.example` com as chaves e valores de placeholder. **Se o `.env` for para ser versionado em algum momento, rotacionar todas as credenciais.**

**Aceite:**
- [ ] `git status` limpo após `git init` + `git add .` (nenhum `.env` rastreado).
- [ ] `.env.example` documenta todas as variáveis.
- [ ] Se houver histórico, credenciais foram rotacionadas.

---

### OPS-008 · `docker-compose.yml` redundante e sem limites de recursos

- **Arquivo:** `docker-compose.yml:18-41`
- **Severidade:** P2 · **Esforço:** S

`env_file: .env` já injeta **todas** as variáveis; o bloco `environment:` reenumera 5 delas. Risco de divergência: `GROQ_MODEL`, `GROQ_WHISPER_MODEL`, `ADMIN_EMAIL`, `JWT_EXPIRE_MINUTES` vêm só pelo `env_file` enquanto as outras têm caminho duplo. Se alguém editar uma variável no `environment:` achando que tem precedência, pode ter surpresa. Também não há `mem_limit`/`cpus`.

**Correção:** escolher um único mecanismo (recomendo `env_file` + `DATABASE_URL` montada) e documentar. Adicionar limites de recurso e `logging` com rotação para não encher o disco.

**Aceite:**
- [ ] Sem variável duplicada entre `env_file` e `environment`.
- [ ] `DATABASE_URL` é a única fonte de credencial de banco no backend.
- [ ] Containers têm limite de memória/CPU e log rotacionado.

---

### FEAT-003 · Service worker com risco de servir bundle antigo

- **Arquivo:** `frontend/public/sw.js`
- **Severidade:** P2 · **Esforço:** S

`CACHE = "analiser-v2"` é manual: após um deploy, o cache existente não é invalidado e o usuário pode continuar recebendo o bundle antigo (o `fetch` só cai no cache em falha de rede). O nome precisa ser bumpado à mão a cada release.

**Correção:** gerar a versão do cache a partir do commit/build (via `VITE_` env injetado no build) ou adotar `vite-plugin-pwa` com autoUpdate.

**Aceite:**
- [ ] Deploy de nova versão invalida o cache sem intervenção manual.
- [ ] Usuário recebe a versão nova no primeiro load após o deploy.

---

### FEAT-004 · Mensagem de sessão expirada e UX de erro frágeis

- **Arquivos:** `frontend/src/api.js:29-32`, `frontend/src/App.jsx`
- **Severidade:** P2 · **Esforço:** S

`api.js:29` trata **qualquer** `401` como "sessão expirada" e dispara logout, o que mascara um `401` de credencial errada. O erro não distingue `401` (reautenticar) de `403` (sem permissão) — o usuário vê "não foi possível concluir" quando deveria ver "sem acesso ao painel". Em `Admin.jsx`, `carregar()` no `useEffect` roda sem guarda de corrida e sem `AbortController`.

**Correção:** diferenciar `401` de `403` nas mensagens; proteger `useEffect` contra `setState` após unmount; cancelar requisições em voo na troca de tela.

**Aceite:**
- [ ] `403` exibe a mensagem de permissão do backend, não genérica.
- [ ] Trocar de tela no meio de um `carregar()` não gera aviso no console.

---

### FEAT-005 · Sem observabilidade

- **Arquivos:** `backend/bot.py:18-23`
- **Severidade:** P2 · **Esforço:** M

Logs em `INFO` com `basicConfig` e formato simples, sem request id, sem métricas, sem rastreamento. Não há como responder "quantas análises rodaram hoje?", "quanto custou?" ou "qual o tempo médio de processamento?".

**Correção:** logs estruturados (JSON) com `request_id` propagado, métricas básicas (contadores e latência de transcrição/análise) expostas em `/metrics`, e correlação entre a mensagem do Telegram e o `analise_id`.

**Aceite:**
- [ ] `/metrics` expõe contagem de análises, falhas e latência.
- [ ] É possível rastrear uma análise pelo log de ponta a ponta.

---

### DEV-001 · Diretório com typo no nome

- **Arquivo:** `bot_analiseR/`
- **Severidade:** P3 · **Esforço:** S

`bot_analiseR` — "R" maiúsculo no lugar de "r". Irritante em imports, caminhos de volume e comandos. O nome interno do projeto (`analiser-frontend` no `package.json:2`) diverge do diretório.

**Aceite:**
- [ ] Nome do diretório consistente com o nome do produto em toda a documentação e nos volumes.

---

### DEV-002 · Prompt do sistema sem versionamento nem few-shot

- **Arquivo:** `backend/bot.py:26-46`
- **Severidade:** P3 · **Esforço:** M

O `SYSTEM_PROMPT` é bom e bem estruturado, mas está embutido no código: não há como comparar a qualidade do relatório entre versões de prompt, nem A/B, nem rollback. A seção "Fora da versão 1" é a mais valiosa e não tem exemplo concreto.

**Correção:** externalizar o prompt (arquivo ou tabela), versioná-lo junto do relatório gerado (`prompt_version` em `analises_mvp`) e incluir um exemplo de referência.

**Aceite:**
- [ ] Trocar o prompt não exige redeploy do código.
- [ ] É possível saber qual versão de prompt gerou cada análise.

---

## 5. Checklist de implantação

Antes do próximo deploy, na ordem:

- [ ] **BUG-001** — corrigir `garantir_admin`, senão o admin não entra.
- [ ] **BUG-002** — download assíncrono, senão a API trava a cada áudio.
- [ ] **SEC-001** — fechar `8092`.
- [ ] **SEC-002** — rate limit no login.
- [ ] **OPS-007** — `.gitignore` + `.env.example`.
- [ ] **OPS-003** — healthchecks, senão a primeira subida falha.

---

## 6. Backlog funcional (não priorizado)

Itens que não surgiram da análise de segurança e correção de defeitos — são funcionalidades ainda não pedidas, registradas para não se perderem:

- Histórico de análises no painel (depende de **OPS-004**).
- Troca de senha pelo próprio usuário e reset pelo admin (decisão de **OPS-004**; hoje só existe o reset de boot).
- Exportar relatório em PDF / DOCX.
- Escolha de modelo por usuário (hoje `GROQ_MODEL` é global).
- Métricas de uso por usuário.
- Multi-tenant / múltiplas prefeituras.
