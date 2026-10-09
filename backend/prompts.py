"""Prompts do AnaliseR, versionados em arquivos (DEV-002).

O texto vive em backend/prompts/*.txt — cada mudança de prompt é um commit
visível, não uma alteração escondida em código. `versao()` resume o conteúdo
de todos os arquivos num hash curto gravado junto de cada reunião.
"""

import hashlib
from pathlib import Path

_PASTA = Path(__file__).parent / "prompts"


def _ler(nome: str) -> str:
    return (_PASTA / nome).read_text(encoding="utf-8")


def versao() -> str:
    """Hash curto dos prompts, para saber com qual texto cada reunião foi gerada."""
    resumo = hashlib.sha1()
    for arquivo in sorted(_PASTA.glob("*.txt")):
        resumo.update(arquivo.name.encode())
        resumo.update(arquivo.read_bytes())
    return resumo.hexdigest()[:10]


def decisao() -> str:
    return _ler("decisao.txt").format(
        secoes_fechadas=_ler("secoes_fechadas.txt"),
        regras_stack=_ler("regras_stack.txt"),
    )


def fechado() -> str:
    return _ler("fechado.txt").format(
        secoes_fechadas=_ler("secoes_fechadas.txt"),
        regras_stack=_ler("regras_stack.txt"),
    )


def identificar_cliente() -> str:
    return _ler("identificar_cliente.txt")
