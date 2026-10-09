"""SEC-002: rate limit no login — brute force devolve 429 e fica registrado."""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api
import auth
from rate_limit import LimitadorTaxa

# ------------------------------------------------------------------ limitador


def test_bloqueia_depois_do_maximo_dentro_da_janela():
    limite = LimitadorTaxa(maximo=3, janela_segundos=60)

    for _ in range(3):
        limite.registrar_falha("ip|email")
    assert limite.bloqueado("ip|email")
    assert not limite.bloqueado("outro-ip|email")


def test_sucesso_limpa_o_contador():
    limite = LimitadorTaxa(maximo=2, janela_segundos=60)

    limite.registrar_falha("k")
    limite.registrar_falha("k")
    assert limite.bloqueado("k")

    limite.registrar_sucesso("k")
    assert not limite.bloqueado("k")


def test_falhas_antigas_sair_da_janela(monkeypatch):
    relogio = [0.0]
    monkeypatch.setattr("rate_limit.time.monotonic", lambda: relogio[0])
    limite = LimitadorTaxa(maximo=2, janela_segundos=60)

    limite.registrar_falha("k")
    relogio[0] = 120.0
    assert limite._tentativas_recentes("k") == 0

    limite.registrar_falha("k")
    assert not limite.bloqueado("k")  # só 1 falha recente


# -------------------------------------------------------------------- endpoint


@pytest.fixture
def client(monkeypatch):
    """App mínima com o login, banco isolado e janela de limpeza controlada."""
    # Limpa o estado entre testes: o limitador é singleton do módulo.
    monkeypatch.setattr(api, "_limite_login", LimitadorTaxa(maximo=3, janela_segundos=60))
    aplicativo = FastAPI()
    aplicativo.include_router(api.criar_rotas())
    return TestClient(aplicativo, raise_server_exceptions=False)


def test_login_bloqueia_apos_tentativas_excedentes(client, monkeypatch):
    async def sem_usuario(email):
        return None

    monkeypatch.setattr(api.db, "buscar_usuario_por_email", sem_usuario)
    corpo = {"email": "alvo@org.br", "password": "errada"}

    for _ in range(3):
        assert client.post("/auth/login", json=corpo).status_code == 401

    resposta = client.post("/auth/login", json=corpo)
    assert resposta.status_code == 429
    assert "muitas tentativas" in resposta.json()["detail"]


def test_login_registra_tentativa_malsucedida_com_ip_e_email(client, monkeypatch, caplog):
    async def sem_usuario(email):
        return None

    monkeypatch.setattr(api.db, "buscar_usuario_por_email", sem_usuario)

    with caplog.at_level(logging.WARNING, logger="analiser.api"):
        client.post(
            "/auth/login",
            json={"email": "alvo@org.br", "password": "errada"},
            headers={"X-Forwarded-For": "203.0.113.9"},
        )

    assert any(
        "alvo@org.br" in registro.getMessage() and "203.0.113.9" in registro.getMessage()
        for registro in caplog.records
    )


def test_login_correto_reseta_janela(client, monkeypatch):
    usuario = {"id": 1, "papel": "admin", "senha_hash": auth.hash_senha("senha-certa")}

    async def buscar(email):
        return usuario

    monkeypatch.setattr(api.db, "buscar_usuario_por_email", buscar)

    # 2 falhas anteriores: o login certo zera o contador e não bloqueia.
    limite = api._limite_login
    limite.registrar_falha("testclient|ok@org.br")
    limite.registrar_falha("testclient|ok@org.br")

    resposta = client.post("/auth/login", json={"email": "ok@org.br", "password": "senha-certa"})
    assert resposta.status_code == 200
    assert not limite.bloqueado("testclient|ok@org.br")
