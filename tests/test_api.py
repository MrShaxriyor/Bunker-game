import asyncio
import hashlib
import hmac
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import httpx
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from api.auth import verify_init_data
from api.main import create_app, get_db_session
from database.models import (
    ParaGift,
    WalletTransaction,
)

BOT_TOKEN = "123456:test-api-token"
NOW = int(time.time())


def make_init_data(user_id: int, *, auth_date: int = NOW) -> str:
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAE-test-query",
        "user": json.dumps(
            {"id": user_id, "first_name": "Test"},
            separators=(",", ":"),
        ),
    }
    check_string = "\n".join(
        f"{key}={fields[key]}" for key in sorted(fields)
    )
    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()
    fields["hash"] = hmac.new(
        secret_key,
        check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(fields)


class _FakeResult:
    def __init__(self, rows=()):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeState:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.wallet = SimpleNamespace(telegram_user_id=101, balance_coins=100)
        self.para = SimpleNamespace(
            id=71,
            proposer_id=101,
            invitee_id=202,
            status="active",
        )
        self.members = [
            SimpleNamespace(
                para_id=71,
                telegram_user_id=101,
                partner_id=202,
                display_name="Sender",
                ended_at=None,
            ),
            SimpleNamespace(
                para_id=71,
                telegram_user_id=202,
                partner_id=101,
                display_name="Partner",
                ended_at=None,
            ),
        ]
        self.item = SimpleNamespace(
            id=9,
            name="Rose",
            description="A gift",
            item_type="para_gift",
            price_coins=25,
            is_active=True,
        )
        self.catalog_items = []
        self.gifts = []
        self.transactions = []
        self.fail_flush = False


class _FakeTransaction:
    def __init__(self, session):
        self.session = session
        self.snapshot = None

    async def __aenter__(self):
        state = self.session.state
        await state.lock.acquire()
        self.snapshot = (
            state.wallet.balance_coins,
            list(state.gifts),
            list(state.transactions),
        )
        return self

    async def __aexit__(self, exc_type, *_):
        if exc_type is not None:
            (
                self.session.state.wallet.balance_coins,
                self.session.state.gifts,
                self.session.state.transactions,
            ) = self.snapshot
        self.session.state.lock.release()
        return False


class _FakeSession:
    def __init__(self, state):
        self.state = state
        self.pending = []

    def begin(self):
        return _FakeTransaction(self)

    async def execute(self, _statement):
        del _statement
        return _FakeResult()

    async def scalar(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        if "FROM player_wallets" in sql:
            if "SELECT player_wallets.balance_coins" in sql:
                return self.state.wallet.balance_coins
            return self.state.wallet
        if "FROM para_gifts" in sql:
            key = sql.split("para_gifts.idempotency_key =", 1)
            if len(key) > 1:
                return next(
                    (
                        gift
                        for gift in self.state.gifts
                        if gift.idempotency_key
                        == statement.compile(
                            dialect=postgresql.dialect()
                        ).params.get("idempotency_key_1")
                    ),
                    None,
                )
            return None
        if "FROM paras JOIN para_members" in sql:
            return self.state.para if self.state.para.status == "active" else None
        if "FROM para_members JOIN paras" in sql:
            return next(
                (
                    member
                    for member in self.state.members
                    if member.telegram_user_id == 101
                    and member.ended_at is None
                ),
                None,
            )
        if "SELECT para_members.display_name" in sql:
            return "Partner"
        if "FROM shop_items" in sql:
            return self.state.item
        return None

    async def scalars(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        if "FROM shop_items" in sql:
            rows = [self.state.item, *self.state.catalog_items]
            if "shop_items.is_active IS true" in sql:
                rows = [item for item in rows if item.is_active]
            if "shop_items.item_type =" in sql:
                item_type = statement.compile(
                    dialect=postgresql.dialect()
                ).params.get("item_type_1")
                rows = [item for item in rows if item.item_type == item_type]
            return _FakeResult(rows)
        if "FROM para_members" in sql:
            return _FakeResult(
                [
                    member
                    for member in self.state.members
                    if member.para_id == 71 and member.ended_at is None
                ]
            )
        if "FROM para_gifts" in sql:
            parameters = statement.compile(dialect=postgresql.dialect()).params
            received = "para_gifts.recipient_id =" in sql
            user_id = parameters.get(
                "recipient_id_1" if received else "sender_id_1"
            )
            rows = [
                gift
                for gift in self.state.gifts
                if (
                    gift.recipient_id == user_id
                    if received
                    else gift.sender_id == user_id
                )
            ]
            rows.sort(key=lambda gift: (gift.created_at, gift.id), reverse=True)
            return _FakeResult(rows)
        return _FakeResult()

    def add_all(self, items):
        self.pending.extend(items)

    async def flush(self):
        if self.state.fail_flush:
            raise SQLAlchemyError("sensitive database diagnostic")
        for item in self.pending:
            if isinstance(item, ParaGift):
                item.id = 501 + len(self.state.gifts)
                if item.created_at is None:
                    item.created_at = datetime.now(timezone.utc)
                self.state.gifts.append(item)
            elif isinstance(item, WalletTransaction):
                item.id = 801 + len(self.state.transactions)
                self.state.transactions.append(item)
        self.pending.clear()


class TelegramInitDataTests(unittest.TestCase):
    def test_verifies_official_signature_and_returns_user_id(self):
        self.assertEqual(
            verify_init_data(make_init_data(101), BOT_TOKEN, now=NOW),
            101,
        )

    def test_rejects_bad_signature_and_expired_auth_date(self):
        invalid = make_init_data(101).replace("Test", "Bad")
        expired = make_init_data(101, auth_date=NOW - 86_401)
        with self.assertRaises(ValueError):
            verify_init_data(invalid, BOT_TOKEN, now=NOW)
        with self.assertRaises(ValueError):
            verify_init_data(expired, BOT_TOKEN, now=NOW)


class ParaGiftsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "BOT_TOKEN": BOT_TOKEN,
                "PARA_GIFTS_WEBAPP_ORIGIN": "https://gifts.example.com",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.state = _FakeState()
        self.app = create_app()

        async def override_session():
            yield _FakeSession(self.state)

        self.app.dependency_overrides[get_db_session] = override_session
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(self.client.aclose)

    def auth_headers(self, user_id=101, *, auth_date=NOW):
        return {
            "Authorization": f"tma {make_init_data(user_id, auth_date=auth_date)}"
        }

    async def test_docs_and_openapi_expose_registered_api_routes(self):
        docs_response = await self.client.get("/docs")
        schema_response = await self.client.get("/openapi.json")

        self.assertEqual(docs_response.status_code, 200)
        self.assertIn("text/html", docs_response.headers["content-type"])
        self.assertEqual(schema_response.status_code, 200)
        paths = schema_response.json()["paths"]
        self.assertIn("/api/me", paths)
        self.assertIn("/api/gifts/catalog", paths)
        self.assertEqual(self.app.root_path, "")

    async def test_invalid_stale_and_client_supplied_ids_are_rejected(self):
        invalid_headers = self.auth_headers()
        invalid_headers["Authorization"] += "x"
        response = await self.client.get(
            "/api/me",
            headers=invalid_headers,
        )
        self.assertEqual(response.status_code, 401)

        response = await self.client.get(
            "/api/me",
            headers=self.auth_headers(auth_date=NOW - 86_401),
        )
        self.assertEqual(response.status_code, 401)

        response = await self.client.post(
            "/api/gifts/send",
            headers=self.auth_headers(),
            json={
                "shop_item_id": 9,
                "idempotency_key": "key",
                "telegram_user_id": 202,
            },
        )
        self.assertEqual(response.status_code, 422)

    async def test_profile_returns_only_authenticated_user_and_active_partner(self):
        response = await self.client.get(
            "/api/me",
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "telegram_user_id": 101,
                "balance_coins": 100,
                "active_para": {
                    "para_id": 71,
                    "partner_display_name": "Partner",
                },
            },
        )

    async def test_catalog_only_returns_active_para_gift_items(self):
        self.state.catalog_items.extend(
            [
                SimpleNamespace(
                    id=10,
                    item_type="survival",
                    is_active=True,
                ),
                SimpleNamespace(
                    id=11,
                    item_type="para_gift",
                    is_active=False,
                ),
            ]
        )
        response = await self.client.get(
            "/api/gifts/catalog",
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["items"]], [9])

        self.state.item.is_active = False
        response = await self.client.get(
            "/api/gifts/catalog",
            headers=self.auth_headers(),
        )
        self.assertEqual(response.json()["items"], [])

    async def test_sent_and_received_history_is_user_scoped_and_newest_first(self):
        older = ParaGift(
            id=1,
            para_id=71,
            sender_id=101,
            recipient_id=202,
            shop_item_id=9,
            gift_name_snapshot="Old",
            price_coins_snapshot=10,
            idempotency_key="old",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        newer = ParaGift(
            id=2,
            para_id=71,
            sender_id=101,
            recipient_id=202,
            shop_item_id=9,
            gift_name_snapshot="New",
            price_coins_snapshot=20,
            idempotency_key="new",
            created_at=datetime.now(timezone.utc),
        )
        unrelated = ParaGift(
            id=3,
            para_id=71,
            sender_id=303,
            recipient_id=202,
            shop_item_id=9,
            gift_name_snapshot="Other",
            price_coins_snapshot=30,
            idempotency_key="other",
            created_at=datetime.now(timezone.utc),
        )
        self.state.gifts.extend([older, newer, unrelated])

        response = await self.client.get(
            "/api/gifts/sent?page_size=1",
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([g["id"] for g in response.json()["items"]], [2])
        self.assertTrue(response.json()["has_more"])

        response = await self.client.get(
            "/api/gifts/received",
            headers=self.auth_headers(202),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [g["id"] for g in response.json()["items"]],
            [3, 2, 1],
        )

        response = await self.client.get(
            "/api/gifts/sent",
            headers=self.auth_headers(303),
        )
        self.assertEqual(
            [g["id"] for g in response.json()["items"]],
            [3],
        )

    async def test_send_rejects_missing_para_inactive_item_and_low_balance(self):
        self.state.para.status = "cancelled"
        response = await self.client.post(
            "/api/gifts/send",
            headers=self.auth_headers(),
            json={"shop_item_id": 9, "idempotency_key": "no-para"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state.wallet.balance_coins, 100)

        self.state.para.status = "active"
        self.state.item.is_active = False
        response = await self.client.post(
            "/api/gifts/send",
            headers=self.auth_headers(),
            json={"shop_item_id": 9, "idempotency_key": "inactive"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.state.wallet.balance_coins, 100)

        self.state.item.is_active = True
        self.state.wallet.balance_coins = 10
        response = await self.client.post(
            "/api/gifts/send",
            headers=self.auth_headers(),
            json={"shop_item_id": 9, "idempotency_key": "low-balance"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state.gifts, [])

    async def test_successful_send_snapshots_item_and_charges_wallet_once(self):
        response = await self.client.post(
            "/api/gifts/send",
            headers=self.auth_headers(),
            json={"shop_item_id": 9, "idempotency_key": "success"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["gift_id"], 501)
        self.assertEqual(payload["balance_coins"], 75)
        self.assertFalse(payload["replayed"])
        self.assertEqual(self.state.wallet.balance_coins, 75)
        self.assertEqual(len(self.state.transactions), 1)
        self.assertEqual(self.state.transactions[0].reason, "para_gift")
        self.assertEqual(len(self.state.gifts), 1)
        self.assertEqual(self.state.gifts[0].recipient_id, 202)
        self.assertEqual(self.state.gifts[0].gift_name_snapshot, "Rose")

    async def test_repeated_key_replays_and_conflicting_reuse_is_rejected(self):
        body = {"shop_item_id": 9, "idempotency_key": "repeat"}
        first = await self.client.post(
            "/api/gifts/send",
            headers=self.auth_headers(),
            json=body,
        )
        second = await self.client.post(
            "/api/gifts/send",
            headers=self.auth_headers(),
            json=body,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(self.state.wallet.balance_coins, 75)
        self.assertEqual(len(self.state.gifts), 1)

        conflict = await self.client.post(
            "/api/gifts/send",
            headers=self.auth_headers(),
            json={"shop_item_id": 10, "idempotency_key": "repeat"},
        )
        self.assertEqual(conflict.status_code, 409)

    async def test_parallel_repeated_key_charges_only_once(self):
        body = {"shop_item_id": 9, "idempotency_key": "parallel"}
        responses = await asyncio.gather(
            *(
                self.client.post(
                    "/api/gifts/send",
                    headers=self.auth_headers(),
                    json=body,
                )
                for _ in range(2)
            )
        )
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertCountEqual(
            [response.json()["replayed"] for response in responses],
            [False, True],
        )
        self.assertEqual(self.state.wallet.balance_coins, 75)
        self.assertEqual(len(self.state.transactions), 1)
        self.assertEqual(len(self.state.gifts), 1)

    async def test_database_failure_rolls_back_and_hides_diagnostics(self):
        self.state.fail_flush = True
        response = await self.client.post(
            "/api/gifts/send",
            headers=self.auth_headers(),
            json={"shop_item_id": 9, "idempotency_key": "db-failure"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("sensitive", response.text)
        self.assertNotIn(BOT_TOKEN, response.text)
        self.assertEqual(self.state.wallet.balance_coins, 100)
        self.assertEqual(self.state.gifts, [])
        self.assertEqual(self.state.transactions, [])

    async def test_cors_is_restricted_and_request_size_is_limited(self):
        response = await self.client.options(
            "/api/me",
            headers={
                "Origin": "https://gifts.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "https://gifts.example.com",
        )
        response = await self.client.options(
            "/api/me",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertNotIn(
            "access-control-allow-origin",
            response.headers,
        )
        response = await self.client.post(
            "/api/gifts/send",
            headers={
                **self.auth_headers(),
                "Content-Length": str(16 * 1024 + 1),
            },
            content=b"",
        )
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
