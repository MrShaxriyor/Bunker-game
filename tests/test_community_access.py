import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, ChatJoinRequest, User

from database import community_access
from database.models import BotSettings
from handlers.mandatory_join import process_chat_member_update, process_join_request_update


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Session:
    def __init__(self, settings):
        self.settings = settings
        self.execute_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return _Transaction()

    async def scalar(self, _statement):
        return self.settings

    async def execute(self, statement):
        self.execute_calls.append(statement)

    async def flush(self):
        return None


class CommunityAccessTests(unittest.IsolatedAsyncioTestCase):
    def _request(self, user_id=101, chat_id=-100500, *, is_bot=False):
        user = User.model_construct(id=user_id, is_bot=is_bot, first_name="Test")
        chat = Chat.model_construct(id=chat_id, type="supergroup")
        return ChatJoinRequest.model_construct(
            chat=chat,
            from_user=user,
            date=datetime.now(timezone.utc),
            user_chat_id=user_id,
        )

    async def test_pending_join_request_does_not_grant_membership(self):
        request = self._request()
        with (
            patch("handlers.mandatory_join.get_join_settings", new=AsyncMock(
                return_value=community_access.JoinSettings(True, -100500, "https://t.me/+sample")
            )),
            patch("handlers.mandatory_join.record_chat_membership", new=AsyncMock()) as grant,
        ):
            self.assertTrue(await process_join_request_update(request))
        grant.assert_not_awaited()

    async def test_accepted_chat_member_update_passes_exact_telegram_ids(self):
        update = SimpleNamespace(
            new_chat_member=SimpleNamespace(
                user=User.model_construct(id=101, is_bot=False, first_name="Test"),
                status="member",
            ),
            chat=Chat.model_construct(id=-100500, type="supergroup"),
        )
        with patch("handlers.mandatory_join.record_chat_membership", new=AsyncMock(return_value=True)) as grant:
            self.assertTrue(await process_chat_member_update(update))
        grant.assert_awaited_once_with(101, -100500)

    async def test_pending_and_removed_statuses_do_not_grant_membership(self):
        for status in ("left", "kicked", "restricted"):
            update = SimpleNamespace(
                new_chat_member=SimpleNamespace(
                    user=User.model_construct(id=101, is_bot=False, first_name="Test"),
                    status=status,
                    is_member=False,
                ),
                chat=Chat.model_construct(id=-100500, type="supergroup"),
            )
            with patch("handlers.mandatory_join.record_chat_membership", new=AsyncMock()) as grant:
                self.assertFalse(await process_chat_member_update(update))
            grant.assert_not_awaited()
        update.new_chat_member.status = "restricted"
        update.new_chat_member.is_member = True
        with patch("handlers.mandatory_join.record_chat_membership", new=AsyncMock(return_value=True)) as grant:
            self.assertTrue(await process_chat_member_update(update))
        grant.assert_awaited_once_with(101, -100500)

    async def test_membership_record_grants_only_for_enabled_configured_chat(self):
        settings = SimpleNamespace(
            id=1,
            mandatory_join_enabled=True,
            join_request_chat_id=-100500,
            join_request_link="https://t.me/+sample",
        )
        session = _Session(settings)
        with patch.object(community_access, "get_sessionmaker", return_value=lambda: session):
            self.assertTrue(await community_access.record_chat_membership(101, -100500))
            self.assertEqual(len(session.execute_calls), 1)
            self.assertFalse(await community_access.record_chat_membership(101, -100501))
            settings.mandatory_join_enabled = False
            self.assertFalse(await community_access.record_chat_membership(101, -100500))
            self.assertEqual(len(session.execute_calls), 1)

    async def test_admin_settings_persist_target_link_and_toggle(self):
        settings = BotSettings(id=1, mandatory_join_enabled=False)
        session = _Session(settings)
        with patch.object(community_access, "get_sessionmaker", return_value=lambda: session):
            await community_access.update_join_settings(
                admin_id=7, chat_id=-100500
            )
            await community_access.update_join_settings(
                admin_id=7, invite_link="https://t.me/+sample"
            )
            await community_access.update_join_settings(admin_id=7, enabled=True)
            self.assertTrue(settings.mandatory_join_enabled)
            self.assertEqual(settings.join_request_chat_id, -100500)
            self.assertEqual(settings.join_request_link, "https://t.me/+sample")
            await community_access.update_join_settings(admin_id=7, enabled=False)
            self.assertFalse(settings.mandatory_join_enabled)

    async def test_bot_join_request_cannot_receive_user_grant(self):
        request = self._request(is_bot=True)
        with patch("handlers.mandatory_join.record_chat_membership", new=AsyncMock()) as grant:
            self.assertFalse(await process_join_request_update(request))
        grant.assert_not_awaited()
