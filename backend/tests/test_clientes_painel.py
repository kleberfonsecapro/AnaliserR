"""Clientes do Telegram no painel: lista, relatório e exclusão."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import api
import auth


def _cliente_http() -> AsyncClient:
    aplicativo = FastAPI()
    aplicativo.include_router(api.criar_rotas())
    return AsyncClient(transport=ASGITransport(app=aplicativo), base_url="http://test")


async def _entrar(cliente: AsyncClient, db, email: str, papel: str) -> dict:
    await db.criar_usuario(email, auth.hash_senha("senha-boa"), papel, None, True)
    resposta = await cliente.post("/auth/login", json={"email": email, "password": "senha-boa"})
    assert resposta.status_code == 200
    return {"Authorization": f"Bearer {resposta.json()['access_token']}"}


async def test_painel_lista_relatorio_e_exclui_cliente(db, monkeypatch):
    monkeypatch.setattr(api, "gerar_pdf", lambda *_args, **_kwargs: b"%PDF-1.4 teste")
    criado = await db.criar_cliente("Clínica São João", "clinica sao joao", 1)
    await db.inserir_reuniao(
        criado["id"], 1, 1, "Problema: fila na recepção.", transcricao="áudio", situacao="fechada"
    )

    async with _cliente_http() as cliente:
        headers = await _entrar(cliente, db, "adm@org.br", "admin")

        lista = await cliente.get("/clientes", headers=headers)
        assert lista.status_code == 200
        assert lista.json()[0]["codigo"] == criado["codigo"]
        assert lista.json()[0]["nome"] == "Clínica São João"

        ficha = await cliente.get(f"/clientes/{criado['codigo']}", headers=headers)
        assert ficha.status_code == 200
        assert ficha.json()["reunioes"][0]["relatorio_gerado"] == "Problema: fila na recepção."

        pdf = await cliente.get(f"/clientes/{criado['codigo']}/reunioes/1/pdf", headers=headers)
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF")

        exclusao = await cliente.delete(f"/clientes/{criado['codigo']}", headers=headers)
        assert exclusao.status_code == 204
        assert (await cliente.get(f"/clientes/{criado['codigo']}", headers=headers)).status_code == 404
        assert (await cliente.get("/clientes", headers=headers)).json() == []

    async with db.pool.acquire() as conn:
        reunioes = await conn.fetchval("SELECT COUNT(*) FROM reunioes WHERE cliente_id = $1", criado["id"])
        auditoria = await conn.fetch("SELECT acao, alvo_email FROM auditoria")
    assert reunioes == 0
    assert ("excluir_cliente", criado["codigo"]) in [(linha["acao"], linha["alvo_email"]) for linha in auditoria]


async def test_usuario_comum_nao_ve_clientes(db):
    pessoa = await db.criar_usuario("comum", auth.hash_senha("senha-boa"), "usuario", None, True)
    token = auth.criar_token(pessoa["id"], "usuario")

    async with _cliente_http() as cliente:
        resposta = await cliente.get("/clientes", headers={"Authorization": f"Bearer {token}"})
    assert resposta.status_code == 403
