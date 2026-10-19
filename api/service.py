"""Database operations for Para Gifts WebApp endpoints."""

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Para,
    ParaGift,
    ParaGiftInventory,
    ParaMember,
    PlayerWallet,
    ShopItem,
    WalletTransaction,
)
from database.wallet import add_coins_in_session


class ActiveParaRequired(Exception):
    """The authenticated user does not have a valid active para pair."""


class GiftItemUnavailable(Exception):
    """The requested shop item is not an active para gift."""


class InsufficientGiftBalance(Exception):
    """The sender's wallet cannot cover the current gift price."""


class GiftIdempotencyConflict(Exception):
    """The idempotency key has already been used for another send."""


class GiftInventoryUnavailable(Exception):
    """The requested gift copy is not owned by the authenticated user."""


class GiftAlreadySold(Exception):
    """The requested gift copy was already sold."""


@dataclass(frozen=True)
class GiftSendResult:
    gift_id: int
    balance_coins: int
    replayed: bool


@dataclass(frozen=True)
class GiftInventorySellResult:
    item: ParaGiftInventory
    balance_coins: int


def _gift_image_emoji(name: str) -> str:
    """Use the catalog's leading emoji as a stable, self-contained artwork."""
    first_part = name.strip().split(maxsplit=1)[0] if name.strip() else ""
    if not any(unicodedata.category(character) == "So" for character in first_part):
        return "🎁"
    return first_part[:32]


async def get_profile(session: AsyncSession, telegram_user_id: int) -> dict:
    """Read the authenticated player's balance and current para partner."""
    balance = await session.scalar(
        select(PlayerWallet.balance_coins).where(
            PlayerWallet.telegram_user_id == telegram_user_id
        )
    )
    member = await session.scalar(
        select(ParaMember)
        .join(Para, Para.id == ParaMember.para_id)
        .where(
            Para.status == "active",
            ParaMember.telegram_user_id == telegram_user_id,
            ParaMember.ended_at.is_(None),
        )
        .limit(1)
    )

    active_para = None
    if member is not None:
        partner_name = await session.scalar(
            select(ParaMember.display_name).where(
                ParaMember.para_id == member.para_id,
                ParaMember.telegram_user_id == member.partner_id,
                ParaMember.partner_id == telegram_user_id,
                ParaMember.ended_at.is_(None),
            )
        )
        if partner_name is not None:
            active_para = {
                "para_id": member.para_id,
                "partner_display_name": partner_name,
            }

    return {
        "telegram_user_id": telegram_user_id,
        "balance_coins": balance or 0,
        "active_para": active_para,
    }


async def get_gift_catalog(session: AsyncSession) -> list[ShopItem]:
    """Return the active para gift catalog only."""
    items = await session.scalars(
        select(ShopItem)
        .where(
            ShopItem.is_active.is_(True),
            ShopItem.item_type == "para_gift",
        )
        .order_by(ShopItem.name, ShopItem.id)
    )
    return list(items.all())


async def get_gift_history(
    session: AsyncSession,
    telegram_user_id: int,
    *,
    received: bool,
    page: int,
    page_size: int,
) -> tuple[list[ParaGift], bool]:
    """Read one user's gift history, newest records first."""
    user_column = (
        ParaGift.recipient_id if received else ParaGift.sender_id
    )
    gifts = await session.scalars(
        select(ParaGift)
        .where(user_column == telegram_user_id)
        .order_by(ParaGift.created_at.desc(), ParaGift.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size + 1)
    )
    rows = list(gifts.all())
    return rows[:page_size], len(rows) > page_size


async def get_gift_inventory(
    session: AsyncSession,
    telegram_user_id: int,
) -> list[ParaGiftInventory]:
    items = await session.scalars(
        select(ParaGiftInventory)
        .where(ParaGiftInventory.owner_id == telegram_user_id)
        .order_by(
            ParaGiftInventory.received_at.desc(),
            ParaGiftInventory.id.desc(),
        )
    )
    return list(items.all())


async def get_gift_inventory_item(
    session: AsyncSession,
    telegram_user_id: int,
    inventory_id: int,
) -> ParaGiftInventory | None:
    return await session.scalar(
        select(ParaGiftInventory).where(
            ParaGiftInventory.id == inventory_id,
            ParaGiftInventory.owner_id == telegram_user_id,
        )
    )


async def sell_gift_inventory_item(
    session: AsyncSession,
    telegram_user_id: int,
    inventory_id: int,
) -> GiftInventorySellResult:
    """Sell one owned gift and credit its wallet in the same transaction."""
    async with session.begin():
        item = await session.scalar(
            select(ParaGiftInventory)
            .where(
                ParaGiftInventory.id == inventory_id,
                ParaGiftInventory.owner_id == telegram_user_id,
            )
            .with_for_update()
        )
        if item is None:
            raise GiftInventoryUnavailable
        if item.sold_at is not None:
            raise GiftAlreadySold

        resale_price = item.original_price_coins // 2
        item.sold_at = datetime.now(timezone.utc)
        item.resale_price_coins = resale_price
        if resale_price:
            wallet_result = await add_coins_in_session(
                session,
                telegram_user_id,
                resale_price,
                reason="gift_resale",
                idempotency_key=f"para-gift-resale:{item.id}",
                note=f"Resold Para Gift #{item.id}",
            )
            balance = wallet_result.balance_coins
        else:
            balance = await session.scalar(
                select(PlayerWallet.balance_coins).where(
                    PlayerWallet.telegram_user_id == telegram_user_id
                )
            ) or 0
        await session.flush()
        return GiftInventorySellResult(
            item=item,
            balance_coins=balance,
        )


def _wallet_transaction_key(
    telegram_user_id: int,
    idempotency_key: str,
) -> str:
    raw_key = f"para-gift:{telegram_user_id}:{idempotency_key}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _validate_replay(
    existing: ParaGift,
    telegram_user_id: int,
    shop_item_id: int,
) -> None:
    if (
        existing.sender_id != telegram_user_id
        or existing.shop_item_id != shop_item_id
    ):
        raise GiftIdempotencyConflict


async def _get_replay(
    session: AsyncSession,
    telegram_user_id: int,
    shop_item_id: int,
    idempotency_key: str,
    balance_coins: int,
) -> GiftSendResult | None:
    existing = await session.scalar(
        select(ParaGift).where(
            ParaGift.idempotency_key == idempotency_key
        )
    )
    if existing is None:
        return None
    _validate_replay(existing, telegram_user_id, shop_item_id)
    return GiftSendResult(
        gift_id=existing.id,
        balance_coins=balance_coins,
        replayed=True,
    )


async def send_gift(
    session: AsyncSession,
    telegram_user_id: int,
    shop_item_id: int,
    idempotency_key: str,
) -> GiftSendResult:
    """Debit the sender and persist ledger and gift in one transaction."""
    try:
        async with session.begin():
            await session.execute(
                postgresql_insert(PlayerWallet)
                .values(telegram_user_id=telegram_user_id)
                .on_conflict_do_nothing(
                    constraint="uq_player_wallets_telegram_user_id"
                )
            )
            wallet = await session.scalar(
                select(PlayerWallet)
                .where(
                    PlayerWallet.telegram_user_id == telegram_user_id
                )
                .with_for_update()
            )
            if wallet is None:
                raise RuntimeError("Wallet row is unavailable.")

            replay = await _get_replay(
                session,
                telegram_user_id,
                shop_item_id,
                idempotency_key,
                wallet.balance_coins,
            )
            if replay is not None:
                return replay

            para = await session.scalar(
                select(Para)
                .join(ParaMember, ParaMember.para_id == Para.id)
                .where(
                    Para.id == ParaMember.para_id,
                    Para.status == "active",
                    ParaMember.telegram_user_id == telegram_user_id,
                    ParaMember.ended_at.is_(None),
                )
                .with_for_update(of=Para)
            )
            if para is None:
                raise ActiveParaRequired

            members = list(
                (
                    await session.scalars(
                        select(ParaMember)
                        .where(
                            ParaMember.para_id == para.id,
                            ParaMember.ended_at.is_(None),
                        )
                        .order_by(ParaMember.telegram_user_id)
                        .with_for_update()
                    )
                ).all()
            )
            expected_users = {para.proposer_id, para.invitee_id}
            partner_by_user = {
                member.telegram_user_id: member.partner_id
                for member in members
            }
            if (
                set(partner_by_user) != expected_users
                or partner_by_user.get(para.proposer_id) != para.invitee_id
                or partner_by_user.get(para.invitee_id) != para.proposer_id
            ):
                raise ActiveParaRequired

            sender = next(
                (
                    member
                    for member in members
                    if member.telegram_user_id == telegram_user_id
                ),
                None,
            )
            if sender is None:
                raise ActiveParaRequired
            recipient_id = sender.partner_id
            recipient = next(
                (member for member in members if member.telegram_user_id == recipient_id),
                None,
            )
            if recipient is None:
                raise ActiveParaRequired

            item = await session.scalar(
                select(ShopItem)
                .where(ShopItem.id == shop_item_id)
                .with_for_update()
            )
            if (
                item is None
                or not item.is_active
                or item.item_type != "para_gift"
            ):
                raise GiftItemUnavailable
            if wallet.balance_coins < item.price_coins:
                raise InsufficientGiftBalance

            wallet.balance_coins -= item.price_coins
            wallet_transaction = WalletTransaction(
                telegram_user_id=telegram_user_id,
                direction="debit",
                amount_coins=item.price_coins,
                reason="para_gift",
                note=f"Para gift: {item.name}"[:255],
                idempotency_key=_wallet_transaction_key(
                    telegram_user_id,
                    idempotency_key,
                ),
            )
            gift = ParaGift(
                para_id=para.id,
                sender_id=telegram_user_id,
                recipient_id=recipient_id,
                shop_item_id=item.id,
                gift_name_snapshot=item.name,
                price_coins_snapshot=item.price_coins,
                idempotency_key=idempotency_key,
            )
            session.add_all([wallet_transaction, gift])
            await session.flush()
            session.add(
                ParaGiftInventory(
                    para_gift_id=gift.id,
                    owner_id=recipient_id,
                    sender_id=telegram_user_id,
                    recipient_id=recipient_id,
                    sender_name_snapshot=sender.display_name,
                    recipient_name_snapshot=recipient.display_name,
                    shop_item_id=item.id,
                    gift_name_snapshot=item.name,
                    image_emoji_snapshot=_gift_image_emoji(item.name),
                    original_price_coins=item.price_coins,
                    received_at=gift.created_at or datetime.now(timezone.utc),
                )
            )
            await session.flush()
            return GiftSendResult(
                gift_id=gift.id,
                balance_coins=wallet.balance_coins,
                replayed=False,
            )
    except IntegrityError:
        replay = await _get_replay(
            session,
            telegram_user_id,
            shop_item_id,
            idempotency_key,
            0,
        )
        if replay is None:
            raise
        balance = await session.scalar(
            select(PlayerWallet.balance_coins).where(
                PlayerWallet.telegram_user_id == telegram_user_id
            )
        )
        return GiftSendResult(
            gift_id=replay.gift_id,
            balance_coins=balance or 0,
            replayed=True,
        )
