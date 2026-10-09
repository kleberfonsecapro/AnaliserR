"""Migrações de esquema aplicadas no boot.

O projeto nasceu com `init.sql`, que o compose nem monta: ele só roda em banco
vazio. Isso deixa qualquer alteração de esquema invisível num banco já
existindo, que é o caso de qualquer instalação real. Este módulo é a fonte
autoritativa do esquema e roda sempre, de forma idempotente, antes do bot
aceitar tráfego. `init.sql` cobre apenas as tabelas anteriores a este recurso.

Cada migração é registrada em `migracoes_aplicadas` para não reexecutar.
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger("analiser.migracoes")

MIGRACAO_0001_CLIENTES = "0001_clientes_e_reunioes"
MIGRACAO_0002_NOME_UNICO = "0002_nome_cliente_unico"
MIGRACAO_0003_SITUACAO = "0003_situacao_reuniao"
MIGRACAO_0004_CONSTRAINT_PAPEL = "0004_constraint_papel_usuarios"
MIGRACAO_0005_FIM_ANALISES_MVP = "0005_fim_analises_mvp"
MIGRACAO_0006_TOKENS_REVOGADOS = "0006_tokens_revogados"
MIGRACAO_0007_AUDITORIA = "0007_auditoria"
MIGRACAO_0008_PROMPT_VERSAO = "0008_prompt_versao"


async def aplicar(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS migracoes_aplicadas (
                nome       TEXT PRIMARY KEY,
                aplicada_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        aplicadas = {
            linha["nome"] for linha in await conn.fetch("SELECT nome FROM migracoes_aplicadas")
        }

        if MIGRACAO_0001_CLIENTES not in aplicadas:
            await _criar_clientes_e_reunioes(conn)
            await _migrar_reunioes_legadas(conn)
            await conn.execute(
                "INSERT INTO migracoes_aplicadas (nome) VALUES ($1) ON CONFLICT DO NOTHING",
                MIGRACAO_0001_CLIENTES,
            )
            logger.info("migração %s aplicada", MIGRACAO_0001_CLIENTES)
        else:
            logger.info("esquema em dia; %s já aplicada", MIGRACAO_0001_CLIENTES)

        if MIGRACAO_0002_NOME_UNICO not in aplicadas:
            await _garantir_nome_cliente_unico(conn)
            await conn.execute(
                "INSERT INTO migracoes_aplicadas (nome) VALUES ($1) ON CONFLICT DO NOTHING",
                MIGRACAO_0002_NOME_UNICO,
            )
            logger.info("migração %s aplicada", MIGRACAO_0002_NOME_UNICO)

        if MIGRACAO_0003_SITUACAO not in aplicadas:
            await _garantir_situacao_reuniao(conn)
            await conn.execute(
                "INSERT INTO migracoes_aplicadas (nome) VALUES ($1) ON CONFLICT DO NOTHING",
                MIGRACAO_0003_SITUACAO,
            )
            logger.info("migração %s aplicada", MIGRACAO_0003_SITUACAO)

        novas = [
            (MIGRACAO_0004_CONSTRAINT_PAPEL, _garantir_constraint_papel),
            (MIGRACAO_0005_FIM_ANALISES_MVP, _remover_analises_mvp),
            (MIGRACAO_0006_TOKENS_REVOGADOS, _criar_tokens_revogados),
            (MIGRACAO_0007_AUDITORIA, _criar_auditoria),
            (MIGRACAO_0008_PROMPT_VERSAO, _criar_prompt_versao),
        ]
        for nome, passo in novas:
            if nome not in aplicadas:
                await passo(conn)
                await conn.execute(
                    "INSERT INTO migracoes_aplicadas (nome) VALUES ($1) ON CONFLICT DO NOTHING",
                    nome,
                )
                logger.info("migração %s aplicada", nome)

        await _garantir_indices_reunioes(conn)


async def _criar_clientes_e_reunioes(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS clientes (
            id               SERIAL PRIMARY KEY,
            codigo           TEXT NOT NULL UNIQUE,
            nome             TEXT NOT NULL,
            nome_normalizado TEXT NOT NULL,
            criado_por       BIGINT,
            data_criacao     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reunioes (
            id               SERIAL PRIMARY KEY,
            cliente_id       INTEGER NOT NULL REFERENCES clientes (id) ON DELETE CASCADE,
            telegram_user_id BIGINT NOT NULL,
            numero           INTEGER NOT NULL,
            transcricao      TEXT,
            relatorio_gerado TEXT NOT NULL,
            data_criacao     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT reunioes_cliente_numero_unicos UNIQUE (cliente_id, numero)
        )
        """
    )
    # A base é compartilhada na agência: dois devs não podem criar dois
    # clientes com o mesmo nome normalizado, ou as reuniões ficariam partidas
    # entre homônimos.
    await conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS clientes_nome_normalizado_unico
            ON clientes (nome_normalizado)
        """
    )


async def _migrar_reunioes_legadas(conn: asyncpg.Connection) -> None:
    """Move as análises sem cliente para um cliente sintético por usuário.

    As reuniões anteriores ao recurso de clientes não têm nome de cliente. Em
    vez de inventar um vínculo, cada dono recebe um cliente marcado como
    legado, com reuniões numeradas na ordem original. Nenhum relatório é
    descartado.
    """
    if not await _tabela_existe(conn, "analises_mvp"):
        logger.info("tabela analises_mvp não existe; nada legado a migrar")
        return

    legadas = await conn.fetch(
        """
        SELECT a.telegram_user_id, a.relatorio_gerado, a.data_criacao
        FROM analises_mvp a
        WHERE NOT EXISTS (SELECT 1 FROM reunioes r WHERE r.data_criacao = a.data_criacao
                            AND r.relatorio_gerado = a.relatorio_gerado)
        ORDER BY a.telegram_user_id, a.data_criacao, a.id
        """
    )
    if not legadas:
        logger.info("nenhuma reunião legada para migrar")
        return

    por_dono: dict[int, list[asyncpg.Record]] = {}
    for linha in legadas:
        por_dono.setdefault(linha["telegram_user_id"], []).append(linha)

    for dono, reunioes_do_dono in por_dono.items():
        codigo = f"LEG-{dono}"
        cliente_id = await conn.fetchval(
            """
            INSERT INTO clientes (codigo, nome, nome_normalizado, criado_por)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (codigo) DO UPDATE SET nome = EXCLUDED.nome
            RETURNING id
            """,
            codigo,
            "Reuniões anteriores (sem cliente)",
            "reunioes anteriores sem cliente",
            dono,
        )
        for numero, reuniao in enumerate(reunioes_do_dono, start=1):
            await conn.execute(
                """
                INSERT INTO reunioes (cliente_id, telegram_user_id, numero,
                                      transcricao, relatorio_gerado, data_criacao)
                VALUES ($1, $2, $3, NULL, $4, $5)
                ON CONFLICT (cliente_id, numero) DO NOTHING
                """,
                cliente_id,
                dono,
                numero,
                reuniao["relatorio_gerado"],
                reuniao["data_criacao"],
            )
        logger.info(
            "migradas %d reuniões do usuário %s para o cliente legado %s",
            len(reunioes_do_dono),
            dono,
            codigo,
        )


async def _tabela_existe(conn: asyncpg.Connection, nome: str) -> bool:
    return bool(
        await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{nome}")
    )


async def _garantir_nome_cliente_unico(conn: asyncpg.Connection) -> None:
    """Impede dois clientes com o mesmo nome normalizado.

    A base é compartilhada na agência: sem esta restrição, dois devs podiam
    criar "Clínica São João" e "clinica sao joao" e as reuniões ficariam
    partidas entre homônimos. Instalações que já rodaram a 0001 não têm o
    índice, porque ele foi junto com a criação das tabelas.
    """
    duplicados = await conn.fetch(
        """
        SELECT nome_normalizado, COUNT(*) AS ocorrencias
        FROM clientes
        GROUP BY nome_normalizado
        HAVING COUNT(*) > 1
        ORDER BY nome_normalizado
        """
    )
    if duplicados:
        # Não some com dados: mantém o mais antigo e aponta o que sobrou, para
        # alguém decidir. Criar o índice agora falharia.
        for linha in duplicados:
            preservado = await conn.fetchrow(
                """
                SELECT c.id, c.codigo, c.nome, COUNT(r.id) AS reunioes
                FROM clientes c
                LEFT JOIN reunioes r ON r.cliente_id = c.id
                WHERE c.nome_normalizado = $1
                GROUP BY c.id, c.codigo, c.nome
                ORDER BY c.id
                """,
                linha["nome_normalizado"],
            )
            logger.warning(
                "cliente duplicado %r: %d cadastros, o mais antigo é %s (%s) com %s reuniões; "
                "os demais precisam ser mesclados à mão antes de criar o índice único",
                linha["nome_normalizado"],
                linha["ocorrencias"],
                preservado["nome"],
                preservado["codigo"],
                preservado["reunioes"],
            )
        return

    await conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS clientes_nome_normalizado_unico
            ON clientes (nome_normalizado)
        """
    )


async def _garantir_situacao_reuniao(conn: asyncpg.Connection) -> None:
    """Marca se o relatório da reunião ainda está aberto.

    Reunião já gravada entra como fechada: o documento dela foi entregue como
    final. Análise nova só nasce parcial quando o código pede isso.
    """
    await conn.execute(
        """
        ALTER TABLE reunioes
            ADD COLUMN IF NOT EXISTS situacao TEXT NOT NULL DEFAULT 'fechada'
        """
    )
    await conn.execute(
        "ALTER TABLE reunioes DROP CONSTRAINT IF EXISTS reunioes_situacao_ok"
    )
    await conn.execute(
        """
        ALTER TABLE reunioes
            ADD CONSTRAINT reunioes_situacao_ok
            CHECK (situacao IN ('parcial', 'fechada'))
        """
    )


async def _garantir_indices_reunioes(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS reunioes_cliente_idx
            ON reunioes (cliente_id, numero)
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS reunioes_telegram_user_id_idx
            ON reunioes (telegram_user_id)
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS clientes_nome_prefixo_idx
            ON clientes (nome_normalizado text_pattern_ops)
        """
    )


async def _garantir_constraint_papel(conn: asyncpg.Connection) -> None:
    """0004 — o antigo `_garantir_niveis()` rodava este DDL a CADA boot
    (OPS-002). Agora roda uma única vez, registrada como migração.
    """
    await conn.execute("ALTER TABLE usuarios DROP CONSTRAINT IF EXISTS usuarios_papel_check")
    await conn.execute("UPDATE usuarios SET papel = 'usuario' WHERE papel = 'user'")
    await conn.execute(
        """
        ALTER TABLE usuarios
            ADD CONSTRAINT usuarios_papel_check
            CHECK (papel IN ('admin', 'gestor', 'usuario'))
        """
    )


async def _remover_analises_mvp(conn: asyncpg.Connection) -> None:
    """0005 — a tabela analises_mvp era morta e com dado sensível (OPS-004).
    Os dados já foram migrados para `reunioes` pela 0001; aqui ela some."""
    await conn.execute("DROP TABLE IF EXISTS analises_mvp")
    logger.info("tabela analises_mvp removida; histórico já migrado para reunioes")


async def _criar_tokens_revogados(conn: asyncpg.Connection) -> None:
    """0006 — denylist de JWT para o logout (SEC-004)."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tokens_revogados (
            jti        TEXT PRIMARY KEY,
            expira_em  TIMESTAMPTZ NOT NULL,
            revogado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


async def _criar_auditoria(conn: asyncpg.Connection) -> None:
    """0007 — trilha de auditoria de mudanças de permissão (AUD-001)."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auditoria (
            id         SERIAL PRIMARY KEY,
            ator_id    INTEGER,
            acao       TEXT NOT NULL,
            alvo_id    INTEGER,
            alvo_email TEXT,
            valores    JSONB,
            ip         TEXT,
            criado_em  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS auditoria_criado_em_idx
            ON auditoria (criado_em)
        """
    )


async def _criar_prompt_versao(conn: asyncpg.Connection) -> None:
    """0008 — cada reunião passa a registrar a versão dos prompts (DEV-002)."""
    await conn.execute("ALTER TABLE reunioes ADD COLUMN IF NOT EXISTS prompt_versao TEXT")
