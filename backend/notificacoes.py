"""Mensagens que o bot envia ao usuário sem esperar um áudio — por exemplo, quando o admin libera o acesso dele."""

import logging

from telegram import Bot
from telegram.error import Forbidden, TelegramError

from mensagens import DESCRICAO_BOT, Acesso, mensagem_de_boas_vindas

logger = logging.getLogger("analiser.notificacoes")

_bot: Bot | None = None


def registrar_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


async def descrever_bot() -> None:
    if _bot is None:
        return
    try:
        await _bot.set_my_description(description=DESCRICAO_BOT)
    except TelegramError:
        logger.warning("não foi possível atualizar a descrição do bot no Telegram")


async def _primeiro_nome(telegram_user_id: int) -> str | None:
    """Serve também para checar se o bot consegue falar com a pessoa."""
    assert _bot is not None
    try:
        chat = await _bot.get_chat(telegram_user_id)
    except Forbidden:
        logger.info(
            "usuário %s ainda não iniciou conversa com o bot; mensagem não enviada",
            telegram_user_id,
        )
        return None
    except TelegramError as exc:
        logger.warning("não foi possível ler o perfil do usuário %s: %s", telegram_user_id, exc)
        return None
    return chat.first_name


async def avisar_boas_vindas(
    telegram_user_id: int,
    acesso: Acesso,
    nome: str | None = None,
) -> bool:
    """Envia a mensagem de acesso fora do fluxo de resposta. Devolve False se não entregou."""
    if _bot is None:
        logger.info("bot ainda não registrado; mensagem do usuário %s não enviada", telegram_user_id)
        return False
    if nome is None:
        nome = await _primeiro_nome(telegram_user_id)
        if nome is None:
            return False
    try:
        await _bot.send_message(
            chat_id=telegram_user_id,
            text=mensagem_de_boas_vindas(telegram_user_id, nome, acesso),
            parse_mode="Markdown",
        )
    except TelegramError as exc:
        logger.warning("não foi possível avisar o usuário %s: %s", telegram_user_id, exc)
        return False
    logger.info("mensagem enviada para o usuário %s (acesso: %s)", telegram_user_id, acesso.value)
    return True
