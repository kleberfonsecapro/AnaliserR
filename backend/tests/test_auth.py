"""Autenticação: hash bcrypt, verificação e JWT de sessão."""

import jwt
import pytest

import auth


def test_hash_senha_gera_bcrypt_verificavel():
    senha_hash = auth.hash_senha("trocar-em-producao")

    assert senha_hash.startswith("$2")
    assert senha_hash != "trocar-em-producao"
    assert auth.verificar_senha("trocar-em-producao", senha_hash)


def test_verificar_senha_rejeita_senha_errada():
    senha_hash = auth.hash_senha("correta")

    assert not auth.verificar_senha("errada", senha_hash)


def test_verificar_senha_rejeita_hash_invalido():
    assert not auth.verificar_senha("qualquer", "nao-e-bcrypt")


def test_senha_longa_nao_quebra_o_bcrypt():
    # O bcrypt considera só 72 bytes; senhas maiores devem funcionar
    # (mesmo comportamento do passlib, que truncava em silêncio).
    senha = "x" * 200
    senha_hash = auth.hash_senha(senha)

    assert auth.verificar_senha(senha, senha_hash)
    assert auth.verificar_senha("x" * 72, senha_hash)


def test_token_carrega_usuario_e_expira():
    token = auth.criar_token(7, "admin")
    payload = auth.ler_token(token)

    assert payload["sub"] == "7"
    assert payload["papel"] == "admin"


def test_token_invalido_e_rejeitado():
    with pytest.raises(jwt.InvalidTokenError):
        auth.ler_token("token-inventado")


def test_minutos_expiracao_vem_do_ambiente(monkeypatch):
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "15")
    assert auth.minutos_expiracao() == 15
