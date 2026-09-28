"""Bot do Telegram e processo que também sobe a API do admin."""

import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from groq import AsyncGroq
from telegram import InputFile, Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from api import criar_rotas
from auth import hash_senha
from database import db
from mensagens import (
    SEM_REUNIAO,
    Acesso,
    classificar_acesso,
    eh_saudacao,
    indice_reuniao,
    linha_saudacao,
    mensagem_de_boas_vindas,
    mensagem_de_recusa,
    quer_documento,
)
from pdf_relatorio import formatar_data, gerar_pdf
from notificacoes import descrever_bot, registrar_bot
import referencia as referencia_mod

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("analiser.bot")

LIMITE_TELEGRAM = 4096
SYSTEM_PROMPT = """Você é o tech lead de IA e analista de requisitos do AnaliseR.
Recebe a transcrição de uma conversa com o cliente e devolve um relatório em Markdown.

Regras:
- Extraia só o necessário para um produto mínimo viável.
- Corte funcionalidades que não são essenciais para a primeira versão e liste-as em "Fora da versão 1".
- Não invente requisitos que não estejam na transcrição. O que faltar fica em "Pontos em aberto".
- A stack padrão da organização é a primeira opção. Use-a sempre que servir ao pedido, sem listar dezenas de alternativas.
- Se alguma tecnologia da stack padrão não servir ao pedido do cliente (aplicativo mobile nativo, IoT, machine learning, jogo, sistema embarcado), use o que servir e diga no topo da seção "Stack sugerida" qual tecnologia foi descartada e por quê, em uma ou duas frases. Sem justificativa, use a stack padrão.
- O contrato de engenharia recebido é condição de entrega, não preferência. Converta cada uma das suas práticas em uma etapa concreta do "Roadmap passo a passo" e em um item verificável de "Critérios de aceite".
- O relatório deve servir como guia para começar a desenvolver.

Use exatamente estas seções:
# MVP
## Problema
## Usuários
## Escopo da versão 1
## Fora da versão 1
## Stack sugerida
## Roadmap passo a passo
## Critérios de aceite
## Pontos em aberto
"""

_groq: AsyncGroq | None = None
_telegram: Application | None = None


def fatiar_texto(texto: str, limite: int = LIMITE_TELEGRAM) -> list[str]:
    restante = texto.strip()
    if not restante:
        return ["(relatório vazio)"]
    partes: list[str] = []
    while restante:
        if len(restante) <= limite:
            partes.append(restante)
            break
        corte = restante.rfind("\n", 0, limite)
        if corte < limite // 2:
            corte = restante.rfind(" ", 0, limite)
        if corte < limite // 2:
            corte = limite
        partes.append(restante[:corte].strip())
        restante = restante[corte:].strip()
    return partes


async def transcrever(caminho: Path) -> str:
    assert _groq is not None
    resultado = await _groq.audio.transcriptions.create(
        file=(caminho.name, caminho.read_bytes()),
        model=os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3"),
        language="pt",
        response_format="json",
    )
    texto = resultado if isinstance(resultado, str) else getattr(resultado, "text", str(resultado))
    return texto.strip()


async def analisar(transcricao: str) -> str:
    assert _groq is not None
    referencia = referencia_mod.hash_referencia()
    logger.info("analisando com a referência %s", referencia)
    mensagens = [
        *referencia_mod.mensagens_de_referencia(),
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Transcrição do áudio do cliente:\n\n{transcricao}"},
    ]
    resposta = await _groq.chat.completions.create(
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        temperature=0.2,
        messages=mensagens,
    )
    conteudo = resposta.choices[0].message.content
    if not conteudo:
        raise RuntimeError("a Groq devolveu um relatório vazio")
    return conteudo.strip()


CONVERSA_PROMPT = """Você é o AnaliseR no Telegram, falando com o desenvolvedor.
O relatório da reunião já está salvo abaixo. Responda em português, em texto simples, sem pedir um áudio novo.
Use só o que está nesse relatório. Se a resposta não estiver nele, diga que ficou em aberto.
Seja direto. Não repita o relatório inteiro."""


async def conversar(pergunta: str, relatorio: str, rotulo: str) -> str:
    assert _groq is not None
    resposta = await _groq.chat.completions.create(
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        temperature=0.2,
        max_tokens=2000,
        messages=[
            {"role": "system", "content": CONVERSA_PROMPT},
            {
                "role": "user",
                "content": f"{rotulo}\n\nRelatório salvo:\n\n{relatorio}\n\nPedido do desenvolvedor:\n{pergunta}",
            },
        ],
    )
    conteudo = resposta.choices[0].message.content
    if not conteudo or not conteudo.strip():
        raise RuntimeError("a Groq devolveu uma resposta vazia")
    sem_marca = re.sub(r"\*\*(.+?)\*\*", r"\1", conteudo)
    return sem_marca.replace("`", "").strip()


async def responder(update: Update, texto: str) -> None:
    assert update.message is not None
    try:
        await update.message.reply_text(texto, parse_mode="Markdown")
    except TelegramError:
        logger.warning("Telegram recusou o Markdown; enviando texto puro")
        await update.message.reply_text(texto)


async def enviar_relatorio(update: Update, relatorio: str) -> None:
    for parte in fatiar_texto(relatorio):
        await responder(update, parte)


async def classificar(telegram_user_id: int) -> Acesso:
    """Estado de acesso do usuário: não cadastrado, cadastrado sem liberação ou liberado."""
    try:
        registro = await db.buscar_usuario_por_telegram_id(telegram_user_id)
    except Exception:
        logger.exception("falha ao consultar o cadastro do usuário %s", telegram_user_id)
        return Acesso.DESCONHECIDO
    if registro is None:
        return Acesso.DESCONHECIDO
    return classificar_acesso(True, bool(registro["pode_usar_bot"]))


async def comando_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message is None or update.effective_user is None:
        return
    usuario = update.effective_user
    acesso = await classificar(usuario.id)
    await responder(update, mensagem_de_boas_vindas(usuario.id, usuario.first_name, acesso))


async def ao_receber_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message is None or update.effective_user is None:
        return
    usuario = update.effective_user
    texto = update.message.text or ""
    if eh_saudacao(texto):
        acesso = await classificar(usuario.id)
        await responder(update, mensagem_de_boas_vindas(usuario.id, usuario.first_name, acesso))
        return

    acesso = await classificar(usuario.id)
    if acesso is not Acesso.LIBERADO:
        await responder(update, mensagem_de_recusa(acesso, usuario.id, usuario.first_name))
        return

    try:
        analises = await db.listar_analises(usuario.id)
    except Exception:
        logger.exception("falha ao listar reuniões do usuário %s", usuario.id)
        await update.message.reply_text("Não consegui consultar as reuniões salvas. Tente de novo.")
        return

    if not analises:
        await responder(update, SEM_REUNIAO.format(saudacao=linha_saudacao(usuario.first_name)))
        return

    if quer_documento(texto):
        await enviar_pdf(update, analises, texto)
        return

    indice = indice_reuniao(texto, len(analises))
    analise = analises[indice]
    rotulo = f"Reunião {indice + 1}, de {formatar_data(analise['data_criacao'])}."
    await update.message.reply_text(f"Vou olhar a reunião {indice + 1}.")
    try:
        resposta = await conversar(texto, analise["relatorio_gerado"], rotulo)
    except Exception:
        logger.exception("falha ao conversar sobre a reunião do usuário %s", usuario.id)
        await update.message.reply_text("Não consegui responder agora. Pode pedir o PDF ou reformular.")
        return
    for parte in fatiar_texto(resposta):
        await update.message.reply_text(parte)


async def enviar_pdf(update: Update, analises: list, texto: str) -> None:
    assert update.message is not None
    indice = indice_reuniao(texto, len(analises))
    analise = analises[indice]
    numero = indice + 1
    quando = formatar_data(analise["data_criacao"])
    await update.message.reply_text(f"Estou gerando o PDF da reunião {numero}.")
    try:
        pdf = gerar_pdf(analise["relatorio_gerado"], numero, analise["data_criacao"])
        nome = f"AnaliseR-reuniao-{numero}.pdf"
        await update.message.reply_document(
            document=InputFile(pdf, filename=nome),
            caption=f"Reunião {numero}, de {quando}. Relatório e roadmap.",
        )
    except Exception:
        logger.exception("falha ao gerar o PDF da reunião %s", analise["id"])
        await update.message.reply_text("Não consegui gerar o PDF. Tente de novo.")
        return
    logger.info("PDF da reunião %s enviado", analise["id"])


async def ao_receber_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    audio = update.message.voice or update.message.audio
    if audio is None:
        return

    usuario_id = update.effective_user.id
    try:
        acesso = await classificar(usuario_id)
    except Exception:
        logger.exception("falha ao consultar permissão do usuário %s", usuario_id)
        await update.message.reply_text("Não consegui consultar a permissão. Tente de novo.")
        return

    if acesso is not Acesso.LIBERADO:
        await responder(update, mensagem_de_recusa(acesso, usuario_id, update.effective_user.first_name))
        return

    caminho = Path("/tmp") / f"analiser-{audio.file_unique_id}.ogg"
    await update.message.reply_text("Recebi o áudio. Estou transcrevendo e montando o MVP.")
    try:
        arquivo = await context.bot.get_file(audio.file_id)
        await arquivo.download_to_drive(custom_path=str(caminho))
        transcricao = await transcrever(caminho)
        if not transcricao:
            await update.message.reply_text("Não consegui ouvir conteúdo nesse áudio.")
            return
        relatorio = await analisar(transcricao)
        try:
            await db.inserir_analise(usuario_id, relatorio)
        except Exception:
            logger.exception("relatório gerado, mas a gravação no banco falhou")
        await enviar_relatorio(update, relatorio)
    except Exception:
        logger.exception("falha ao processar áudio do usuário %s", usuario_id)
        await update.message.reply_text("Não consegui processar esse áudio. Tente de novo.")
    finally:
        if caminho.exists():
            caminho.unlink()
            logger.info("arquivo temporário removido: %s", caminho.name)


def criar_telegram() -> Application:
    aplicacao = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    aplicacao.add_handler(CommandHandler("start", comando_start))
    aplicacao.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, ao_receber_audio))
    aplicacao.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ao_receber_texto))
    return aplicacao


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    global _groq, _telegram
    try:
        referencia_mod.carregar()
    except referencia_mod.ReferenciaIndisponivel as erro:
        logger.error("subindo sem a referência da organização: %s", erro)
        raise
    await db.connect()
    await db.garantir_admin(
        os.environ["ADMIN_EMAIL"].strip().lower(),
        hash_senha(os.environ["ADMIN_PASSWORD"]),
    )
    _groq = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    _telegram = criar_telegram()
    await _telegram.initialize()
    registrar_bot(_telegram.bot)
    await descrever_bot()
    await _telegram.start()
    if _telegram.updater is None:
        raise RuntimeError("o updater do Telegram não foi criado")
    await _telegram.updater.start_polling(drop_pending_updates=True, allowed_updates=["message"])
    logger.info("bot e API no ar")
    try:
        yield
    finally:
        if _telegram is not None:
            if _telegram.updater is not None:
                await _telegram.updater.stop()
            await _telegram.stop()
            await _telegram.shutdown()
            _telegram = None
        if _groq is not None:
            fechar = getattr(_groq, "close", None)
            if fechar is not None:
                resultado = fechar()
                if hasattr(resultado, "__await__"):
                    await resultado
            _groq = None
        await db.close()


app = FastAPI(title="AnaliseR", lifespan=ciclo_de_vida)
app.include_router(criar_rotas())
