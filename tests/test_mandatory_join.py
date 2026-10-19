import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, Message, User

from database.community_access import JoinSettings
from handlers.mandatory_join import (
    MandatoryJoinMiddleware,
    check_membership_callback,
    get_membership_status,
)


class MandatoryJoinMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def _message(self, text="/shop"):
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=55, type="private"),
            from_user=User(id=10, is_bot=False, first_name="Test"),
            text=text,
        )
        return message

    async def test_disabled_gate_passes_action(self):
        event = self._message()
        handler = AsyncMock(return_value="handled")
        with (
            patch.object(Message, "answer", new_callable=AsyncMock) as answer,
            patch("handlers.mandatory_join.register_user", new=AsyncMock()),
            patch("handlers.mandatory_join.get_admin_ids", return_value=frozenset()),
            patch("handlers.mandatory_join.get_user_access", new=AsyncMock(return_value=(JoinSettings(), False))),
        ):
            result = await MandatoryJoinMiddleware()(handler, event, {})
        self.assertEqual(result, "handled")
        handler.assert_awaited_once()
        answer.assert_not_awaited()

    async def test_unverified_user_is_blocked_with_join_request_link(self):
        event = self._message()
        handler = AsyncMock(return_value="handled")
        settings = JoinSettings(True, -100123, "https://t.me/+joinrequest")
        with (
            patch.object(Message, "answer", new_callable=AsyncMock) as answer,
            patch("handlers.mandatory_join.register_user", new=AsyncMock()),
            patch("handlers.mandatory_join.get_admin_ids", return_value=frozenset()),
            patch("handlers.mandatory_join.get_user_access", new=AsyncMock(return_value=(settings, False))),
            patch("handlers.mandatory_join.get_membership_status", new=AsyncMock(return_value=False)),
        ):
            result = await MandatoryJoinMiddleware()(handler, event, {})
        self.assertIsNone(result)
        handler.assert_not_awaited()
        answer.assert_awaited_once()
        kwargs = answer.await_args.kwargs
        self.assertEqual(kwargs["reply_markup"].inline_keyboard[0][0].url, settings.invite_link)

    async def test_verified_user_and_start_are_allowed(self):
        settings = JoinSettings(True, -100123, "https://t.me/+joinrequest")
        for text in ("/shop", "/start"):
            event = self._message(text)
            handler = AsyncMock(return_value="handled")
            with (
                patch.object(Message, "answer", new_callable=AsyncMock),
                patch("handlers.mandatory_join.register_user", new=AsyncMock()),
                patch("handlers.mandatory_join.get_admin_ids", return_value=frozenset()),
                patch("handlers.mandatory_join.get_user_access", new=AsyncMock(return_value=(settings, False))),
                patch("handlers.mandatory_join.get_membership_status", new=AsyncMock(return_value=True)),
            ):
                result = await MandatoryJoinMiddleware()(handler, event, {})
            self.assertEqual(result, "handled")
            handler.assert_awaited_once()

    async def test_telegram_member_statuses_are_classified_correctly(self):
        class FakeBot:
            def __init__(self, status, is_member=False):
                self.status = status
                self.is_member = is_member

            async def get_chat_member(self, *, chat_id, user_id):
                self.requested = (chat_id, user_id)
                return SimpleNamespace(status=self.status, is_member=self.is_member)

        for status in ("creator", "administrator", "member"):
            bot = FakeBot(status)
            self.assertTrue(await get_membership_status(bot, -100123, 10))
            self.assertEqual(bot.requested, (-100123, 10))
        self.assertTrue(await get_membership_status(FakeBot("restricted", True), -100123, 10))
        for status in ("left", "kicked"):
            self.assertFalse(await get_membership_status(FakeBot(status), -100123, 10))
        self.assertFalse(await get_membership_status(FakeBot("restricted", False), -100123, 10))

    async def test_existing_member_is_allowed_without_an_invite_link(self):
        event = self._message("/start")
        handler = AsyncMock(return_value="handled")
        settings = JoinSettings(True, -100123, None)
        with (
            patch("handlers.mandatory_join.register_user", new=AsyncMock()),
            patch("handlers.mandatory_join.get_admin_ids", return_value=frozenset()),
            patch("handlers.mandatory_join.get_user_access", new=AsyncMock(return_value=(settings, False))),
            patch("handlers.mandatory_join.get_membership_status", new=AsyncMock(return_value=True)),
        ):
            result = await MandatoryJoinMiddleware()(handler, event, {})
        self.assertEqual(result, "handled")
        handler.assert_awaited_once()

    async def test_telegram_api_error_is_reported_as_temporary_not_nonmember(self):
        class ApiFailure(Exception):
            pass

        event = self._message()
        handler = AsyncMock(return_value="handled")
        settings = JoinSettings(True, -100123, "https://t.me/+joinrequest")
        with (
            patch.object(Message, "answer", new_callable=AsyncMock) as answer,
            patch("handlers.mandatory_join.TelegramAPIError", ApiFailure),
            patch("handlers.mandatory_join.register_user", new=AsyncMock()),
            patch("handlers.mandatory_join.get_admin_ids", return_value=frozenset()),
            patch("handlers.mandatory_join.get_user_access", new=AsyncMock(return_value=(settings, False))),
            patch("handlers.mandatory_join.get_membership_status", new=AsyncMock(side_effect=ApiFailure())),
        ):
            result = await MandatoryJoinMiddleware()(handler, event, {})
        self.assertIsNone(result)
        handler.assert_not_awaited()
        self.assertIn("tekshirib bo‘lmadi", answer.await_args.args[0])

    async def test_recheck_button_uses_the_configured_chat_id(self):
        callback = SimpleNamespace(
            bot=object(),
            from_user=User(id=10, is_bot=False, first_name="Test"),
            answer=AsyncMock(),
        )
        settings = JoinSettings(True, -100999, "https://t.me/+joinrequest")
        with (
            patch("handlers.mandatory_join.get_join_settings", new=AsyncMock(return_value=settings)),
            patch("handlers.mandatory_join.get_membership_status", new=AsyncMock(return_value=True)) as check,
        ):
            await check_membership_callback(callback)
        check.assert_awaited_once_with(callback.bot, -100999, 10)
        callback.answer.assert_awaited_once()
