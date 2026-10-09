"""Base de testes do AnaliseR.

Rodrigo rápido: os testes de unidade (auth, normalização) não pedem banco.
Os de integração (`db`) precisam de um PostgreSQL acessível em DATABASE_URL
(ex.: `docker compose up -d db` e DATABASE_URL=postgresql://...  localhost:5432).
"""

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("JWT_SECRET", "segredo-de-teste")
os.environ.setdefault("JWT_EXPIRE_MINUTES", "5")

# Banco de testes separado: nunca apontar os testes para o banco de produção.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://analiser:analiser@localhost:5432/analiser_test")

import asyncpg  # noqa: E402

import database as database_module  # noqa: E402


@pytest.fixture
async def db():
    """Banco real e limpo: esquema do init.sql + migrações, usuários vazios.

    Troca o singleton `database.db` (usado pela API/bot) pelo banco de testes.
    """
    os.environ["DATABASE_URL"] = DATABASE_URL
    try:
        conn = await asyncpg.connect(DATABASE_URL)
    except OSError as exc:
        pytest.skip(f"PostgreSQL de teste indisponível ({exc})")
    except asyncpg.PostgresError as exc:
        pytest.skip(f"PostgreSQL de teste indisponível ({exc}: {exc})")

    await conn.execute((BACKEND / "init.sql").read_text(encoding="utf-8"))
    await conn.close()

    database = database_module.Database()
    await database.connect()
    async with database.pool.acquire() as conn:
        await conn.execute("TRUNCATE usuarios RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE auditoria RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE tokens_revogados")

    anterior = database_module.db
    database_module.db = database
    import api as api_module

    api_module.db = database
    yield database
    api_module.db = anterior
    database_module.db = anterior
    await database.close()
