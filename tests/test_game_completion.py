import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError
from unittest.mock import AsyncMock

from database.models import Game, GamePlayer, PlayerWallet, WalletTransaction
from handlers.lobby import (
    Lobby,
    _persist_game_completion,
    finish_game_with_two_winners,
    lobbies,
    settle_reveal_round,
    start_voting,
)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Transaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        state = self.session.state
        self.snapshot = (
            copy.deepcopy(vars(state.game)),
            {key: wallet.balance_coins for key, wallet in state.wallets.items()},
            dict(state.ledger),
        )
        state.begin_count += 1
        return self

    async def __aexit__(self, error_type, *_):
        if error_type is not None:
            game_values, balances, ledger = self.snapshot
            vars(self.session.state.game).clear()
            vars(self.session.state.game).update(game_values)
            for user_id in list(self.session.state.wallets):
                if user_id in balances:
                    self.session.state.wallets[user_id].balance_coins = balances[user_id]
                else:
                    del self.session.state.wallets[user_id]
            self.session.state.ledger = ledger
        else:
            self.session.state.commit_count += 1
        return False


class _State:
    def __init__(self, status="active", fail_flush=False):
        self.game = SimpleNamespace(
            id=44, chat_id=-1007, status=status, phase="active", version=1,
            phase_started_at=None, phase_deadline_at=None, finished_at=None,
            first_winner_id=None, second_winner_id=None,
        )
        self.players = [
            SimpleNamespace(telegram_user_id=user_id) for user_id in (1, 2, 3)
        ]
        self.wallets = {}
        self.ledger = {}
        self.pending = []
        self.begin_count = 0
        self.commit_count = 0
        self.fail_flush = fail_flush


class _Session:
    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return _Transaction(self)

    async def scalar(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is Game:
            return self.state.game if self.state.game.status == "active" else None
        params = statement.compile().params
        if entity is PlayerWallet:
            user_id = next(value for value in params.values() if type(value) is int)
            return self.state.wallets.get(user_id)
        if entity is WalletTransaction:
            key = next(value for value in params.values() if isinstance(value, str))
            return self.state.ledger.get(key)
        raise AssertionError(f"Unexpected scalar model {entity}")

    async def scalars(self, statement):
        self.assert_entity = statement.column_descriptions[0]["entity"]
        if self.assert_entity is not GamePlayer:
            raise AssertionError(f"Unexpected scalars model {self.assert_entity}")
        return _Rows(self.state.players)

    async def execute(self, statement):
        user_id = statement.compile().params["telegram_user_id"]
        self.state.wallets.setdefault(
            user_id, SimpleNamespace(telegram_user_id=user_id, balance_coins=0)
        )

    def add(self, transaction):
        self.state.pending.append(transaction)

    async def flush(self):
        if self.state.fail_flush:
            raise SQLAlchemyError("simulated failure")
        for transaction in self.state.pending:
            self.state.ledger[transaction.idempotency_key] = transaction
        self.state.pending.clear()


class GameCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def _finish(self, state):
        with patch(
            "handlers.lobby.get_sessionmaker",
            return_value=lambda: _Session(state),
        ):
            return await _persist_game_completion(Lobby(chat_id=-1007, host_id=1), 1, 2)

    async def test_completion_commits_rewards_and_repeat_does_not_credit_twice(self):
        state = _State()
        rewards = await self._finish(state)
        self.assertEqual(rewards, {1: 30, 2: 30, 3: 15})
        self.assertEqual(
            {user_id: wallet.balance_coins for user_id, wallet in state.wallets.items()},
            {1: 30, 2: 30, 3: 15},
        )
        self.assertEqual(len(state.ledger), 3)
        self.assertEqual((state.game.status, state.begin_count, state.commit_count), ("completed", 1, 1))

        self.assertIsNone(await self._finish(state))
        self.assertEqual(len(state.ledger), 3)
        self.assertEqual(state.wallets[1].balance_coins, 30)

    async def test_cancelled_game_never_receives_rewards(self):
        state = _State(status="cancelled")
        self.assertIsNone(await self._finish(state))
        self.assertEqual(state.wallets, {})
        self.assertEqual(state.ledger, {})
        self.assertEqual(state.begin_count, 1)

    async def test_wallet_failure_rolls_back_game_and_all_rewards(self):
        state = _State(fail_flush=True)
        self.assertIsNone(await self._finish(state))
        self.assertEqual(state.game.status, "active")
        self.assertEqual(state.wallets, {})
        self.assertEqual(state.ledger, {})
        self.assertEqual(state.commit_count, 0)

    async def test_two_live_players_finish_without_entering_voting(self):
        for phase in ("discussion", "voting"):
            lobby = Lobby(chat_id=-1007, host_id=1, started=True, phase=phase)
            lobby.players = {1: "One", 2: "Two"}
            lobbies[-1007] = lobby
            bot = SimpleNamespace(send_message=AsyncMock())
            with patch("handlers.lobby.finish_game_with_two_winners", new=AsyncMock(return_value=True)) as finish:
                try:
                    await start_voting(-1007, bot)
                finally:
                    lobbies.pop(-1007, None)
            finish.assert_awaited_once_with(lobby, bot)
            bot.send_message.assert_not_awaited()

    async def test_reveal_settlement_finishes_when_inactivity_leaves_two(self):
        lobby = Lobby(chat_id=-1007, host_id=1, started=True, phase="reveal")
        lobby.players = {1: "One", 2: "Two", 3: "Three"}
        lobby.usernames = {1: None, 2: None, 3: None}
        snapshot = [
            (1, "One", 2, "eliminated"),
            (2, "Two", 0, "active"),
            (3, "Three", 0, "active"),
        ]
        bot = SimpleNamespace(send_message=AsyncMock())
        with (
            patch("handlers.lobby._persist_reveal_round_settlement", new=AsyncMock(return_value=(True, snapshot))),
            patch("handlers.lobby.finish_game_with_two_winners", new=AsyncMock(return_value=True)) as finish,
        ):
            await settle_reveal_round(lobby, bot, 1)
        self.assertEqual(set(lobby.players), {2, 3})
        finish.assert_awaited_once_with(lobby, bot)

    async def test_three_live_players_still_start_normal_voting(self):
        lobby = Lobby(chat_id=-1007, host_id=1, started=True, phase="discussion")
        lobby.players = {1: "One", 2: "Two", 3: "Three"}
        lobbies[-1007] = lobby
        bot = SimpleNamespace(send_message=AsyncMock())
        with (
            patch("handlers.lobby._persist_active_phase", new=AsyncMock(return_value=True)),
            patch("handlers.lobby.asyncio.create_task", side_effect=lambda coroutine: coroutine.close()),
        ):
            try:
                await start_voting(-1007, bot)
            finally:
                lobbies.pop(-1007, None)
        self.assertEqual(lobby.phase, "voting")
        self.assertEqual(bot.send_message.await_count, 4)
