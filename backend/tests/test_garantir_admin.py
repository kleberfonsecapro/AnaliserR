"""BUG-001: garantir_admin eleva o papel sem resetar a senha a cada boot."""

import pytest

import auth

pytestmark = pytest.mark.asyncio


async def test_cria_admin_quando_email_nao_existe(db):
    await db.garantir_admin("dono@org.br", auth.hash_senha("s3nh4"))

    usuario = await db.buscar_usuario_por_email("dono@org.br")
    assert usuario is not None
    assert usuario["papel"] == "admin"
    assert usuario["pode_usar_bot"] is True
    assert auth.verificar_senha("s3nh4", usuario["senha_hash"])


async def test_promove_usuario_existente_sem_tocar_na_senha(db):
    senha_hash_original = auth.hash_senha("minha-senha")
    await db.criar_usuario(
        email="joao@org.br",
        senha_hash=senha_hash_original,
        papel="usuario",
        telegram_user_id=None,
        pode_usar_bot=False,
    )

    await db.garantir_admin("joao@org.br", auth.hash_senha("outra-senha"))

    usuario = await db.buscar_usuario_por_email("joao@org.br")
    assert usuario["papel"] == "admin"
    assert usuario["pode_usar_bot"] is True
    # A senha NÃO pode ser sobrescrita pelo boot.
    assert usuario["senha_hash"] == senha_hash_original
    assert auth.verificar_senha("minha-senha", usuario["senha_hash"])
    assert not auth.verificar_senha("outra-senha", usuario["senha_hash"])


async def test_dois_boots_seguidos_nao_alteram_nada(db):
    await db.garantir_admin("dono@org.br", auth.hash_senha("s3nh4"))
    antes = await db.buscar_usuario_por_email("dono@org.br")

    await db.garantir_admin("dono@org.br", auth.hash_senha("s3nh4"))

    depois = await db.buscar_usuario_por_email("dono@org.br")
    assert dict(depois) == dict(antes)
