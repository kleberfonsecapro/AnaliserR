"""BUG-002: processar o áudio não pode travar o event loop."""

import asyncio
import time
from types import SimpleNamespace

import bot


async def test_transcrever_nao_bloqueia_o_loop(monkeypatch, tmp_path):
    """A leitura do arquivo roda em thread; a chamada à Groq é async.
    Enquanto transcreve, o loop tem de continuar rodando outros callbacks."""

    caminho = tmp_path / "audio.ogg"
    caminho.write_bytes(b"\x00" * 1024)

    class Transcricoes:
        async def create(self, **kwargs):
            await asyncio.sleep(0.05)  # simula latência de rede da Groq
            return SimpleNamespace(text="transcrição")

    class GroqFalso:
        audio = SimpleNamespace(transcriptions=Transcricoes())

    monkeypatch.setattr(bot, "_groq", GroqFalso())

    atrasos: list[float] = []

    async def marcador():
        marco = time.perf_counter()
        for _ in range(20):
            await asyncio.sleep(0.005)
            atrasos.append(time.perf_counter() - marco)
            marco = time.perf_counter()

    texto, _ = await asyncio.gather(bot.transcrever(caminho), marcador())

    assert texto == "transcrição"
    # Nenhum tick pode ficar parado: bloqueio de disco apareceria aqui.
    assert max(atrasos) < 0.05
