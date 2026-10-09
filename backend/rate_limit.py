"""Limitador de taxa em memória, com janela deslizante (SEC-002).

Uma instância por processo basta: o backend roda num único processo (SEC-004/
OPS-001 tratam a separação). O estado é volátil de propósito — reiniciar o
container reabre a janela, o que é aceitável para brute force de login.
"""

import os
import time
from collections import defaultdict, deque


class LimitadorTaxa:
    """Bloqueia uma chave (IP+e-mail) depois de N falhas dentro da janela."""

    def __init__(self, maximo: int, janela_segundos: int) -> None:
        self._maximo = maximo
        self._janela = janela_segundos
        self._falhas: dict[str, deque[float]] = defaultdict(deque)

    def bloqueado(self, chave: str) -> bool:
        return self._tentativas_recentes(chave) >= self._maximo

    def registrar_falha(self, chave: str) -> None:
        self._tentativas_recentes(chave)
        self._falhas[chave].append(time.monotonic())

    def registrar_sucesso(self, chave: str) -> None:
        self._falhas.pop(chave, None)

    def _tentativas_recentes(self, chave: str) -> int:
        agora = time.monotonic()
        fila = self._falhas.get(chave)
        if fila is None:
            return 0
        while fila and agora - fila[0] > self._janela:
            fila.popleft()
        if not fila:
            self._falhas.pop(chave, None)
            return 0
        return len(fila)


def limitador_login() -> LimitadorTaxa:
    """Limite do login, configurável por ambiente (padrão: 5 falhas em 5 min)."""
    return LimitadorTaxa(
        maximo=int(os.environ.get("LOGIN_MAX_TENTATIVAS", "5")),
        janela_segundos=int(os.environ.get("LOGIN_JANELA_SEGUNDOS", "300")),
    )
