"""Recuperação de senha via Telegram: pedido de código e redefinição."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import api
import auth

FAKE_ENVIO = []


async def _envio_falso(tid, codigo):
    FAKE_ENVIO.append((tid, codigo))
    return True


def _monkeypatch_envio(monkeypatch):
    FAKE_ENVIO.clear()
    import api
    monkeypatch.setattr(api, "avisar_recuperacao", _envio_falso)
    return FAKE_ENVIO

def _cliente_http() -> AsyncClient:
    aplicativo = FastAPI()
    aplicativo.include_router(api.criar_rotas())
    return AsyncClient(transport=ASGITransport(app=aplicativo), base_url="http://test")


async def _criar_painelista(db, email: str, telegram_user_id=12345):
    return await db.criar_usuario(
        email, auth.hash_senha("senha-boa"), "admin", telegram_user_id, True
    )


async def test_recuperar_nao_revela_se_o_email_existe(db, monkeypatch):
    enviados = _monkeypatch_envio(monkeypatch)

    async with _cliente_http() as cliente:
        for email in ("fantasma@org.br", "outro@org.br"):
            resposta = await cliente.post("/auth/recuperar", json={"email": email})
            assert resposta.status_code == 202
            assert "mensagem" in resposta.json()
    assert enviados == []


async def test_recuperar_envia_codigo_no_telegram(db, monkeypatch):
    await _criar_painelista(db, "adm@org.br", telegram_user_id=777)
    enviados = _monkeypatch_envio(monkeypatch)

    async with _cliente_http() as cliente:
        resposta = await cliente.post("/auth/recuperar", json={"email": "adm@org.br"})
    assert resposta.status_code == 202
    assert len(enviados) == 1
    assert enviados[0][0] == 777
    assert len(enviados[0][1]) == 8


async def test_redefinir_troca_a_senha_com_codigo_certo(db, monkeypatch):
    await _criar_painelista(db, "adm@org.br")
    enviados = _monkeypatch_envio(monkeypatch)

    async with _cliente_http() as cliente:
        await cliente.post("/auth/recuperar", json={"email": "adm@org.br"})
        codigo = enviados[0][1]

        # Código errado não troca nada.
        errada = await cliente.post("/auth/redefinir", json={
            "email": "adm@org.br", "codigo": "XXXXXXXX", "password": "outra-senha-9",
        })
        assert errada.status_code == 400

        # Código certo troca.
        certa = await cliente.post("/auth/redefinir", json={
            "email": "adm@org.br", "codigo": codigo, "password": "senha-nova-123",
        })
        assert certa.status_code == 200

        # Código é de uso único.
        repetida = await cliente.post("/auth/redefinir", json={
            "email": "adm@org.br", "codigo": codigo, "password": "senha-nova-456",
        })
        assert repetida.status_code == 400

        # Login já aceita a senha nova.
        login = await cliente.post("/auth/login", json={
            "email": "adm@org.br", "password": "senha-nova-123",
        })
        assert login.status_code == 200


async def test_redefinir_sem_pedido_previo_falha(db):
    await _criar_painelista(db, "adm@org.br")
    async with _cliente_http() as cliente:
        resposta = await cliente.post("/auth/redefinir", json={
            "email": "adm@org.br", "codigo": "ABCD1234", "password": "qualquer-9",
        })
    assert resposta.status_code == 400


async def test_usuario_sem_telegram_nao_recebe_codigo(db, monkeypatch):
    await _criar_painelista(db, "sem-telegram@org.br", telegram_user_id=None)
    enviados = _monkeypatch_envio(monkeypatch)
    async with _cliente_http() as cliente:
        resposta = await cliente.post("/auth/recuperar", json={"email": "sem-telegram@org.br"})
    assert resposta.status_code == 202
    assert enviados == []


async def test_codigo_expira_e_limpa_da_tabela(db):
    usuario = await _criar_painelista(db, "adm@org.br")
    from datetime import UTC, datetime, timedelta

    await db.registrar_recuperacao(
        usuario["id"], auth.hash_senha("ABCD1234"), datetime.now(UTC) - timedelta(minutes=1)
    )
    assert await db.buscar_recuperacao(usuario["id"]) is None
