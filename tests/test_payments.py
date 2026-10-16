import asyncio
import copy
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import Select
from sqlalchemy.dialects import postgresql

from database import payments as payment_service
from database import wallet as wallet_service
from database.models import (
    CoinPackage,
    PaymentOrder,
    PlayerWallet,
    WalletTransaction,
)
from handlers import payments as payment_handler


class _Result:
    def __init__(self, rows=()):
        self._rows = rows

    def all(self):
        return self._rows


class _PaymentState:
    def __init__(self):
        self.packages = {
            1: SimpleNamespace(
                id=1,
                code="small",
                name="Small Pack",
                description="100 bunker coins",
                coins=100,
                price_stars=25,
                is_active=True,
            ),
            2: SimpleNamespace(
                id=2,
                code="inactive",
                name="Inactive Pack",
                description=None,
                coins=200,
                price_stars=40,
                is_active=False,
            ),
        }
        self.wallets = {}
        self.orders = {}
        self.transactions = {}
        self.next_wallet_id = 1
        self.next_order_id = 1
        self.next_transaction_id = 1
        self.lock = asyncio.Lock()
        self.fail_next_flush = False


class _Transaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        await self.session.state.lock.acquire()
        state = self.session.state
        self.session.snapshot = (
            copy.deepcopy(state.wallets),
            copy.deepcopy(state.orders),
            copy.deepcopy(state.transactions),
            state.next_wallet_id,
            state.next_order_id,
            state.next_transaction_id,
        )
        return self

    async def __aexit__(self, exception_type, *_):
        if exception_type is not None:
            state = self.session.state
            (
                state.wallets,
                state.orders,
                state.transactions,
                state.next_wallet_id,
                state.next_order_id,
                state.next_transaction_id,
            ) = self.session.snapshot
        self.session.state.lock.release()
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
        return _Transaction(self)

    async def execute(self, statement):
        table_name = getattr(getattr(statement, "table", None), "name", None)
        if table_name == "player_wallets":
            params = statement.compile(dialect=postgresql.dialect()).params
            user_id = params["telegram_user_id"]
            if user_id not in self.state.wallets:
                now = datetime.now(timezone.utc)
                self.state.wallets[user_id] = SimpleNamespace(
                    id=self.state.next_wallet_id,
                    telegram_user_id=user_id,
                    balance_coins=0,
                    created_at=now,
                    updated_at=now,
                )
                self.state.next_wallet_id += 1
            return _Result()
        raise AssertionError(f"Unexpected execute statement: {table_name}")

    async def scalar(self, statement):
        if not isinstance(statement, Select):
            raise AssertionError("Unexpected scalar statement")
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        params = compiled.params
        entity = statement.column_descriptions[0]["entity"]

        if entity is PaymentOrder:
            if "WHERE payment_orders.telegram_payment_charge_id" in sql:
                charge_id = next(
                    value for value in params.values() if isinstance(value, str)
                )
                order = next(
                    (
                        order
                        for order in self.state.orders.values()
                        if order.telegram_payment_charge_id == charge_id
                    ),
                    None,
                )
                return order.id if order is not None else None
            payload = next(
                value for value in params.values() if isinstance(value, str)
            )
            return self.state.orders.get(payload)

        if entity is CoinPackage:
            package_id = next(
                value for value in params.values() if isinstance(value, int)
            )
            package = self.state.packages.get(package_id)
            if package is not None and "is_active IS true" in sql:
                return package if package.is_active else None
            return package

        if entity is WalletTransaction:
            key = next(
                value for value in params.values() if isinstance(value, str)
            )
            return self.state.transactions.get(key)

        if entity is PlayerWallet:
            user_id = next(
                value for value in params.values() if isinstance(value, int)
            )
            if "FOR UPDATE" not in sql:
                raise AssertionError("Wallet row must be selected FOR UPDATE")
            return self.state.wallets.get(user_id)

        raise AssertionError(f"Unexpected scalar entity: {entity}")

    async def scalars(self, statement):
        if not isinstance(statement, Select):
            raise AssertionError("Unexpected scalars statement")
        if statement.column_descriptions[0]["entity"] is not CoinPackage:
            raise AssertionError("Unexpected package query")
        return _Result(
            [package for package in self.state.packages.values() if package.is_active]
        )

    def add(self, row):
        self.pending.append(row)

    async def flush(self):
        if self.state.fail_next_flush:
            self.state.fail_next_flush = False
            from sqlalchemy.exc import SQLAlchemyError

            raise SQLAlchemyError("private database details")
        for row in self.pending:
            if isinstance(row, PaymentOrder):
                row.id = self.state.next_order_id
                self.state.next_order_id += 1
                self.state.orders[row.invoice_payload] = row
            elif isinstance(row, WalletTransaction):
                row.id = self.state.next_transaction_id
                self.state.next_transaction_id += 1
                self.state.transactions[row.idempotency_key] = row
            else:
                raise AssertionError(f"Unexpected pending row: {type(row)}")
        self.pending.clear()


class TelegramStarsPaymentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = _PaymentState()
        session_factory = lambda: _Session(self.state)
        self.payment_patch = patch.object(
            payment_service,
            "get_sessionmaker",
            return_value=session_factory,
        )
        self.wallet_patch = patch.object(
            wallet_service,
            "get_sessionmaker",
            return_value=session_factory,
        )
        self.payment_patch.start()
        self.wallet_patch.start()
        self.addCleanup(self.payment_patch.stop)
        self.addCleanup(self.wallet_patch.stop)

    async def create_order(self, user_id=101, package_id=1):
        return await payment_service.create_payment_order(user_id, package_id)

    async def test_only_active_packages_are_listed_and_order_snapshots_terms(self):
        packages = await payment_service.get_active_coin_packages()
        invoice = await self.create_order()
        order = self.state.orders[invoice.invoice_payload]

        self.assertEqual([package.id for package in packages], [1])
        self.assertEqual((invoice.coins, invoice.price_stars), (100, 25))
        self.assertEqual((order.coins, order.price_stars), (100, 25))
        self.assertNotIn("=", invoice.invoice_payload)
        self.assertGreaterEqual(len(invoice.invoice_payload), 40)

    async def test_inactive_package_cannot_create_order(self):
        with self.assertRaises(payment_service.PaymentRejected):
            await self.create_order(package_id=2)

    async def test_deactivated_package_cannot_pass_pre_checkout(self):
        invoice = await self.create_order()
        self.state.packages[1].is_active = False

        validation = await payment_service.validate_pre_checkout(
            101,
            invoice.invoice_payload,
            "XTR",
            25,
        )

        self.assertFalse(validation.ok)

    async def test_cancelled_order_cannot_credit_coins(self):
        invoice = await self.create_order()
        self.state.orders[invoice.invoice_payload].status = "cancelled"

        with self.assertRaises(payment_service.PaymentRejected):
            await payment_service.settle_successful_payment(
                101,
                invoice.invoice_payload,
                "XTR",
                25,
                "charge-cancelled",
            )

        self.assertEqual(self.state.wallets[101].balance_coins, 0)
        self.assertEqual(self.state.transactions, {})

    async def test_pre_checkout_requires_matching_user_payload_amount_and_xtr(self):
        invoice = await self.create_order()

        valid = await payment_service.validate_pre_checkout(
            101,
            invoice.invoice_payload,
            "XTR",
            25,
        )
        wrong_user = await payment_service.validate_pre_checkout(
            202,
            invoice.invoice_payload,
            "XTR",
            25,
        )
        wrong_amount = await payment_service.validate_pre_checkout(
            101,
            invoice.invoice_payload,
            "XTR",
            24,
        )
        wrong_currency = await payment_service.validate_pre_checkout(
            101,
            invoice.invoice_payload,
            "USD",
            25,
        )
        missing_payload = await payment_service.validate_pre_checkout(
            101,
            "unrecognized-payload",
            "XTR",
            25,
        )

        self.assertTrue(valid.ok)
        self.assertFalse(wrong_user.ok)
        self.assertFalse(wrong_amount.ok)
        self.assertFalse(wrong_currency.ok)
        self.assertFalse(missing_payload.ok)

    async def test_successful_payment_credits_snapshot_and_marks_order_paid(self):
        invoice = await self.create_order()
        result = await payment_service.settle_successful_payment(
            101,
            invoice.invoice_payload,
            "XTR",
            25,
            "charge-1",
        )
        order = self.state.orders[invoice.invoice_payload]
        transaction = self.state.transactions[
            f"telegram-stars:{invoice.invoice_payload}"
        ]

        self.assertFalse(result.replayed)
        self.assertEqual(result.coins, 100)
        self.assertEqual(result.balance_coins, 100)
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.telegram_payment_charge_id, "charge-1")
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(
            (transaction.direction, transaction.amount_coins, transaction.reason),
            ("credit", 100, "payment"),
        )

    async def test_successful_payment_rejects_wrong_owner_amount_or_currency(self):
        invoice = await self.create_order()
        with self.assertRaises(payment_service.PaymentRejected):
            await payment_service.settle_successful_payment(
                202,
                invoice.invoice_payload,
                "XTR",
                25,
                "charge-wrong-user",
            )
        with self.assertRaises(payment_service.PaymentRejected):
            await payment_service.settle_successful_payment(
                101,
                invoice.invoice_payload,
                "USD",
                25,
                "charge-wrong-currency",
            )
        with self.assertRaises(payment_service.PaymentRejected):
            await payment_service.settle_successful_payment(
                101,
                invoice.invoice_payload,
                "XTR",
                24,
                "charge-wrong-amount",
            )

        self.assertEqual(self.state.wallets[101].balance_coins, 0)
        self.assertEqual(self.state.transactions, {})
        self.assertEqual(self.state.orders[invoice.invoice_payload].status, "pending")

    async def test_duplicate_successful_payment_does_not_credit_twice(self):
        invoice = await self.create_order()
        first = await payment_service.settle_successful_payment(
            101,
            invoice.invoice_payload,
            "XTR",
            25,
            "charge-repeat",
        )
        replay = await payment_service.settle_successful_payment(
            101,
            invoice.invoice_payload,
            "XTR",
            25,
            "charge-repeat",
        )

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(self.state.wallets[101].balance_coins, 100)
        self.assertEqual(len(self.state.transactions), 1)

    async def test_charge_id_cannot_be_reused_for_another_payload(self):
        first_order = await self.create_order()
        second_order = await self.create_order()
        await payment_service.settle_successful_payment(
            101,
            first_order.invoice_payload,
            "XTR",
            25,
            "charge-global-unique",
        )

        with self.assertRaises(payment_service.PaymentRejected):
            await payment_service.settle_successful_payment(
                101,
                second_order.invoice_payload,
                "XTR",
                25,
                "charge-global-unique",
            )

        self.assertEqual(self.state.wallets[101].balance_coins, 100)
        self.assertEqual(self.state.orders[second_order.invoice_payload].status, "pending")
        self.assertEqual(len(self.state.transactions), 1)

    async def test_parallel_successful_updates_credit_once(self):
        invoice = await self.create_order()

        results = await asyncio.gather(
            payment_service.settle_successful_payment(
                101,
                invoice.invoice_payload,
                "XTR",
                25,
                "charge-parallel",
            ),
            payment_service.settle_successful_payment(
                101,
                invoice.invoice_payload,
                "XTR",
                25,
                "charge-parallel",
            ),
        )

        self.assertCountEqual([result.replayed for result in results], [False, True])
        self.assertEqual(self.state.wallets[101].balance_coins, 100)
        self.assertEqual(len(self.state.transactions), 1)

    async def test_database_error_rolls_back_order_wallet_and_ledger(self):
        invoice = await self.create_order()
        self.state.fail_next_flush = True

        with self.assertRaises(payment_service.PaymentStorageError):
            await payment_service.settle_successful_payment(
                101,
                invoice.invoice_payload,
                "XTR",
                25,
                "charge-rollback",
            )

        order = self.state.orders[invoice.invoice_payload]
        self.assertEqual(self.state.wallets[101].balance_coins, 0)
        self.assertEqual(order.status, "pending")
        self.assertIsNone(order.telegram_payment_charge_id)
        self.assertEqual(self.state.transactions, {})

        retried = await payment_service.settle_successful_payment(
            101,
            invoice.invoice_payload,
            "XTR",
            25,
            "charge-rollback",
        )
        self.assertFalse(retried.replayed)
        self.assertEqual(self.state.wallets[101].balance_coins, 100)


class PaymentHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        payment_handler._recent_invoice_clicks.pop((101, 1), None)
        self.state = _PaymentState()
        session_factory = lambda: _Session(self.state)
        self.payment_patch = patch.object(
            payment_service,
            "get_sessionmaker",
            return_value=session_factory,
        )
        self.wallet_patch = patch.object(
            wallet_service,
            "get_sessionmaker",
            return_value=session_factory,
        )
        self.payment_patch.start()
        self.wallet_patch.start()
        self.addCleanup(self.payment_patch.stop)
        self.addCleanup(self.wallet_patch.stop)

    async def test_invoice_is_created_after_order_and_uses_xtr(self):
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=101),
            bot=SimpleNamespace(send_invoice=AsyncMock()),
            answer=AsyncMock(),
        )

        async def verify_order_before_send(**kwargs):
            self.assertIn(kwargs["payload"], self.state.orders)
            self.assertEqual(kwargs["currency"], "XTR")
            self.assertEqual(kwargs["provider_token"], "")
            self.assertEqual(kwargs["prices"][0].amount, 25)

        callback.bot.send_invoice.side_effect = verify_order_before_send
        await payment_handler._send_package_invoice(callback, 101, 1)

        callback.bot.send_invoice.assert_awaited_once()
        callback.answer.assert_awaited_once()

    async def test_repeated_package_click_does_not_send_duplicate_invoice(self):
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=101),
            bot=SimpleNamespace(send_invoice=AsyncMock()),
            answer=AsyncMock(),
        )

        await payment_handler._send_package_invoice(callback, 101, 1)
        await payment_handler._send_package_invoice(callback, 101, 1)

        callback.bot.send_invoice.assert_awaited_once()
        self.assertEqual(len(self.state.orders), 1)
        self.assertEqual(callback.answer.await_count, 2)
        self.assertTrue(callback.answer.await_args.kwargs.get("show_alert", False))

    async def test_start_menu_coin_button_uses_existing_package_callback(self):
        callback = SimpleNamespace(from_user=SimpleNamespace(id=101))
        with patch.object(
            payment_handler,
            "_show_packages_from_callback",
            new=AsyncMock(),
        ) as show_packages:
            await payment_handler.open_coin_packages_from_menu(callback)

        show_packages.assert_awaited_once_with(callback, 101)

    async def test_pre_checkout_handler_answers_false_when_validation_fails(self):
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=101),
            invoice_payload="bad-payload",
            currency="XTR",
            total_amount=1,
            answer=AsyncMock(),
        )
        with patch.object(
            payment_handler.payment_service,
            "validate_pre_checkout",
            new=AsyncMock(
                return_value=payment_service.CheckoutValidation(
                    False,
                    "To‘lov summasi mos emas.",
                )
            ),
        ):
            await payment_handler.validate_pre_checkout(query)

        query.answer.assert_awaited_once_with(
            ok=False,
            error_message="To‘lov summasi mos emas.",
        )

    async def test_successful_payment_retries_transient_database_error(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=101),
            successful_payment=SimpleNamespace(
                invoice_payload="payload-token",
                currency="XTR",
                total_amount=25,
                telegram_payment_charge_id="charge-1",
            ),
            answer=AsyncMock(),
        )
        with (
            patch.object(
                payment_handler.payment_service,
                "settle_successful_payment",
                new=AsyncMock(
                    side_effect=[
                        payment_service.PaymentStorageError("transient"),
                        payment_service.PaymentSettlement(100, 100, False),
                    ]
                ),
            ) as settle,
            patch.object(payment_handler.asyncio, "sleep", new=AsyncMock()),
        ):
            await payment_handler.process_successful_payment(message)

        self.assertEqual(settle.await_count, 2)
        self.assertIn("100", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
