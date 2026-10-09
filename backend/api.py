"""API de login do admin e cadastro de quem pode usar o bot."""

import logging
import secrets
import string
from datetime import UTC, datetime, timedelta

import asyncpg
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from auth import criar_token, hash_senha, ler_token, minutos_expiracao, verificar_senha
from database import db
from mensagens import classificar_acesso
from notificacoes import avisar_boas_vindas, avisar_recuperacao
from pdf_relatorio import gerar_pdf
from rate_limit import LimitadorTaxa, limitador_login

logger = logging.getLogger("analiser.api")
_bearer = HTTPBearer(auto_error=False)
_LOGIN_MAX = 120
NIVEIS = ("admin", "gestor", "usuario")
_ENTRA_NO_PAINEL = ("admin", "gestor")
_limite_login = limitador_login()
_limite_recuperacao = LimitadorTaxa(maximo=3, janela_segundos=600)
_ALFABETO_CODIGO = string.ascii_uppercase + string.digits
_VALIDADE_CODIGO = timedelta(minutes=10)


def _ip_do_cliente(request: Request) -> str:
    """IP real: o frontend (nginx) injeta X-Forwarded-For ao fazer proxy."""
    encaminhado = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if encaminhado:
        return encaminhado
    return request.client.host if request.client else "desconhecido"


def _login(valor: str) -> str:
    """Usuário de entrada: nome livre ou e-mail. O @ não é obrigatório."""
    limpo = valor.strip().lower()
    if not limpo or any(caractere.isspace() for caractere in limpo) or len(limpo) > _LOGIN_MAX:
        raise ValueError("informe o usuário, sem espaços")
    return limpo


_limite_login = limitador_login()


def _ip_do_cliente(request: Request) -> str:
    """IP real atrás do nginx: primeiro do X-Forwarded-For, senão o socket."""
    encaminhado = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if encaminhado:
        return encaminhado
    return request.client.host if request.client else "?"


class LoginIn(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validar_email(cls, valor: str) -> str:
        return _login(valor)


class RecuperarIn(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validar_email(cls, valor: str) -> str:
        return _login(valor)


class RedefinirIn(BaseModel):
    email: str
    codigo: str = Field(min_length=8, max_length=8)
    password: str = Field(min_length=8)

    @field_validator("email")
    @classmethod
    def validar_email(cls, valor: str) -> str:
        return _login(valor)

    @field_validator("codigo")
    @classmethod
    def validar_codigo(cls, valor: str) -> str:
        return valor.strip().upper()


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
        return _login(valor)

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


class ClienteOut(BaseModel):
    codigo: str
    nome: str
    total_reunioes: int
    data_criacao: datetime


class ReuniaoOut(BaseModel):
    numero: int
    situacao: str
    data_criacao: datetime
    relatorio_gerado: str


class ClienteDetalhe(ClienteOut):
    reunioes: list[ReuniaoOut]


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
    jti = payload.get("jti")
    if jti and await db.token_revogado(jti):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sessão encerrada")
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
    async def login(corpo: LoginIn, request: Request) -> dict:
        ip = _ip_do_cliente(request)
        chave = f"{ip}|{corpo.email}"
        if _limite_login.bloqueado(chave):
            logger.warning("login bloqueado por excesso de tentativas: %s (%s)", corpo.email, ip)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "muitas tentativas; aguarde alguns minutos e tente de novo",
            )
        usuario = await db.buscar_usuario_por_email(corpo.email)
        if usuario is None or not verificar_senha(corpo.password, usuario["senha_hash"]):
            _limite_login.registrar_falha(chave)
            logger.warning("tentativa de login malsucedida: %s (%s)", corpo.email, ip)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "usuário ou senha incorretos")
        if usuario["papel"] not in _ENTRA_NO_PAINEL:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "sem acesso ao painel")
        _limite_login.registrar_sucesso(chave)
        token = criar_token(usuario["id"], usuario["papel"])
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": minutos_expiracao() * 60,
            "papel": usuario["papel"],
            "usuario_id": usuario["id"],
        }

    _RECUPERACAO_RESPOSTA = {
        "mensagem": "Se o usuário tiver acesso ao painel, um código chega no Telegram."
    }

    @rotas.post("/auth/recuperar", status_code=status.HTTP_202_ACCEPTED)
    async def recuperar(corpo: RecuperarIn, request: Request) -> dict:
        """Manda um código de uso único para o Telegram do usuário.

        Resposta e status são sempre os mesmos, exista ou não a conta: o
        endpoint não serve para descobrir quem está cadastrado.
        """
        ip = _ip_do_cliente(request)
        chave = f"rec|{ip}|{corpo.email}"
        if _limite_recuperacao.bloqueado(chave):
            logger.warning("recuperação bloqueada por excesso: %s (%s)", corpo.email, ip)
            return _RECUPERACAO_RESPOSTA
        _limite_recuperacao.registrar_falha(chave)

        usuario = await db.buscar_usuario_por_email(corpo.email)
        if (
            usuario is None
            or usuario["papel"] not in _ENTRA_NO_PAINEL
            or usuario["telegram_user_id"] is None
        ):
            return _RECUPERACAO_RESPOSTA
        codigo = "".join(secrets.choice(_ALFABETO_CODIGO) for _ in range(8))
        await db.registrar_recuperacao(
            usuario["id"], hash_senha(codigo), datetime.now(UTC) + _VALIDADE_CODIGO
        )
        if await avisar_recuperacao(usuario["telegram_user_id"], codigo):
            logger.info("recuperação de senha iniciada para %s", corpo.email)
        return _RECUPERACAO_RESPOSTA

    @rotas.post("/auth/redefinir")
    async def redefinir(corpo: RedefinirIn, request: Request) -> dict:
        """Troca a senha se o código estiver certo e dentro da validade."""
        usuario = await db.buscar_usuario_por_email(corpo.email)
        pedido = None if usuario is None else await db.buscar_recuperacao(usuario["id"])
        if (
            usuario is None
            or pedido is None
            or not verificar_senha(corpo.codigo, pedido["codigo_hash"])
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "código inválido ou vencido; peça outro"
            )
        await db.redefinir_senha(usuario["id"], hash_senha(corpo.password))
        await db.limpar_recuperacao(usuario["id"])
        logger.info("senha redefinida por recuperação para %s", corpo.email)
        await _auditar("redefinir_senha", usuario, ip=_ip_do_cliente(request))
        return {"mensagem": "Senha nova gravada. Entre com ela no painel."}

    @rotas.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(credenciais: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
        """Revoga o token atual (SEC-004). 204 sempre: repetir logout não é erro."""
        if credenciais is None:
            return
        try:
            payload = ler_token(credenciais.credentials)
        except jwt.PyJWTError:
            return
        jti = payload.get("jti")
        if jti:
            await db.revogar_token(jti, datetime.fromtimestamp(payload["exp"], tz=UTC))

    @rotas.get("/users", response_model=list[UsuarioOut])
    async def listar(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        _ator: asyncpg.Record = Depends(painel_atual),
    ) -> list[UsuarioOut]:
        return [_usuario(row) for row in await db.listar_usuarios(limit=limit, offset=offset)]

    @rotas.post("/users", response_model=UsuarioOut, status_code=status.HTTP_201_CREATED)
    async def criar(
        corpo: UsuarioIn,
        request: Request,
        ator: asyncpg.Record = Depends(painel_atual),
    ) -> UsuarioOut:
        if not _pode_atribuir(ator["papel"], corpo.papel):
            await _auditar(
                "permissao_negada",
                ator,
                alvo_email=corpo.email,
                valores={"papel": corpo.papel},
                ip=_ip_do_cliente(request),
            )
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
            raise HTTPException(status.HTTP_409_CONFLICT, "usuário ou ID do Telegram já cadastrado")
        logger.info("usuário %s cadastrado com nível %s", row["id"], row["papel"])
        await _auditar(
            "criar_usuario",
            ator,
            alvo_id=row["id"],
            alvo_email=row["email"],
            valores={"papel": row["papel"], "pode_usar_bot": row["pode_usar_bot"]},
            ip=_ip_do_cliente(request),
        )
        await _cumprimentar(row)
        return _usuario(row)

    @rotas.patch("/users/{usuario_id}", response_model=UsuarioOut)
    async def atualizar(
        usuario_id: int,
        corpo: UsuarioPatch,
        request: Request,
        ator: asyncpg.Record = Depends(painel_atual),
    ) -> UsuarioOut:
        alvo = await db.buscar_usuario_por_id(usuario_id)
        if alvo is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "usuário não encontrado")
        if not _pode_gerenciar(ator, alvo):
            await _auditar("permissao_negada", ator, alvo, campos=None, ip=_ip_do_cliente(request))
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
        await _auditar("atualizar_usuario", ator, alvo, campos, ip=_ip_do_cliente(request))
        if (
            campos.get("telegram_user_id") is not None
            and campos["telegram_user_id"] != alvo["telegram_user_id"]
        ):
            await _cumprimentar(row)
        return _usuario(row)

    @rotas.delete("/users/{usuario_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def excluir(
        usuario_id: int,
        request: Request,
        ator: asyncpg.Record = Depends(painel_atual),
    ) -> None:
        alvo = await db.buscar_usuario_por_id(usuario_id)
        if alvo is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "usuário não encontrado")
        if ator["id"] == alvo["id"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "não é possível remover o próprio usuário")
        if not _pode_gerenciar(ator, alvo):
            await _auditar("permissao_negada", ator, alvo, campos={"acao": "excluir"}, ip=_ip_do_cliente(request))
            raise HTTPException(status.HTTP_403_FORBIDDEN, "sem permissão para gerenciar este usuário")
        if alvo["papel"] == "admin" and await db.contar_admins() <= 1:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "mantenha ao menos um administrador")
        await db.excluir_usuario(usuario_id)
        await _auditar("excluir_usuario", ator, alvo, campos=None, ip=_ip_do_cliente(request))

    @rotas.get("/clientes", response_model=list[ClienteOut])
    async def listar_clientes(_ator: asyncpg.Record = Depends(painel_atual)) -> list[ClienteOut]:
        return [
            ClienteOut(
                codigo=row["codigo"],
                nome=row["nome"],
                total_reunioes=row["total_reunioes"],
                data_criacao=row["data_criacao"],
            )
            for row in await db.listar_clientes()
        ]

    @rotas.get("/clientes/{codigo}", response_model=ClienteDetalhe)
    async def detalhar_cliente(
        codigo: str,
        _ator: asyncpg.Record = Depends(painel_atual),
    ) -> ClienteDetalhe:
        cliente = await db.buscar_cliente_por_codigo(codigo)
        if cliente is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "cliente não encontrado")
        reunioes = await db.listar_reunioes_do_cliente(cliente["id"])
        return ClienteDetalhe(
            codigo=cliente["codigo"],
            nome=cliente["nome"],
            total_reunioes=cliente["total_reunioes"],
            data_criacao=cliente["data_criacao"],
            reunioes=[
                ReuniaoOut(
                    numero=reuniao["numero"],
                    situacao=reuniao["situacao"],
                    data_criacao=reuniao["data_criacao"],
                    relatorio_gerado=reuniao["relatorio_gerado"],
                )
                for reuniao in reunioes
            ],
        )

    @rotas.get("/clientes/{codigo}/reunioes/{numero}/pdf")
    async def pdf_da_reuniao(
        codigo: str,
        numero: int,
        _ator: asyncpg.Record = Depends(painel_atual),
    ) -> Response:
        cliente = await db.buscar_cliente_por_codigo(codigo)
        if cliente is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "cliente não encontrado")
        registro = await db.buscar_reuniao(cliente["id"], numero)
        if registro is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "reunião não encontrada")
        conteudo = gerar_pdf(
            registro["relatorio_gerado"],
            numero,
            registro["data_criacao"],
            registro["situacao"],
        )
        nome = f"AnaliseR-{cliente['codigo']}-reuniao-{numero}.pdf"
        return Response(
            content=conteudo,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{nome}"'},
        )

    @rotas.delete("/clientes/{codigo}", status_code=status.HTTP_204_NO_CONTENT)
    async def excluir_cliente(
        codigo: str,
        request: Request,
        ator: asyncpg.Record = Depends(painel_atual),
    ) -> None:
        cliente = await db.buscar_cliente_por_codigo(codigo)
        if cliente is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "cliente não encontrado")
        await db.excluir_cliente(cliente["id"])
        await _auditar(
            "excluir_cliente",
            ator,
            alvo_id=cliente["id"],
            alvo_email=cliente["codigo"],
            valores={"nome": cliente["nome"], "codigo": cliente["codigo"]},
            ip=_ip_do_cliente(request),
        )

    return rotas


async def _auditar(
    acao: str,
    ator: asyncpg.Record,
    alvo: asyncpg.Record | None = None,
    alvo_id: int | None = None,
    alvo_email: str | None = None,
    campos: dict | None = None,
    valores: dict | None = None,
    ip: str | None = None,
) -> None:
    """Falha de auditoria nunca derruba a operação; o log de erro cobre isso."""
    try:
        await db.registrar_auditoria(
            acao=acao,
            ator_id=ator["id"],
            alvo_id=(alvo["id"] if alvo else alvo_id),
            alvo_email=(alvo["email"] if alvo else alvo_email),
            valores=valores if valores is not None else campos,
            ip=ip,
        )
    except Exception:
        logger.exception("falha ao registrar auditoria de %s", acao)
