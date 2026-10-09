# Tickets — AnaliseR

Derivados do [`BACKLOG.md`](BACKLOG.md) na auditoria de 2026-10-09.

Cada ticket é fino e end-to-end (tracer bullet): entra código + teste/validação do aceite do card. As arestas **Bloqueado por** indicam o que precisa estar pronto antes; tickets sem bloqueio podem começar imediatamente.

**Legenda:** prioridade do card de origem · esforço `S` (< 1 dia) · `M` (1–3 dias) · `L` (> 3 dias).

---

## Onda 0 — Fundação (sem bloqueios) — ✅ concluída em 2026-10-09

### TP-01 · OPS-006 — Base de testes, lint e CI
- **Prioridade:** P2 · **Esforço:** M
- **Status: ✅ FEITO (2026-10-09)** — `backend/tests/` (pytest+asyncpg, 10 testes passando, incl. aceite 4 do BUG-001), ruff com baseline no `pyproject.toml`, CI em `.github/workflows/ci.yml`.
- **Escopo:** criar `backend/tests/` (pytest + pytest-asyncio), ruff configurado, workflow GitHub Actions rodando lint + testes; teste mínimo cobrindo `garantir_admin` (fecha o aceite pendente do BUG-001).
- **Bloqueado por:** —
- **Desbloqueia:** TP-04, TP-05 e todo ticket cujo aceite exige teste automatizado.

### TP-02 · OPS-007 — Auditoria de segredos no git
- **Prioridade:** P1 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — `git log --all -- .env` vazio e `git ls-files` lista só `.env.example`: nenhum segredo commitado neste repo (criado já com `.gitignore`). Rotação de credenciais **não necessária**.
- **Escopo:** `git log -- .env` no histórico; se houver segredo commitado, rotacionar as credenciais (Telegram, Groq, Postgres, JWT) e registrar. Documentar no README.
- **Bloqueado por:** —
- **Desbloqueia:** —

### TP-03 · OPS-005 — Migrar de passlib para bcrypt direto
- **Prioridade:** P2 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — `auth.py` usa `bcrypt` nativo (truncamento de 72 bytes explícito mantém hashes antigos válidos), `passlib` removido do `requirements.txt`, backend recriado em produção.
- **Escopo:** trocar `passlib` arquivado por `bcrypt` nativo em `auth.py`; despin `bcrypt==4.0.1`; validar login e hash antigo.
- **Bloqueado por:** —
- **Desbloqueia:** TP-06 (reformulação da autenticação em cima da base nova).

## Onda 1 — P0 (bloqueadores) — ✅ código entregue em 2026-10-09 (TP-06 aguarda domínio)

### TP-04 · SEC-002 — Rate limit no login
- **Prioridade:** P0 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — limitador por IP+e-mail com 429 e log de tentativa (rate_limit.py + api.py), 7 testes novos.
- **Escopo:** limite por IP+e-mail em `POST /auth/login` (slowapi ou implementação própria com janela deslizante), resposta `429`, registro de tentativa malsucedida (IP, e-mail, horário) e teste de bloqueio.
- **Bloqueado por:** TP-01 (aceite exige teste automatizado)
- **Desbloqueia:** —

### TP-05 · BUG-002 — Download de áudio sem bloquear o event loop
- **Prioridade:** P0 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — disco em `asyncio.to_thread`; regra ASYNC240 reativada no ruff como guarda contínua; teste de não-bloqueio do loop.
- **Escopo:** substituir `download_to_drive` no handler async (`bot.py:1262`) por `asyncio.to_thread`/`download_as_bytearray`; validar `/health` respondendo < 100 ms durante um download; teste de concorrência.
- **Bloqueado por:** TP-01 (aceite exige teste de integração)
- **Desbloqueia:** —

### TP-06 · SEC-001 — TLS e fechamento da porta 8092
- **Prioridade:** P0 · **Esforço:** M
- **Status: 🟡 PARCIAL (2026-10-09)** — porta 8092 fechada no host e backend acessível só pela rede interna; proxy Caddy com Let's Encrypt pronto (profile `tls`). Falta definir/apontar o domínio e subir o proxy no servidor (decisão de infra).
- **Escopo:** remover `8092:8000` do host (backend acessível só via proxy do frontend); TLS no nginx (Let's Encrypt ou certificado da organização) com redirect HTTP→HTTPS. **Depende de decisão operacional:** como o TLS será provido (Caddy/Traefik/nginx+Certs vs. proxy externo já existente no host).
- **Bloqueado por:** — (decisão de infra necessária antes da execução)
- **Desbloqueia:** —

## Onda 2 — P1 (segurança e operação) — ✅ concluída em 2026-10-09

### TP-07 · OPS-002 — Remover DDL destrutivo do boot
- **Prioridade:** P1 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — migração 0004; boot sem DDL destrutivo.
- **Escopo:** converter `_garantir_niveis()` (database.py:93-114) em migração versionada idempotente; boot não executa mais `DROP CONSTRAINT`.
- **Bloqueado por:** —
- **Desbloqueia:** TP-08

### TP-08 · SEC-008 — SQL seguro com whitelist declarativa
- **Prioridade:** P2 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — whitelist declarativa + teste.
- **Escopo:** eliminar as f-strings SQL restantes (`atualizar_usuario`, DDL) com mapeamento declarativo de colunas permitidas + teste cobrindo o mapa.
- **Bloqueado por:** TP-07 (mesma região de código), TP-01
- **Desbloqueia:** —

### TP-09 · OPS-004 — Destino da `analises_mvp`
- **Prioridade:** P1 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — tabela legada removida (0005); retenção via `RETENCAO_REUNIOES_DIAS`.
- **Escopo:** remover a criação da tabela do `init.sql`, documentar o destino dos dados (já migrados para `reunioes`) e criar rotina de expurgo com prazo configurável.
- **Bloqueado por:** TP-07 (versionamento de migrações)
- **Desbloqueia:** —

### TP-10 · SEC-004 — Logout real e JWT revogável
- **Prioridade:** P1 · **Esforço:** M
- **Status: ✅ FEITO (2026-10-09)** — JWT com jti + denylist + logout; refresh desnecessário com sessão curta.
- **Escopo:** `jti` + denylist ou refresh token rotativo com revogação de família; endpoint `POST /auth/logout` revogando; frontend chama o endpoint em "Sair".
- **Bloqueado por:** TP-03, TP-01
- **Desbloqueia:** TP-11

### TP-11 · FEAT-001 — Mensagem de sessão sem texto fixo
- **Prioridade:** P1 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — mensagem usa expires_in real.
- **Escopo:** frontend usa o `expires_in` real do backend na mensagem de expiração; alterar `JWT_EXPIRE_MINUTES` não gera texto incorreto.
- **Bloqueado por:** TP-10 (a reformulação de sessão muda o contrato)
- **Desbloqueia:** —

### TP-12 · FEAT-002 — Paginação em `GET /users`
- **Prioridade:** P1 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — limit/offset com teto 200 + UI paginada.
- **Escopo:** `limit`/`offset` com teto; UI paginada em `Admin.jsx`.
- **Bloqueado por:** —
- **Desbloqueia:** —

### TP-13 · AUD-001 — Auditoria de mudanças de permissão
- **Prioridade:** P1 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — tabela auditoria + gravação nas mutações e negações.
- **Escopo:** tabela `auditoria` (ator, ação, alvo, valores anterior/novo, IP, horário) gravada nas mutações de `api.py`; tentativas negadas também registradas.
- **Bloqueado por:** TP-01
- **Desbloqueia:** —

## Onda 3 — Infraestrutura de containers — ✅ concluída em 2026-10-09

### TP-14 · SEC-005 — Containers não-root
- **Prioridade:** P1 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — backend `USER app`, frontend `USER nginx`
- **Escopo:** `USER` não-root nos Dockerfiles do backend e frontend (nginx em porta alta interna ou `setcap`); compose sobe normal.
- **Bloqueado por:** —
- **Desbloqueia:** TP-16

### TP-15 · OPS-003 — Healthchecks e `depends_on` com condição
- **Prioridade:** P1 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — healthchecks backend/frontend + depends_on condicional
- **Escopo:** healthcheck no backend (`/health`) e frontend (`wget` no nginx); `depends_on: condition: service_healthy` no frontend.
- **Bloqueado por:** —
- **Desbloqueia:** TP-16

### TP-16 · OPS-008 — Compose limpo com limites de recursos
- **Prioridade:** P2 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — compose sem duplicação, com limites e rotação de log
- **Escopo:** remover duplicação `environment:` × `env_file` (manter só `DATABASE_URL` montada), adicionar `mem_limit`/`cpus` e logging com rotação.
- **Bloqueado por:** TP-14, TP-15 (mexem nos mesmos arquivos)
- **Desbloqueia:** —

### TP-17 · SEC-006 — Headers de segurança e compressão no nginx
- **Prioridade:** P1 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — headers+gzip+cache no nginx (HSTS ativo sob TLS do proxy)
- **Escopo:** HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP, gzip, `Cache-Control: immutable` para assets com hash, `client_max_body_size`.
- **Bloqueado por:** TP-06 (HSTS exige TLS ativo)
- **Desbloqueia:** —

### TP-18 · SEC-007 — TrustedHost e CORS explícito
- **Prioridade:** P2 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — TrustedHostMiddleware; CORS omitido (same-origin)
- **Escopo:** `TrustedHostMiddleware` com hosts permitidos; `CORSMiddleware` só se houver consumo cross-origin real (senão, omitir).
- **Bloqueado por:** —
- **Desbloqueia:** —

## Onda 4 — P2 (qualidade e DX) — ✅ concluída em 2026-10-09 (exceto TP-24, aguardando janela de manutenção)

### TP-19 · OPS-001 — Separar bot do processo da API
- **Prioridade:** P1 · **Esforço:** L
- **Status: ✅ FEITO (2026-10-09)** — bot em serviço próprio com heartbeat
- **Escopo:** bot em container próprio (`python bot.py` com polling + backoff/reconexão) ou supervisor dentro do container; `/health` reflete o estado real do bot (heartbeat do polling).
- **Bloqueado por:** TP-15 (healthchecks), TP-16 (compose)
- **Desbloqueia:** —

### TP-20 · FEAT-003 — Service worker versionado por build
- **Prioridade:** P2 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — cache do SW versionado por build
- **Escopo:** nome do cache derivado do hash do build (vite-plugin-pwa ou bump automático), sem servir bundle antigo.
- **Bloqueado por:** —
- **Desbloqueia:** —

### TP-21 · FEAT-004 — Finalizar UX de erro (AbortController)
- **Prioridade:** P2 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — AbortController no carregar()
- **Escopo:** `AbortController`/guarda de unmount no `carregar()` de `Admin.jsx`.
- **Bloqueado por:** —
- **Desbloqueia:** —

### TP-22 · FEAT-005 — Observabilidade
- **Prioridade:** P2 · **Esforço:** M
- **Status: ✅ FEITO (2026-10-09)** — log com request id (X-Request-Id)
- **Escopo:** logging estruturado com request id correlacionando `analise_id`; endpoint `/metrics` ou integração mínima definida.
- **Bloqueado por:** TP-01
- **Desbloqueia:** —

### TP-23 · DEV-002 — Versionamento de prompt
- **Prioridade:** P3 · **Esforço:** S
- **Status: ✅ FEITO (2026-10-09)** — prompts em arquivos + prompt_versao (0008)
- **Escopo:** extrair prompts de `bot.py` para arquivos versionados (mesmo padrão de `padrao_stack/`), gravar a versão do prompt/referência usada em cada `reuniao`.
- **Bloqueado por:** —
- **Desbloqueia:** —

### TP-24 · DEV-001 — Renomear diretório `bot_analiseR`
- **Prioridade:** P3 · **Esforço:** S
- **Escopo:** renomear para `analiser`, ajustar nomes de containers/compose e referências no README.
- **Bloqueado por:** todos os anteriores (evita conflito com trabalho em andamento)
- **Desbloqueia:** —

---

## Grafo de bloqueio (resumo)

```
TP-01 (tests/CI) ─► TP-04, TP-05, TP-08, TP-10, TP-13, TP-22
TP-03 (bcrypt)   ─► TP-10 ─► TP-11
TP-07 (DDL boot) ─► TP-08, TP-09
TP-06 (TLS)      ─► TP-17
TP-14, TP-15     ─► TP-16
TP-15, TP-16     ─► TP-19
TP-24             ◄─ por último
```

**Primeiros a executar (sem bloqueio):** TP-01, TP-02, TP-03, TP-07, TP-12, TP-14, TP-15, TP-18, TP-20, TP-21, TP-23.
**P0 imediatos ao destravar:** TP-04, TP-05 (após TP-01) e TP-06 (após decisão de TLS).
