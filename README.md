# AnaliseR

Sistema automático de análise de requisito. O desenvolvedor grava a conversa com o cliente no Telegram; o AnaliseR devolve o relatório do produto mínimo, a stack sugerida a partir da base da organização e, quando a análise fecha, os planos de ação para começar a desenvolver.

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Uvicorn](https://img.shields.io/badge/Uvicorn-ASGI-499848?logo=gunicorn&logoColor=white)](https://www.uvicorn.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![Vite](https://img.shields.io/badge/Vite-5-646CFF?logo=vite&logoColor=white)](https://vite.dev/)
[![nginx](https://img.shields.io/badge/nginx-1.27-009639?logo=nginx&logoColor=white)](https://nginx.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots/api)
[![Groq](https://img.shields.io/badge/Groq-API-F55036?logo=groq&logoColor=white)](https://groq.com/)

## Finalidade

A entrevista com o cliente deixa de ser anotada no papel. O áudio vira um documento que o time já consegue usar:

- problema, usuários e escopo da versão 1;
- o que fica fora da primeira versão;
- stack sugerida, ancorada em [`padrao_stack/STACK_PADRAO.md`](padrao_stack/STACK_PADRAO.md);
- roadmap passo a passo separado em backend e frontend, e critérios de aceite;
- planos de ação, cada um dizendo o que construir, quando está pronto e de qual outro depende.

Se o áudio não decide algo que muda a tecnologia (plataforma, dados, integração ou quem usa), a reunião fica **aberta**: o bot faz até duas perguntas, cada uma com duas opções, e guarda um relatório parcial. Os planos de ação só entram quando a análise fecha. O desenvolvedor pode fechar mesmo assim; o que continuar sem resposta fica em "Pontos em aberto" e não vira ticket.

O AnaliseR não gera código, não faz orçamento e não envia o PDF ao cliente.

## Stack

O produto em si e a stack que ele sugere para o sistema do cliente são coisas diferentes.

| Camada | Tecnologia |
|---|---|
| Bot e API | Python 3.11, FastAPI, Uvicorn |
| Telegram | python-telegram-bot 21 |
| Transcrição | Groq, Whisper `whisper-large-v3` |
| Relatório | Groq, `openai/gpt-oss-120b` |
| Banco | PostgreSQL 15, asyncpg |
| PDF | fpdf2, fonte DejaVu |
| Painel | React 18, Vite 5, nginx |
| Sessão do painel | JWT, 5 minutos |
| Execução | Docker Compose, `restart: unless-stopped` |

A referência injetada em toda análise está em [`padrao_stack/`](padrao_stack/): Bun, TypeScript, TanStack Start, Tailwind, Hono, PostgreSQL com Drizzle, Better Auth, Vitest e Playwright, mais o contrato de engenharia. Da stack é derivado um índice por camada que guia a escolha de tecnologia do roadmap: o roadmap fechado sai sempre em `### Backend` e `### Frontend`, e cada etapa cita uma tecnologia da lista da própria camada. O modelo usa essa base quando ela serve ao pedido e diz, na seção "Stack sugerida", o que foi trocado e por quê; se ele não separar as camadas, uma passada de correção reescreve só a seção do roadmap.

## Como usar

### Bot

O administrador libera o desenvolvedor no painel, com o ID numérico do Telegram e a permissão de usar o bot. Sem isso, o áudio não é transcrito.

1. Abra o bot e use `/menu`.
2. Escolha o cliente pelo nome ou pelo código `CLI-xxxx`. Cliente novo: `/novo_cliente` pede só o nome e devolve o código.
3. Na ficha, toque em **Nova reunião** e envie o áudio da conversa. Áudio solto, sem cliente, não entra.
4. Se a análise abrir, responda nos botões ou em texto. **Fechar mesmo assim** gera o documento final.
5. Na ficha, reunião aberta aparece como **Reunião N · aberta**, com **Continuar**. Reunião fechada abre o texto ou o PDF.

| Comando | O que faz |
|---|---|
| `/menu` | Lista os clientes |
| `/nova_reuniao` | Acrescenta uma reunião a um cliente |
| `/novo_cliente` | Cadastra o cliente e devolve o código |
| `/cancelar` | Desiste do passo em andamento |

O áudio aceito tem até 20 MB e 30 minutos. Há um minuto de espera entre áudios do mesmo usuário.

### Painel

O painel fica em `http://localhost:8091`. A API não tem porta no host: o nginx do frontend encaminha `/api/` para o backend pela rede interna. Para acesso externo, use o proxy TLS (seção anterior).

| Papel | O que pode |
|---|---|
| `admin` | Entra no painel e gerencia todos os usuários |
| `gestor` | Entra no painel e cadastra apenas usuários |
| `usuario` | Não entra no painel; usa o bot se estiver liberado |

A sessão expira em 5 minutos. O primeiro administrador é criado na subida do backend, com o e-mail e a senha definidos no ambiente.

## Subir o projeto

Requisitos: Docker e Docker Compose.

```bash
cp .env.example .env
```

Preencha o `.env` na sua máquina. Os nomes estão na seção seguinte. Não commite esse arquivo: o `.gitignore` já o ignora.

```bash
docker compose up -d --build
```

| Serviço | Porta no host |
|---|---|
| Painel | `8091` |
| API | só na rede interna do Compose (SEC-001) |
| PostgreSQL | só na rede interna do Compose |

Para expor o painel com HTTPS, defina `DOMAIN=painel.suaorg.com.br` no `.env` (domínio apontando para o host, portas 80/443 livres) e suba o proxy:

```bash
docker compose --profile tls up -d proxy
```

O Caddy emite/renova o certificado via Let's Encrypt e redireciona HTTP→HTTPS.

O esquema é aplicado na subida do backend, em [`backend/migracoes.py`](backend/migracoes.py). [`backend/init.sql`](backend/init.sql) cobre só o banco vazio.

## Variáveis de ambiente

Copie [`.env.example`](.env.example). Nenhum valor real entra no repositório.

| Variável | Uso |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token do bot, criado no BotFather |
| `GROQ_API_KEY` | Chave da API Groq |
| `GROQ_MODEL` | Modelo do relatório. Padrão do código: `openai/gpt-oss-120b` |
| `GROQ_WHISPER_MODEL` | Modelo da transcrição. Padrão: `whisper-large-v3` |
| `POSTGRES_USER` | Usuário do PostgreSQL |
| `POSTGRES_PASSWORD` | Senha do PostgreSQL |
| `POSTGRES_DB` | Nome do banco |
| `JWT_SECRET` | Segredo da sessão. Gere com `openssl rand -hex 32` |
| `JWT_EXPIRE_MINUTES` | Duração da sessão do painel. Padrão: `5` |
| `ADMIN_EMAIL` | E-mail do primeiro administrador |
| `ADMIN_PASSWORD` | Senha do primeiro administrador |

`DATABASE_URL` é montada pelo Compose. Não precisa constar no `.env`.

## Estrutura

```
backend/          bot, API, autenticação, banco, PDF e migrações
frontend/         login e painel de usuários
padrao_stack/     stack e contrato que a análise deve preferir
docker-compose.yml
.env.example
```
