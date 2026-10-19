import asyncio
import copy
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, Message, User
from sqlalchemy import Select
from sqlalchemy.exc import IntegrityError

from database import admin_shop as admin_shop_service
from database.models import CoinPackage, PaymentOrder, ShopItem
from database.wallet import WalletOperationResult
from handlers.admin_shop import (
    PrivateAdminAccessFilter,
    _confirm_wallet_adjustment,
)
from ui.keyboards import admin_payment_history_keyboard
from ui import texts


class _Rows:
    def __init__(self, rows=()):
        self._rows = rows

    def all(self):
        return self._rows


class _State:
    def __init__(self):
        self.items = {}
        self.packages = {}
        self.wallets = {}
        self.transactions = {}
        self.payment_orders = []
        self.next_item_id = 1
        self.next_package_id = 1
        self.lock = asyncio.Lock()


class _Transaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        await self.session.state.lock.acquire()
        state = self.session.state
        self.session.snapshot = (
            copy.deepcopy(state.items),
            copy.deepcopy(state.packages),
            copy.deepcopy(state.wallets),
            copy.deepcopy(state.transactions),
            state.next_item_id,
            state.next_package_id,
        )
        return self

    async def __aexit__(self, exception_type, *_):
        if exception_type is not None:
            state = self.session.state
            (
                state.items,
                state.packages,
                state.wallets,
                state.transactions,
                state.next_item_id,
                state.next_package_id,
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

    async def get(self, model, entity_id):
        table = self.state.items if model is ShopItem else self.state.packages
        return table.get(entity_id)

    async def scalar(self, statement):
        sql = str(statement)
        if "count(" in sql.lower():
            table = statement.get_final_froms()[0].name
            if table == "shop_items":
                return len(self.state.items)
            if table == "coin_packages":
                return len(self.state.packages)
            if table == "payment_orders":
                return len(self.state.payment_orders)
            raise AssertionError(f"Unexpected count table: {table}")
        if not isinstance(statement, Select):
            raise AssertionError("Unexpected scalar statement")
        entity = statement.column_descriptions[0]["entity"]
        if entity is ShopItem:
            table = self.state.items
        elif entity is CoinPackage:
            table = self.state.packages
        else:
            raise AssertionError(f"Unexpected entity: {entity}")
        entity_id = next(
            value
            for value in statement.compile().params.values()
            if isinstance(value, int)
        )
        return table.get(entity_id)

    async def scalars(self, statement):
        if not isinstance(statement, Select):
            raise AssertionError("Unexpected scalars statement")
        entity = statement.column_descriptions[0]["entity"]
        if entity is ShopItem:
            return _Rows(list(self.state.items.values()))
        if entity is CoinPackage:
            return _Rows(list(self.state.packages.values()))
        raise AssertionError(f"Unexpected entity: {entity}")

    async def execute(self, statement):
        if statement.column_descriptions[0]["entity"] is PaymentOrder:
            return _Rows(self.state.payment_orders)
        raise AssertionError("Unexpected execute statement")

    def add(self, row):
        self.pending.append(row)

    async def flush(self):
        for row in self.pending:
            if isinstance(row, ShopItem):
                if any(item.code == row.code for item in self.state.items.values()):
                    raise IntegrityError("duplicate code", {}, Exception())
                row.id = self.state.next_item_id
                self.state.next_item_id += 1
                self.state.items[row.id] = row
            elif isinstance(row, CoinPackage):
                if any(p.code == row.code for p in self.state.packages.values()):
                    raise IntegrityError("duplicate code", {}, Exception())
                row.id = self.state.next_package_id
                self.state.next_package_id += 1
                self.state.packages[row.id] = row
            else:
                raise AssertionError(f"Unexpected new row: {type(row)}")
        self.pending.clear()


class AdminShopServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = _State()
        self.patch_session = patch.object(
            admin_shop_service,
            "get_sessionmaker",
            return_value=lambda: _Session(self.state),
        )
        self.patch_session.start()
        self.addCleanup(self.patch_session.stop)

    async def test_shop_item_create_update_price_and_deactivate(self):
        item = await admin_shop_service.create_shop_item(
            name="Medkit",
            code="medkit",
            description="Restores health",
            category="survival",
            price_coins=15,
        )
        updated = await admin_shop_service.update_shop_item_price(item.id, 20)
        inactive = await admin_shop_service.toggle_shop_item(item.id)

        self.assertEqual(updated.price_coins, 20)
        self.assertFalse(inactive.is_active)
        self.assertEqual((await admin_shop_service.get_shop_item_page()).total, 1)

    async def test_para_gift_admin_listing_and_rename_use_existing_shop_item(self):
        item = await admin_shop_service.create_shop_item(
            name="Rose", code="rose", description=None,
            category="para_gift", price_coins=50,
        )
        page = await admin_shop_service.get_para_gift_page()
        repriced = await admin_shop_service.update_shop_item_price(item.id, 75)
        renamed = await admin_shop_service.update_shop_item_name(item.id, "Red Rose")
        inactive = await admin_shop_service.toggle_shop_item(item.id)
        self.assertEqual(page.total, 1)
        self.assertEqual(page.entries[0].item_type, "para_gift")
        self.assertEqual(repriced.price_coins, 75)
        self.assertEqual(renamed.name, "Red Rose")
        self.assertFalse(inactive.is_active)

    async def test_coin_package_create_edit_and_deactivate(self):
        package = await admin_shop_service.create_coin_package(
            name="Starter",
            code="starter",
            coins=100,
            price_stars=25,
        )
        updated = await admin_shop_service.update_coin_package(
            package.id,
            coins=150,
            price_stars=30,
        )
        inactive = await admin_shop_service.toggle_coin_package(package.id)

        self.assertEqual((updated.coins, updated.price_stars), (150, 30))
        self.assertFalse(inactive.is_active)
        self.assertEqual((await admin_shop_service.get_coin_package_page()).total, 1)

    async def test_invalid_prices_and_coin_amounts_are_rejected(self):
        with self.assertRaises(admin_shop_service.AdminShopValidationError):
            await admin_shop_service.create_shop_item(
                name="Bad",
                code="bad_item",
                description=None,
                category="utility",
                price_coins=0,
            )
        with self.assertRaises(admin_shop_service.AdminShopValidationError):
            await admin_shop_service.create_coin_package(
                name="Bad",
                code="bad_pack",
                coins=-1,
                price_stars=25,
            )
        with self.assertRaises(admin_shop_service.AdminShopValidationError):
            await admin_shop_service.create_coin_package(
                name="Bad",
                code="bad_stars",
                coins=10,
                price_stars=0,
            )

    async def test_duplicate_catalog_codes_are_rejected(self):
        await admin_shop_service.create_shop_item(
            name="Medkit",
            code="medkit",
            description=None,
            category="survival",
            price_coins=15,
        )
        with self.assertRaises(admin_shop_service.AdminShopConflict):
            await admin_shop_service.create_shop_item(
                name="Another Medkit",
                code="medkit",
                description=None,
                category="survival",
                price_coins=20,
            )

    async def test_payment_history_contains_order_amount_and_coin_snapshot(self):
        self.state.payment_orders = [
            SimpleNamespace(
                id=44,
                telegram_user_id=101,
                name="Starter",
                status="paid",
                price_stars=25,
                coins=100,
                created_at="2026-10-15",
                paid_at="2026-10-15",
            )
        ]
        result = await admin_shop_service.get_payment_history_page()

        self.assertEqual(result.total, 1)
        self.assertEqual(result.entries[0].telegram_user_id, 101)
        self.assertEqual(result.entries[0].price_stars, 25)
        self.assertEqual(result.entries[0].coins, 100)
        rendered = texts.admin_payment_history_entry(result.entries[0])
        self.assertIn("25 ⭐", rendered)
        self.assertIn("100 coin", rendered)
        self.assertNotIn(
            "paid",
            [
                button.callback_data
                for row in admin_payment_history_keyboard(0, 1).inline_keyboard
                for button in row
                if button.callback_data
            ],
        )


class AdminShopSecurityAndAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_admin_callback_is_rejected_server_side(self):
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=123),
            message=SimpleNamespace(chat=SimpleNamespace(type="private")),
            answer=AsyncMock(),
        )
        with patch("handlers.admin.get_admin_ids", return_value=frozenset({999})):
            allowed = await PrivateAdminAccessFilter()(callback)

        self.assertFalse(allowed)
        callback.answer.assert_awaited_once()
        self.assertTrue(callback.answer.await_args.kwargs["show_alert"])

    async def test_admin_message_in_group_is_rejected(self):
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=-100, type="group"),
            from_user=User(id=999, is_bot=False, first_name="Admin"),
        )
        with (
            patch("handlers.admin.get_admin_ids", return_value=frozenset({999})),
            patch.object(Message, "answer", new=AsyncMock()) as answer,
        ):
            allowed = await PrivateAdminAccessFilter()(message)

        self.assertFalse(allowed)
        answer.assert_awaited_once()

    async def test_admin_adjustment_requires_reason_and_uses_wallet_service(self):
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "adjustment_admin_id": 999,
                    "adjustment_user_id": 123,
                    "adjustment_amount": 50,
                    "adjustment_direction": "add",
                    "adjustment_reason": "Refund for support ticket 77",
                    "adjustment_key": "admin-adjust:999:123:random",
                }
            ),
            clear=AsyncMock(),
        )
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            message=SimpleNamespace(),
            answer=AsyncMock(),
        )
        with (
            patch(
                "handlers.admin_shop.add_coins",
                new=AsyncMock(
                    return_value=WalletOperationResult(1, 50, False)
                ),
            ) as add,
            patch(
                "handlers.admin_shop.get_transaction_history",
                new=AsyncMock(return_value=[]),
            ),
        ):
            await _confirm_wallet_adjustment(callback, state)

        add.assert_awaited_once_with(
            123,
            50,
            reason="admin_adjustment",
            idempotency_key="admin-adjust:999:123:random",
            note="Admin 999: Refund for support ticket 77",
        )
        state.clear.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
