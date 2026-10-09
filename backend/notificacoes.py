"""Configuração e mensagens que o bot envia sem esperar um áudio.

Duas coisas acontecem aqui no boot, sem depender de o usuário mandar comando:
a descrição do bot no perfil e os comandos que aparecem no botão de menu, ao
lado do campo de texto, no chat do Telegram.
"""

import logging
import os

from telegram import Bot, BotCommand, MenuButtonCommands
from telegram.error import Forbidden, TelegramError

from mensagens import DESCRICAO_BOT, Acesso, mensagem_de_boas_vindas

logger = logging.getLogger("analiser.notificacoes")

_bot: Bot | None = None

# Os comandos que o dev precisa sem decorar. A ordem é a do botão de menu:
# escolher o cliente, acrescentar reunião, cadastrar um nome novo, desistir.
COMANDOS = (
    BotCommand("menu", "escolher cliente pelo nome ou código"),
    BotCommand("nova_reuniao", "acrescentar reunião a um cliente"),
    BotCommand("novo_cliente", "cadastrar um cliente novo"),
    BotCommand("cancelar", "desistir do que está em andamento"),
)


def registrar_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def _obter_bot() -> Bot | None:
    """No processo do bot o `_bot` já está registrado. Na API não: lá criamos
    um cliente sob demanda, só para envio (polling não passa por aqui)."""
    global _bot
    if _bot is None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            return None
        _bot = Bot(token)
    return _bot


async def descrever_bot() -> None:
    if _bot is None:
        return
    try:
        await _bot.set_my_description(description=DESCRICAO_BOT)
    except TelegramError:
        logger.warning("não foi possível atualizar a descrição do bot no Telegram")
    await _publicar_comandos()


async def _publicar_comandos() -> None:
    """Publica os comandos e liga o botão de menu ao lado do campo de texto.

    Sem `set_chat_menu_button`, o usuário precisa decorar "/menu"; sem
    `set_my_commands`, o botão abre uma lista vazia. As duas chamadas juntas
    são o que faz o menu aparecer.
    """
    if _bot is None:
        return
    try:
        await _bot.set_my_commands(COMANDOS)
    except TelegramError:
        logger.warning("não foi possível publicar os comandos no Telegram", exc_info=True)
        return
    try:
        await _bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except TelegramError:
        logger.warning("não foi possível ligar o botão de menu no Telegram", exc_info=True)
        return
    logger.info(
        "comandos publicados (%s) e botão de menu ligado",
        ", ".join(f"/{c.command}" for c in COMANDOS),
    )


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


async def avisar_recuperacao(telegram_user_id: int, codigo: str) -> bool:
    """Código de redefinição de senha do painel, direto no Telegram do usuário."""
    bot = _obter_bot()
    if bot is None:
        logger.info("bot indisponível; código do usuário %s não enviado", telegram_user_id)
        return False
    try:
        await bot.send_message(
            chat_id=telegram_user_id,
            text=(
                "Pedido de recuperação de senha do painel AnaliseR.\n\n"
                f"Código: *{codigo}* — vale por 10 minutos, uma única vez.\n\n"
                "Se não foi você, ignore: nada muda sem o código."
            ),
            parse_mode="Markdown",
        )
    except TelegramError as exc:
        logger.warning(
            "não foi possível enviar o código de recuperação para %s: %s",
            telegram_user_id, exc,
        )
        return False
    logger.info("código de recuperação enviado para o usuário %s", telegram_user_id)
    return True


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
