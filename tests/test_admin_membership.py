import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiogram.types import CallbackQuery, Chat, Message, User

from database.community_access import JoinSettings
from handlers.admin import (
    MembershipSettingsStates,
    receive_membership_setting,
    toggle_membership_gate,
)


class _State:
    def __init__(self, state):
        self.current = state
        self.clear = AsyncMock()

    async def get_state(self):
        return self.current


class AdminMembershipInputTests(unittest.IsolatedAsyncioTestCase):
    def _message(self, text):
        return Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=55, type="private"),
            from_user=User(id=77, is_bot=False, first_name="Admin"),
            text=text,
        )

    def _callback(self):
        return CallbackQuery(
            id="callback-id",
            from_user=User(id=77, is_bot=False, first_name="Admin"),
            chat_instance="chat-instance",
            message=Message(
                message_id=2,
                date=datetime.now(timezone.utc),
                chat=Chat(id=77, type="private"),
                from_user=User(id=1, is_bot=True, first_name="Bot"),
                text="settings",
            ),
            data="admin:membership:toggle",
        )

    async def test_admin_chat_id_input_persists_negative_group_id(self):
        state = _State(MembershipSettingsStates.waiting_for_chat_id.state)
        with (
            patch.object(Message, "answer", new_callable=AsyncMock),
            patch("handlers.admin.update_join_settings", new=AsyncMock()) as update,
            patch("handlers.admin.get_join_settings", new=AsyncMock(return_value=JoinSettings(False, -100500, None))),
        ):
            await receive_membership_setting(self._message("-100500"), state)
        update.assert_awaited_once_with(admin_id=77, chat_id=-100500)
        state.clear.assert_awaited_once()

    async def test_admin_join_request_link_requires_https_telegram_link(self):
        state = _State(MembershipSettingsStates.waiting_for_invite_link.state)
        with (
            patch.object(Message, "answer", new_callable=AsyncMock) as answer,
            patch("handlers.admin.update_join_settings", new=AsyncMock()) as update,
            patch("handlers.admin.get_join_settings", new=AsyncMock(return_value=JoinSettings(False, -100500, "https://t.me/+invite"))),
        ):
            await receive_membership_setting(self._message("http://t.me/+invite"), state)
            update.assert_not_awaited()
            self.assertIn("https://t.me", answer.await_args_list[0].args[0])
            await receive_membership_setting(self._message("https://t.me/+invite"), state)
        update.assert_awaited_once_with(admin_id=77, invite_link="https://t.me/+invite")

    async def test_admin_toggle_button_enables_and_disables_gate(self):
        states = [
            JoinSettings(False, -100500, "https://t.me/+invite"),
            JoinSettings(True, -100500, "https://t.me/+invite"),
            JoinSettings(True, -100500, "https://t.me/+invite"),
            JoinSettings(False, -100500, "https://t.me/+invite"),
        ]
        with (
            patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
            patch.object(Message, "edit_text", new_callable=AsyncMock),
            patch("handlers.admin.get_join_settings", new=AsyncMock(side_effect=states)),
            patch("handlers.admin.update_join_settings", new=AsyncMock()) as update,
        ):
            await toggle_membership_gate(self._callback())
            await toggle_membership_gate(self._callback())
        self.assertEqual(
            [call.kwargs for call in update.await_args_list],
            [
                {"admin_id": 77, "enabled": True},
                {"admin_id": 77, "enabled": False},
            ],
        )

