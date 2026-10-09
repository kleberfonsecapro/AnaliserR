"""Contexto da organização injetado na geração do relatório.

A pasta `padrao_stack/` é a referência que a IA usa para decidir a stack
sugerida e o roadmap de cada cliente. O roadmap sair separado em backend e
frontend, e cada camada é indexada por um guia derivado deste arquivo — é esse
índice que diz onde a IA deve procurar a tecnologia de cada etapa. Este módulo
lê esses documentos uma única vez, no boot, e falha alto se eles não estiverem
disponíveis: degradar em silêncio para "sugerir qualquer stack" é o pior modo
de falha possível, porque ninguém percebe.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

logger = logging.getLogger("analiser.referencia")

STACK_ARQUIVO = "STACK_PADRAO.md"
CONTRATO_ARQUIVO = "AGENT_CONTRACT.md"

# Seções do contrato que valem para o relatório do cliente. Identidade e
# Comportamento, Qualidade de Código, Arquitetura e a Regra de Ouro ficam de
# fora: são contrato do agente de engenharia, não guia de produto.
SECOES_CONTRATO = ("3", "4", "5")

TITULO_SECOES = {
    "3": "DevSecOps e Ciclo de Vida",
    "4": "Disaster Recovery (DR) e Backups",
    "5": "Integração e Entrega Contínuas (CI/CD)",
}

_CANDIDATOS = (
    Path("/app/padrao_stack"),
    Path(__file__).resolve().parent.parent / "padrao_stack",
)

_HEADING = re.compile(r"^##\s+(\d+)\.\s*(.+?)\s*$", re.MULTILINE)
_HEADING_QUALQUER = re.compile(r"^##\s+", re.MULTILINE)

# Uma linha da stack padrão, no formato "Categoria: tecnologias" — vindo de
# "* Frontend: TanStack Start (React 19) + Vite" ou de "- **Banco de dados**: ...".
_CATEGORIA = re.compile(
    r"^\s*(?:[-*+]\s+|\d+\.\s+)?(?:\*\*|__)?([^:*\n]{2,48}?)(?:\*\*|__)?\s*:\s+(\S.*)$"
)

# Camada do roadmap e os fragmentos de nome de categoria que a abastecem. A
# ordem importa: a primeira casa leva a tecnologia. O que não casar com nada
# entra em "Comum", porque linguagem, runtime e testes servem às duas camadas.
CAMADAS = (
    ("Frontend", ("front", "ui", "estilo", "css", "visual", "component", "interface")),
    ("Backend", ("back", "api", "servidor", "server", "banco", "database", "dados",
                 "orm", "auth", "autentic", "fila", "cache", "mensageria")),
    ("Comum", ()),
)


class ReferenciaIndisponivel(RuntimeError):
    """A pasta de referência da organização não pôde ser carregada."""


_cache: dict[str, str] | None = None


def _localizar() -> Path:
    procurados = ", ".join(str(c) for c in _CANDIDATOS)
    for pasta in _CANDIDATOS:
        if (pasta / STACK_ARQUIVO).is_file() and (pasta / CONTRATO_ARQUIVO).is_file():
            return pasta
    raise ReferenciaIndisponivel(
        f"pasta de referência não encontrada (procurado em: {procurados}). "
        "O relatório sairia sem a stack padrão da organização."
    )


def _digest(bruto: bytes) -> str:
    return hashlib.sha256(bruto).hexdigest()


def _extrair_secoes(texto: str) -> str:
    """Recorta as seções numeradas pedidas, sem o cabeçalho de cada bloco."""
    cortes = [m.start() for m in _HEADING_QUALQUER.finditer(texto)]
    if not cortes:
        raise ReferenciaIndisponivel(
            f"{CONTRATO_ARQUIVO} não tem nenhuma seção '## N. Título'; "
            "a extração por seção foi concluída sem resultado"
        )

    blocos: list[str] = []
    for m in _HEADING.finditer(texto):
        numero, titulo = m.group(1), m.group(2)
        if numero not in SECOES_CONTRATO:
            continue
        inicio = m.start()
        fim = next((c for c in cortes if c > inicio), len(texto))
        blocos.append(texto[inicio:fim].rstrip())

    faltando = [n for n in SECOES_CONTRATO if not any(f"## {n}." in b for b in blocos)]
    if faltando or not blocos:
        raise ReferenciaIndisponivel(
            f"{CONTRATO_ARQUIVO} não contém as seções {', '.join(SECOES_CONTRATO)}; "
            f"faltando: {', '.join(faltando) or 'todas'}. "
            "O contrato mudou de forma e o relatório perderia o guia de engenharia."
        )

    return "\n\n".join(blocos)


def camadas(stack: str) -> dict[str, list[str]]:
    """Indexa a stack padrão por camada do roadmap (Frontend, Backend, Comum).

    É o índice que a IA percorre para achar a tecnologia de cada etapa sem
    precisar reler o documento inteiro nem inventar fora do padrão.
    """
    achadas: dict[str, list[str]] = {nome: [] for nome, _ in CAMADAS}
    for linha in stack.splitlines():
        achou = _CATEGORIA.match(linha)
        if not achou:
            continue
        categoria, tecnologias = achou.group(1).strip(), achou.group(2).strip()
        normalizada = categoria.lower()
        alvo = next(
            (nome for nome, chaves in CAMADAS if any(chave in normalizada for chave in chaves)),
            "Comum",
        )
        etiqueta = (
            tecnologias
            if normalizada == alvo.lower()
            else f"{categoria}: {tecnologias}"
        )
        achadas[alvo].append(etiqueta)
    return {nome: itens for nome, itens in achadas.items() if itens}


def _guia_de_camadas(stack: str) -> str:
    """Instrução que aponta, por camada, onde a IA deve procurar a tecnologia."""
    por_camada = camadas(stack)
    linhas = [
        "Guia de escolha de tecnologia por camada, derivado da stack padrão acima.",
        "O 'Roadmap passo a passo' sai em duas subseções — ### Backend e ### Frontend — "
        "e cada etapa só pode usar tecnologia da lista da sua camada:",
    ]
    for nome, _ in CAMADAS:
        itens = por_camada.get(nome)
        if not itens:
            continue
        linhas.append(f"- {nome}: " + " · ".join(itens))
    for nome, _ in CAMADAS:
        if nome != "Comum" and not por_camada.get(nome):
            linhas.append(
                f"- {nome}: a stack padrão não declara esta camada; não invente tecnologia "
                "para ela, sinalize o caso em 'Pontos em aberto'."
            )
    linhas.append(
        "Antes de sugerir qualquer tecnologia, procure-a nesta lista. Fora dela, só use "
        "o que servir melhor quando nenhuma linha der conta do pedido, e declare a troca "
        "no topo de 'Stack sugerida', com o motivo, em uma ou duas frases."
    )
    return "\n".join(linhas)


def carregar() -> dict[str, str]:
    """Lê, extrai e memoriza a referência. Idempotente."""
    global _cache
    if _cache is not None:
        return _cache

    pasta = _localizar()

    stack_bruto = (pasta / STACK_ARQUIVO).read_bytes()
    contrato_bruto = (pasta / CONTRATO_ARQUIVO).read_bytes()

    stack = stack_bruto.decode("utf-8").strip()
    contrato = _extrair_secoes(contrato_bruto.decode("utf-8"))
    guia = _guia_de_camadas(stack)

    _cache = {
        "pasta": str(pasta),
        "stack": stack,
        "contrato": contrato,
        "guia": guia,
        "hash_stack": _digest(stack_bruto),
        "hash_contrato": _digest(contrato_bruto),
    }

    por_camada = camadas(stack)
    for nome, _ in CAMADAS:
        total = len(por_camada.get(nome, []))
        if nome != "Comum" and not total:
            logger.warning(
                "stack padrão não declara a camada %s: o roadmap sairia sem tecnologia "
                "de referência para ela",
                nome,
            )
            continue
        logger.info(
            "stack padrão: camada %s com %d %s",
            nome,
            total,
            "linha" if total == 1 else "linhas",
        )

    logger.info(
        "referência carregada de %s | stack %s (%d bytes) | contrato %s (%d bytes)",
        pasta,
        _cache["hash_stack"][:12],
        len(stack.encode("utf-8")),
        _cache["hash_contrato"][:12],
        len(contrato.encode("utf-8")),
    )
    for numero in SECOES_CONTRATO:
        logger.info("contrato: injetando a seção %s (%s)", numero, TITULO_SECOES[numero])

    return _cache


def mensagens_de_referencia() -> list[dict[str, str]]:
    """Documentos da organização como contexto do LLM.

    Três mensagens de sistema separadas para o modelo distinguir o que se
    constrói (stack), como o time trabalha (contrato) e onde procurar a
    tecnologia de cada camada do roadmap (guia de camadas).
    """
    ref = carregar()
    mensagens = [
        {
            "role": "system",
            "content": (
                "Stack padrão da organização. Esta é a primeira opção para a "
                "seção 'Stack sugerida' e para o roadmap, sempre que ela servir ao "
                "pedido do cliente.\n\n" + ref["stack"]
            ),
        },
        {
            "role": "system",
            "content": (
                "Contrato de engenharia da organização. Não é preferência: são as "
                "condições que a aplicação do cliente terá que satisfazer. O "
                "'Roadmap passo a passo' e os 'Critérios de aceite' devem refletir "
                "estas práticas, convertendo cada uma em uma entrega concreta.\n\n"
                + ref["contrato"]
            ),
        },
    ]
    if ref.get("guia"):
        mensagens.append({"role": "system", "content": ref["guia"]})
    return mensagens


def hash_referencia() -> str:
    """Identifica a versão da referência usada, para auditoria do relatório."""
    ref = carregar()
    return f"stack:{ref['hash_stack'][:12]}/contrato:{ref['hash_contrato'][:12]}"
