"""Processo dedicado do bot (OPS-001): roda o polling fora do processo da API.

Sobe com `python bot_worker.py`. O heartbeat em /tmp/bot-ok alimenta o
healthcheck do container: se o polling morrer, o mtime envelhece e o
restart policy recria o container.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

from bot import iniciar_telegram, parar_telegram

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("analiser.bot_worker")

_HEARTBEAT = "/tmp/bot-ok"


async def _pulsar(parado: asyncio.Event) -> None:
    while not parado.is_set():
        try:
            await asyncio.to_thread(Path(_HEARTBEAT).write_text, str(time.time()))
        except OSError:
            logger.exception("não consegui gravar o heartbeat")
        await asyncio.sleep(15)


async def main() -> None:
    os.environ.setdefault("ROLE", "bot")
    telegram = await iniciar_telegram()
    parado = asyncio.Event()
    pulse = asyncio.create_task(_pulsar(parado))
    parar = asyncio.get_running_loop()
    try:
        await asyncio.Event().wait()  # vive até receber sinal do Docker
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        parado.set()
        await pulse
        if telegram.updater is not None:
            await telegram.updater.stop()
        await parar_telegram()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
