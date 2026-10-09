"""SEC-004 (logout/JWT revogável), AUD-001 (trilha) e FEAT-002 (paginação)."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import api
import auth


def _cliente_http() -> AsyncClient:
    aplicativo = FastAPI()
    aplicativo.include_router(api.criar_rotas())
    return AsyncClient(transport=ASGITransport(app=aplicativo), base_url="http://test")


async def _criar_e_logar(cliente: AsyncClient, db, email: str, papel: str) -> dict:
    await db.criar_usuario(email, auth.hash_senha("senha-boa"), papel, None, True)
    resposta = await cliente.post("/auth/login", json={"email": email, "password": "senha-boa"})
    assert resposta.status_code == 200
    return {"Authorization": f"Bearer {resposta.json()['access_token']}"}


async def test_logout_revoga_o_token(db):
    async with _cliente_http() as cliente:
        headers = await _criar_e_logar(cliente, db, "adm@org.br", "admin")

        assert (await cliente.get("/users", headers=headers)).status_code == 200
        assert (await cliente.post("/auth/logout", headers=headers)).status_code == 204
        assert (await cliente.get("/users", headers=headers)).status_code == 401

        # Logout repetido não é erro.
        assert (await cliente.post("/auth/logout", headers=headers)).status_code == 204


async def test_logout_sem_token_devolve_204(db):
    async with _cliente_http() as cliente:
        assert (await cliente.post("/auth/logout")).status_code == 204


async def test_auditoria_registra_criacao_e_negacao(db):
    async with _cliente_http() as cliente:
        admin = await _criar_e_logar(cliente, db, "adm@org.br", "admin")
        gestor = await _criar_e_logar(cliente, db, "gestor@org.br", "gestor")

        # gestor não pode criar admin: 403 registrado
        negada = await cliente.post(
            "/users",
            json={"email": "x@org.br", "password": "12345678", "papel": "admin"},
            headers=gestor,
        )
        assert negada.status_code == 403

        # admin cria usuário: registrado
        criado = await cliente.post(
            "/users",
            json={"email": "novo@org.br", "password": "12345678", "papel": "usuario"},
            headers=admin,
        )
        assert criado.status_code == 201

    async with db.pool.acquire() as conn:
        linhas = await conn.fetch("SELECT acao, alvo_email FROM auditoria ORDER BY id")
    acoes = [(linha["acao"], linha["alvo_email"]) for linha in linhas]
    assert ("permissao_negada", "x@org.br") in acoes
    assert ("criar_usuario", "novo@org.br") in acoes


async def test_campo_desconhecido_rejeitado(db):
    usuario = await db.criar_usuario("alvo@org.br", auth.hash_senha("x" * 8), "usuario", None, False)

    with pytest.raises(ValueError):
        await db.atualizar_usuario(usuario["id"], {"senha_hash": "injetado"})


async def test_paginacao_de_usuarios(db):
    for i in range(3):
        await db.criar_usuario(f"u{i}@org.br", auth.hash_senha("12345678"), "usuario", None, False)

    async with _cliente_http() as cliente:
        headers = await _criar_e_logar(cliente, db, "adm@org.br", "admin")

        pagina1 = await cliente.get("/users?limit=2", headers=headers)
        assert pagina1.status_code == 200
        assert len(pagina1.json()) == 2

        pagina2 = await cliente.get("/users?limit=2&offset=2", headers=headers)
        assert pagina2.status_code == 200
        assert [p["email"] for p in pagina1.json()] + [p["email"] for p in pagina2.json()] == [
            "u0@org.br",
            "u1@org.br",
            "u2@org.br",
            "adm@org.br",
        ]


async def test_limite_acima_do_teto_rejeitado(db):
    async with _cliente_http() as cliente:
        headers = await _criar_e_logar(cliente, db, "adm@org.br", "admin")
        assert (await cliente.get("/users?limit=201", headers=headers)).status_code == 422
