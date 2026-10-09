"""Senha com bcrypt e JWT de sessão curta."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

_ALGORITMO = "HS256"

# O bcrypt considera só os 72 primeiros bytes. Truncamos explicitamente para
# manter compatibilidade com o comportamento do passlib (truncamento silencioso)
# e não quebrar senhas longas já cadastradas.
_LIMITE_BCRYPT = 72


def _senha_bytes(senha: str) -> bytes:
    return senha.encode("utf-8")[:_LIMITE_BCRYPT]


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(_senha_bytes(senha), bcrypt.gensalt()).decode("utf-8")


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_senha_bytes(senha), senha_hash.encode("utf-8"))
    except ValueError:
        return False


def minutos_expiracao() -> int:
    return int(os.environ.get("JWT_EXPIRE_MINUTES", "5"))


def criar_token(usuario_id: int, papel: str) -> str:
    expira_em = datetime.now(UTC) + timedelta(minutes=minutos_expiracao())
    payload = {"sub": str(usuario_id), "papel": papel, "exp": expira_em, "jti": uuid.uuid4().hex}
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=_ALGORITMO)


def ler_token(token: str) -> dict:
    return jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[_ALGORITMO])
