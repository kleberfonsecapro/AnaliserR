"""Textos que o bot mostra para o usuário, reconhecimento de chamados e estado de acesso."""

import re
import unicodedata
from enum import StrEnum

_MARCAS_MARKDOWN = re.compile(r"[*_`\[]")


class Acesso(StrEnum):
    DESCONHECIDO = "desconhecido"
    AGUARDANDO = "aguardando"
    LIBERADO = "liberado"


SAUDACOES = frozenset(
    {
        "oi",
        "ola",
        "oie",
        "oio",
        "opa",
        "opaa",
        "eai",
        "e ai",
        "eae",
        "salve",
        "fala",
        "fala ai",
        "bom dia",
        "boa tarde",
        "boa noite",
        "tudo bem",
        "tudo certo",
        "como vai",
        "como esta",
        "hello",
        "hi",
        "hey",
        "test",
        "teste",
    }
)

_CORPO = """*AnaliseR* — análise automática de requisitos.

Você manda o áudio de uma conversa com o cliente e eu devolvo o escopo de um MVP, já cortado no que é essencial.

*O que eu faço*
1. Transcrevo o áudio automaticamente.
2. Reduzo o escopo ao mínimo viável da primeira versão.
3. Listo o que fica *fora* da versão 1, para o projeto não inflar.
4. Sugiro a stack, um roadmap passo a passo e os critérios de aceite.
5. Registro o que ficou em aberto na conversa."""

_COMO_USAR = """*Como usar*
Envie uma mensagem de voz, ou um arquivo de áudio, aqui no chat. O relatório volta aqui.

Depois, pode conversar sobre essa reunião ou pedir o PDF. Por exemplo: "gera o PDF da primeira reunião"."""

PRIMEIRO_USO = """{saudacao}

{corpo}

{como_usar}

*Antes do primeiro uso*
Seu ID do Telegram precisa estar autorizado pelo administrador.
Seu ID é {id}.

Mande /start a qualquer momento para ver esta mensagem de novo."""

AGUARDANDO_LIBERACAO = """{saudacao}

{corpo}

{como_usar}

*Seu cadastro já existe*
Só falta liberar o uso do bot. Peça ao administrador para ativar "Pode usar o bot" no painel."""

JA_AUTORIZADO = """{saudacao}

{corpo}

{como_usar}

Você já está autorizado. Pode mandar o áudio."""

SEM_REUNIAO = """{saudacao}

Ainda não tenho uma reunião sua salva, então não consigo responder nem gerar o PDF.

Envie o áudio da conversa com o cliente. Quando o relatório estiver pronto, peça o PDF ou pergunte sobre o roadmap aqui no chat."""

SEM_AUTORIZACAO = """O uso do bot ainda não está liberado para você.

Seu ID do Telegram é {id}. Peça ao administrador para cadastrar e liberar esse ID no painel."""

RECUSA_AUDIO = """{saudacao}

Seu cadastro já existe, mas o uso do bot ainda não está liberado.

Peça ao administrador para ativar "Pode usar o bot" no painel."""

DESCRICAO_BOT = (
    "O AnaliseR transforma o áudio de uma conversa com cliente em um relatório de MVP. "
    "Envie uma mensagem de voz e receba o escopo da versão 1, o que fica de fora, a stack sugerida, "
    "um roadmap passo a passo e os critérios de aceite. Depois, converse sobre a reunião ou peça o PDF. "
    "O uso exige autorização do administrador."
)

_PEDIDO_DOCUMENTO = re.compile(
    r"\b(faz|faca|fazer|fizesse|gera|gerar|gere|manda|mandar|mande|envia|enviar|envie|"
    r"cria|criar|crie|exporta|exportar|monta|montar|monte|quero|preciso|baixa|baixar)\b"
)
_ORDINAIS = (
    ("primeir", 1),
    ("segund", 2),
    ("terceir", 3),
    ("quart", 4),
    ("quint", 5),
)


def normalizar(texto: str) -> list[str]:
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", " ", sem_acento).split()


MAX_PALAVRAS_SAUDACAO = 5


def quer_documento(texto: str) -> bool:
    """Pedido para gerar o PDF, e não uma pergunta sobre a palavra PDF no relatório."""
    frase = " ".join(normalizar(texto))
    tokens = frase.split()
    if "pdf" not in tokens and "documento" not in tokens:
        return False
    if _PEDIDO_DOCUMENTO.search(frase):
        return True
    if any(pista in frase for pista in ("o que", "ficou", "contem", "tem ", "sobre o pdf")):
        return False
    return "pdf" in tokens and any(p in frase for p in ("relatorio", "roadmap", "reuniao"))


def indice_reuniao(texto: str, total: int) -> int:
    """Índice 0-based da reunião citada. Sem número, fica a mais recente."""
    if total <= 0:
        raise ValueError("não há reunião salva")
    frase = " ".join(normalizar(texto))
    tokens = frase.split()
    for raiz, numero in _ORDINAIS:
        if any(token.startswith(raiz) for token in tokens) and numero <= total:
            return numero - 1
    encontrado = re.search(r"reuniao\s+(\d+)", frase)
    if encontrado:
        numero = int(encontrado.group(1))
        if 1 <= numero <= total:
            return numero - 1
    return total - 1


def eh_saudacao(texto: str) -> bool:
    palavras = normalizar(texto)
    if len(palavras) > MAX_PALAVRAS_SAUDACAO:
        return False
    for tamanho in (3, 2, 1):
        if " ".join(palavras[:tamanho]) in SAUDACOES:
            return True
    return False


def classificar_acesso(cadastrado: bool, pode_usar_bot: bool) -> Acesso:
    if not cadastrado:
        return Acesso.DESCONHECIDO
    return Acesso.LIBERADO if pode_usar_bot else Acesso.AGUARDANDO


def linha_saudacao(nome: str | None) -> str:
    partes = (nome or "").strip().split()
    primeiro = _MARCAS_MARKDOWN.sub("", partes[0]) if partes else ""
    return f"Olá, {primeiro}!" if primeiro else "Olá!"


def mensagem_de_boas_vindas(telegram_user_id: int, nome: str | None, acesso: Acesso) -> str:
    comum = {
        "saudacao": linha_saudacao(nome),
        "corpo": _CORPO,
        "como_usar": _COMO_USAR,
        "id": telegram_user_id,
    }
    if acesso is Acesso.LIBERADO:
        return JA_AUTORIZADO.format(**comum)
    if acesso is Acesso.AGUARDANDO:
        return AGUARDANDO_LIBERACAO.format(**comum)
    return PRIMEIRO_USO.format(**comum)


def mensagem_de_recusa(acesso: Acesso, telegram_user_id: int, nome: str | None = None) -> str:
    if acesso is Acesso.AGUARDANDO:
        return RECUSA_AUDIO.format(saudacao=linha_saudacao(nome))
    return SEM_AUTORIZACAO.format(id=telegram_user_id)
