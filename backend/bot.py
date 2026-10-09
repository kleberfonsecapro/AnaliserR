"""Bot do Telegram e processo que também sobe a API do admin."""

import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import asyncpg
from fastapi import FastAPI
from groq import AsyncGroq
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import referencia as referencia_mod
from api import criar_rotas
from auth import hash_senha
from database import db, normalizar_pesquisa
from mensagens import (
    ANALISE_ABERTA,
    AUDIO_SEM_CLIENTE,
    CANCELADO,
    CLIENTE_PRONTO_AUDIO,
    CLIENTE_SALVO,
    CONFIRMAR_CLIENTE,
    CONFIRMAR_CLIENTE_DESCONHECIDO,
    ESCOLHA_DA_ANALISE,
    MENU_CLIENTE_DETALHE,
    MENU_CLIENTE_ENTRADA,
    MENU_CLIENTE_NAO_ENCONTRADO,
    MENU_CLIENTE_SEM_REUNIAO,
    MENU_CLIENTE_VARIOS,
    MENU_CLIENTES,
    NOVA_REUNIAO_CLIENTE,
    NOVO_CLIENTE_NOME,
    OPCOES_CLIENTE,
    SEM_REUNIAO,
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
from notificacoes import descrever_bot, registrar_bot
from pdf_relatorio import formatar_data, gerar_pdf

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("analiser.bot")

LIMITE_TELEGRAM = 4096

# Estados da conversa: menu de clientes e gravação de nova reunião.
ESCOLHER_CLIENTE, CLIENTE_NOVA_REUNIAO, CONFIRMAR_NUMERO = 0, 1, 2
AGUARDAR_AUDIO, ESCOLHER_CLIENTE_DO_AUDIO, PEDIR_NOME = 3, 4, 5
AGUARDAR_RESPOSTA = 6

_REGRAS_STACK = """Regras da stack e do roadmap:
- A stack padrão da organização é a primeira opção. Use-a sempre que servir ao pedido, sem listar dezenas de alternativas.
- Antes de sugerir qualquer tecnologia, procure-a na stack padrão: consulte a lista da camada indicada pelo guia de camadas (Frontend, Backend ou Comum). Só saia dessa lista quando nenhuma linha dela servir ao pedido.
- Se alguma tecnologia da stack padrão não servir ao pedido do cliente (aplicativo mobile nativo, IoT, machine learning, jogo, sistema embarcado), use o que servir e diga qual tecnologia foi descartada e por quê, em uma ou duas frases. Sem justificativa, use a stack padrão.
- O contrato de engenharia recebido é condição de entrega, não preferência.
- Não invente requisito que não esteja na transcrição nem nas respostas do desenvolvedor.
- O Roadmap passo a passo sai sempre em duas subseções, nesta ordem: ### Backend e ### Frontend. Cada etapa cita as tecnologias da stack padrão que serão usadas; etapa do Frontend que depende de algum serviço aponta a etapa do Backend da qual depende.
- O que uma camada precisar e não estiver na stack padrão não vira tecnologia inventada: vira item de Pontos em aberto ou troca declarada no topo de Stack sugerida."""

_SECOES_FECHADAS = """# MVP
## Problema
## Usuários
## Escopo da versão 1
## Fora da versão 1
## Stack sugerida
## Roadmap passo a passo
### Backend
### Frontend
## Critérios de aceite
## Planos de ação
## Pontos em aberto"""

PROMPT_DECISAO = f"""Você é o tech lead de IA e analista de requisitos do AnaliseR.
Recebe a transcrição de uma conversa com o cliente, e as respostas do desenvolvedor quando já houver.

A primeira linha da resposta é exatamente uma destas, sem markdown e sem texto antes:
SITUACAO: parcial
SITUACAO: fechada

Escolha parcial quando faltar uma decisão que muda a tecnologia: plataforma, dados, integração ou quem usa o sistema. Detalhe de tela, cor, texto ou nome de campo não segura o fechamento.

Se escolher parcial, depois da primeira linha use exatamente estas seções e nenhuma outra:
# Análise aberta
## Entendido
## Stack provisória
## Perguntas
## Pontos em aberto

Em Stack provisória, não tranque a stack. Diga o que a stack padrão cobriria e o que ainda depende da resposta.
Em Perguntas, no máximo duas, a mais importante primeiro. Cada uma neste formato, com duas opções curtas:
1. Enunciado?
- A: opção
- B: opção
É proibido escrever a seção Planos de ação.

Se escolher fechada, depois da primeira linha use exatamente estas seções:
{_SECOES_FECHADAS}

No documento fechado:
- Extraia só o necessário para um produto mínimo viável.
- Corte o que não é essencial e liste em Fora da versão 1.
- O que faltar fica em Pontos em aberto e não vira ticket.
- No topo de Stack sugerida, diga o que veio da stack padrão e o que foi trocado, com o motivo.
- Converta cada prática do contrato de engenharia em uma etapa do Roadmap e em um item verificável dos Critérios de aceite.
- Em Planos de ação, cada ticket é uma fatia vertical pronta para desenvolver, nesta forma:
### T1. Título
- Construir: o que entra nesta fatia, de ponta a ponta
- Pronto quando: como saber que acabou
- Depende de: Tn, ou nenhum
Não escreva ticket para ponto em aberto.

{_REGRAS_STACK}"""

PROMPT_FECHADO = f"""Você é o tech lead de IA e analista de requisitos do AnaliseR.
A análise está sendo fechada agora, mesmo que ainda exista lacuna. Não faça perguntas.

A primeira linha da resposta é exatamente, sem markdown e sem texto antes:
SITUACAO: fechada

Depois use exatamente estas seções:
{_SECOES_FECHADAS}

- Extraia só o necessário para um produto mínimo viável.
- Corte o que não é essencial e liste em Fora da versão 1.
- Lacuna que sobrar vai para Pontos em aberto, sem ticket.
- No topo de Stack sugerida, diga o que veio da stack padrão e o que foi trocado, com o motivo.
- Converta cada prática do contrato de engenharia em uma etapa do Roadmap e em um item verificável dos Critérios de aceite.
- Em Planos de ação, cada ticket é uma fatia vertical pronta para desenvolver, nesta forma:
### T1. Título
- Construir: o que entra nesta fatia, de ponta a ponta
- Pronto quando: como saber que acabou
- Depende de: Tn, ou nenhum

{_REGRAS_STACK}"""

_LINHA_SITUACAO = re.compile(r"^SITUACAO:\s*(parcial|fechada)\s*$", re.IGNORECASE)
_SECAO_PLANOS = re.compile(r"^##\s+Planos de ação\s*$", re.IGNORECASE)
_SECAO_PERGUNTAS = re.compile(r"^##\s+Perguntas\s*$", re.IGNORECASE)
_TITULO_SECAO = re.compile(r"^#{1,2}\s+")
_LINHA_PERGUNTA = re.compile(r"^(\d+)\.\s+(.+)$")
_LINHA_OPCAO = re.compile(r"^[-*]\s+([A-Za-z])\s*[:.)-]\s+(.+)$")
_CODIGO_OPCAO = re.compile(r"^(\d+)([A-Za-z])$")
_PEDIDO_FECHAR = re.compile(
    r"^(fechar|fecha|pode fechar|fechar mesmo assim|encerrar)( a an[aá]lise)?$",
    re.IGNORECASE,
)

_groq: AsyncGroq | None = None

# Limites de áudio (SEC-003) — valores conservadores, ajustáveis via .env depois.
MAX_AUDIO_FILE_SIZE = 20 * 1024 * 1024      # 20 MB (Telegram aceita até 50 MB)
MAX_AUDIO_DURATION = 30 * 60                # 30 minutos
AUDIO_COOLDOWN_SEGUNDOS = 60                # 1 minuto entre áudios do mesmo usuário
MAX_AUDIO_CONCORRENTES = 2                  # semáforo para não saturar a Groq
_semaforo_audio: asyncio.Semaphore | None = None
_ultimo_audio_por_usuario: dict[int, float] = {}


async def _inicializar_limites() -> None:
    """Cria o semáforo no loop de eventos certo."""
    global _semaforo_audio
    if _semaforo_audio is None:
        _semaforo_audio = asyncio.Semaphore(MAX_AUDIO_CONCORRENTES)


def _verificar_limites_audio(update: Update) -> str | None:
    """Valida tamanho, duração e cooldown. Devolve mensagem de erro ou None se OK."""
    if update.message is None:
        return "Mensagem inválida."
    audio = update.message.voice or update.message.audio
    if audio is None:
        return "Não é áudio."
    if audio.file_size is not None and audio.file_size > MAX_AUDIO_FILE_SIZE:
        return f"Áudio muito grande ({audio.file_size // 1024 // 1024} MB). Limite: {MAX_AUDIO_FILE_SIZE // 1024 // 1024} MB."
    if audio.duration is not None and audio.duration > MAX_AUDIO_DURATION:
        return f"Áudio muito longo ({audio.duration // 60} min). Limite: {MAX_AUDIO_DURATION // 60} min."
    user_id = update.effective_user.id if update.effective_user else 0
    agora = time.time()
    ultimo = _ultimo_audio_por_usuario.get(user_id, 0)
    if agora - ultimo < AUDIO_COOLDOWN_SEGUNDOS:
        espera = int(AUDIO_COOLDOWN_SEGUNDOS - (agora - ultimo))
        return f"Espere {espera}s antes de enviar outro áudio."
    return None


def _registrar_audio_processado(update: Update) -> None:
    if update.effective_user:
        _ultimo_audio_por_usuario[update.effective_user.id] = time.time()
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
    # Leitura em thread: áudio de até 20 MB não pode travar o event loop
    # dentro da chamada à Groq (BUG-002).
    conteudo = await asyncio.to_thread(caminho.read_bytes)
    resultado = await _groq.audio.transcriptions.create(
        file=(caminho.name, conteudo),
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


def interpretar_relatorio(conteudo: str, fechar: bool) -> tuple[str, str]:
    """Separa a situação da primeira linha e devolve (markdown, situacao)."""
    linhas = conteudo.strip().splitlines()
    situacao = "fechada"
    corpo = linhas
    if linhas:
        achou = _LINHA_SITUACAO.match(linhas[0].strip())
        if achou:
            situacao = achou.group(1).lower()
            corpo = linhas[1:]
            while corpo and not corpo[0].strip():
                corpo = corpo[1:]
    if fechar:
        situacao = "fechada"
    texto = "\n".join(corpo).strip()
    if situacao == "parcial":
        texto = _sem_planos_de_acao(texto)
    if not texto:
        raise RuntimeError("a Groq devolveu um relatório vazio")
    return texto, situacao


def _sem_planos_de_acao(texto: str) -> str:
    """Tira a seção de tickets se o modelo a escrever num relatório ainda aberto."""
    saida: list[str] = []
    pulando = False
    for linha in texto.splitlines():
        if _SECAO_PLANOS.match(linha.strip()):
            pulando = True
            continue
        if pulando and _TITULO_SECAO.match(linha.strip()):
            pulando = False
        if not pulando:
            saida.append(linha)
    return "\n".join(saida).strip()


_SECAO_ROADMAP = re.compile(r"^##\s+Roadmap passo a passo\s*$", re.IGNORECASE)
_SECAO_CRITERIOS = re.compile(r"^##\s+Critérios de aceite\s*$", re.IGNORECASE)
_SECAO_NIVEL2 = re.compile(r"^##\s+")
_SUBSECAO_ROADMAP = re.compile(r"^###\s+(backend|frontend)\s*$", re.IGNORECASE)


def _limites_roadmap(linhas: list[str]) -> tuple[int | None, int]:
    """Índices [inicio, fim) da seção do roadmap; fim exclusivo."""
    inicio = next(
        (i for i, linha in enumerate(linhas) if _SECAO_ROADMAP.match(linha.strip())),
        None,
    )
    if inicio is None:
        return None, len(linhas)
    fim = next(
        (
            i
            for i in range(inicio + 1, len(linhas))
            if _SECAO_NIVEL2.match(linhas[i].strip())
        ),
        len(linhas),
    )
    return inicio, fim


def _roadmap_esta_separado(relatorio: str) -> bool:
    """O roadmap tem as duas subseções, Backend e Frontend, na ordem pedida."""
    linhas = relatorio.splitlines()
    inicio, fim = _limites_roadmap(linhas)
    if inicio is None:
        return False
    subsecoes = [_SUBSECAO_ROADMAP.match(l.strip()) for l in linhas[inicio:fim]]
    subsecoes = [m for m in subsecoes if m]
    return [m.group(1).lower() for m in subsecoes][:2] == ["backend", "frontend"]


def _limpar_roadmap_reescrito(resposta: str) -> str | None:
    """Valida a resposta de correção: uma única seção, começando pelo roadmap."""
    linhas = resposta.strip().splitlines()
    if linhas and linhas[0].strip().startswith("```"):
        linhas = linhas[1:]
        if linhas and linhas[-1].strip().startswith("```"):
            linhas = linhas[:-1]
    texto = "\n".join(linhas).strip()
    if not texto or not _SECAO_ROADMAP.match(texto.splitlines()[0].strip()):
        return None
    if any(_SECAO_NIVEL2.match(l.strip()) for l in texto.splitlines()[1:]):
        return None  # outra seção veio junto; descarta a correção
    return texto


def _substituir_roadmap(relatorio: str, novo_bloco: str) -> str:
    linhas = relatorio.splitlines()
    inicio, fim = _limites_roadmap(linhas)
    bloco = novo_bloco.strip().splitlines()
    if inicio is None:
        destino = next(
            (i for i, l in enumerate(linhas) if _SECAO_CRITERIOS.match(l.strip())),
            len(linhas),
        )
        linhas[destino:destino] = ["", "", *bloco, ""]
        return "\n".join(linhas).rstrip()
    linhas[inicio:fim] = bloco + ([""] if fim < len(linhas) else [])
    return "\n".join(linhas).rstrip()


_REPARA_ROADMAP = """Você recebe um relatório do AnaliseR em markdown.
Reescreva apenas a seção '## Roadmap passo a passo' deste relatório.

A seção reescrita tem exatamente duas subseções, nesta ordem:
### Backend
### Frontend

Regras:
- Mantenha o texto das etapas existentes e a numeração quando der; coloque cada etapa na subseção certa.
- Cada etapa cita a tecnologia da stack padrão da camada, consultando o guia de camadas. Etapa sem tecnologia na stack padrão não vira tecnologia inventada: vira item de 'Pontos em aberto', fora da seção.
- Não altere nenhuma outra seção do relatório. Dentro da seção, além do cabeçalho, não escreva '##'.

Responda apenas com a seção reescrita, começando exatamente por '## Roadmap passo a passo'."""


async def _separar_roadmap(relatorio: str) -> str | None:
    """Uma chamada só para reescrever o roadmap, quando ele não saiu separado."""
    assert _groq is not None
    resposta = await _groq.chat.completions.create(
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        temperature=0.2,
        max_tokens=4000,
        messages=[
            *referencia_mod.mensagens_de_referencia(),
            {"role": "system", "content": _REPARA_ROADMAP},
            {"role": "user", "content": relatorio},
        ],
    )
    conteudo = (resposta.choices[0].message.content or "").strip()
    return _limpar_roadmap_reescrito(conteudo)


async def _garantir_roadmap(relatorio: str) -> str:
    """Roadmap fechado sempre em ### Backend e ### Frontend.

    O prompt já pede a separação; esta passada corrige o relatório quando o
    modelo não atende, e nunca piora: em qualquer dúvida, devolve o original.
    """
    if _roadmap_esta_separado(relatorio):
        return relatorio
    logger.info("roadmap sem Backend/Frontend; tentando separar por camada")
    try:
        reescrito = await _separar_roadmap(relatorio)
    except Exception:
        logger.exception("falha ao separar o roadmap; mantendo o relatório original")
        return relatorio
    if not reescrito:
        logger.warning("resposta de correção do roadmap inválida; mantendo o original")
        return relatorio
    return _substituir_roadmap(relatorio, reescrito)


def extrair_perguntas(relatorio: str) -> list[dict]:
    """Perguntas do relatório parcial, cada uma com até duas opções."""
    dentro = False
    perguntas: list[dict] = []
    for linha in relatorio.splitlines():
        titulo = linha.strip()
        if _TITULO_SECAO.match(titulo) and titulo.startswith("##"):
            dentro = bool(_SECAO_PERGUNTAS.match(titulo))
            continue
        if not dentro:
            continue
        pergunta = _LINHA_PERGUNTA.match(titulo)
        if pergunta:
            perguntas.append({
                "n": int(pergunta.group(1)),
                "texto": pergunta.group(2).strip(),
                "opcoes": [],
            })
            continue
        opcao = _LINHA_OPCAO.match(titulo)
        if opcao and perguntas and len(perguntas[-1]["opcoes"]) < 2:
            perguntas[-1]["opcoes"].append((opcao.group(1).upper(), opcao.group(2).strip()))
    return [item for item in perguntas if item["opcoes"]][:2]


def rotulo_da_opcao(relatorio: str, codigo_opcao: str) -> str:
    """Texto que entra na transcrição quando o dev toca numa opção."""
    achou = _CODIGO_OPCAO.match(codigo_opcao.strip())
    if not achou:
        return codigo_opcao
    numero = int(achou.group(1))
    letra = achou.group(2).upper()
    for pergunta in extrair_perguntas(relatorio):
        if pergunta["n"] != numero:
            continue
        for opcao_letra, texto in pergunta["opcoes"]:
            if opcao_letra == letra:
                return f"{numero}{letra}: {texto}"
    return f"Pergunta {numero}, opção {letra}"


async def analisar(transcricao: str, fechar: bool = False) -> tuple[str, str]:
    """Devolve (relatório, situacao). Com fechar=True, o documento sai fechado."""
    assert _groq is not None
    referencia = referencia_mod.hash_referencia()
    logger.info("analisando com a referência %s (fechar=%s)", referencia, fechar)
    mensagens = [
        *referencia_mod.mensagens_de_referencia(),
        {"role": "system", "content": PROMPT_FECHADO if fechar else PROMPT_DECISAO},
        {"role": "user", "content": f"Transcrição do áudio do cliente:\n\n{transcricao}"},
    ]
    resposta = await _groq.chat.completions.create(
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        temperature=0.2,
        max_tokens=8000,
        messages=mensagens,
    )
    conteudo = resposta.choices[0].message.content
    if not conteudo or not conteudo.strip():
        raise RuntimeError("a Groq devolveu um relatório vazio")
    texto, situacao = interpretar_relatorio(conteudo, fechar)
    if situacao == "fechada":
        # O roadmap fechado sai sempre separado em backend e frontend, e cada
        # etapa cita as tecnologias da stack padrão da organização.
        texto = await _garantir_roadmap(texto)
    return texto, situacao


CONVERSA_PROMPT = """Você é o AnaliseR no Telegram, falando com o desenvolvedor.
O relatório da reunião já está salvo abaixo. Responda em português, em texto simples, sem pedir um áudio novo.
Use só o que está nesse relatório. Se a resposta não estiver nele, diga que ficou em aberto.
Seja direto. Não repita o relatório inteiro."""

CONVERSA_PARCIAL = (
    CONVERSA_PROMPT
    + "\nEste relatório ainda está aberto. Não trate como especificação fechada"
    " e não invente planos de ação."
)


async def conversar(pergunta: str, relatorio: str, rotulo: str, situacao: str = "fechada") -> str:
    assert _groq is not None
    prompt = CONVERSA_PARCIAL if situacao == "parcial" else CONVERSA_PROMPT
    resposta = await _groq.chat.completions.create(
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
        temperature=0.2,
        max_tokens=2000,
        messages=[
            {"role": "system", "content": prompt},
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


def _alvo(update: Update):
    """Mensagem em que a resposta pode ser pendurada, inclusive num toque de botão."""
    if update.message is not None:
        return update.message
    if update.callback_query is not None:
        return update.callback_query.message
    return None


def _md(texto: str) -> str:
    return re.sub(r"([_*`\[])", r"\\\1", str(texto))


def _teclado_clientes(clientes: list, incluir_novo: bool = True) -> InlineKeyboardMarkup:
    linhas = []
    for cliente in clientes[:25]:
        rotulo = f"{cliente['nome']} · {cliente['codigo']} · {cliente['total_reunioes']}"
        if len(rotulo) > 60:
            rotulo = f"{str(cliente['nome'])[:28]} · {cliente['codigo']}"
        linhas.append([InlineKeyboardButton(rotulo, callback_data=f"c:{cliente['codigo']}")])
    if incluir_novo:
        linhas.append([InlineKeyboardButton("Novo cliente", callback_data="novo")])
    return InlineKeyboardMarkup(linhas)


def _teclado_ficha(cliente: asyncpg.Record, reunioes: list) -> InlineKeyboardMarkup:
    codigo = cliente["codigo"]
    linhas = [[InlineKeyboardButton("Nova reunião", callback_data=f"n:{codigo}")]]
    for reuniao in reunioes:
        numero = reuniao["numero"]
        aberta = reuniao["situacao"] == "parcial"
        rotulo = f"Reunião {numero} · aberta" if aberta else f"Reunião {numero}"
        linha = [
            InlineKeyboardButton(rotulo, callback_data=f"a:{codigo}:{numero}"),
            InlineKeyboardButton("PDF", callback_data=f"p:{codigo}:{numero}"),
        ]
        if aberta:
            linha.append(InlineKeyboardButton("Continuar", callback_data=f"k:{codigo}:{numero}"))
        linhas.append(linha)
    linhas.append([InlineKeyboardButton("Voltar aos clientes", callback_data="menu")])
    return InlineKeyboardMarkup(linhas)


def _teclado_perguntas(codigo: str, numero: int, relatorio: str) -> InlineKeyboardMarkup:
    linhas = []
    for pergunta in extrair_perguntas(relatorio):
        for letra, texto in pergunta["opcoes"]:
            rotulo = f"{pergunta['n']}{letra}. {texto}"
            if len(rotulo) > 40:
                rotulo = rotulo[:39] + "…"
            linhas.append([InlineKeyboardButton(
                rotulo,
                callback_data=f"q:{codigo}:{numero}:{pergunta['n']}{letra}",
            )])
    linhas.append([InlineKeyboardButton(
        "Fechar mesmo assim",
        callback_data=f"f:{codigo}:{numero}",
    )])
    return InlineKeyboardMarkup(linhas)


async def responder(update: Update, texto: str, teclado: InlineKeyboardMarkup | None = None) -> None:
    alvo = _alvo(update)
    if alvo is None:
        return
    extras = {"reply_markup": teclado} if teclado is not None else {}
    try:
        await alvo.reply_text(texto, parse_mode="Markdown", **extras)
    except TelegramError:
        logger.warning("Telegram recusou o Markdown; enviando texto puro")
        await alvo.reply_text(texto, **extras)


_TITULO_PREVIA = re.compile(r"^(#{1,3})\s+(.+)$")


def _previa(texto: str) -> str:
    """Títulos viram negrito na prévia: o Markdown do Telegram não renderiza '#'."""
    saida: list[str] = []
    for linha in texto.splitlines():
        titulo = _TITULO_PREVIA.match(linha)
        if not titulo:
            saida.append(linha)
            continue
        rotulo = re.sub(r"([_*`\[])", r"\\\1", titulo.group(2))
        saida.append(f"*{rotulo}*" if len(titulo.group(1)) <= 2 else f"_{rotulo}_")
    return "\n".join(saida)


async def enviar_relatorio(update: Update, relatorio: str) -> None:
    for parte in fatiar_texto(_previa(relatorio)):
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
                        nome: str | None, situacao: str) -> None:
    context.user_data["relatorio_pendente"] = relatorio
    context.user_data["transcricao_pendente"] = transcricao
    context.user_data["nome_sugerido"] = nome
    context.user_data["situacao_pendente"] = situacao


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
    situacao = context.user_data.pop("situacao_pendente", "fechada")
    context.user_data.pop("nome_sugerido", None)
    usuario_id = context.user_data.get("telegram_user_id")
    if numero is None:
        numero = await db.proximo_numero_reuniao(cliente["id"])
    try:
        await db.inserir_reuniao(
            cliente["id"], usuario_id, numero, relatorio, transcricao, situacao
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
                cliente["id"], usuario_id, numero, relatorio, transcricao, situacao
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
    teclado = _teclado_ficha(cliente, reunioes)
    nome = _md(cliente["nome"])
    codigo = _md(cliente["codigo"])
    if not reunioes:
        await responder(update, MENU_CLIENTE_SEM_REUNIAO.format(nome=nome, codigo=codigo), teclado)
        return
    await responder(update, MENU_CLIENTE_DETALHE.format(
        nome=nome,
        codigo=codigo,
        quantidade=len(reunioes),
        lista=listar_reunioes_menu(reunioes),
    ), teclado)


async def _enviar_menu(update: Update) -> int:
    if update.effective_user is None:
        return ESCOLHER_CLIENTE
    try:
        clientes = await db.listar_clientes()
    except Exception:
        logger.exception("falha ao listar clientes do menu")
        await responder(update, "Não consegui listar os clientes. Tente de novo.")
        return ESCOLHER_CLIENTE
    await responder(update, MENU_CLIENTES.format(
        saudacao=linha_saudacao(update.effective_user.first_name),
        clientes=listar_clientes_menu(clientes),
    ), _teclado_clientes(clientes))
    return ESCOLHER_CLIENTE


async def comando_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    del context
    if update.effective_user is None:
        return ESCOLHER_CLIENTE
    if not await _liberado(update, update.effective_user.id):
        return ConversationHandler.END
    return await _enviar_menu(update)


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
        ), _teclado_clientes(todos))
        return ESCOLHER_CLIENTE

    if len(encontrados) > 1 and not exato:
        await responder(update, MENU_CLIENTE_VARIOS.format(
            total=len(encontrados),
            termo=termo_de_busca(texto) or texto,
            lista=listar_encontrados(encontrados),
        ), _teclado_clientes(encontrados, incluir_novo=False))
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
        ), _teclado_clientes(todos))
        return CLIENTE_NOVA_REUNIAO

    if len(encontrados) > 1 and not exato:
        await responder(update, MENU_CLIENTE_VARIOS.format(
            total=len(encontrados),
            termo=termo_de_busca(texto) or texto,
            lista=listar_encontrados(encontrados),
        ), _teclado_clientes(encontrados, incluir_novo=False))
        return CLIENTE_NOVA_REUNIAO

    await _mostrar_cliente(update, encontrados[0])
    return ESCOLHER_CLIENTE


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


async def _preparar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          cliente: asyncpg.Record) -> int:
    """Reserva o próximo número e espera o áudio já com o cliente definido."""
    if update.effective_user is not None:
        context.user_data["telegram_user_id"] = update.effective_user.id
    proximo = await db.proximo_numero_reuniao(cliente["id"])
    context.user_data["cliente_nova_reuniao"] = dict(cliente)
    context.user_data["numero_reservado"] = proximo
    await responder(update, CLIENTE_PRONTO_AUDIO.format(
        nome=_md(cliente["nome"]),
        codigo=_md(cliente["codigo"]),
        numero=proximo,
    ))
    return AGUARDAR_AUDIO


async def comando_novo_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    del context
    if update.effective_user is None:
        return PEDIR_NOME
    if not await _liberado(update, update.effective_user.id):
        return ConversationHandler.END
    await responder(update, NOVO_CLIENTE_NOME)
    return PEDIR_NOME


async def receber_nome_novo_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.effective_user is None:
        return PEDIR_NOME
    nome = (update.message.text or "").strip()
    if len(nome) < 2:
        await responder(update, NOVO_CLIENTE_NOME)
        return PEDIR_NOME
    codigo = parece_codigo_cliente(nome)
    if codigo:
        achado = await db.buscar_cliente_por_codigo(codigo)
        if achado is None:
            await responder(update, f"Não achei {codigo}. Escreva o nome do cliente novo.")
            return PEDIR_NOME
        await _mostrar_cliente(update, achado)
        return ESCOLHER_CLIENTE
    try:
        cliente = await db.criar_cliente(
            nome, normalizar_pesquisa(nome), update.effective_user.id
        )
    except Exception:
        logger.exception("falha ao cadastrar cliente")
        await responder(update, "Não consegui cadastrar esse cliente. Tente de novo.")
        return PEDIR_NOME
    return await _preparar_audio(update, context, cliente)


async def audio_sem_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Áudio chega sem cliente escolhido: não transcreve."""
    del context
    if update.effective_user is not None and not await _liberado(update, update.effective_user.id):
        return ConversationHandler.END
    await responder(update, AUDIO_SEM_CLIENTE)
    return ESCOLHER_CLIENTE


async def ao_toque(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    consulta = update.callback_query
    if consulta is None:
        return ESCOLHER_CLIENTE
    if consulta.message is None:
        await consulta.answer("Abra /menu de novo.", show_alert=True)
        return ConversationHandler.END
    await consulta.answer()
    if update.effective_user is None:
        return ESCOLHER_CLIENTE
    if not await _liberado(update, update.effective_user.id):
        return ConversationHandler.END

    dado = consulta.data or ""
    try:
        if dado in {"menu", "novo"} or dado.startswith(("c:", "n:")):
            return await _toque_cliente(update, context, dado)
        if dado.startswith(("a:", "p:", "k:")):
            return await _toque_reuniao(update, context, dado)
        if dado.startswith(("q:", "f:")):
            return await _toque_decisao(update, context, dado)
    except Exception:
        logger.exception("falha ao tratar o botão %s", dado)
        await responder(update, "Não consegui abrir isso. Tente de novo.")
    return ESCOLHER_CLIENTE


async def _toque_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE, dado: str) -> int:
    if dado == "menu":
        return await _enviar_menu(update)
    if dado == "novo":
        await responder(update, NOVO_CLIENTE_NOME)
        return PEDIR_NOME
    codigo = dado.split(":", 1)[1]
    cliente = await db.buscar_cliente_por_codigo(codigo)
    if cliente is None:
        await responder(update, f"Não achei {codigo}. Abra /menu de novo.")
        return ESCOLHER_CLIENTE
    if dado.startswith("n:"):
        return await _preparar_audio(update, context, cliente)
    await _mostrar_cliente(update, cliente)
    return ESCOLHER_CLIENTE


async def _toque_reuniao(update: Update, context: ContextTypes.DEFAULT_TYPE, dado: str) -> int:
    acao, codigo, bruto = dado.split(":", 2)
    try:
        numero = int(bruto)
    except ValueError:
        await responder(update, "Não consegui abrir essa reunião.")
        return ESCOLHER_CLIENTE
    cliente = await db.buscar_cliente_por_codigo(codigo)
    if cliente is None:
        await responder(update, f"Não achei {codigo}.")
        return ESCOLHER_CLIENTE
    registro = await db.buscar_reuniao(cliente["id"], numero)
    if registro is None:
        await responder(update, f"{cliente['nome']} ({codigo}) não tem reunião {numero}.")
        return ESCOLHER_CLIENTE
    contexto = f"{cliente['nome']} ({codigo}) — reunião {numero}"
    quando = formatar_data(registro["data_criacao"])
    if acao == "p":
        await _enviar_pdf(update, registro, contexto, quando, codigo)
        return ESCOLHER_CLIENTE
    if acao == "k":
        if registro["situacao"] != "parcial":
            await responder(update, f"*{_md(cliente['nome'])}* ({_md(codigo)}) — reunião {numero} já está fechada.")
            await enviar_relatorio(update, registro["relatorio_gerado"])
            return ESCOLHER_CLIENTE
        return await _entregar_analise(
            update, context, cliente, numero, registro["relatorio_gerado"], "parcial",
            registro["transcricao"] or "",
        )
    await responder(update, f"*{_md(cliente['nome'])}* ({_md(codigo)}) — reunião {numero}")
    await enviar_relatorio(update, registro["relatorio_gerado"])
    return ESCOLHER_CLIENTE


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
                                   f"{contexto}, de {quando}", registro["situacao"])
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
    alvo = _alvo(update)
    if alvo is None:
        return
    numero = registro["numero"]
    await alvo.reply_text(f"Estou gerando o PDF: {contexto}.")
    try:
        situacao = registro["situacao"]
        pdf = gerar_pdf(registro["relatorio_gerado"], numero, registro["data_criacao"], situacao)
        nome = f"AnaliseR-{cliente_codigo}-reuniao-{numero}.pdf"
        if situacao == "parcial":
            legenda = "Relatório parcial. A análise ainda está aberta."
        else:
            legenda = "Relatório e roadmap."
        await alvo.reply_document(
            document=InputFile(pdf, filename=nome),
            caption=f"{contexto}, de {quando}. {legenda}",
        )
    except Exception:
        logger.exception("falha ao gerar o PDF da reunião %s", registro["id"])
        await alvo.reply_text("Não consegui gerar o PDF. Tente de novo.")
        return
    logger.info("PDF da reunião %s enviado", registro["id"])


async def ao_receber_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    await _inicializar_limites()

    audio = update.message.voice or update.message.audio
    if audio is None:
        return

    erro = _verificar_limites_audio(update)
    if erro:
        assert update.message is not None
        await update.message.reply_text(erro)
        return ConversationHandler.END

    usuario_id = update.effective_user.id
    context.user_data["telegram_user_id"] = usuario_id
    if not await _liberado(update, usuario_id):
        return ConversationHandler.END

    processado = await _processar_audio(update, context, audio)
    if processado is None:
        return ConversationHandler.END
    relatorio, transcricao, situacao = processado

    # A reunião só é gravada depois que o dev confirmar o cliente. O relatório
    # fica pendente no user_data enquanto isso.
    nome, trecho = await identificar_cliente(transcricao)
    _salvar_no_contexto(context, relatorio, transcricao, nome, situacao)
    await _convidar_confirmar_cliente(update, context, nome, trecho)
    return ESCOLHER_CLIENTE_DO_AUDIO


async def _liberado(update: Update, usuario_id: int) -> bool:
    try:
        acesso = await classificar(usuario_id)
    except Exception:
        logger.exception("falha ao consultar permissão do usuário %s", usuario_id)
        await responder(update, "Não consegui consultar a permissão. Tente de novo.")
        return False
    if acesso is Acesso.LIBERADO:
        return True
    await responder(update, mensagem_de_recusa(acesso, usuario_id,
                                                update.effective_user.first_name
                                                if update.effective_user else None))
    return False


async def _processar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           audio) -> tuple[str, str, str] | None:
    """Baixa, transcreve e gera o relatório. Devolve None se não deu para seguir.

    Usa semáforo para limitar processamento concorrente (SEC-003).
    """
    assert update.message is not None
    await _inicializar_limites()
    caminho = Path("/tmp") / f"analiser-{audio.file_unique_id}.ogg"
    await update.message.reply_text("Recebi o áudio. Estou transcrevendo e analisando.")
    async with _semaforo_audio:
        try:
            arquivo = await context.bot.get_file(audio.file_id)
            # A rede é assíncrona (PTB); a escrita em disco não é. Em thread
            # separada para não travar o loop durante o download.
            conteudo = await arquivo.download_as_bytearray()
            await asyncio.to_thread(caminho.write_bytes, bytes(conteudo))
            transcricao = await transcrever(caminho)
            if not transcricao:
                await update.message.reply_text("Não consegui ouvir conteúdo nesse áudio.")
                return None
            relatorio, situacao = await analisar(transcricao)
        except Exception:
            logger.exception("falha ao processar áudio do usuário %s",
                             update.effective_user.id if update.effective_user else "?")
            await update.message.reply_text("Não consegui processar esse áudio. Tente de novo.")
            return None
        finally:
            if caminho.exists():
                caminho.unlink()
                logger.info("arquivo temporário removido: %s", caminho.name)
    _registrar_audio_processado(update)
    return relatorio, transcricao, situacao


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


async def _entregar_analise(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            cliente: asyncpg.Record, numero: int, relatorio: str,
                            situacao: str, transcricao: str, fonte: str = "") -> int:
    """Manda o parcial com as perguntas, ou o final com os planos de ação."""
    nome = cliente["nome"]
    codigo = cliente["codigo"]
    if situacao == "parcial":
        context.user_data["cliente_nova_reuniao"] = cliente
        context.user_data["numero_reservado"] = numero
        context.user_data["transcricao_pendente"] = transcricao
        await responder(update, ANALISE_ABERTA.format(nome=nome, codigo=codigo, numero=numero))
        await enviar_relatorio(update, relatorio)
        await responder(
            update,
            ESCOLHA_DA_ANALISE,
            _teclado_perguntas(codigo, numero, relatorio),
        )
        return AGUARDAR_RESPOSTA
    context.user_data.pop("cliente_nova_reuniao", None)
    context.user_data.pop("numero_reservado", None)
    context.user_data.pop("transcricao_pendente", None)
    await responder(update, CLIENTE_SALVO.format(
        nome=nome, codigo=codigo, numero=numero, fonte=fonte
    ))
    await enviar_relatorio(update, relatorio)
    return ConversationHandler.END


async def _seguir_analise(update: Update, context: ContextTypes.DEFAULT_TYPE, codigo: str,
                          numero: int, *, fechar: bool, resposta: str | None,
                          opcao: str | None = None) -> int:
    """Acrescenta a resposta na mesma reunião e gera o próximo documento."""
    cliente = await db.buscar_cliente_por_codigo(codigo)
    if cliente is None:
        await responder(update, f"Não achei {codigo}.")
        return ESCOLHER_CLIENTE
    registro = await db.buscar_reuniao(cliente["id"], numero)
    if registro is None:
        await responder(update, f"{cliente['nome']} ({codigo}) não tem reunião {numero}.")
        return ESCOLHER_CLIENTE
    if registro["situacao"] == "fechada":
        await responder(update, f"*{_md(cliente['nome'])}* ({_md(codigo)}) — reunião {numero} já está fechada.")
        await enviar_relatorio(update, registro["relatorio_gerado"])
        return ConversationHandler.END

    transcricao = registro["transcricao"] or ""
    if not fechar:
        if opcao:
            texto = rotulo_da_opcao(registro["relatorio_gerado"], opcao)
        else:
            texto = (resposta or "").strip()
        if not texto:
            await responder(update, "Escreva a resposta ou escolha um botão.")
            return AGUARDAR_RESPOSTA
        transcricao = transcricao.rstrip() + "\n\nResposta do desenvolvedor:\n" + texto

    await responder(update, "Estou atualizando a análise.")
    try:
        relatorio, situacao = await analisar(transcricao, fechar=fechar)
    except Exception:
        logger.exception("falha ao atualizar a análise da reunião %s de %s", numero, codigo)
        await responder(update, "Não consegui atualizar a análise. A versão aberta continua salva.")
        return AGUARDAR_RESPOSTA
    try:
        await db.atualizar_reuniao(cliente["id"], numero, relatorio, transcricao, situacao)
    except Exception:
        logger.exception("falha ao gravar a atualização da reunião %s de %s", numero, codigo)
        await responder(update, "Não consegui salvar a atualização. Tente de novo.")
        return AGUARDAR_RESPOSTA
    return await _entregar_analise(
        update, context, cliente, numero, relatorio, situacao, transcricao
    )


async def _toque_decisao(update: Update, context: ContextTypes.DEFAULT_TYPE, dado: str) -> int:
    if dado.startswith("f:"):
        _, codigo, bruto = dado.split(":", 2)
        opcao = None
        fechar = True
    else:
        partes = dado.split(":", 3)
        if len(partes) != 4:
            await responder(update, "Não consegui ler essa opção. Responda em texto.")
            return AGUARDAR_RESPOSTA
        _, codigo, bruto, opcao = partes
        fechar = False
    try:
        numero = int(bruto)
    except ValueError:
        await responder(update, "Não consegui abrir essa reunião.")
        return ESCOLHER_CLIENTE
    return await _seguir_analise(
        update, context, codigo, numero, fechar=fechar, resposta=None, opcao=opcao
    )


async def receber_resposta_analise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return AGUARDAR_RESPOSTA
    texto = (update.message.text or "").strip()
    cliente = context.user_data.get("cliente_nova_reuniao")
    numero = context.user_data.get("numero_reservado")
    if cliente is None or numero is None:
        await responder(update, "Perdi a análise aberta. Abra a ficha do cliente e toque em Continuar.")
        return ESCOLHER_CLIENTE
    fechar = bool(_PEDIDO_FECHAR.match(texto))
    return await _seguir_analise(
        update, context, cliente["codigo"], int(numero),
        fechar=fechar, resposta=None if fechar else texto,
    )


async def _audio_durante_analise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    del context
    if update.message is not None:
        await update.message.reply_text(
            "A análise está aberta. Responda em texto ou nos botões. "
            "Um áudio novo entra em outra reunião, pelo menu."
        )
    return AGUARDAR_RESPOSTA


async def _concluir_gravacao(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             cliente: asyncpg.Record) -> int:
    relatorio = context.user_data.get("relatorio_pendente")
    transcricao = context.user_data.get("transcricao_pendente") or ""
    situacao = context.user_data.get("situacao_pendente", "fechada")
    reservado = context.user_data.pop("numero_reservado", None)
    salvo = await _gravar_reuniao(context, cliente, reservado)
    if salvo is None:
        await responder(update, "Não consegui salvar essa reunião. O relatório não foi perdido, tente de novo.")
        return ESCOLHER_CLIENTE_DO_AUDIO
    _codigo, _nome, numero = salvo
    fonte = " (número reservado no /nova_reuniao)" if reservado is not None else ""
    return await _entregar_analise(
        update, context, cliente, numero, relatorio or "", situacao, transcricao, fonte
    )


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
    relatorio, transcricao, situacao = processado

    numero = context.user_data.get("numero_reservado")
    _salvar_no_contexto(context, relatorio, transcricao, cliente["nome"], situacao)
    salvo = await _gravar_reuniao(context, cliente, numero)
    if salvo is None:
        await responder(update, "Não consegui salvar essa reunião. Tente de novo.")
        return AGUARDAR_AUDIO
    _codigo, _nome, numero = salvo
    return await _entregar_analise(
        update, context, cliente, numero, relatorio, situacao, transcricao
    )


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


def _toque() -> CallbackQueryHandler:
    return CallbackQueryHandler(
        ao_toque,
        pattern=r"^(menu|novo|c:.+|n:.+|a:.+:\d+|p:.+:\d+|k:.+:\d+|f:.+:\d+|q:.+:\d+:\d+[A-Za-z])$",
    )


def _conversa_clientes() -> ConversationHandler:
    """Cliente primeiro: o áudio só entra depois que nome e código estão escolhidos."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("menu", comando_menu),
            CommandHandler("nova_reuniao", comando_nova_reuniao),
            CommandHandler("novo_cliente", comando_novo_cliente),
            _toque(),
        ],
        states={
            ESCOLHER_CLIENTE: [
                _toque(),
                MessageHandler(filters.VOICE | filters.AUDIO, audio_sem_cliente),
                MessageHandler(filters.TEXT & ~filters.COMMAND, escolher_cliente),
            ],
            CLIENTE_NOVA_REUNIAO: [
                _toque(),
                MessageHandler(filters.VOICE | filters.AUDIO, audio_sem_cliente),
                MessageHandler(filters.TEXT & ~filters.COMMAND, escolher_cliente_nova_reuniao),
            ],
            CONFIRMAR_NUMERO: [
                _toque(),
                MessageHandler(filters.VOICE | filters.AUDIO, audio_da_nova_reuniao),
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_numero),
            ],
            AGUARDAR_AUDIO: [
                _toque(),
                MessageHandler(filters.VOICE | filters.AUDIO, audio_da_nova_reuniao),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _texto_inesperado_aguardando),
            ],
            ESCOLHER_CLIENTE_DO_AUDIO: [
                _toque(),
                MessageHandler(filters.VOICE | filters.AUDIO, audio_sem_cliente),
                MessageHandler(filters.TEXT & ~filters.COMMAND, escolher_cliente_do_audio),
            ],
            PEDIR_NOME: [
                _toque(),
                MessageHandler(filters.VOICE | filters.AUDIO, audio_sem_cliente),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receber_nome_novo_cliente),
            ],
            AGUARDAR_RESPOSTA: [
                _toque(),
                MessageHandler(filters.VOICE | filters.AUDIO, _audio_durante_analise),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receber_resposta_analise),
            ],
        },
        fallbacks=[CANCELAR],
        conversation_timeout=LIMITE_CONVERSA,
        per_message=False,
        allow_reentry=True,
    )


def criar_telegram() -> Application:
    aplicacao = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    aplicacao.add_handler(_conversa_clientes())
    aplicacao.add_handler(CommandHandler("start", comando_start))
    aplicacao.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, audio_sem_cliente))
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
    await _telegram.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )
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
