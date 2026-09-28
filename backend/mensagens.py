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

Envie o áudio da conversa com o cliente. Quando o relatório estiver pronto, peça o PDF ou pergunte sobre o roadmap aqui no chat.

*Mudando de assunto:* agora eu separo as reuniões por cliente. Use /menu para escolher um cliente, ou /nova_reuniao para acrescentar uma reunião a um cliente que já existe."""

MENU_CLIENTES = """{saudacao}

*Escolha um cliente*
{clientes}

Você também pode escrever direto, sem usar o menu:
"cliente nome" / "cliente CLI-0007" / "reuniao 2 da CLI-0007"."""

MENU_CLIENTE_ENTRADA = """Qual cliente?

*Opções*
- Escreva o nome, mesmo parcial: "clínica São"
- Escreva o código: "CLI-0007"
- /cancelar para desistir"""

MENU_CLIENTE_NAO_ENCONTRADO = """Nenhum cliente com "{termo}".

{disponiveis}

/menu para ver todos, ou mande o áudio de uma conversa nova."""

MENU_CLIENTE_VARIOS = """Achei {total} clientes com "{termo}":

{lista}

Refine com mais letras ou escreva o código exato."""

MENU_CLIENTE_DETALHE = """*{nome}* ({codigo})
{quantidade} reunião(ões) salva(s).

{lista}"""

MENU_CLIENTE_SEM_REUNIAO = """*{nome}* ({codigo}) ainda não tem nenhuma reunião.

Use /nova_reuniao para acrescentar a primeira, ou mande o áudio da conversa."""

CONFIRMAR_CLIENTE = """Confirma o cliente?

*{nome}*
{trecho}
{ja_existe}

O relatório já está pronto. Escolha:
{opcoes}"""

CONFIRMAR_CLIENTE_DESCONHECIDO = """Não consegui identificar o cliente na conversa.

O relatório está pronto. Em qual cliente salvo?

{opcoes}

/menu para ver todos, ou mande o áudio de novo se o cliente for outro."""

CLIENTE_SALVO = """*{nome}* ({codigo}) — reunião {numero} salva{fonte}

O relatório abaixo é desta reunião."""

CANCELADO = """Cancelado. Nada foi salvo.

Pode mandar o áudio de novo quando quiser."""

NOVA_REUNIAO_CLIENTE = """*Nova reunião*

Para quem é a reunião?

*Opções*
- nome do cliente, mesmo parcial: "clínica São"
- código: "CLI-0007"
- /cancelar para desistir"""

NOVA_REUNIAO_NUMERO = """Cliente: *{nome}* ({codigo})
{quantidade} reunião(ões) salva(s){ultimo}.

A próxima é a reunião *{proximo}*? Responda *sim* para gravar, ou mande o áudio da conversa agora."""

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
_TERMOS_NUMERO = ("dez", "onze", "doze", "treze", "catorze", "quinze", "dezesseis",
                  "dezessete", "dezoito", "dezenove", "vinte")


def normalizar(texto: str) -> list[str]:
    decomposto = unicodedata.normalize("NFKD", texto.lower())
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", " ", sem_acento).split()


MAX_PALAVRAS_SAUDACAO = 5
MAX_CLIENTES_MENU = 25
MAX_CLIENTES_ENCONTRADOS = 8

_CODIGO_CLIENTE = re.compile(r"\bcli[-\s]?(\d{1,8})\b", re.IGNORECASE)


def parece_codigo_cliente(texto: str) -> str | None:
    """Extrai 'CLI-0007' de um texto livre. None se não parece código."""
    achado = _CODIGO_CLIENTE.search(texto)
    if achado:
        return f"CLI-{int(achado.group(1)):04d}"
    limpa = texto.strip().upper()
    if limpa.isdigit() and 1 <= int(limpa) <= 99999999:
        return f"CLI-{int(limpa):04d}"
    return None


def termo_de_busca(texto: str) -> str:
    """Tira o rótulo e a pontuação, sobrando só o nome digitado."""
    limpo = re.sub(r"^\s*(qual\s+cliente|cliente|para\s+o\s+cliente)\b", " ", texto, flags=re.IGNORECASE)
    return re.sub(r"[\"'`*_?]+", " ", limpo).strip()


def listar_clientes_menu(clientes: list) -> str:
    if not clientes:
        return "Nenhum cliente cadastrado ainda."
    linhas = [
        f"- *{c['codigo']}* — {c['nome']} ({c['total_reunioes']} reunião(ões))"
        for c in clientes[:MAX_CLIENTES_MENU]
    ]
    if len(clientes) > MAX_CLIENTES_MENU:
        linhas.append(f"- … e mais {len(clientes) - MAX_CLIENTES_MENU}")
    return "\n".join(linhas)


def resumir_clientes(clientes: list) -> str:
    if not clientes:
        return "Nenhum cliente cadastrado ainda."
    return ", ".join(f"{c['codigo']} ({c['nome']})" for c in clientes[:MAX_CLIENTES_MENU])


def listar_reunioes_menu(reunioes: list) -> str:
    return "\n".join(
        f"- Reunião {r['numero']} — {r['data_criacao'].strftime('%d/%m/%Y')}"
        for r in reunioes
    ) or "- Nenhuma reunião ainda."


def listar_encontrados(clientes: list) -> str:
    linhas = [
        f"- *{c['codigo']}* — {c['nome']} ({c['total_reunioes']} reunião(ões))"
        for c in clientes[:MAX_CLIENTES_ENCONTRADOS]
    ]
    if len(clientes) > MAX_CLIENTES_ENCONTRADOS:
        linhas.append(f"- … e mais {len(clientes) - MAX_CLIENTES_ENCONTRADOS}")
    return "\n".join(linhas)


OPCOES_CLIENTE = (
    "1 — é esse cliente, salvar a reunião aqui\n"
    "2 — salvar em outro cliente já cadastrado (envie o nome ou o código)\n"
    "3 — criar um cliente novo com esse nome"
)


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
    """Índice 0-based da reunião citada. Sem número, fica a mais recente.

    `total` é a quantidade de reuniões do cliente, e o resultado é o número da
    reunião menos um: reunião 2 de um cliente com 3 reuniões devolve 1.
    """
    if total <= 0:
        raise ValueError("não há reunião salva")
    frase = " ".join(normalizar(texto))
    tokens = frase.split()
    for raiz, numero in _ORDINAIS:
        if any(token.startswith(raiz) for token in tokens) and numero <= total:
            return numero - 1
    for posicao, raiz in enumerate(_TERMOS_NUMERO, start=10):
        if any(token.startswith(raiz) for token in tokens) and posicao <= total:
            return posicao - 1
    # O número da reunião, não a posição: "reuniao 2" é a segunda.
    for achado in re.finditer(r"(?:reuniao|numero)\s*(?:n[o°]?\s*)?(\d{1,3})", frase):
        numero = int(achado.group(1))
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
