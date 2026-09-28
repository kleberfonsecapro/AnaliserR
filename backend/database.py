"""Conexão com o PostgreSQL e persistência das análises e dos usuários."""

import asyncio
import logging
import os

import asyncpg

logger = logging.getLogger("analiser.database")


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
                    await self._garantir_niveis()
                except Exception:
                    await self.pool.close()
                    self.pool = None
                    raise
                logger.info("conectado ao PostgreSQL")
                return
            except Exception as exc:
                last_error = exc
                logger.warning("banco indisponível, tentativa %s/10", attempt)
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

    async def inserir_analise(self, telegram_user_id: int, relatorio_gerado: str) -> int:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            analise_id = await conn.fetchval(
                """
                INSERT INTO analises_mvp (telegram_user_id, relatorio_gerado)
                VALUES ($1, $2)
                RETURNING id
                """,
                telegram_user_id,
                relatorio_gerado,
            )
        logger.info("análise %s gravada para o usuário %s", analise_id, telegram_user_id)
        return analise_id

    async def listar_analises(self, telegram_user_id: int) -> list[asyncpg.Record]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT id, relatorio_gerado, data_criacao
                FROM analises_mvp
                WHERE telegram_user_id = $1
                ORDER BY data_criacao ASC, id ASC
                """,
                telegram_user_id,
            )

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
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO usuarios (email, senha_hash, papel, pode_usar_bot)
                VALUES ($1, $2, 'admin', FALSE)
                ON CONFLICT (email) DO UPDATE SET senha_hash = EXCLUDED.senha_hash
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
