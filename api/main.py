"""FastAPI application for the Para Gifts Telegram WebApp."""

import logging
import os
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Receive, Scope, Send

from api.auth import verify_init_data
from config import get_bot_token, load_project_env
from api.schemas import (
    CatalogGift,
    CatalogResponse,
    GiftHistoryPage,
    GiftHistoryRecord,
    GiftInventoryRecord,
    GiftInventoryResponse,
    GiftInventorySellResponse,
    GiftSendRequest,
    GiftSendResponse,
    MeResponse,
)
from api.service import (
    ActiveParaRequired,
    GiftIdempotencyConflict,
    GiftAlreadySold,
    GiftItemUnavailable,
    GiftInventoryUnavailable,
    InsufficientGiftBalance,
    get_gift_catalog,
    get_gift_history,
    get_gift_inventory,
    get_gift_inventory_item,
    get_profile,
    send_gift,
    sell_gift_inventory_item,
)
from database.engine import get_sessionmaker
from database.engine import close_engine

logger = logging.getLogger(__name__)
MAX_REQUEST_BODY_BYTES = 16 * 1024
load_project_env()


class RequestBodyLimitMiddleware:
    """Reject oversized API POST bodies before parsing or buffering them."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                await self._respond(send, 400, "Invalid request.")
                return
            if declared_length < 0:
                await self._respond(send, 400, "Invalid request.")
                return
            if declared_length > MAX_REQUEST_BODY_BYTES:
                await self._respond(send, 413, "Request body is too large.")
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BODY_BYTES:
                await self._respond(send, 413, "Request body is too large.")
                return
            if not message.get("more_body", False):
                break

        replayed = False

        async def receive_buffered() -> dict:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {
                "type": "http.request",
                "body": bytes(body),
                "more_body": False,
            }

        await self.app(scope, receive_buffered, send)

    @staticmethod
    async def _respond(send: Send, response_status: int, detail: str) -> None:
        body = ('{"detail":"' + detail + '"}').encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": response_status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
                "more_body": False,
            }
        )


async def get_db_session() -> AsyncIterator[AsyncSession]:
    try:
        async with get_sessionmaker()() as session:
            yield session
    except (RuntimeError, SQLAlchemyError) as error:
        logger.error(
            "Para Gifts API database unavailable: error_type=%s",
            type(error).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is temporarily unavailable.",
        ) from None


async def get_authenticated_user_id(
    authorization: str | None = Header(default=None),
) -> int:
    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid Telegram WebApp authentication is required.",
        )
    scheme, separator, init_data = authorization.partition(" ")
    if scheme != "tma" or not separator or not init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid Telegram WebApp authentication is required.",
        )

    try:
        bot_token = get_bot_token()
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable.",
        ) from None
    try:
        return verify_init_data(init_data, bot_token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid Telegram WebApp authentication is required.",
        ) from None


def _configured_webapp_origin() -> list[str]:
    origin = os.getenv("PARA_GIFTS_WEBAPP_ORIGIN", "").strip()
    if not origin:
        return []
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RuntimeError("PARA_GIFTS_WEBAPP_ORIGIN must be an origin URL.")
    return [origin.rstrip("/")]


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await close_engine()

    app = FastAPI(
        title="Bunker TG Para Gifts API",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(RequestBodyLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_configured_webapp_origin(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    @app.get("/api/me", response_model=MeResponse)
    async def me(
        telegram_user_id: int = Depends(get_authenticated_user_id),
        session: AsyncSession = Depends(get_db_session),
    ) -> MeResponse:
        try:
            profile = await get_profile(session, telegram_user_id)
        except SQLAlchemyError as error:
            logger.error(
                "Para Gifts API database read failed: error_type=%s",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Profile is temporarily unavailable.",
            ) from None
        return MeResponse.model_validate(profile)

    @app.get("/api/gifts/catalog", response_model=CatalogResponse)
    async def catalog(
        telegram_user_id: int = Depends(get_authenticated_user_id),
        session: AsyncSession = Depends(get_db_session),
    ) -> CatalogResponse:
        del telegram_user_id
        try:
            items = await get_gift_catalog(session)
        except SQLAlchemyError as error:
            logger.error(
                "Para Gifts API catalog read failed: error_type=%s",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Gift catalog is temporarily unavailable.",
            ) from None
        return CatalogResponse(
            items=[
                CatalogGift(
                    id=item.id,
                    name=item.name,
                    description=item.description,
                    price_coins=item.price_coins,
                )
                for item in items
            ]
        )

    async def history_response(
        session: AsyncSession,
        telegram_user_id: int,
        *,
        received: bool,
        page: int,
        page_size: int,
    ) -> GiftHistoryPage:
        try:
            gifts, has_more = await get_gift_history(
                session,
                telegram_user_id,
                received=received,
                page=page,
                page_size=page_size,
            )
        except SQLAlchemyError as error:
            logger.error(
                "Para Gifts API history read failed: error_type=%s",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Gift history is temporarily unavailable.",
            ) from None
        return GiftHistoryPage(
            items=[
                GiftHistoryRecord(
                    id=gift.id,
                    para_id=gift.para_id,
                    gift_name=gift.gift_name_snapshot,
                    price_coins=gift.price_coins_snapshot,
                    created_at=gift.created_at,
                )
                for gift in gifts
            ],
            page=page,
            page_size=page_size,
            has_more=has_more,
        )

    @app.get("/api/gifts/received", response_model=GiftHistoryPage)
    async def received_gifts(
        page: int = Query(default=1, ge=1, le=10_000),
        page_size: int = Query(default=20, ge=1, le=100),
        telegram_user_id: int = Depends(get_authenticated_user_id),
        session: AsyncSession = Depends(get_db_session),
    ) -> GiftHistoryPage:
        return await history_response(
            session,
            telegram_user_id,
            received=True,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/gifts/sent", response_model=GiftHistoryPage)
    async def sent_gifts(
        page: int = Query(default=1, ge=1, le=10_000),
        page_size: int = Query(default=20, ge=1, le=100),
        telegram_user_id: int = Depends(get_authenticated_user_id),
        session: AsyncSession = Depends(get_db_session),
    ) -> GiftHistoryPage:
        return await history_response(
            session,
            telegram_user_id,
            received=False,
            page=page,
            page_size=page_size,
        )

    def inventory_record(item) -> GiftInventoryRecord:
        sold = item.sold_at is not None
        return GiftInventoryRecord(
            id=item.id,
            sender_id=item.sender_id,
            recipient_id=item.recipient_id,
            sender_name=item.sender_name_snapshot,
            recipient_name=item.recipient_name_snapshot,
            gift_name=item.gift_name_snapshot,
            image_emoji=item.image_emoji_snapshot,
            received_at=item.received_at,
            original_price_coins=item.original_price_coins,
            resale_price_coins=(
                item.resale_price_coins
                if sold
                else item.original_price_coins // 2
            ),
            sold_at=item.sold_at,
            status="sold" if sold else "owned",
        )

    @app.get("/api/gifts/inventory", response_model=GiftInventoryResponse)
    async def gift_inventory(
        telegram_user_id: int = Depends(get_authenticated_user_id),
        session: AsyncSession = Depends(get_db_session),
    ) -> GiftInventoryResponse:
        try:
            items = await get_gift_inventory(session, telegram_user_id)
        except SQLAlchemyError as error:
            logger.error(
                "Para Gifts API inventory read failed: error_type=%s",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Gift inventory is temporarily unavailable.",
            ) from None
        return GiftInventoryResponse(items=[inventory_record(item) for item in items])

    @app.get(
        "/api/gifts/inventory/{inventory_id}",
        response_model=GiftInventoryRecord,
    )
    async def gift_inventory_detail(
        inventory_id: int,
        telegram_user_id: int = Depends(get_authenticated_user_id),
        session: AsyncSession = Depends(get_db_session),
    ) -> GiftInventoryRecord:
        try:
            item = await get_gift_inventory_item(
                session,
                telegram_user_id,
                inventory_id,
            )
        except SQLAlchemyError as error:
            logger.error(
                "Para Gifts API inventory detail failed: error_type=%s",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Gift inventory is temporarily unavailable.",
            ) from None
        if item is None:
            raise HTTPException(status_code=404, detail="Gift copy not found.")
        return inventory_record(item)

    @app.post(
        "/api/gifts/inventory/{inventory_id}/sell",
        response_model=GiftInventorySellResponse,
    )
    async def sell_inventory_gift(
        inventory_id: int,
        telegram_user_id: int = Depends(get_authenticated_user_id),
        session: AsyncSession = Depends(get_db_session),
    ) -> GiftInventorySellResponse:
        try:
            result = await sell_gift_inventory_item(
                session,
                telegram_user_id,
                inventory_id,
            )
        except GiftInventoryUnavailable:
            raise HTTPException(status_code=404, detail="Gift copy not found.") from None
        except GiftAlreadySold:
            raise HTTPException(status_code=409, detail="This gift copy was already sold.") from None
        except SQLAlchemyError as error:
            logger.error(
                "Para Gifts API inventory sale failed: error_type=%s",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Gift sale is temporarily unavailable.",
            ) from None
        return GiftInventorySellResponse(
            item=inventory_record(result.item),
            balance_coins=result.balance_coins,
        )

    @app.post("/api/gifts/send", response_model=GiftSendResponse)
    async def send(
        payload: GiftSendRequest,
        telegram_user_id: int = Depends(get_authenticated_user_id),
        session: AsyncSession = Depends(get_db_session),
    ) -> GiftSendResponse:
        if not payload.idempotency_key.strip():
            raise HTTPException(
                status_code=422,
                detail="A non-empty idempotency key is required.",
            )
        try:
            result = await send_gift(
                session,
                telegram_user_id,
                payload.shop_item_id,
                payload.idempotency_key,
            )
        except ActiveParaRequired:
            raise HTTPException(
                status_code=409,
                detail="An active para partner is required to send a gift.",
            ) from None
        except GiftItemUnavailable:
            raise HTTPException(
                status_code=404,
                detail="The requested gift is unavailable.",
            ) from None
        except InsufficientGiftBalance:
            raise HTTPException(
                status_code=409,
                detail="There are not enough coins for this gift.",
            ) from None
        except GiftIdempotencyConflict:
            raise HTTPException(
                status_code=409,
                detail="This idempotency key was used for another gift.",
            ) from None
        except SQLAlchemyError as error:
            logger.error(
                "Para Gifts API send failed: error_type=%s",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Gift sending is temporarily unavailable.",
            ) from None
        except RuntimeError as error:
            logger.error(
                "Para Gifts API send failed: error_type=%s",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail="Gift sending is temporarily unavailable.",
            ) from None
        return GiftSendResponse(
            gift_id=result.gift_id,
            balance_coins=result.balance_coins,
            replayed=result.replayed,
            message="Sovg‘a muvaffaqiyatli yuborildi.",
        )

    return app


app = create_app()
