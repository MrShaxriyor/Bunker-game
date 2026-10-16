import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Message

from handlers.group_command_cleanup import GroupCommandCleanupMiddleware


def _message(text: str, chat_type: str = "supergroup") -> tuple[Bot, Message]:
    command = text.split(maxsplit=1)[0]
    bot = Bot(token="123456:TEST_TOKEN")
    message = Message.model_validate(
        {
            "message_id": 44,
            "date": datetime.now(timezone.utc).isoformat(),
            "chat": {"id": -1001, "type": chat_type, "title": "Test"},
            "from": {"id": 101, "is_bot": False, "first_name": "Test"},
            "text": text,
            "entities": [
                {
                    "type": "bot_command",
                    "offset": 0,
                    "length": len(command),
                }
            ],
        }
    ).as_(bot)
    return bot, message


class GroupCommandCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_command_is_deleted_after_handler_reply(self):
        bot, message = _message("/bunker@BunkerBot")
        events = []
        bot.get_me = AsyncMock(return_value=SimpleNamespace(username="BunkerBot"))
        bot.get_chat = AsyncMock(
            return_value=SimpleNamespace(pinned_message=None)
        )

        async def delete_message():
            events.append(("delete", message.message_id))

        async def handler(event, data):
            events.append(("reply", event.text))
            return "handled"

        with patch.object(
            Message,
            "delete",
            new=AsyncMock(side_effect=delete_message),
        ) as delete:
            result = await GroupCommandCleanupMiddleware()(handler, message, {})

        self.assertEqual(result, "handled")
        self.assertEqual(events, [("reply", "/bunker@BunkerBot"), ("delete", 44)])
        delete.assert_awaited_once()

    async def test_private_chat_commands_are_not_deleted(self):
        bot, message = _message("/shop", chat_type="private")
        bot.get_chat = AsyncMock()

        async def handler(event, data):
            return "handled"

        with patch.object(Message, "delete", new=AsyncMock()) as delete:
            result = await GroupCommandCleanupMiddleware()(handler, message, {})

        self.assertEqual(result, "handled")
        delete.assert_not_awaited()
        bot.get_chat.assert_not_awaited()

    async def test_pinned_command_is_preserved(self):
        bot, message = _message("/bunker")
        bot.get_chat = AsyncMock(
            return_value=SimpleNamespace(pinned_message=message)
        )

        async def handler(event, data):
            return "handled"

        with patch.object(Message, "delete", new=AsyncMock()) as delete:
            await GroupCommandCleanupMiddleware()(handler, message, {})

        delete.assert_not_awaited()

    async def test_command_directed_to_another_bot_is_preserved(self):
        bot, message = _message("/bunker@OtherBot")
        bot.get_me = AsyncMock(return_value=SimpleNamespace(username="BunkerBot"))
        bot.get_chat = AsyncMock()

        async def handler(event, data):
            return "handled"

        with patch.object(Message, "delete", new=AsyncMock()) as delete:
            await GroupCommandCleanupMiddleware()(handler, message, {})

        delete.assert_not_awaited()

    async def test_unhandled_command_is_left_for_child_router(self):
        bot, message = _message("/bunker")
        bot.get_chat = AsyncMock()

        async def handler(event, data):
            return UNHANDLED

        with patch.object(Message, "delete", new=AsyncMock()) as delete:
            result = await GroupCommandCleanupMiddleware()(handler, message, {})

        self.assertIs(result, UNHANDLED)
        delete.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
