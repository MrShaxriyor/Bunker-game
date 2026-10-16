"""Safely remove handled bot commands from group chats after processing."""

import logging

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

logger = logging.getLogger(__name__)

GROUP_COMMANDS = frozenset(
    {
        "start",
        "bunker",
        "bunkercancel",
        "mycards",
        "para",
        "mypara",
        "dpara",
        "shop",
        "inventory",
        "buycoins",
        "paysupport",
        "admin",
    }
)


class GroupCommandCleanupMiddleware(BaseMiddleware):
    """Delete only recognized group commands, after their handler returns."""

    def __init__(self) -> None:
        self._bot_username: str | None = None

    async def __call__(self, handler, event, data):
        if not isinstance(event, Message):
            return await handler(event, data)

        command = self._extract_command(event)
        if (
            command is None
            or command not in GROUP_COMMANDS
            or event.chat.type not in {"group", "supergroup"}
        ):
            return await handler(event, data)

        mention = self._extract_mention(event)
        if mention is not None and not await self._mention_targets_this_bot(event, mention):
            return await handler(event, data)

        result = await handler(event, data)
        if result is not UNHANDLED:
            await self._delete_if_not_pinned(event)
        return result

    @staticmethod
    def _extract_command(message: Message) -> str | None:
        text = message.text
        if not text:
            return None
        for entity in message.entities or ():
            if entity.type == "bot_command" and entity.offset == 0:
                token = text[: entity.length]
                return token[1:].split("@", 1)[0].casefold()
        return None

    @staticmethod
    def _extract_mention(message: Message) -> str | None:
        if not message.text:
            return None
        token = message.text.split(maxsplit=1)[0]
        if "@" not in token:
            return None
        return token.rsplit("@", 1)[1].casefold()

    async def _mention_targets_this_bot(
        self,
        message: Message,
        mention: str,
    ) -> bool:
        if self._bot_username is None:
            try:
                bot_info = await message.bot.get_me()
            except TelegramAPIError as error:
                logger.info(
                    "Guruh buyrug‘i manzilini tekshirib bo‘lmadi: error_type=%s",
                    type(error).__name__,
                )
                return False
            self._bot_username = (bot_info.username or "").casefold()
        return bool(self._bot_username) and mention == self._bot_username

    @staticmethod
    async def _delete_if_not_pinned(message: Message) -> None:
        try:
            chat = await message.bot.get_chat(message.chat.id)
        except TelegramAPIError as error:
            logger.info(
                "Guruh buyrug‘ini pinned holatiga tekshirib bo‘lmadi; o‘chirish bekor qilindi: error_type=%s",
                type(error).__name__,
            )
            return

        pinned_message = getattr(chat, "pinned_message", None)
        if pinned_message is not None and pinned_message.message_id == message.message_id:
            return

        try:
            await message.delete()
        except TelegramAPIError as error:
            logger.info(
                "Guruh buyrug‘ini o‘chirib bo‘lmadi: error_type=%s",
                type(error).__name__,
            )
