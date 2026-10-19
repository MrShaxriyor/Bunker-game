import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, User
from sqlalchemy import Select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from database import wallet
from database.models import BotUser, PlayerWallet, WalletTransaction
from handlers.coin_transfer import (
    coin_transfer_text,
    parse_transfer_amount,
    transfer_coin_command,
)
from ui.texts import admin_wallet_text
from aiogram.filters.command import CommandObject


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Transaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        await self.session.state.lock.acquire()
        self.session.snapshot = copy.deepcopy(
            (self.session.state.wallets, self.session.state.ledger)
        )
        return self

    async def __aexit__(self, exception_type, *_):
        if exception_type is not None:
            self.session.state.wallets, self.session.state.ledger = self.session.snapshot
        self.session.state.lock.release()
        return False


class _State:
    def __init__(self):
        self.users = {10, 20}
        self.wallets = {
            10: SimpleNamespace(telegram_user_id=10, balance_coins=500),
            20: SimpleNamespace(telegram_user_id=20, balance_coins=25),
        }
        self.ledger = {}
        self.lock = asyncio.Lock()
        self.fail_flush = False
        self.queries = []


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
        return _Transaction(self)

    async def scalars(self, statement):
        if not isinstance(statement, Select):
            raise AssertionError("Expected SELECT")
        entity = statement.column_descriptions[0]["entity"]
        compiled = statement.compile(dialect=postgresql.dialect())
        self.state.queries.append((entity, str(compiled)))
        values = [
            item
            for value in compiled.params.values()
            for item in (value if isinstance(value, list) else [value])
        ]
        if entity is BotUser:
            ids = {value for value in values if type(value) is int}
            return _Rows([
                SimpleNamespace(telegram_user_id=user_id)
                for user_id in sorted(ids & self.state.users)
            ])
        if entity is PlayerWallet:
            ids = {value for value in values if type(value) is int}
            return _Rows([
                self.state.wallets[user_id]
                for user_id in sorted(ids & self.state.wallets.keys())
            ])
        if entity is WalletTransaction:
            keys = {value for value in values if isinstance(value, str)}
            return _Rows([
                row for key, row in self.state.ledger.items() if key in keys
            ])
        raise AssertionError(f"Unexpected entity: {entity}")

    def add_all(self, rows):
        self.pending.extend(rows)

    async def flush(self):
        if self.state.fail_flush:
            self.state.fail_flush = False
            raise SQLAlchemyError("test-only database failure")
        for row in self.pending:
            self.state.ledger[row.idempotency_key] = row
        self.pending.clear()


class CoinTransferServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = _State()
        self.patch_session = patch.object(
            wallet,
            "get_sessionmaker",
            return_value=lambda: _Session(self.state),
        )
        self.patch_session.start()
        self.addCleanup(self.patch_session.stop)

    async def test_transfer_debits_amount_and_fee_and_credits_exact_amount(self):
        result = await wallet.transfer_coins(
            10, 20, 100, idempotency_key="coin-transfer:-100:7"
        )
        self.assertEqual(self.state.wallets[10].balance_coins, 390)
        self.assertEqual(self.state.wallets[20].balance_coins, 125)
        self.assertEqual(result.sender_balance_coins, 390)
        self.assertEqual(result.recipient_balance_coins, 125)
        self.assertEqual(
            [entity for entity, _ in self.state.queries],
            [BotUser, PlayerWallet, WalletTransaction],
        )
        for entity, sql in self.state.queries[:2]:
            self.assertIn("FOR UPDATE", sql)
            self.assertIn("ORDER BY", sql)
        self.assertIn("bot_users", self.state.queries[0][1])
        self.assertIn("player_wallets", self.state.queries[1][1])
        self.assertEqual(len(self.state.ledger), 3)
        ledger = list(self.state.ledger.values())
        sender_transfer = next(
            row for row in ledger
            if row.reason == "coin_transfer" and row.direction == "debit"
        )
        fee = next(row for row in ledger if row.reason == "coin_transfer_fee")
        recipient_credit = next(
            row for row in ledger
            if row.reason == "coin_transfer" and row.direction == "credit"
        )
        self.assertEqual(sender_transfer.amount_coins, 100)
        self.assertEqual(fee.direction, "debit")
        self.assertEqual(fee.amount_coins, 10)
        self.assertEqual(recipient_credit.amount_coins, 100)
        self.assertEqual(
            sum(row.amount_coins for row in self.state.ledger.values() if row.direction == "debit"),
            110,
        )
        sender_rows = [
            row for row in ledger
            if row.telegram_user_id == 10
        ]
        self.assertIn(
            "−10 · coin_transfer_fee · Coin transfer fee sink",
            admin_wallet_text(10, 390, sender_rows),
        )

    async def test_reusing_idempotency_key_for_different_amount_is_rejected(self):
        key = "coin-transfer:-100:14"
        await wallet.transfer_coins(10, 20, 100, idempotency_key=key)
        with self.assertRaises(wallet.IdempotencyConflict):
            await wallet.transfer_coins(10, 20, 99, idempotency_key=key)
        self.assertEqual(self.state.wallets[10].balance_coins, 390)
        self.assertEqual(self.state.wallets[20].balance_coins, 125)
        self.assertEqual(len(self.state.ledger), 3)

    async def test_insufficient_funds_leaves_balances_and_ledger_unchanged(self):
        before = {user_id: row.balance_coins for user_id, row in self.state.wallets.items()}
        with self.assertRaises(wallet.InsufficientFunds) as context:
            await wallet.transfer_coins(
                10, 20, 491, idempotency_key="coin-transfer:-100:8"
            )
        self.assertEqual(context.exception.requested_coins, 501)
        self.assertEqual({user_id: row.balance_coins for user_id, row in self.state.wallets.items()}, before)
        self.assertEqual(self.state.ledger, {})

    async def test_invalid_amount_self_transfer_and_missing_account_are_rejected(self):
        for amount in (0, -1, 1_000_001, True):
            with self.subTest(amount=amount), self.assertRaises(wallet.InvalidWalletOperation):
                await wallet.transfer_coins(
                    10, 20, amount, idempotency_key="coin-transfer:-100:9"
                )
        with self.assertRaises(wallet.InvalidWalletOperation):
            await wallet.transfer_coins(
                10, 10, 1, idempotency_key="coin-transfer:-100:10"
            )
        with self.assertRaises(wallet.WalletAccountNotFound):
            await wallet.transfer_coins(
                10, 30, 1, idempotency_key="coin-transfer:-100:11"
            )

    async def test_database_failure_rolls_back_both_wallets_and_all_ledger_rows(self):
        before = {user_id: row.balance_coins for user_id, row in self.state.wallets.items()}
        self.state.fail_flush = True
        with self.assertRaises(wallet.WalletStorageError):
            await wallet.transfer_coins(
                10, 20, 100, idempotency_key="coin-transfer:-100:12"
            )
        self.assertEqual({user_id: row.balance_coins for user_id, row in self.state.wallets.items()}, before)
        self.assertEqual(self.state.ledger, {})

    async def test_parallel_duplicate_request_is_applied_only_once(self):
        async def perform():
            try:
                return await wallet.transfer_coins(
                    10, 20, 100, idempotency_key="coin-transfer:-100:13"
                )
            except wallet.DuplicateWalletTransfer as error:
                return error

        outcomes = await asyncio.gather(perform(), perform())
        self.assertEqual(sum(not isinstance(item, Exception) for item in outcomes), 1)
        self.assertEqual(self.state.wallets[10].balance_coins, 390)
        self.assertEqual(self.state.wallets[20].balance_coins, 125)
        self.assertEqual(len(self.state.ledger), 3)


class CoinTransferHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sender = User(id=10, is_bot=False, first_name="Ali <Admin>", username="ali_user")
        self.recipient = User(id=20, is_bot=False, first_name="Vali & Co")

    def _message(self, *, args="100", reply=True, message_id=77):
        replied = SimpleNamespace(from_user=self.recipient, sender_chat=None) if reply else None
        return SimpleNamespace(
            chat=Chat(id=-100, type="supergroup"),
            from_user=self.sender,
            message_id=message_id,
            reply_to_message=replied,
            answer=AsyncMock(),
        ), CommandObject(command="coin", args=args, mention=None)

    def test_amount_parser_rejects_invalid_and_oversized_input(self):
        self.assertEqual(parse_transfer_amount("100"), 100)
        for value in (None, "0", "-1", "+1", "1.5", "abc", "1 2", "1000001"):
            with self.subTest(value=value):
                self.assertIsNone(parse_transfer_amount(value))

    async def test_reply_command_transfers_then_posts_escaped_confirmation(self):
        message, command = self._message()
        with patch("handlers.coin_transfer.transfer_coins", new=AsyncMock()) as transfer:
            await transfer_coin_command(message, command)
        transfer.assert_awaited_once_with(
            10, 20, 100, idempotency_key="coin-transfer:-100:77"
        )
        text, = message.answer.await_args.args
        self.assertIn("https://t.me/ali_user", text)
        self.assertIn("tg://user?id=20", text)
        self.assertIn("Ali &lt;Admin&gt;", text)
        self.assertIn("Vali &amp; Co", text)
        self.assertIn("10 coin", text)
        self.assertEqual(message.answer.await_args.kwargs["parse_mode"], "HTML")
        self.assertTrue(message.answer.await_args.kwargs["link_preview_options"].is_disabled)

    async def test_reply_missing_or_non_person_target_never_calls_transfer(self):
        message, command = self._message(reply=False)
        with patch("handlers.coin_transfer.transfer_coins", new=AsyncMock()) as transfer:
            await transfer_coin_command(message, command)
        transfer.assert_not_awaited()
        self.assertIn("reply", message.answer.await_args.args[0])

        message, command = self._message()
        message.reply_to_message.from_user = User(
            id=99, is_bot=True, first_name="Bot", username="other_bot"
        )
        with patch("handlers.coin_transfer.transfer_coins", new=AsyncMock()) as transfer:
            await transfer_coin_command(message, command)
        transfer.assert_not_awaited()

    async def test_private_chat_is_rejected_before_transfer(self):
        message, command = self._message()
        message.chat = Chat(id=10, type="private")
        with patch("handlers.coin_transfer.transfer_coins", new=AsyncMock()) as transfer:
            await transfer_coin_command(message, command)
        transfer.assert_not_awaited()
        self.assertIn("faqat guruhda", message.answer.await_args.args[0])

    async def test_invalid_amount_and_self_transfer_are_rejected_without_success_notice(self):
        message, command = self._message(args="-10")
        with patch("handlers.coin_transfer.transfer_coins", new=AsyncMock()) as transfer:
            await transfer_coin_command(message, command)
        transfer.assert_not_awaited()
        self.assertIn("butun son", message.answer.await_args.args[0])

        message, command = self._message()
        message.reply_to_message.from_user = self.sender
        with patch("handlers.coin_transfer.transfer_coins", new=AsyncMock()) as transfer:
            await transfer_coin_command(message, command)
        transfer.assert_not_awaited()
        self.assertIn("O‘zingizga", message.answer.await_args.args[0])

    async def test_db_failure_does_not_send_success_confirmation(self):
        message, command = self._message()
        with patch(
            "handlers.coin_transfer.transfer_coins",
            new=AsyncMock(side_effect=wallet.WalletStorageError()),
        ):
            await transfer_coin_command(message, command)
        self.assertEqual(message.answer.await_count, 1)
        self.assertIn("bajarib bo‘lmadi", message.answer.await_args.args[0])

    async def test_insufficient_funds_explains_amount_fee_and_total(self):
        message, command = self._message(args="100")
        error = wallet.InsufficientFunds(balance_coins=109, requested_coins=110)
        with patch(
            "handlers.coin_transfer.transfer_coins",
            new=AsyncMock(side_effect=error),
        ):
            await transfer_coin_command(message, command)
        response = message.answer.await_args.args[0]
        self.assertIn("100 coin", response)
        self.assertIn("10 coin", response)
        self.assertIn("110 coin", response)
        self.assertIn("109 coin", response)

    async def test_notice_failure_does_not_retry_or_reverse_committed_transfer(self):
        message, command = self._message()
        message.answer.side_effect = TelegramBadRequest(
            method=SendMessage(chat_id=-100, text="notice"), message="blocked"
        )
        with patch("handlers.coin_transfer.transfer_coins", new=AsyncMock()) as transfer:
            await transfer_coin_command(message, command)
        transfer.assert_awaited_once()
        self.assertEqual(message.answer.await_count, 1)

    def test_link_rendering_uses_username_or_telegram_user_id(self):
        sender = User(id=1, is_bot=False, first_name="<A>", username="alice_1")
        no_username = User(id=2, is_bot=False, first_name="B & C")
        text = coin_transfer_text(sender, no_username, 5)
        self.assertIn('href="https://t.me/alice_1"', text)
        self.assertIn('href="tg://user?id=2"', text)
        self.assertIn("&lt;A&gt;", text)
        self.assertIn("B &amp; C", text)


if __name__ == "__main__":
    unittest.main()
