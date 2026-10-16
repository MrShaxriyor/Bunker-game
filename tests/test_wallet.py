import asyncio
import copy
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import Select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from database import wallet
from database.models import PlayerWallet, WalletTransaction


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _WalletState:
    def __init__(self):
        self.wallets = {}
        self.transactions = {}
        self.transaction_rows = []
        self.next_wallet_id = 1
        self.next_transaction_id = 1
        self.transaction_lock = asyncio.Lock()
        self.fail_next_flush = False


class _SessionTransaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        await self.session.state.transaction_lock.acquire()
        self.session.snapshot = (
            copy.deepcopy(self.session.state.wallets),
            copy.deepcopy(self.session.state.transactions),
            copy.deepcopy(self.session.state.transaction_rows),
            self.session.state.next_wallet_id,
            self.session.state.next_transaction_id,
        )
        return self

    async def __aexit__(self, exception_type, *_):
        if exception_type is not None:
            (
                self.session.state.wallets,
                self.session.state.transactions,
                self.session.state.transaction_rows,
                self.session.state.next_wallet_id,
                self.session.state.next_transaction_id,
            ) = self.session.snapshot
        self.session.state.transaction_lock.release()
        return False


class _Session:
    def __init__(self, state):
        self.state = state
        self.snapshot = None
        self.pending = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def begin(self):
        return _SessionTransaction(self)

    async def execute(self, statement):
        if getattr(getattr(statement, "table", None), "name", None) != "player_wallets":
            raise AssertionError("Unexpected wallet SQL statement")
        compiled = statement.compile(dialect=postgresql.dialect())
        telegram_user_id = compiled.params["telegram_user_id"]
        if telegram_user_id not in self.state.wallets:
            now = datetime.now(timezone.utc)
            self.state.wallets[telegram_user_id] = SimpleNamespace(
                id=self.state.next_wallet_id,
                telegram_user_id=telegram_user_id,
                balance_coins=0,
                created_at=now,
                updated_at=now,
            )
            self.state.next_wallet_id += 1

    async def scalar(self, statement):
        if not isinstance(statement, Select):
            raise AssertionError("Unexpected scalar statement")
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        params = compiled.params
        description = statement.column_descriptions[0]

        if description["entity"] is WalletTransaction:
            idempotency_key = next(
                value for value in params.values() if isinstance(value, str)
            )
            return self.state.transactions.get(idempotency_key)

        telegram_user_id = next(
            value for value in params.values() if isinstance(value, int)
        )
        if description["expr"] is PlayerWallet.balance_coins:
            wallet_row = self.state.wallets.get(telegram_user_id)
            return wallet_row.balance_coins if wallet_row is not None else None

        if "FOR UPDATE" not in sql:
            raise AssertionError("Wallet row must be selected FOR UPDATE")
        return self.state.wallets.get(telegram_user_id)

    def add(self, transaction):
        self.pending.append(transaction)

    async def flush(self):
        if self.state.fail_next_flush:
            self.state.fail_next_flush = False
            raise SQLAlchemyError("private database details")
        for transaction in self.pending:
            transaction.id = self.state.next_transaction_id
            self.state.next_transaction_id += 1
            self.state.transactions[transaction.idempotency_key] = transaction
            self.state.transaction_rows.append(transaction)
        self.pending.clear()

    async def scalars(self, statement):
        if not isinstance(statement, Select):
            raise AssertionError("Unexpected transaction history statement")
        compiled = statement.compile(dialect=postgresql.dialect())
        telegram_user_id = next(
            value
            for value in compiled.params.values()
            if isinstance(value, int)
        )
        limit = statement._limit_clause.value
        offset = statement._offset_clause.value
        rows = [
            transaction
            for transaction in reversed(self.state.transaction_rows)
            if transaction.telegram_user_id == telegram_user_id
        ]
        return _ScalarResult(rows[offset : offset + limit])


class WalletServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = _WalletState()
        self.sessionmaker_patch = patch.object(
            wallet,
            "get_sessionmaker",
            return_value=lambda: _Session(self.state),
        )
        self.sessionmaker_patch.start()
        self.addCleanup(self.sessionmaker_patch.stop)

    async def test_get_or_create_returns_existing_wallet(self):
        first = await wallet.get_or_create_wallet(101)
        first_balance = await wallet.get_balance(101)
        second = await wallet.get_or_create_wallet(101)

        self.assertEqual(first.id, second.id)
        self.assertEqual(first_balance, 0)
        self.assertEqual(len(self.state.wallets), 1)

    async def test_add_and_spend_coins_and_read_history(self):
        credit = await wallet.add_coins(
            101,
            25,
            reason="daily_bonus",
            idempotency_key="daily:101:2026-10-15",
        )
        debit = await wallet.spend_coins(
            101,
            7,
            reason="shop_purchase",
            idempotency_key="purchase:order-31",
        )
        history = await wallet.get_transaction_history(101)

        self.assertEqual((credit.balance_coins, debit.balance_coins), (25, 18))
        self.assertEqual(await wallet.get_balance(101), 18)
        self.assertEqual(len(history), 2)
        self.assertCountEqual(
            [(item.direction, item.reason, item.amount_coins) for item in history],
            [
                ("credit", "daily_bonus", 25),
                ("debit", "shop_purchase", 7),
            ],
        )

    async def test_insufficient_balance_does_not_write_transaction(self):
        await wallet.get_or_create_wallet(101)

        with self.assertRaises(wallet.InsufficientFunds) as context:
            await wallet.spend_coins(
                101,
                1,
                reason="shop_purchase",
                idempotency_key="purchase:empty-wallet",
            )

        self.assertEqual(context.exception.balance_coins, 0)
        self.assertEqual(self.state.wallets[101].balance_coins, 0)
        self.assertEqual(self.state.transaction_rows, [])

    async def test_zero_and_negative_amounts_are_rejected(self):
        for amount in (0, -1):
            with self.subTest(amount=amount):
                with self.assertRaises(wallet.InvalidWalletOperation):
                    await wallet.add_coins(
                        101,
                        amount,
                        reason="admin_adjustment",
                        idempotency_key=f"adjust:{amount}",
                    )
                with self.assertRaises(wallet.InvalidWalletOperation):
                    await wallet.spend_coins(
                        101,
                        amount,
                        reason="shop_purchase",
                        idempotency_key=f"purchase:{amount}",
                    )

    async def test_idempotency_replays_without_changing_balance_twice(self):
        first = await wallet.add_coins(
            101,
            10,
            reason="game_reward",
            idempotency_key="game:round-4:user-101",
        )
        replay = await wallet.add_coins(
            101,
            10,
            reason="game_reward",
            idempotency_key="game:round-4:user-101",
        )

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.transaction_id, replay.transaction_id)
        self.assertEqual(await wallet.get_balance(101), 10)
        self.assertEqual(len(self.state.transaction_rows), 1)

        with self.assertRaises(wallet.IdempotencyConflict):
            await wallet.add_coins(
                101,
                11,
                reason="game_reward",
                idempotency_key="game:round-4:user-101",
            )

    async def test_audit_note_is_saved_and_checked_on_replay(self):
        await wallet.add_coins(
            101,
            5,
            reason="admin_adjustment",
            idempotency_key="admin-adjust:999:101:nonce",
            note="Support refund ticket 77",
        )

        self.assertEqual(
            self.state.transactions["admin-adjust:999:101:nonce"].note,
            "Support refund ticket 77",
        )
        with self.assertRaises(wallet.IdempotencyConflict):
            await wallet.add_coins(
                101,
                5,
                reason="admin_adjustment",
                idempotency_key="admin-adjust:999:101:nonce",
                note="Different reason",
            )

    async def test_database_failure_rolls_back_balance_and_transaction(self):
        await wallet.add_coins(
            101,
            10,
            reason="admin_adjustment",
            idempotency_key="admin:initial-credit",
        )
        self.state.fail_next_flush = True

        with self.assertRaises(wallet.WalletStorageError):
            await wallet.spend_coins(
                101,
                6,
                reason="shop_purchase",
                idempotency_key="purchase:rollback-test",
            )

        self.assertEqual(self.state.wallets[101].balance_coins, 10)
        self.assertEqual(len(self.state.transaction_rows), 1)

    async def test_parallel_spending_cannot_overdraw_wallet(self):
        await wallet.add_coins(
            101,
            10,
            reason="admin_adjustment",
            idempotency_key="admin:parallel-test-credit",
        )

        outcomes = await asyncio.gather(
            wallet.spend_coins(
                101,
                7,
                reason="shop_purchase",
                idempotency_key="purchase:parallel-a",
            ),
            wallet.spend_coins(
                101,
                7,
                reason="shop_purchase",
                idempotency_key="purchase:parallel-b",
            ),
            return_exceptions=True,
        )

        self.assertEqual(sum(isinstance(item, wallet.InsufficientFunds) for item in outcomes), 1)
        self.assertEqual(sum(isinstance(item, wallet.WalletOperationResult) for item in outcomes), 1)
        self.assertEqual(await wallet.get_balance(101), 3)
        self.assertEqual(len(self.state.transaction_rows), 2)


if __name__ == "__main__":
    unittest.main()