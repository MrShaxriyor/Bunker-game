"""Database operations for the Survival Shop and player inventory."""

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database.engine import get_sessionmaker
from database.models import (
    PlayerInventory,
    PlayerWallet,
    ShopItem,
    WalletTransaction,
)
from database.wallet import InsufficientFunds

logger = logging.getLogger(__name__)


class ShopError(Exception):
    """Base class for expected Shop operation errors."""


class ShopItemUnavailable(ShopError):
    """Raised when an item is missing, inactive, or not purchasable."""


class ShopPurchaseConflict(ShopError):
    """Raised when an idempotency key belongs to a different operation."""


class ShopStorageError(ShopError):
    """Raised when a Shop operation cannot be persisted or read."""


@dataclass(frozen=True)
class ShopItemView:
    id: int
    name: str
    description: str | None
    category: str
    price_coins: int


@dataclass(frozen=True)
class InventoryEntry:
    item_id: int
    name: str
    description: str | None
    quantity: int


@dataclass(frozen=True)
class ShopPurchaseResult:
    item_id: int
    item_name: str
    price_coins: int
    balance_coins: int
    quantity: int
    replayed: bool


def _validate_positive_id(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} musbat butun son bo‘lishi kerak.")


def _validate_purchase_key(
    telegram_user_id: int,
    item_id: int,
    idempotency_key: str,
) -> None:
    prefix = f"shop:{telegram_user_id}:{item_id}:"
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key.startswith(prefix)
        or len(idempotency_key) > 128
    ):
        raise ValueError("Xarid idempotency kaliti noto‘g‘ri.")


def _log_storage_error(error: SQLAlchemyError) -> None:
    logger.error(
        "Shop database operation failed: error_type=%s",
        type(error).__name__,
    )


def _item_view(item: ShopItem) -> ShopItemView:
    return ShopItemView(
        id=item.id,
        name=item.name,
        description=item.description,
        category=item.item_type,
        price_coins=item.price_coins,
    )


async def get_active_shop_items() -> list[ShopItemView]:
    """Return only currently active catalog items."""
    try:
        async with get_sessionmaker()() as session:
            items = await session.scalars(
                select(ShopItem)
                .where(ShopItem.is_active.is_(True))
                .order_by(ShopItem.item_type, ShopItem.name, ShopItem.id)
            )
            return [_item_view(item) for item in items.all()]
    except SQLAlchemyError as error:
        _log_storage_error(error)
        raise ShopStorageError("Do‘kon ma’lumotlarini olib bo‘lmadi.") from None


async def get_active_shop_item(item_id: int) -> ShopItemView | None:
    """Return one active item for detail and confirmation screens."""
    _validate_positive_id(item_id, "Buyum ID’si")
    try:
        async with get_sessionmaker()() as session:
            item = await session.scalar(
                select(ShopItem).where(
                    ShopItem.id == item_id,
                    ShopItem.is_active.is_(True),
                )
            )
            return _item_view(item) if item is not None else None
    except SQLAlchemyError as error:
        _log_storage_error(error)
        raise ShopStorageError("Buyum ma’lumotlarini olib bo‘lmadi.") from None


async def get_player_inventory(
    telegram_user_id: int,
) -> list[InventoryEntry]:
    """Return inventory entries with their current catalog descriptions."""
    _validate_positive_id(telegram_user_id, "Telegram foydalanuvchi ID’si")
    try:
        async with get_sessionmaker()() as session:
            rows = await session.execute(
                select(
                    PlayerInventory.shop_item_id,
                    ShopItem.name,
                    ShopItem.description,
                    PlayerInventory.quantity,
                )
                .join(ShopItem, ShopItem.id == PlayerInventory.shop_item_id)
                .where(PlayerInventory.telegram_user_id == telegram_user_id)
                .order_by(ShopItem.name, PlayerInventory.shop_item_id)
            )
            return [
                InventoryEntry(
                    item_id=row.shop_item_id,
                    name=row.name,
                    description=row.description,
                    quantity=row.quantity,
                )
                for row in rows.all()
            ]
    except SQLAlchemyError as error:
        _log_storage_error(error)
        raise ShopStorageError("Inventar ma’lumotlarini olib bo‘lmadi.") from None


async def _get_or_create_locked_wallet(session, telegram_user_id: int):
    await session.execute(
        postgresql_insert(PlayerWallet)
        .values(telegram_user_id=telegram_user_id)
        .on_conflict_do_nothing(
            constraint="uq_player_wallets_telegram_user_id"
        )
    )
    wallet = await session.scalar(
        select(PlayerWallet)
        .where(PlayerWallet.telegram_user_id == telegram_user_id)
        .with_for_update()
    )
    if wallet is None:
        raise ShopStorageError("Hamyon ma’lumotlarini olib bo‘lmadi.")
    return wallet


def _purchase_result(
    *,
    item_id: int,
    item_name: str,
    price_coins: int,
    balance_coins: int,
    quantity: int,
    replayed: bool,
) -> ShopPurchaseResult:
    return ShopPurchaseResult(
        item_id=item_id,
        item_name=item_name,
        price_coins=price_coins,
        balance_coins=balance_coins,
        quantity=quantity,
        replayed=replayed,
    )


async def _find_replayed_purchase(
    telegram_user_id: int,
    item_id: int,
    idempotency_key: str,
) -> ShopPurchaseResult | None:
    async with get_sessionmaker()() as session:
        transaction = await session.scalar(
            select(WalletTransaction).where(
                WalletTransaction.idempotency_key == idempotency_key
            )
        )
        if transaction is None:
            return None
        if (
            transaction.telegram_user_id != telegram_user_id
            or transaction.direction != "debit"
            or transaction.reason != "shop_purchase"
        ):
            raise ShopPurchaseConflict(
                "Bu xarid identifikatori boshqa operatsiyaga tegishli."
            )

        item = await session.scalar(
            select(ShopItem).where(ShopItem.id == item_id)
        )
        wallet = await session.scalar(
            select(PlayerWallet).where(
                PlayerWallet.telegram_user_id == telegram_user_id
            )
        )
        quantity = await session.scalar(
            select(PlayerInventory.quantity).where(
                PlayerInventory.telegram_user_id == telegram_user_id,
                PlayerInventory.shop_item_id == item_id,
            )
        )
        if item is None or wallet is None or quantity is None:
            raise ShopStorageError("Saqlangan xarid ma’lumotlari to‘liq emas.")
        return _purchase_result(
            item_id=item_id,
            item_name=item.name,
            price_coins=transaction.amount_coins,
            balance_coins=wallet.balance_coins,
            quantity=quantity,
            replayed=True,
        )


async def purchase_shop_item(
    telegram_user_id: int,
    item_id: int,
    *,
    idempotency_key: str,
) -> ShopPurchaseResult:
    """Atomically debit coins, append a ledger row, and increment inventory."""
    _validate_positive_id(telegram_user_id, "Telegram foydalanuvchi ID’si")
    _validate_positive_id(item_id, "Buyum ID’si")
    _validate_purchase_key(telegram_user_id, item_id, idempotency_key)

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                wallet = await _get_or_create_locked_wallet(
                    session,
                    telegram_user_id,
                )
                existing = await session.scalar(
                    select(WalletTransaction).where(
                        WalletTransaction.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    if (
                        existing.telegram_user_id != telegram_user_id
                        or existing.direction != "debit"
                        or existing.reason != "shop_purchase"
                    ):
                        raise ShopPurchaseConflict(
                            "Bu xarid identifikatori boshqa operatsiyaga tegishli."
                        )
                    item = await session.scalar(
                        select(ShopItem).where(ShopItem.id == item_id)
                    )
                    quantity = await session.scalar(
                        select(PlayerInventory.quantity).where(
                            PlayerInventory.telegram_user_id == telegram_user_id,
                            PlayerInventory.shop_item_id == item_id,
                        )
                    )
                    if item is None or quantity is None:
                        raise ShopStorageError(
                            "Saqlangan xarid ma’lumotlari to‘liq emas."
                        )
                    return _purchase_result(
                        item_id=item_id,
                        item_name=item.name,
                        price_coins=existing.amount_coins,
                        balance_coins=wallet.balance_coins,
                        quantity=quantity,
                        replayed=True,
                    )

                item = await session.scalar(
                    select(ShopItem)
                    .where(
                        ShopItem.id == item_id,
                        ShopItem.is_active.is_(True),
                    )
                    .with_for_update()
                )
                if item is None:
                    raise ShopItemUnavailable(
                        "Bu buyum do‘konda mavjud emas yoki faol emas."
                    )
                if item.price_coins <= 0:
                    raise ShopItemUnavailable(
                        "Bu buyum uchun xarid narxi sozlanmagan."
                    )
                if wallet.balance_coins < item.price_coins:
                    raise InsufficientFunds(
                        wallet.balance_coins,
                        item.price_coins,
                    )

                wallet.balance_coins -= item.price_coins
                transaction = WalletTransaction(
                    telegram_user_id=telegram_user_id,
                    direction="debit",
                    amount_coins=item.price_coins,
                    reason="shop_purchase",
                    idempotency_key=idempotency_key,
                )
                session.add(transaction)
                await session.flush()

                inventory_result = await session.execute(
                    postgresql_insert(PlayerInventory)
                    .values(
                        telegram_user_id=telegram_user_id,
                        shop_item_id=item.id,
                        quantity=1,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            PlayerInventory.telegram_user_id,
                            PlayerInventory.shop_item_id,
                        ],
                        set_={
                            "quantity": PlayerInventory.quantity + 1,
                            "updated_at": func.now(),
                        },
                    )
                    .returning(PlayerInventory.quantity)
                )
                quantity = inventory_result.scalar_one()
                return _purchase_result(
                    item_id=item.id,
                    item_name=item.name,
                    price_coins=item.price_coins,
                    balance_coins=wallet.balance_coins,
                    quantity=quantity,
                    replayed=False,
                )
    except (InsufficientFunds, ShopItemUnavailable, ShopPurchaseConflict):
        raise
    except IntegrityError as error:
        try:
            replayed = await _find_replayed_purchase(
                telegram_user_id,
                item_id,
                idempotency_key,
            )
        except ShopPurchaseConflict:
            raise
        except SQLAlchemyError as lookup_error:
            _log_storage_error(lookup_error)
            raise ShopStorageError("Xarid holatini tekshirib bo‘lmadi.") from None
        if replayed is not None:
            return replayed
        _log_storage_error(error)
        raise ShopStorageError("Xaridni saqlab bo‘lmadi.") from None
    except SQLAlchemyError as error:
        _log_storage_error(error)
        raise ShopStorageError("Xaridni saqlab bo‘lmadi.") from None