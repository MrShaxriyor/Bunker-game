# Para Gifts WebApp API

This is a separate FastAPI service. It shares the bot's async SQLAlchemy
session factory and PostgreSQL database; it does not start or modify Aiogram.

Configure the existing `DATABASE_URL` (`postgresql+asyncpg://...`) and
`BOT_TOKEN` environment variables, plus `PARA_GIFTS_WEBAPP_ORIGIN` set to the
exact WebApp origin (for example, `https://gifts.example.com`). The API does
not enable cross-origin browser requests if the origin is unset. Never place
these values in source control.

Install dependencies with `pip install -r requirements.txt`, then start the
service from the repository root:

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Send Telegram's raw `Telegram.WebApp.initData` in the request header
`Authorization: tma <initData>`. The service verifies Telegram's HMAC and
`auth_date`; it does not accept a frontend-supplied Telegram user ID as
authentication.

Endpoints:

- `GET /api/me`
- `GET /api/gifts/catalog`
- `GET /api/gifts/received?page=1&page_size=20`
- `GET /api/gifts/sent?page=1&page_size=20`
- `POST /api/gifts/send` with `{"shop_item_id": 123, "idempotency_key": "..."}`

History page sizes are limited to 100 and page numbers to 10,000. The send
operation charges the authenticated sender and writes its wallet ledger and
gift record in one database transaction.
