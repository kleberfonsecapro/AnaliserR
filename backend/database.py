"""Conexão com o PostgreSQL e persistência de clientes, reuniões e usuários."""

import asyncio
import logging
import os
import unicodedata

import asyncpg

import migracoes

logger = logging.getLogger("analiser.database")


def _falha_de_conexao(erro: Exception) -> bool:
    """Só vale a pena repetir se o problema for o banco fora do ar.

    A lógica é invertida de propósito: em vez de listar o que é bug, o que se
    pergunta é se o erro é de conexão. Qualquer outra coisa — NameError,
    MigrationError, constraint violada — é defeito de código, e repetir dez
    vezes só esconde a causa atrás de "banco indisponível".
    """
    se_conexao = (
        asyncpg.CannotConnectNowError,
        asyncpg.TooManyConnectionsError,
        ConnectionError,
        OSError,
        asyncio.TimeoutError,
    )
    if isinstance(erro, se_conexao):
        return True
    # Driver de conexão: falha de socket/DNS é transitória, mas erro de protocolo
    # ou credencial não é.
    return isinstance(erro, asyncpg.PostgresError) and not isinstance(
        erro,
        (
            asyncpg.UndefinedTableError,
            asyncpg.UndefinedColumnError,
            asyncpg.UndefinedFunctionError,
            asyncpg.SyntaxOrAccessError,
            asyncpg.DuplicateTableError,
            asyncpg.InvalidCatalogNameError,
        ),
    )


def normalizar_pesquisa(texto: str) -> str:
    """Minúsculas e sem acentos, para comparar nomes digitados no Telegram."""
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() or c.isspace() else " " for c in sem_acento).split())


class Database:
    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        dsn = os.environ["DATABASE_URL"]
        last_error: Exception | None = None
        for attempt in range(1, 11):
            try:
                self.pool = await asyncpg.create_pool(dsn)
                try:
                    await migracoes.aplicar(self.pool)
                    await self._garantir_niveis()
                except Exception:
                    await self.pool.close()
                    self.pool = None
                    raise
                logger.info("conectado ao PostgreSQL")
                return
            except Exception as exc:
                last_error = exc
                # A causa real importa: uma migração com bug de sintaxe é
                # indistinguível de banco fora do ar se o log só diz
                # "indisponível". Logar a exceção, mas não repetir 10x um erro
                # que não vai se resolver com espera.
                logger.warning("falha ao conectar (tentativa %s/10): %s: %s",
                               attempt, type(exc).__name__, exc, exc_info=True)
                if not _falha_de_conexao(exc):
                    logger.error("erro que não se resolve com espera; abortando")
                    raise
                await asyncio.sleep(2)
        assert last_error is not None
        raise last_error

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def _garantir_niveis(self) -> None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            restricoes = await conn.fetch(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'usuarios'::regclass AND contype = 'c'
                """
            )
            for restricao in restricoes:
                await conn.execute(
                    f'ALTER TABLE usuarios DROP CONSTRAINT "{restricao["conname"]}"'
                )
            await conn.execute("UPDATE usuarios SET papel = 'usuario' WHERE papel = 'user'")
            await conn.execute(
                """
                ALTER TABLE usuarios
                ADD CONSTRAINT usuarios_papel_check
                CHECK (papel IN ('admin', 'gestor', 'usuario'))
                """
            )

    # ---------------------------------------------------------------- clientes

    async def criar_cliente(
        self,
        nome: str,
        nome_normalizado: str,
        criado_por: int | None,
    ) -> asyncpg.Record:
        """Devolve o cliente já cadastrado com esse nome, ou cria um novo.

        A base é compartilhada na agência, então dois devs digitando "Clínica
        São João" precisam cair no mesmo cliente. Sem esta guarda, as reuniões
        ficariam partedas entre homônimos.
        """
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            existente = await conn.fetchrow(
                """
                SELECT id, codigo, nome, nome_normalizado, data_criacao
                FROM clientes
                WHERE nome_normalizado = $1
                """,
                nome_normalizado,
            )
            if existente is not None:
                logger.info(
                    "cliente %s reaproveitado para %r (já existia como %r)",
                    existente["codigo"],
                    nome,
                    existente["nome"],
                )
                return existente
            return await conn.fetchrow(
                """
                INSERT INTO clientes (codigo, nome, nome_normalizado, criado_por)
                VALUES (
                    'CLI-' || LPAD(
                        (
                            SELECT COALESCE(MAX(SUBSTRING(c.codigo FROM 5)::int), 0) + 1
                            FROM clientes c
                            WHERE c.codigo ~ '^CLI-[0-9]+$'
                        )::text, 4, '0'
                    ),
                    $1, $2, $3
                )
                RETURNING id, codigo, nome, nome_normalizado, data_criacao
                """,
                nome,
                nome_normalizado,
                criado_por,
            )

    async def buscar_cliente_por_codigo(self, codigo: str) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT c.id, c.codigo, c.nome, c.nome_normalizado,
                       COUNT(r.id)::int AS total_reunioes,
                       COALESCE(MAX(r.numero), 0) AS ultima_reuniao
                FROM clientes c
                LEFT JOIN reunioes r ON r.cliente_id = c.id
                WHERE UPPER(c.codigo) = UPPER($1)
                GROUP BY c.id
                """,
                codigo.strip(),
            )

    async def buscar_cliente_por_nome(self, termo: str) -> list[asyncpg.Record]:
        """Busca por nome, ignorando acentos e caixa. Prefere quem começa com o termo."""
        assert self.pool is not None
        alvo = normalizar_pesquisa(termo)
        if not alvo:
            return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT c.id, c.codigo, c.nome,
                       COUNT(r.id)::int AS total_reunioes,
                       COALESCE(MAX(r.numero), 0) AS ultima_reuniao
                FROM clientes c
                LEFT JOIN reunioes r ON r.cliente_id = c.id
                WHERE c.nome_normalizado LIKE $1 || '%'
                   OR c.nome_normalizado LIKE '% ' || $1 || '%'
                GROUP BY c.id
                ORDER BY
                    (c.nome_normalizado LIKE $1 || '%') DESC,
                    c.nome_normalizado ASC
                LIMIT 10
                """,
                alvo,
            )

    async def listar_clientes(self) -> list[asyncpg.Record]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT c.id, c.codigo, c.nome,
                       COUNT(r.id)::int AS total_reunioes,
                       COALESCE(MAX(r.numero), 0) AS ultima_reuniao,
                       c.data_criacao
                FROM clientes c
                LEFT JOIN reunioes r ON r.cliente_id = c.id
                GROUP BY c.id
                ORDER BY c.nome_normalizado ASC
                """
            )

    # --------------------------------------------------------------- reuniões

    async def inserir_reuniao(
        self,
        cliente_id: int,
        telegram_user_id: int,
        numero: int,
        relatorio_gerado: str,
        transcricao: str | None = None,
        situacao: str = "fechada",
    ) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            reuniao_id = await conn.fetchval(
                """
                INSERT INTO reunioes
                    (cliente_id, telegram_user_id, numero, transcricao,
                     relatorio_gerado, situacao)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                cliente_id,
                telegram_user_id,
                numero,
                transcricao,
                relatorio_gerado,
                situacao,
            )
        logger.info(
            "reunião %s do cliente %s gravada para o usuário %s",
            numero,
            cliente_id,
            telegram_user_id,
        )
        return reuniao_id

    async def atualizar_reuniao(
        self,
        cliente_id: int,
        numero: int,
        relatorio_gerado: str,
        transcricao: str | None,
        situacao: str,
    ) -> None:
        """Reescreve o relatório da mesma reunião. Não abre outro número."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE reunioes
                SET relatorio_gerado = $3, transcricao = $4, situacao = $5
                WHERE cliente_id = $1 AND numero = $2
                """,
                cliente_id,
                numero,
                relatorio_gerado,
                transcricao,
                situacao,
            )
        if status == "UPDATE 0":
            raise LookupError(f"reunião {numero} do cliente {cliente_id} não existe")
        logger.info(
            "reunião %s do cliente %s atualizada (%s)",
            numero,
            cliente_id,
            situacao,
        )

    async def listar_reunioes_do_cliente(self, cliente_id: int) -> list[asyncpg.Record]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT id, numero, relatorio_gerado, transcricao, data_criacao, situacao
                FROM reunioes
                WHERE cliente_id = $1
                ORDER BY numero ASC
                """,
                cliente_id,
            )

    async def buscar_reuniao(self, cliente_id: int, numero: int) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT id, numero, relatorio_gerado, transcricao, data_criacao, situacao
                FROM reunioes
                WHERE cliente_id = $1 AND numero = $2
                """,
                cliente_id,
                numero,
            )

    async def proximo_numero_reuniao(self, cliente_id: int) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            ultimo = await conn.fetchval(
                "SELECT COALESCE(MAX(numero), 0) FROM reunioes WHERE cliente_id = $1",
                cliente_id,
            )
        return int(ultimo) + 1

    async def listar_reunioes_do_usuario(self, telegram_user_id: int) -> list[asyncpg.Record]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT r.id, r.numero, r.data_criacao, r.relatorio_gerado, r.situacao,
                       c.id AS cliente_id, c.codigo AS cliente_codigo, c.nome AS cliente_nome
                FROM reunioes r
                JOIN clientes c ON c.id = r.cliente_id
                WHERE r.telegram_user_id = $1
                ORDER BY r.data_criacao DESC, r.id DESC
                """,
                telegram_user_id,
            )

    async def ultima_reuniao(self) -> asyncpg.Record | None:
        """A reunião mais recente de todos, com o cliente dela."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT r.numero, r.data_criacao, r.relatorio_gerado, r.situacao,
                       r.total_reunioes, r.ultima_reuniao,
                       c.id AS cliente_id, c.id, c.codigo, c.nome
                FROM (
                    SELECT cliente_id, numero, data_criacao, relatorio_gerado, situacao,
                           COUNT(*) OVER (PARTITION BY cliente_id)::int AS total_reunioes,
                           MAX(numero) OVER (PARTITION BY cliente_id) AS ultima_reuniao
                    FROM reunioes
                ) r
                JOIN clientes c ON c.id = r.cliente_id
                ORDER BY r.data_criacao DESC, r.numero DESC
                LIMIT 1
                """
            )

    async def total_reunioes(self) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM reunioes"))

    async def buscar_usuario_por_telegram_id(self, telegram_user_id: int) -> asyncpg.Record | None:
        """Procura pelo ID do Telegram. None = ainda não cadastrado."""
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT id, email, papel, pode_usar_bot
                FROM usuarios
                WHERE telegram_user_id = $1
                """,
                telegram_user_id,
            )

    async def buscar_usuario_por_id(self, usuario_id: int) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT id, email, senha_hash, papel, telegram_user_id, pode_usar_bot
                FROM usuarios
                WHERE id = $1
                """,
                usuario_id,
            )

    async def contar_admins(self) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM usuarios WHERE papel = 'admin'")
        return int(total)

    async def buscar_usuario_por_email(self, email: str) -> asyncpg.Record | None:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT id, email, senha_hash, papel, telegram_user_id, pode_usar_bot
                FROM usuarios
                WHERE email = $1
                """,
                email,
            )

    async def garantir_admin(self, email: str, senha_hash: str) -> None:
        """Cria o admin na primeira vez; garante papel e acesso.

        Não atualiza a senha a cada boot (bcrypt usa salt diferente). Se o admin
        quiser trocar a senha, deve usar o painel ou um script dedicado.
        """
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO usuarios (email, senha_hash, papel, pode_usar_bot)
                VALUES ($1, $2, 'admin', TRUE)
                ON CONFLICT (email) DO UPDATE SET
                    papel         = CASE WHEN usuarios.papel <> 'admin'
                                         THEN 'admin'
                                         ELSE usuarios.papel END,
                    pode_usar_bot = CASE WHEN usuarios.pode_usar_bot <> TRUE
                                         THEN TRUE
                                         ELSE usuarios.pode_usar_bot END
                """,
                email,
                senha_hash,
            )
        logger.info("admin conferido para %s", email)

    async def listar_usuarios(self) -> list[asyncpg.Record]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT id, email, papel, telegram_user_id, pode_usar_bot
                FROM usuarios
                ORDER BY id
                """
            )

    async def criar_usuario(
        self,
        email: str,
        senha_hash: str,
        papel: str,
        telegram_user_id: int | None,
        pode_usar_bot: bool,
    ) -> asyncpg.Record:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                INSERT INTO usuarios (email, senha_hash, papel, telegram_user_id, pode_usar_bot)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, email, papel, telegram_user_id, pode_usar_bot
                """,
                email,
                senha_hash,
                papel,
                telegram_user_id,
                pode_usar_bot,
            )

    async def atualizar_usuario(
        self,
        usuario_id: int,
        campos: dict,
    ) -> asyncpg.Record | None:
        assert self.pool is not None
        atribuicoes = []
        valores: list = []
        if "telegram_user_id" in campos:
            valores.append(campos["telegram_user_id"])
            atribuicoes.append(f"telegram_user_id = ${len(valores)}")
        if "pode_usar_bot" in campos:
            valores.append(campos["pode_usar_bot"])
            atribuicoes.append(f"pode_usar_bot = ${len(valores)}")
        if "papel" in campos:
            valores.append(campos["papel"])
            atribuicoes.append(f"papel = ${len(valores)}")
        if not atribuicoes:
            async with self.pool.acquire() as conn:
                return await conn.fetchrow(
                    """
                    SELECT id, email, papel, telegram_user_id, pode_usar_bot
                    FROM usuarios
                    WHERE id = $1
                    """,
                    usuario_id,
                )
        valores.append(usuario_id)
        sql = f"""
            UPDATE usuarios
            SET {", ".join(atribuicoes)}
            WHERE id = ${len(valores)}
            RETURNING id, email, papel, telegram_user_id, pode_usar_bot
        """
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(sql, *valores)

    async def excluir_usuario(self, usuario_id: int) -> bool:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            resultado = await conn.execute("DELETE FROM usuarios WHERE id = $1", usuario_id)
        return resultado.endswith("1")


db = Database()
