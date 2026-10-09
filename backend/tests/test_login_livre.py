"""O usuário de entrada aceita nome livre. O @ continua válido para contas antigas."""

import pytest
from pydantic import ValidationError

import api


def test_aceita_nome_sem_arroba():
    assert api.LoginIn(email="  Kleber ", password="x").email == "kleber"
    assert api.UsuarioIn(email="maria.souza", password="12345678").email == "maria.souza"


def test_aceita_email_antigo():
    entrada = api.LoginIn(email="Admin@Analiser.Local", password="x")
    assert entrada.email == "admin@analiser.local"


def test_rejeita_espaco_e_vazio():
    with pytest.raises(ValidationError):
        api.LoginIn(email="kleber fonseca", password="x")
    with pytest.raises(ValidationError):
        api.LoginIn(email="   ", password="x")
