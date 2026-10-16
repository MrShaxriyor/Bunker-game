"""Public request and response schemas for the Para Gifts API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ActiveParaResponse(BaseModel):
    para_id: int
    partner_display_name: str


class MeResponse(BaseModel):
    telegram_user_id: int
    balance_coins: int
    active_para: ActiveParaResponse | None


class CatalogGift(BaseModel):
    id: int
    name: str
    description: str | None
    price_coins: int


class CatalogResponse(BaseModel):
    items: list[CatalogGift]


class GiftHistoryRecord(BaseModel):
    id: int
    para_id: int
    gift_name: str
    price_coins: int
    created_at: datetime


class GiftHistoryPage(BaseModel):
    items: list[GiftHistoryRecord]
    page: int
    page_size: int
    has_more: bool


class GiftSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_item_id: int = Field(strict=True, gt=0, le=2**63 - 1)
    idempotency_key: str = Field(strict=True, min_length=1, max_length=128)


class GiftSendResponse(BaseModel):
    gift_id: int
    balance_coins: int
    replayed: bool
    message: str
