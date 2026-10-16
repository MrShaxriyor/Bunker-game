import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import Select
from sqlalchemy.dialects import postgresql

from handlers import para as para_handler


class _Result:
    def __init__(self, rows=(), rowcount=0):
        self._rows = rows
        self.rowcount = rowcount

    def all(self):
        return self._rows


class _SharedParaState:
    def __init__(self, *, active=True):
        self.para_id = 71
        self.status = "active" if active else "cancelled"
        self.proposer_id = 101
        self.invitee_id = 202
        self.lock = asyncio.Lock()
        self.members = [
            SimpleNamespace(telegram_user_id=101, partner_id=202, ended_at=None),
            SimpleNamespace(telegram_user_id=202, partner_id=101, ended_at=None),
        ]


class _ParaRecord:
    def __init__(self, state):
        self._state = state
        self.id = state.para_id
        self.proposer_id = state.proposer_id
        self.invitee_id = state.invitee_id
        self.responded_at = None

    @property
    def status(self):
        return self._state.status

    @status.setter
    def status(self, value):
        self._state.status = value


class _Transaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        if self.session.locked:
            self.session.state.lock.release()
            self.session.locked = False
        return False


class _Session:
    def __init__(self, state):
        self.state = state
        self.locked = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return _Transaction(self)

    async def scalar(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        if "FOR UPDATE" in sql:
            await self.state.lock.acquire()
            self.locked = True
            return _ParaRecord(self.state)

        await asyncio.sleep(0)
        requester_is_active = any(
            member.telegram_user_id == 101 and member.ended_at is None
            for member in self.state.members
        )
        if self.state.status == "active" and requester_is_active:
            return self.state.para_id
        return None

    async def execute(self, statement):
        if isinstance(statement, Select):
            return _Result(
                [
                    SimpleNamespace(
                        telegram_user_id=member.telegram_user_id,
                        partner_id=member.partner_id,
                    )
                    for member in self.state.members
                    if member.ended_at is None
                ]
            )

        ended_at = datetime.now(timezone.utc)
        changed = 0
        for member in self.state.members:
            if member.ended_at is None:
                member.ended_at = ended_at
                changed += 1
        return _Result(rowcount=changed)


class ParaCancellationTests(unittest.IsolatedAsyncioTestCase):
    def _patch_sessionmaker(self, state):
        return patch.object(
            para_handler,
            "get_sessionmaker",
            return_value=lambda: _Session(state),
        )

    async def test_active_para_ends_both_preserved_memberships(self):
        state = _SharedParaState()
        with self._patch_sessionmaker(state):
            outcome = await para_handler._end_active_para(101)

        self.assertEqual(outcome, "ended")
        self.assertEqual(state.status, "cancelled")
        self.assertTrue(all(member.ended_at is not None for member in state.members))
        self.assertEqual(state.members[0].ended_at, state.members[1].ended_at)
        self.assertEqual(len(state.members), 2)

    async def test_user_without_active_para_gets_clear_message(self):
        state = _SharedParaState(active=False)
        message = SimpleNamespace(
            chat=SimpleNamespace(type="group"),
            from_user=SimpleNamespace(id=101),
            answer=AsyncMock(),
        )
        with self._patch_sessionmaker(state):
            await para_handler.end_para(message)

        message.answer.assert_awaited_once_with("💔 Sizda hozir faol para yo‘q.")

    async def test_repeated_cancellation_is_no_longer_active(self):
        state = _SharedParaState()
        with self._patch_sessionmaker(state):
            first = await para_handler._end_active_para(101)
            ended_at = state.members[0].ended_at
            second = await para_handler._end_active_para(101)

        self.assertEqual((first, second), ("ended", "no_active"))
        self.assertEqual(state.members[0].ended_at, ended_at)

    async def test_parallel_cancellations_only_end_para_once(self):
        state = _SharedParaState()
        with self._patch_sessionmaker(state):
            outcomes = await asyncio.gather(
                para_handler._end_active_para(101),
                para_handler._end_active_para(101),
            )

        self.assertCountEqual(outcomes, ["ended", "no_active"])
        self.assertEqual(state.status, "cancelled")
        self.assertEqual(state.members[0].ended_at, state.members[1].ended_at)


if __name__ == "__main__":
    unittest.main()