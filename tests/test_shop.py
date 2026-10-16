import asyncio
import copy
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import Select
from sqlalchemy.dialects import postgresql

from database import shop as shop_service
from database.models import (
    PlayerInventory,
    PlayerWallet,
    ShopItem,
    WalletTransaction,
)
from database.wallet import InsufficientFunds
from handlers import shop as shop_handler
from ui import texts


class _Rows:
    def __init__(self, rows=(), scalar=None):
        self._rows = rows
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class _ShopState:
    def __init__(self):
        self.items = {
            1: SimpleNamespace(
                id=1,
                name="Medical Kit",
                description="Restores health",
                item_type="survival",
                price_coins=12,
                is_active=True,
            ),
            2: SimpleNamespace(
                id=2,
                name="Inactive Item",
                description="Hidden item",
                item_type="utility",
                price_coins=4,
                is_active=False,
            ),
        }
        self.wallets = {}
        self.inventory = {}
        self.transactions = {}
        self.next_wallet_id = 1
        self.next_transaction_id = 1
        self.lock = asyncio.Lock()
        self.fail_next_inventory_upsert = False


class _Transaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        await self.session.state.lock.acquire()
        state = self.session.state
        self.session.snapshot = (
            copy.deepcopy(state.wallets),
            copy.deepcopy(state.inventory),
            copy.deepcopy(state.transactions),
            state.next_wallet_id,
            state.next_transaction_id,
        )
        return self

    async def __aexit__(self, exception_type, *_):
        if exception_type is not None:
            state = self.session.state
            (
                state.wallets,
                state.inventory,
                state.transactions,
                state.next_wallet_id,
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
            telegram_user_id = params["telegram_user_id"]
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
            return _Rows()

        if table_name == "player_inventory":
            if self.state.fail_next_inventory_upsert:
                self.state.fail_next_inventory_upsert = False
                from sqlalchemy.exc import SQLAlchemyError

                raise SQLAlchemyError("private database details")
            params = statement.compile(dialect=postgresql.dialect()).params
            key = (params["telegram_user_id"], params["shop_item_id"])
            quantity = self.state.inventory.get(key, 0) + 1
            self.state.inventory[key] = quantity
            return _Rows(scalar=quantity)

        if isinstance(statement, Select):
            params = statement.compile(dialect=postgresql.dialect()).params
            user_id = next(
                value
                for key, value in params.items()
                if "telegram_user_id" in key and isinstance(value, int)
            )
            rows = []
            for (inventory_user_id, inventory_item_id), quantity in (
                self.state.inventory.items()
            ):
                if inventory_user_id != user_id:
                    continue
                item = self.state.items[inventory_item_id]
                rows.append(
                    SimpleNamespace(
                        shop_item_id=inventory_item_id,
                        name=item.name,
                        description=item.description,
                        quantity=quantity,
                    )
                )
            return _Rows(rows)

        raise AssertionError("Unexpected Shop SQL statement")

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

        if description["entity"] is ShopItem:
            item_id = next(value for value in params.values() if isinstance(value, int))
            item = self.state.items.get(item_id)
            if item is not None and "is_active IS true" in sql and not item.is_active:
                return None
            return item

        telegram_user_id = next(
            value
            for key, value in params.items()
            if "telegram_user_id" in key and isinstance(value, int)
        )
        if description["expr"] is PlayerInventory.quantity:
            item_id = next(
                value
                for key, value in params.items()
                if "shop_item_id" in key and isinstance(value, int)
            )
            return self.state.inventory.get((telegram_user_id, item_id))
        if description["expr"] is PlayerWallet.balance_coins:
            row = self.state.wallets.get(telegram_user_id)
            return row.balance_coins if row is not None else None
        if "FOR UPDATE" not in sql:
            raise AssertionError("Wallet row must be selected FOR UPDATE")
        return self.state.wallets.get(telegram_user_id)

    async def scalars(self, statement):
        if not isinstance(statement, Select):
            raise AssertionError("Unexpected scalar query")
        entity = statement.column_descriptions[0]["entity"]
        if entity is ShopItem:
            items = [
                item
                for item in self.state.items.values()
                if item.is_active
            ]
            return _Rows(items)
        raise AssertionError("Unexpected Shop scalar entity")

    def add(self, row):
        self.pending.append(row)

    async def flush(self):
        for transaction in self.pending:
            transaction.id = self.state.next_transaction_id
            self.state.next_transaction_id += 1
            self.state.transactions[transaction.idempotency_key] = transaction
        self.pending.clear()


class ShopServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = _ShopState()
        self.sessionmaker_patch = patch.object(
            shop_service,
            "get_sessionmaker",
            return_value=lambda: _Session(self.state),
        )
        self.sessionmaker_patch.start()
        self.addCleanup(self.sessionmaker_patch.stop)

    def set_balance(self, telegram_user_id: int, amount: int) -> None:
        self.state.wallets[telegram_user_id] = SimpleNamespace(
            id=telegram_user_id,
            telegram_user_id=telegram_user_id,
            balance_coins=amount,
        )

    async def test_catalog_and_details_include_only_active_items(self):
        items = await shop_service.get_active_shop_items()
        active_item = await shop_service.get_active_shop_item(1)
        inactive_item = await shop_service.get_active_shop_item(2)

        self.assertEqual([item.id for item in items], [1])
        self.assertEqual(active_item.category, "survival")
        self.assertIsNone(inactive_item)

    async def test_purchase_debits_wallet_and_increments_inventory(self):
        self.set_balance(101, 30)

        result = await shop_service.purchase_shop_item(
            101,
            1,
            idempotency_key="shop:101:1:-100:25",
        )

        self.assertEqual(result.price_coins, 12)
        self.assertEqual(result.balance_coins, 18)
        self.assertEqual(result.quantity, 1)
        self.assertEqual(self.state.wallets[101].balance_coins, 18)
        self.assertEqual(self.state.inventory[(101, 1)], 1)
        transaction = self.state.transactions["shop:101:1:-100:25"]
        self.assertEqual((transaction.direction, transaction.reason), ("debit", "shop_purchase"))

    async def test_insufficient_balance_leaves_purchase_state_unchanged(self):
        self.set_balance(101, 5)

        with self.assertRaises(InsufficientFunds) as context:
            await shop_service.purchase_shop_item(
                101,
                1,
                idempotency_key="shop:101:1:-100:26",
            )

        self.assertEqual(context.exception.requested_coins, 12)
        self.assertEqual(self.state.wallets[101].balance_coins, 5)
        self.assertEqual(self.state.inventory, {})
        self.assertEqual(self.state.transactions, {})

    async def test_repeated_purchase_callback_is_idempotent(self):
        self.set_balance(101, 30)
        key = "shop:101:1:-100:27"

        first = await shop_service.purchase_shop_item(101, 1, idempotency_key=key)
        replay = await shop_service.purchase_shop_item(101, 1, idempotency_key=key)

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(self.state.wallets[101].balance_coins, 18)
        self.assertEqual(self.state.inventory[(101, 1)], 1)
        self.assertEqual(len(self.state.transactions), 1)

    async def test_parallel_duplicate_purchase_changes_state_once(self):
        self.set_balance(101, 30)
        key = "shop:101:1:-100:28"

        first, second = await asyncio.gather(
            shop_service.purchase_shop_item(101, 1, idempotency_key=key),
            shop_service.purchase_shop_item(101, 1, idempotency_key=key),
        )

        self.assertCountEqual([first.replayed, second.replayed], [False, True])
        self.assertEqual(self.state.wallets[101].balance_coins, 18)
        self.assertEqual(self.state.inventory[(101, 1)], 1)
        self.assertEqual(len(self.state.transactions), 1)

    async def test_database_error_rolls_back_wallet_ledger_and_inventory(self):
        self.set_balance(101, 30)
        self.state.fail_next_inventory_upsert = True

        with self.assertRaises(shop_service.ShopStorageError):
            await shop_service.purchase_shop_item(
                101,
                1,
                idempotency_key="shop:101:1:-100:29",
            )

        self.assertEqual(self.state.wallets[101].balance_coins, 30)
        self.assertEqual(self.state.inventory, {})
        self.assertEqual(self.state.transactions, {})

    async def test_inventory_query_returns_current_item_details(self):
        self.state.inventory[(101, 1)] = 3

        entries = await shop_service.get_player_inventory(101)

        self.assertEqual(len(entries), 1)
        self.assertEqual(
            (entries[0].name, entries[0].description, entries[0].quantity),
            ("Medical Kit", "Restores health", 3),
        )


class ShopHandlerTests(unittest.IsolatedAsyncioTestCase):
    def test_new_shop_navigation_callbacks_parse_without_changing_legacy_actions(self):
        self.assertEqual(shop_handler._parse_callback("sh:m:101"), ("m", 101, None))
        self.assertEqual(shop_handler._parse_callback("sh:s:101"), ("s", 101, None))
        self.assertEqual(shop_handler._parse_callback("sh:l:101"), ("l", 101, None))
        self.assertEqual(shop_handler._parse_callback("sh:d:101:7"), ("d", 101, 7))

    async def test_start_menu_shop_callback_edits_to_shop_home(self):
        message = SimpleNamespace()
        callback = SimpleNamespace(
            message=message,
            from_user=SimpleNamespace(id=101),
            answer=AsyncMock(),
        )
        with (
            patch.object(shop_handler, "Message", SimpleNamespace),
            patch.object(
                shop_handler,
                "_edit_shop_home",
                new=AsyncMock(),
            ) as edit_shop_home,
        ):
            await shop_handler.open_shop_from_menu(callback)

        edit_shop_home.assert_awaited_once_with(callback, 101)
        callback.answer.assert_not_awaited()

    async def test_empty_inventory_shows_shop_button(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=101),
            answer=AsyncMock(),
        )
        with patch.object(
            shop_handler.shop_service,
            "get_player_inventory",
            new=AsyncMock(return_value=[]),
        ):
            await shop_handler.show_inventory(message)

        kwargs = message.answer.await_args.kwargs
        self.assertIn("bo‘sh", message.answer.await_args.args[0])
        self.assertEqual(
            kwargs["reply_markup"].inline_keyboard[0][0].callback_data,
            "sh:m:101",
        )

    async def test_inventory_text_escapes_catalog_content(self):
        rendered = texts.inventory_text([("<Kit>", "<unsafe>", 2)])

        self.assertIn("&lt;Kit&gt;", rendered)
        self.assertIn("&lt;unsafe&gt;", rendered)
        self.assertIn("× <b>2</b>", rendered)

    async def test_shop_callback_rejects_another_users_button(self):
        callback = SimpleNamespace(
            data="sh:d:101:1",
            from_user=SimpleNamespace(id=202),
            answer=AsyncMock(),
        )
        with patch.object(
            shop_handler.shop_service,
            "get_active_shop_item",
            new=AsyncMock(),
        ) as get_item:
            await shop_handler.handle_shop_callback(callback)

        get_item.assert_not_awaited()
        callback.answer.assert_awaited_once_with(
            "Bu tugma boshqa foydalanuvchi uchun.",
            show_alert=True,
        )

    async def test_survival_menu_filters_out_para_gift_items(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=10),
            message_id=20,
            edit_text=AsyncMock(),
        )
        callback = SimpleNamespace(
            data="sh:s:101",
            from_user=SimpleNamespace(id=101),
            message=message,
            answer=AsyncMock(),
        )
        with (
            patch.object(shop_handler, "Message", SimpleNamespace),
            patch.object(
                shop_handler.shop_service,
                "get_active_shop_items",
                new=AsyncMock(return_value=[
                    SimpleNamespace(id=1, name="Kit", price_coins=10, category="survival"),
                    SimpleNamespace(id=2, name="Rose", price_coins=50, category="para_gift"),
                ]),
            ),
            patch.object(shop_handler, "get_balance", new=AsyncMock(return_value=15)),
        ):
            await shop_handler.handle_shop_callback(callback)

        markup = message.edit_text.await_args.kwargs["reply_markup"]
        product_buttons = [
            button
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data.startswith("sh:d:")
        ]
        self.assertEqual([button.callback_data for button in product_buttons], ["sh:d:101:1"])

    async def test_insufficient_balance_edits_message_with_coin_purchase_button(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=10),
            message_id=20,
            edit_text=AsyncMock(),
        )
        callback = SimpleNamespace(
            data="sh:c:101:7",
            from_user=SimpleNamespace(id=101),
            message=message,
            answer=AsyncMock(),
        )
        with (
            patch.object(shop_handler, "Message", SimpleNamespace),
            patch.object(
                shop_handler.shop_service,
                "purchase_shop_item",
                new=AsyncMock(side_effect=InsufficientFunds(100, 1000)),
            ),
        ):
            await shop_handler.handle_shop_callback(callback)

        self.assertIn("Coin yetarli emas", message.edit_text.await_args.args[0])
        markup = message.edit_text.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "pay:l:101")
        callback.answer.assert_awaited_once_with("")


if __name__ == "__main__":
    unittest.main()
