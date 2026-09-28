"""Senha com bcrypt e JWT de sessão curta."""

import os
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_ALGORITMO = "HS256"


def hash_senha(senha: str) -> str:
    return _pwd.hash(senha)


def verificar_senha(senha: str, senha_hash: str) -> bool:
    return _pwd.verify(senha, senha_hash)


def minutos_expiracao() -> int:
    return int(os.environ.get("JWT_EXPIRE_MINUTES", "5"))


def criar_token(usuario_id: int, papel: str) -> str:
    expira_em = datetime.now(timezone.utc) + timedelta(minutes=minutos_expiracao())
    payload = {"sub": str(usuario_id), "papel": papel, "exp": expira_em}
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=_ALGORITMO)


def ler_token(token: str) -> dict:
    return jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[_ALGORITMO])
