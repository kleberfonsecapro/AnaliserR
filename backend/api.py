"""API de login do admin e cadastro de quem pode usar o bot."""

import logging
import re

import asyncpg
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from auth import criar_token, hash_senha, ler_token, minutos_expiracao, verificar_senha
from database import db
from mensagens import classificar_acesso
from notificacoes import avisar_boas_vindas

logger = logging.getLogger("analiser.api")
_bearer = HTTPBearer(auto_error=False)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")
NIVEIS = ("admin", "gestor", "usuario")
_ENTRA_NO_PAINEL = ("admin", "gestor")


def _email(valor: str) -> str:
    limpo = valor.strip().lower()
    if not _EMAIL.match(limpo):
        raise ValueError("e-mail inválido")
    return limpo


class LoginIn(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validar_email(cls, valor: str) -> str:
        return _email(valor)


def _nivel(valor: str) -> str:
    if valor not in NIVEIS:
        raise ValueError("nível de permissão inválido")
    return valor


class UsuarioIn(BaseModel):
    email: str
    password: str = Field(min_length=8)
    papel: str = "usuario"
    telegram_user_id: int | None = None
    pode_usar_bot: bool = False

    @field_validator("email")
    @classmethod
    def validar_email(cls, valor: str) -> str:
        return _email(valor)

    @field_validator("papel")
    @classmethod
    def validar_papel(cls, valor: str) -> str:
        return _nivel(valor)


class UsuarioPatch(BaseModel):
    papel: str | None = None
    telegram_user_id: int | None = None
    pode_usar_bot: bool | None = None

    @field_validator("papel")
    @classmethod
    def validar_papel(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        return _nivel(valor)


class UsuarioOut(BaseModel):
    id: int
    email: str
    papel: str
    telegram_user_id: int | None
    pode_usar_bot: bool


def _usuario(row: asyncpg.Record) -> UsuarioOut:
    return UsuarioOut(
        id=row["id"],
        email=row["email"],
        papel=row["papel"],
        telegram_user_id=row["telegram_user_id"],
        pode_usar_bot=row["pode_usar_bot"],
    )


def _pode_atribuir(ator: str, papel: str) -> bool:
    if ator == "admin":
        return True
    return ator == "gestor" and papel == "usuario"


def _pode_gerenciar(ator: asyncpg.Record, alvo: asyncpg.Record) -> bool:
    if ator["papel"] == "admin":
        return True
    return ator["papel"] == "gestor" and alvo["papel"] == "usuario"


async def _cumprimentar(row: asyncpg.Record) -> None:
    """Manda a mensagem de acesso no Telegram. Falha aqui não pode derrubar o cadastro."""
    telegram_user_id = row["telegram_user_id"]
    if telegram_user_id is None:
        return
    acesso = classificar_acesso(True, bool(row["pode_usar_bot"]))
    try:
        entregue = await avisar_boas_vindas(telegram_user_id, acesso)
    except Exception:
        logger.exception("falha inesperada ao avisar o usuário %s", telegram_user_id)
        return
    if not entregue:
        logger.info("mensagem não entregue para o usuário %s", telegram_user_id)


async def painel_atual(
    credenciais: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> asyncpg.Record:
    if credenciais is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "faça login")
    try:
        payload = ler_token(credenciais.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessão inválida ou expirada")
    try:
        usuario_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessão inválida ou expirada")
    usuario = await db.buscar_usuario_por_id(usuario_id)
    if usuario is None or usuario["papel"] not in _ENTRA_NO_PAINEL:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "sem acesso ao painel")
    return usuario


def criar_rotas() -> APIRouter:
    rotas = APIRouter()

    @rotas.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @rotas.post("/auth/login")
    async def login(corpo: LoginIn) -> dict:
        usuario = await db.buscar_usuario_por_email(corpo.email)
        if usuario is None or not verificar_senha(corpo.password, usuario["senha_hash"]):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "e-mail ou senha incorretos")
        if usuario["papel"] not in _ENTRA_NO_PAINEL:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "sem acesso ao painel")
        token = criar_token(usuario["id"], usuario["papel"])
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": minutos_expiracao() * 60,
            "papel": usuario["papel"],
            "usuario_id": usuario["id"],
        }

    @rotas.get("/users", response_model=list[UsuarioOut])
    async def listar(_ator: asyncpg.Record = Depends(painel_atual)) -> list[UsuarioOut]:
        return [_usuario(row) for row in await db.listar_usuarios()]

    @rotas.post("/users", response_model=UsuarioOut, status_code=status.HTTP_201_CREATED)
    async def criar(
        corpo: UsuarioIn,
        ator: asyncpg.Record = Depends(painel_atual),
    ) -> UsuarioOut:
        if not _pode_atribuir(ator["papel"], corpo.papel):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "gestor só cadastra usuários")
        try:
            row = await db.criar_usuario(
                corpo.email,
                hash_senha(corpo.password),
                corpo.papel,
                corpo.telegram_user_id,
                corpo.pode_usar_bot,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status.HTTP_409_CONFLICT, "e-mail ou ID do Telegram já cadastrado")
        logger.info("usuário %s cadastrado com nível %s", row["id"], row["papel"])
        await _cumprimentar(row)
        return _usuario(row)

    @rotas.patch("/users/{usuario_id}", response_model=UsuarioOut)
    async def atualizar(
        usuario_id: int,
        corpo: UsuarioPatch,
        ator: asyncpg.Record = Depends(painel_atual),
    ) -> UsuarioOut:
        alvo = await db.buscar_usuario_por_id(usuario_id)
        if alvo is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "usuário não encontrado")
        if not _pode_gerenciar(ator, alvo):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "sem permissão para gerenciar este usuário")
        campos = corpo.model_dump(exclude_unset=True)
        if "papel" in campos and campos["papel"] != alvo["papel"]:
            if ator["id"] == alvo["id"]:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "não é possível alterar o próprio nível")
            if not _pode_atribuir(ator["papel"], campos["papel"]):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "gestor só cadastra usuários")
            if alvo["papel"] == "admin" and campos["papel"] != "admin" and await db.contar_admins() <= 1:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "mantenha ao menos um administrador")
        try:
            row = await db.atualizar_usuario(usuario_id, campos)
        except asyncpg.UniqueViolationError:
            raise HTTPException(status.HTTP_409_CONFLICT, "ID do Telegram já cadastrado")
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "usuário não encontrado")
        if (
            campos.get("telegram_user_id") is not None
            and campos["telegram_user_id"] != alvo["telegram_user_id"]
        ):
            await _cumprimentar(row)
        return _usuario(row)

    @rotas.delete("/users/{usuario_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def excluir(usuario_id: int, ator: asyncpg.Record = Depends(painel_atual)) -> None:
        alvo = await db.buscar_usuario_por_id(usuario_id)
        if alvo is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "usuário não encontrado")
        if ator["id"] == alvo["id"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "não é possível remover o próprio usuário")
        if not _pode_gerenciar(ator, alvo):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "sem permissão para gerenciar este usuário")
        if alvo["papel"] == "admin" and await db.contar_admins() <= 1:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "mantenha ao menos um administrador")
        await db.excluir_usuario(usuario_id)

    return rotas
