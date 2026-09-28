"""Bot do Telegram e processo que também sobe a API do admin."""

import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import asyncpg

from fastapi import FastAPI
from groq import AsyncGroq
from telegram import InputFile, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from api import criar_rotas
from auth import hash_senha
from database import db
from database import normalizar_pesquisa
from mensagens import (
    CLIENTE_SALVO,
    CONFIRMAR_CLIENTE,
    CONFIRMAR_CLIENTE_DESCONHECIDO,
    MENU_CLIENTE_DETALHE,
    MENU_CLIENTE_ENTRADA,
    MENU_CLIENTE_NAO_ENCONTRADO,
    MENU_CLIENTE_SEM_REUNIAO,
    MENU_CLIENTE_VARIOS,
    MENU_CLIENTES,
    NOVA_REUNIAO_CLIENTE,
    NOVA_REUNIAO_NUMERO,
    OPCOES_CLIENTE,
    SEM_REUNIAO,
    CANCELADO,
    Acesso,
    classificar_acesso,
    eh_saudacao,
    indice_reuniao,
    linha_saudacao,
    listar_clientes_menu,
    listar_encontrados,
    listar_reunioes_menu,
    mensagem_de_boas_vindas,
    mensagem_de_recusa,
    parece_codigo_cliente,
    quer_documento,
    resumir_clientes,
    termo_de_busca,
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

# Estados da conversa: menu de clientes e gravação de nova reunião.
ESCOLHER_CLIENTE, CLIENTE_NOVA_REUNIAO, CONFIRMAR_NUMERO = 0, 1, 2
AGUARDAR_AUDIO, ESCOLHER_CLIENTE_DO_AUDIO = 3, 4

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


_CLIENTE_PROMPT = """Leia a transcrição de uma conversa de vendas e identifique quem é o cliente.

Responda em duas linhas, sem markdown, sem explicação:
NOME: o nome da empresa ou da pessoa do cliente, como aparece na conversa
TRECHO: o trecho literal da transcrição que comprova o nome

Se a transcrição não identificar o cliente com segurança, escreva NOME: DESCONHECIDO.
Não invente. Na dúvida, DESCONHECIDO."""

_CLIENTE_VAZIO = re.compile(r"^DESCONHECIDO\s*$", re.IGNORECASE)


async def identificar_cliente(transcricao: str) -> tuple[str | None, str]:
    """Nome do cliente dito na conversa, e o trecho que o comprova.

    Serve só de sugestão: o dev confirma antes da gravação, porque nome
    pronounceado em áudio costuma vir parcialmente errado.
    """
    assert _groq is not None
    try:
        resposta = await _groq.chat.completions.create(
            model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
            temperature=0.0,
            # O modelo de raciocínio gasta do mesmo orçamento: com 200 tokens a
            # resposta saía vazia, porque o raciocínio consumiu tudo.
            max_tokens=800,
            messages=[
                {"role": "system", "content": _CLIENTE_PROMPT},
                {"role": "user", "content": transcricao},
            ],
        )
    except Exception:
        logger.exception("falha ao identificar o cliente na transcrição")
        return None, ""

    conteudo = (resposta.choices[0].message.content or "").strip()
    nome: str | None = None
    trecho = ""
    for linha in conteudo.splitlines():
        limpa = linha.strip()
        if limpa.upper().startswith("NOME:"):
            candidato = limpa[5:].strip().strip(".")
            if candidato and not _CLIENTE_VAZIO.match(candidato):
                nome = candidato
        elif limpa.upper().startswith("TRECHO:"):
            trecho = limpa[7:].strip().strip('"')
    if nome:
        logger.info("cliente sugerido pela transcrição: %s", nome)
    else:
        logger.info("cliente não identificado na transcrição")
    return nome, trecho


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


def _salvar_no_contexto(context: ContextTypes.DEFAULT_TYPE, relatorio: str, transcricao: str,
                        nome: str | None) -> None:
    context.user_data["relatorio_pendente"] = relatorio
    context.user_data["transcricao_pendente"] = transcricao
    context.user_data["nome_sugerido"] = nome


async def _gravar_reuniao(context: ContextTypes.DEFAULT_TYPE, cliente: asyncpg.Record,
                          numero: int | None = None) -> tuple[str, str, int] | None:
    """Grava a reunião pendente e devolve (código, nome, número), ou None.

    O número volta para a resposta: sem ele, a confirmação diria sempre
    "reunião 1", mesmo tendo gravado a terceira.
    """
    relatorio = context.user_data.pop("relatorio_pendente", None)
    if not relatorio:
        return None
    transcricao = context.user_data.pop("transcricao_pendente", None)
    context.user_data.pop("nome_sugerido", None)
    usuario_id = context.user_data.get("telegram_user_id")
    if numero is None:
        numero = await db.proximo_numero_reuniao(cliente["id"])
    try:
        await db.inserir_reuniao(
            cliente["id"], usuario_id, numero, relatorio, transcricao
        )
    except asyncpg.UniqueViolationError:
        # Outra reunião entrou entre a reserva e a gravação: o número reservado
        # já foi usado. Recalcula e tenta uma vez, para não perder a reunião.
        logger.warning(
            "número %s do cliente %s já foi usado; recalculando", numero, cliente["codigo"]
        )
        numero = await db.proximo_numero_reuniao(cliente["id"])
        try:
            await db.inserir_reuniao(
                cliente["id"], usuario_id, numero, relatorio, transcricao
            )
        except Exception:
            logger.exception(
                "relatório gerado, mas a gravação da reunião %s do cliente %s falhou",
                numero,
                cliente["codigo"],
            )
            return None
    except Exception:
        logger.exception(
            "relatório gerado, mas a gravação da reunião %s do cliente %s falhou",
            numero,
            cliente["codigo"],
        )
        return None
    return cliente["codigo"], cliente["nome"], numero


def _menciona_cliente(texto: str) -> bool:
    """True quando o dev chamou alguém de cliente, e não só fez uma pergunta.

    Sem isso, "qual o roadmap?" seria tratado como busca pelo nome "qual o
    roadmap" e o bot responderia que não achou o cliente.
    """
    if re.search(r"\b(cliente|clientes|conta|empresa)\b", texto, re.IGNORECASE):
        return True
    if re.search(r"\b(reuni[aã]o|reunioes)\s*(n[o°]?\s*)?\d", texto, re.IGNORECASE):
        return True
    return False


async def _reuniao_mais_recente() -> tuple[asyncpg.Record, int] | None:
    ultima = await db.ultima_reuniao()
    if ultima is None:
        return None
    return ultima, ultima["numero"]


async def _resolver_cliente(_update: Update, texto: str) -> tuple[list[asyncpg.Record], bool]:
    """Busca por código primeiro, depois por nome. Devolve (resultados, exato)."""
    codigo = parece_codigo_cliente(texto)
    if codigo:
        achado = await db.buscar_cliente_por_codigo(codigo)
        return ([achado] if achado else []), True
    termo = termo_de_busca(texto)
    if not termo:
        return [], False
    return await db.buscar_cliente_por_nome(termo), False


async def _mostrar_cliente(update: Update, cliente: asyncpg.Record) -> None:
    reunioes = await db.listar_reunioes_do_cliente(cliente["id"])
    if not reunioes:
        await responder(update, MENU_CLIENTE_SEM_REUNIAO.format(
            nome=cliente["nome"], codigo=cliente["codigo"]
        ))
        return
    await responder(update, MENU_CLIENTE_DETALHE.format(
        nome=cliente["nome"],
        codigo=cliente["codigo"],
        quantidade=len(reunioes),
        lista=listar_reunioes_menu(reunioes),
    ))


async def comando_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    del context
    if update.message is None or update.effective_user is None:
        return ESCOLHER_CLIENTE
    acesso = await classificar(update.effective_user.id)
    if acesso is not Acesso.LIBERADO:
        await responder(update, mensagem_de_recusa(acesso, update.effective_user.id,
                                                    update.effective_user.first_name))
        return ESCOLHER_CLIENTE

    try:
        clientes = await db.listar_clientes()
    except Exception:
        logger.exception("falha ao listar clientes do menu")
        await update.message.reply_text("Não consegui listar os clientes. Tente de novo.")
        return ESCOLHER_CLIENTE

    await responder(update, MENU_CLIENTES.format(
        saudacao=linha_saudacao(update.effective_user.first_name),
        clientes=listar_clientes_menu(clientes),
    ))
    return ESCOLHER_CLIENTE


async def escolher_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.effective_user is None:
        return ESCOLHER_CLIENTE
    texto = (update.message.text or "").strip()
    if not texto:
        await responder(update, MENU_CLIENTE_ENTRADA)
        return ESCOLHER_CLIENTE

    try:
        encontrados, exato = await _resolver_cliente(update, texto)
    except Exception:
        logger.exception("falha ao buscar cliente")
        await update.message.reply_text("Não consegui buscar esse cliente. Tente de novo.")
        return ESCOLHER_CLIENTE

    if not encontrados:
        todos = await db.listar_clientes()
        await responder(update, MENU_CLIENTE_NAO_ENCONTRADO.format(
            termo=termo_de_busca(texto) or texto,
            disponiveis=resumir_clientes(todos) or "Nenhum cliente cadastrado ainda.",
        ))
        return ESCOLHER_CLIENTE

    if len(encontrados) > 1 and not exato:
        await responder(update, MENU_CLIENTE_VARIOS.format(
            total=len(encontrados),
            termo=termo_de_busca(texto) or texto,
            lista=listar_encontrados(encontrados),
        ))
        return ESCOLHER_CLIENTE

    await _mostrar_cliente(update, encontrados[0])
    return ESCOLHER_CLIENTE


async def comando_nova_reuniao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.effective_user is None:
        return CLIENTE_NOVA_REUNIAO
    acesso = await classificar(update.effective_user.id)
    if acesso is not Acesso.LIBERADO:
        await responder(update, mensagem_de_recusa(acesso, update.effective_user.id,
                                                    update.effective_user.first_name))
        return ConversationHandler.END
    context.user_data["telegram_user_id"] = update.effective_user.id
    await responder(update, NOVA_REUNIAO_CLIENTE)
    return CLIENTE_NOVA_REUNIAO


async def escolher_cliente_nova_reuniao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.effective_user is None:
        return CLIENTE_NOVA_REUNIAO
    texto = (update.message.text or "").strip()
    if not texto:
        await responder(update, NOVA_REUNIAO_CLIENTE)
        return CLIENTE_NOVA_REUNIAO

    try:
        encontrados, exato = await _resolver_cliente(update, texto)
    except Exception:
        logger.exception("falha ao buscar cliente da nova reunião")
        await update.message.reply_text("Não consegui buscar esse cliente. Tente de novo.")
        return CLIENTE_NOVA_REUNIAO

    if not encontrados:
        todos = await db.listar_clientes()
        await responder(update, MENU_CLIENTE_NAO_ENCONTRADO.format(
            termo=termo_de_busca(texto) or texto,
            disponiveis=resumir_clientes(todos) or "Nenhum cliente cadastrado ainda.",
        ))
        return CLIENTE_NOVA_REUNIAO

    if len(encontrados) > 1 and not exato:
        await responder(update, MENU_CLIENTE_VARIOS.format(
            total=len(encontrados),
            termo=termo_de_busca(texto) or texto,
            lista=listar_encontrados(encontrados),
        ))
        return CLIENTE_NOVA_REUNIAO

    cliente = encontrados[0]
    reunioes = await db.listar_reunioes_do_cliente(cliente["id"])
    contexto = {
        "nome": cliente["nome"],
        "codigo": cliente["codigo"],
        "quantidade": len(reunioes),
        "ultimo": (
            f", última em {reunioes[-1]['data_criacao'].strftime('%d/%m/%Y')}" if reunioes else ""
        ),
        "proximo": await db.proximo_numero_reuniao(cliente["id"]),
    }
    context.user_data["cliente_nova_reuniao"] = dict(cliente)
    await responder(update, NOVA_REUNIAO_NUMERO.format(**contexto))
    return CONFIRMAR_NUMERO


async def confirmar_numero(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return CONFIRMAR_NUMERO
    texto = (update.message.text or "").strip().lower()
    cliente = context.user_data.get("cliente_nova_reuniao")
    if cliente is None:
        await responder(update, CANCELADO)
        return ConversationHandler.END

    if texto in {"sim", "s", "ok", "ok!", "pode ser", "confirmo", "1"}:
        proximo = await db.proximo_numero_reuniao(cliente["id"])
        await responder(update, (
            f"Reunião {proximo} de *{cliente['nome']}* ({cliente['codigo']}) pronta para gravar.\n\n"
            "Mande agora o áudio da conversa. O relatório vai direto para este cliente."
        ))
        context.user_data["numero_reservado"] = proximo
        return AGUARDAR_AUDIO
    if texto in {"nao", "não", "n", "cancela", "2"}:
        await responder(update, CANCELADO)
        return ConversationHandler.END
    await responder(update, 'Responda "sim" para gravar, ou "não" para cancelar.')
    return CONFIRMAR_NUMERO


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    del context
    if update.message is not None:
        await responder(update, CANCELADO)
    return ConversationHandler.END


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

    # "cliente X" / "reuniao 2 da CLI-0007" escolhem a reunião; senão, a mais recente.
    reuniao = await _resolver_reuniao(update, texto)
    if reuniao is None:
        return
    cliente, numero = reuniao

    try:
        registro = await db.buscar_reuniao(cliente["id"], numero)
    except Exception:
        logger.exception("falha ao buscar a reunião %s do cliente %s", numero, cliente["codigo"])
        await update.message.reply_text("Não consegui abrir essa reunião. Tente de novo.")
        return
    if registro is None:
        await responder(update, (
            f"O cliente *{cliente['nome']}* ({cliente['codigo']}) não tem reunião {numero}.\n\n"
            f"Ele tem até a {cliente['ultima_reuniao']}."
        ))
        return

    contexto = f"{cliente['nome']} ({cliente['codigo']}) — reunião {numero}"
    quando = formatar_data(registro["data_criacao"])

    if quer_documento(texto):
        await _enviar_pdf(update, registro, contexto, quando, cliente["codigo"])
        return

    await update.message.reply_text(f"Vou olhar: {contexto}.")
    try:
        resposta = await conversar(texto, registro["relatorio_gerado"],
                                   f"{contexto}, de {quando}")
    except Exception:
        logger.exception("falha ao conversar sobre a reunião %s do cliente %s",
                         numero, cliente["codigo"])
        await update.message.reply_text("Não consegui responder agora. Pode pedir o PDF ou reformular.")
        return
    for parte in fatiar_texto(resposta):
        await update.message.reply_text(parte)


async def _resolver_reuniao(update: Update, texto: str) -> tuple[asyncpg.Record, int] | None:
    """Resolve (cliente, número) a partir do texto livre do dev.

    Só procura cliente quando o texto realmente cita um. Sem menção, cai na
    reunião mais recente — o comportamento antigo, preservado para não quebrar
    quem perguntava "qual o roadmap?" depois de gravar.
    """
    primeiro_nome = update.effective_user.first_name if update.effective_user else None
    try:
        if not await db.total_reunioes():
            await responder(update, SEM_REUNIAO.format(saudacao=linha_saudacao(primeiro_nome)))
            return None

        citados = _menciona_cliente(texto) or parece_codigo_cliente(texto) is not None
        if citados:
            encontrados, exato = await _resolver_cliente(update, texto)
            if not encontrados:
                todos = await db.listar_clientes()
                await responder(update, MENU_CLIENTE_NAO_ENCONTRADO.format(
                    termo=termo_de_busca(texto) or texto,
                    disponiveis=resumir_clientes(todos) or "Nenhum cliente cadastrado ainda.",
                ))
                return None
            if len(encontrados) > 1 and not exato:
                await responder(update, MENU_CLIENTE_VARIOS.format(
                    total=len(encontrados),
                    termo=termo_de_busca(texto) or texto,
                    lista=listar_encontrados(encontrados),
                ))
                return None
            cliente = encontrados[0]
            if not cliente["total_reunioes"]:
                await responder(update, MENU_CLIENTE_SEM_REUNIAO.format(
                    nome=cliente["nome"], codigo=cliente["codigo"]
                ))
                return None
            return cliente, indice_reuniao(texto, cliente["ultima_reuniao"])

        # O dev não citou cliente: tenta a busca pelo termo mesmo assim, e cai
        # na reunião mais recente se nada bater. A busca só é tentada porque o
        # nome pode aparecer solto, como "o roadmap da São João".
        try:
            encontrados, _ = await _resolver_cliente(update, termo_de_busca(texto))
        except Exception:
            logger.exception("busca opcional por cliente falhou; usando a reunião mais recente")
            encontrados = []
        if len(encontrados) == 1 and encontrados[0]["total_reunioes"]:
            return encontrados[0], indice_reuniao(texto, encontrados[0]["ultima_reuniao"])
        return await _reuniao_mais_recente()
    except Exception:
        logger.exception("falha ao resolver a reunião pedida")
        await update.message.reply_text(  # type: ignore[union-attr]
            "Não consegui consultar as reuniões salvas. Tente de novo."
        )
        return None


async def _enviar_pdf(update: Update, registro: asyncpg.Record, contexto: str, quando: str,
                      cliente_codigo: str) -> None:
    assert update.message is not None
    numero = registro["numero"]
    await update.message.reply_text(f"Estou gerando o PDF: {contexto}.")
    try:
        pdf = gerar_pdf(registro["relatorio_gerado"], numero, registro["data_criacao"])
        nome = f"AnaliseR-{cliente_codigo}-reuniao-{numero}.pdf"
        await update.message.reply_document(
            document=InputFile(pdf, filename=nome),
            caption=f"{contexto}, de {quando}. Relatório e roadmap.",
        )
    except Exception:
        logger.exception("falha ao gerar o PDF da reunião %s", registro["id"])
        await update.message.reply_text("Não consegui gerar o PDF. Tente de novo.")
        return
    logger.info("PDF da reunião %s enviado", registro["id"])


async def ao_receber_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    audio = update.message.voice or update.message.audio
    if audio is None:
        return

    usuario_id = update.effective_user.id
    context.user_data["telegram_user_id"] = usuario_id
    if not await _liberado(update, usuario_id):
        return ConversationHandler.END

    processado = await _processar_audio(update, context, audio)
    if processado is None:
        return ConversationHandler.END
    relatorio, transcricao = processado

    # A reunião só é gravada depois que o dev confirmar o cliente. O relatório
    # fica pendente no user_data enquanto isso.
    nome, trecho = await identificar_cliente(transcricao)
    _salvar_no_contexto(context, relatorio, transcricao, nome)
    await _convidar_confirmar_cliente(update, context, nome, trecho)
    return ESCOLHER_CLIENTE_DO_AUDIO


async def _liberado(update: Update, usuario_id: int) -> bool:
    try:
        acesso = await classificar(usuario_id)
    except Exception:
        logger.exception("falha ao consultar permissão do usuário %s", usuario_id)
        assert update.message is not None
        await update.message.reply_text("Não consegui consultar a permissão. Tente de novo.")
        return False
    if acesso is Acesso.LIBERADO:
        return True
    assert update.message is not None
    await responder(update, mensagem_de_recusa(acesso, usuario_id,
                                                update.effective_user.first_name
                                                if update.effective_user else None))
    return False


async def _processar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           audio) -> tuple[str, str] | None:
    """Baixa, transcreve e gera o relatório. Devolve None se não deu para seguir."""
    assert update.message is not None
    caminho = Path("/tmp") / f"analiser-{audio.file_unique_id}.ogg"
    await update.message.reply_text("Recebi o áudio. Estou transcrevendo e montando o MVP.")
    try:
        arquivo = await context.bot.get_file(audio.file_id)
        await arquivo.download_to_drive(custom_path=str(caminho))
        transcricao = await transcrever(caminho)
        if not transcricao:
            await update.message.reply_text("Não consegui ouvir conteúdo nesse áudio.")
            return None
        relatorio = await analisar(transcricao)
    except Exception:
        logger.exception("falha ao processar áudio do usuário %s",
                         update.effective_user.id if update.effective_user else "?")
        await update.message.reply_text("Não consegui processar esse áudio. Tente de novo.")
        return None
    finally:
        if caminho.exists():
            caminho.unlink()
            logger.info("arquivo temporário removido: %s", caminho.name)
    return relatorio, transcricao


async def _convidar_confirmar_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                      nome: str | None, trecho: str) -> None:
    if not nome:
        await responder(update, CONFIRMAR_CLIENTE_DESCONHECIDO.format(opcoes=OPCOES_CLIENTE))
        return
    existentes = await db.buscar_cliente_por_nome(normalizar_pesquisa(nome))
    ja_existe = ""
    if existentes:
        primeiro = existentes[0]
        ja_existe = (
            f"Já tenho {primeiro['codigo']} com esse nome, "
            f"com {primeiro['total_reunioes']} reunião(ões)."
        )
    await responder(update, CONFIRMAR_CLIENTE.format(
        nome=nome,
        trecho=f"(vi no áudio: {trecho})" if trecho else "",
        ja_existe=ja_existe,
        opcoes=OPCOES_CLIENTE,
    ))


async def escolher_cliente_do_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Trata a resposta ao convite de confirmação depois de um áudio."""
    if update.message is None:
        return ESCOLHER_CLIENTE_DO_AUDIO
    texto = (update.message.text or "").strip()
    if not texto:
        await responder(update, CONFIRMAR_CLIENTE_DESCONHECIDO.format(opcoes=OPCOES_CLIENTE))
        return ESCOLHER_CLIENTE_DO_AUDIO

    relatorio = context.user_data.get("relatorio_pendente")
    if not relatorio:
        await responder(update, CANCELADO)
        return ConversationHandler.END

    escolha = texto.lower()
    if escolha in {"1", "sim", "é esse", "e esse", "isso", "ok"}:
        nome = context.user_data.get("nome_sugerido")
        if not nome:
            encontrados = await _resolver_cliente(update, texto)
            if not encontrados:
                await responder(update, CONFIRMAR_CLIENTE_DESCONHECIDO.format(opcoes=OPCOES_CLIENTE))
                return ESCOLHER_CLIENTE_DO_AUDIO
            cliente = encontrados[0][0]
        else:
            existentes = await db.buscar_cliente_por_nome(normalizar_pesquisa(nome))
            cliente = existentes[0] if existentes else await db.criar_cliente(
                nome, normalizar_pesquisa(nome), context.user_data.get("telegram_user_id")
            )
        return await _concluir_gravacao(update, context, cliente)

    if escolha in {"3", "criar", "criar novo", "novo"}:
        nome = context.user_data.get("nome_sugerido")
        if not nome:
            await responder(update, 'Responda "1" para confirmar, ou envie o nome do cliente.')
            return ESCOLHER_CLIENTE_DO_AUDIO
        cliente = await db.criar_cliente(
            nome, normalizar_pesquisa(nome), context.user_data.get("telegram_user_id")
        )
        return await _concluir_gravacao(update, context, cliente)

    # "2" e qualquer outra coisa: buscar um cliente existente pelo nome ou código.
    encontrados, exato = await _resolver_cliente(update, texto)
    if not encontrados:
        todos = await db.listar_clientes()
        await responder(update, MENU_CLIENTE_NAO_ENCONTRADO.format(
            termo=termo_de_busca(texto) or texto,
            disponiveis=resumir_clientes(todos) or "Nenhum cliente cadastrado ainda.",
        ))
        return ESCOLHER_CLIENTE_DO_AUDIO
    if len(encontrados) > 1 and not exato:
        await responder(update, MENU_CLIENTE_VARIOS.format(
            total=len(encontrados),
            termo=termo_de_busca(texto) or texto,
            lista=listar_encontrados(encontrados),
        ))
        return ESCOLHER_CLIENTE_DO_AUDIO
    return await _concluir_gravacao(update, context, encontrados[0])


async def _concluir_gravacao(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             cliente: asyncpg.Record) -> int:
    relatorio = context.user_data.get("relatorio_pendente")
    reservado = context.user_data.pop("numero_reservado", None)
    salvo = await _gravar_reuniao(context, cliente, reservado)
    if salvo is None:
        await responder(update, "Não consegui salvar essa reunião. O relatório não foi perdido, tente de novo.")
        return ESCOLHER_CLIENTE_DO_AUDIO
    codigo, nome, numero = salvo
    fonte = " (número reservado no /nova_reuniao)" if reservado is not None else ""
    await responder(update, CLIENTE_SALVO.format(
        nome=nome, codigo=codigo, numero=numero, fonte=fonte
    ))
    await enviar_relatorio(update, relatorio)
    return ConversationHandler.END


async def audio_da_nova_reuniao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Áudio que chega depois de /nova_reuniao, já com cliente e número definidos.

    Aqui o cliente está resolvido e o número confirmado, então o relatório vai
    direto para o banco, sem passar pela confirmação de cliente.
    """
    if update.message is None or update.effective_user is None:
        return ConversationHandler.END
    cliente = context.user_data.get("cliente_nova_reuniao")
    if cliente is None:
        await responder(update, "Perdi o cliente da reunião. Use /nova_reuniao de novo.")
        return ConversationHandler.END

    audio = update.message.voice or update.message.audio
    if audio is None:
        await responder(update, "Isso não parece um áudio. Mande a mensagem de voz da conversa.")
        return AGUARDAR_AUDIO

    processado = await _processar_audio(update, context, audio)
    if processado is None:
        return AGUARDAR_AUDIO
    relatorio, transcricao = processado

    numero = context.user_data.pop("numero_reservado", None)
    _salvar_no_contexto(context, relatorio, transcricao, cliente["nome"])
    salvo = await _gravar_reuniao(context, cliente, numero)
    if salvo is None:
        await responder(update, "Não consegui salvar essa reunião. Tente de novo.")
        return AGUARDAR_AUDIO
    codigo, nome, numero = salvo
    await responder(update, CLIENTE_SALVO.format(
        nome=nome, codigo=codigo, numero=numero, fonte=""
    ))
    await enviar_relatorio(update, relatorio)
    context.user_data.pop("cliente_nova_reuniao", None)
    return ConversationHandler.END


CANCELAR = CommandHandler("cancelar", cancelar)
LIMITE_CONVERSA = timedelta(hours=2)


async def _audio_inesperado(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Áudio chega enquanto o bot ainda pergunta o cliente."""
    del context
    if update.message is not None:
        await update.message.reply_text(
            "Antes do áudio, me diga o cliente: nome, mesmo parcial, ou o código CLI-0007."
        )
    return ESCOLHER_CLIENTE_DO_AUDIO


async def _texto_inesperado_aguardando(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    del context
    if update.message is not None:
        await update.message.reply_text("Mande agora o áudio da conversa, ou /cancelar.")
    return AGUARDAR_AUDIO


def _conversa_clientes() -> ConversationHandler:
    """Toda interação com cliente mora num único ConversationHandler.

    Handler por fluxo não funciona aqui: o grupo de áudio do `/nova_reuniao`
    seria interceptado pelo handler de áudio solto, que é registrado antes.
    Um só handler deixa o estado explícito e elimina essa ambiguidade.
    """
    return ConversationHandler(
        entry_points=[
            CommandHandler("menu", comando_menu),
            CommandHandler("nova_reuniao", comando_nova_reuniao),
            MessageHandler(filters.VOICE | filters.AUDIO, ao_receber_audio),
        ],
        states={
            ESCOLHER_CLIENTE: [
                MessageHandler(filters.VOICE | filters.AUDIO, _audio_inesperado),
                MessageHandler(filters.TEXT & ~filters.COMMAND, escolher_cliente),
            ],
            CLIENTE_NOVA_REUNIAO: [
                MessageHandler(filters.VOICE | filters.AUDIO, _audio_inesperado),
                MessageHandler(filters.TEXT & ~filters.COMMAND, escolher_cliente_nova_reuniao),
            ],
            CONFIRMAR_NUMERO: [
                MessageHandler(filters.VOICE | filters.AUDIO, audio_da_nova_reuniao),
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_numero),
            ],
            AGUARDAR_AUDIO: [
                MessageHandler(filters.VOICE | filters.AUDIO, audio_da_nova_reuniao),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _texto_inesperado_aguardando),
            ],
            ESCOLHER_CLIENTE_DO_AUDIO: [
                MessageHandler(filters.VOICE | filters.AUDIO, _audio_inesperado),
                MessageHandler(filters.TEXT & ~filters.COMMAND, escolher_cliente_do_audio),
            ],
        },
        fallbacks=[CANCELAR],
        conversation_timeout=LIMITE_CONVERSA,
        per_message=False,
    )


def criar_telegram() -> Application:
    aplicacao = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    aplicacao.add_handler(_conversa_clientes())
    aplicacao.add_handler(CommandHandler("start", comando_start))
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
