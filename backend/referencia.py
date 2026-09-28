"""Contexto da organização injetado na geração do relatório.

A pasta `padrao_stack/` é a referência que a IA usa para decidir a stack
sugerida e o roadmap de cada cliente. Este módulo lê esses documentos uma
única vez, no boot, e falha alto se eles não estiverem disponíveis: degradar
em silêncio para "sugerir qualquer stack" é o pior modo de falha possível,
porque ninguém percebe.
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

    _cache = {
        "pasta": str(pasta),
        "stack": stack,
        "contrato": contrato,
        "hash_stack": _digest(stack_bruto),
        "hash_contrato": _digest(contrato_bruto),
    }

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

    Duas mensagens de sistema separadas para o modelo distinguir o que se
    constrói (stack) de como o time trabalha (contrato).
    """
    ref = carregar()
    return [
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


def hash_referencia() -> str:
    """Identifica a versão da referência usada, para auditoria do relatório."""
    ref = carregar()
    return f"stack:{ref['hash_stack'][:12]}/contrato:{ref['hash_contrato'][:12]}"
