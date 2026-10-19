"""Database operations for administrative Survival Shop management."""

import logging
import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database.engine import get_sessionmaker
from database.models import (
    CoinPackage,
    PaymentOrder,
    PlayerWallet,
    ShopItem,
    WalletTransaction,
)

logger = logging.getLogger(__name__)
ADMIN_PAGE_SIZE = 8
_CODE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")


class AdminShopError(Exception):
    """Base class for Shop administration errors."""


class AdminShopValidationError(AdminShopError, ValueError):
    """Raised when an administrator submits invalid catalog data."""


class AdminShopConflict(AdminShopError):
    """Raised when a catalog code is already in use."""


class AdminShopStorageError(AdminShopError):
    """Raised when an administrative database operation fails."""


@dataclass(frozen=True)
class AdminPage:
    entries: list[object]
    page: int
    page_count: int
    total: int


@dataclass(frozen=True)
class PaymentHistoryEntry:
    order_id: int
    telegram_user_id: int
    package_name: str
    status: str
    price_stars: int
    coins: int
    created_at: object
    paid_at: object | None


def _validate_text(value: str, name: str, maximum: int) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum:
        raise AdminShopValidationError(
            f"{name} 1–{maximum} belgi oralig‘ida bo‘lishi kerak."
        )
    return cleaned


def _validate_code(code: str) -> str:
    cleaned = code.strip().lower()
    if not _CODE_PATTERN.fullmatch(cleaned):
        raise AdminShopValidationError(
            "Kod faqat kichik lotin harfi, raqam, _ va - dan iborat bo‘lsin."
        )
    return cleaned


def _validate_positive_integer(value: int, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise AdminShopValidationError(f"{name} musbat butun son bo‘lishi kerak.")
    return value


def _log_error(error: SQLAlchemyError) -> None:
    logger.error(
        "Admin Shop database operation failed: error_type=%s",
        type(error).__name__,
    )


async def _page_query(model, page: int) -> AdminPage:
    if type(page) is not int or page < 0:
        raise AdminShopValidationError("Sahifa raqami noto‘g‘ri.")
    try:
        async with get_sessionmaker()() as session:
            total = await session.scalar(select(func.count()).select_from(model)) or 0
            page_count = (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE
            page = min(page, max(page_count - 1, 0))
            entries = await session.scalars(
                select(model)
                .order_by(model.id.desc())
                .limit(ADMIN_PAGE_SIZE)
                .offset(page * ADMIN_PAGE_SIZE)
            )
            return AdminPage(list(entries.all()), page, page_count, total)
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Ro‘yxatni olib bo‘lmadi.") from None


async def get_shop_item_page(page: int = 0) -> AdminPage:
    return await _page_query(ShopItem, page)


async def get_para_gift_page(page: int = 0) -> AdminPage:
    if type(page) is not int or page < 0:
        raise AdminShopValidationError("Sahifa raqami noto‘g‘ri.")
    try:
        async with get_sessionmaker()() as session:
            query = select(ShopItem).where(ShopItem.item_type == "para_gift")
            total = await session.scalar(
                select(func.count()).select_from(ShopItem).where(
                    ShopItem.item_type == "para_gift"
                )
            ) or 0
            page_count = (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE
            page = min(page, max(page_count - 1, 0))
            entries = await session.scalars(
                query.order_by(ShopItem.id.desc())
                .limit(ADMIN_PAGE_SIZE).offset(page * ADMIN_PAGE_SIZE)
            )
            return AdminPage(list(entries.all()), page, page_count, total)
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Para Gifts ro‘yxatini olib bo‘lmadi.") from None


async def get_coin_package_page(page: int = 0) -> AdminPage:
    return await _page_query(CoinPackage, page)


async def get_coin_package(package_id: int) -> CoinPackage | None:
    try:
        async with get_sessionmaker()() as session:
            return await session.get(CoinPackage, package_id)
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Coin paketi ma’lumotini olib bo‘lmadi.") from None


async def get_shop_item(item_id: int) -> ShopItem | None:
    try:
        async with get_sessionmaker()() as session:
            return await session.get(ShopItem, item_id)
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Buyum ma’lumotini olib bo‘lmadi.") from None


async def create_shop_item(
    *,
    name: str,
    code: str,
    description: str | None,
    category: str,
    price_coins: int,
) -> ShopItem:
    name = _validate_text(name, "Buyum nomi", 255)
    code = _validate_code(code)
    category = _validate_text(category, "Kategoriya", 32)
    price_coins = _validate_positive_integer(price_coins, "Coin narxi")
    if description is not None and len(description) > 4000:
        raise AdminShopValidationError("Tavsif 4000 belgidan oshmasin.")

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                item = ShopItem(
                    name=name,
                    code=code,
                    description=description or None,
                    item_type=category,
                    price_coins=price_coins,
                    is_active=True,
                )
                session.add(item)
                await session.flush()
                return item
    except IntegrityError:
        raise AdminShopConflict("Bu buyum kodi allaqachon mavjud.") from None
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Buyumni yaratib bo‘lmadi.") from None


async def update_shop_item_price(item_id: int, price_coins: int) -> ShopItem:
    price_coins = _validate_positive_integer(price_coins, "Coin narxi")
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                item = await session.scalar(
                    select(ShopItem).where(ShopItem.id == item_id).with_for_update()
                )
                if item is None:
                    raise AdminShopValidationError("Buyum topilmadi.")
                item.price_coins = price_coins
                await session.flush()
                return item
    except AdminShopValidationError:
        raise
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Buyum narxini o‘zgartirib bo‘lmadi.") from None


async def update_shop_item_name(item_id: int, name: str) -> ShopItem:
    name = _validate_text(name, "Buyum nomi", 255)
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                item = await session.scalar(
                    select(ShopItem).where(ShopItem.id == item_id).with_for_update()
                )
                if item is None:
                    raise AdminShopValidationError("Buyum topilmadi.")
                item.name = name
                await session.flush()
                return item
    except AdminShopValidationError:
        raise
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Buyum nomini o‘zgartirib bo‘lmadi.") from None


async def toggle_shop_item(item_id: int) -> ShopItem:
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                item = await session.scalar(
                    select(ShopItem).where(ShopItem.id == item_id).with_for_update()
                )
                if item is None:
                    raise AdminShopValidationError("Buyum topilmadi.")
                item.is_active = not item.is_active
                await session.flush()
                return item
    except AdminShopValidationError:
        raise
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Buyum holatini o‘zgartirib bo‘lmadi.") from None


async def create_coin_package(
    *,
    name: str,
    code: str,
    coins: int,
    price_stars: int,
) -> CoinPackage:
    name = _validate_text(name, "Paket nomi", 255)
    code = _validate_code(code)
    coins = _validate_positive_integer(coins, "Coin miqdori")
    price_stars = _validate_positive_integer(price_stars, "Stars narxi")

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                package = CoinPackage(
                    name=name,
                    code=code,
                    coins=coins,
                    price_stars=price_stars,
                    is_active=True,
                )
                session.add(package)
                await session.flush()
                return package
    except IntegrityError:
        raise AdminShopConflict("Bu paket kodi allaqachon mavjud.") from None
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Coin paketini yaratib bo‘lmadi.") from None


async def update_coin_package(
    package_id: int,
    *,
    coins: int,
    price_stars: int,
) -> CoinPackage:
    coins = _validate_positive_integer(coins, "Coin miqdori")
    price_stars = _validate_positive_integer(price_stars, "Stars narxi")
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                package = await session.scalar(
                    select(CoinPackage)
                    .where(CoinPackage.id == package_id)
                    .with_for_update()
                )
                if package is None:
                    raise AdminShopValidationError("Coin paketi topilmadi.")
                package.coins = coins
                package.price_stars = price_stars
                await session.flush()
                return package
    except AdminShopValidationError:
        raise
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Coin paketini yangilab bo‘lmadi.") from None


async def toggle_coin_package(package_id: int) -> CoinPackage:
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                package = await session.scalar(
                    select(CoinPackage)
                    .where(CoinPackage.id == package_id)
                    .with_for_update()
                )
                if package is None:
                    raise AdminShopValidationError("Coin paketi topilmadi.")
                package.is_active = not package.is_active
                await session.flush()
                return package
    except AdminShopValidationError:
        raise
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Coin paketi holatini o‘zgartirib bo‘lmadi.") from None


async def get_wallet_report(telegram_user_id: int):
    if type(telegram_user_id) is not int or telegram_user_id <= 0:
        raise AdminShopValidationError("Telegram ID musbat bo‘lishi kerak.")
    try:
        async with get_sessionmaker()() as session:
            wallet = await session.scalar(
                select(PlayerWallet).where(
                    PlayerWallet.telegram_user_id == telegram_user_id
                )
            )
            transactions = await session.scalars(
                select(WalletTransaction)
                .where(WalletTransaction.telegram_user_id == telegram_user_id)
                .order_by(
                    WalletTransaction.created_at.desc(),
                    WalletTransaction.id.desc(),
                )
                .limit(10)
            )
            return wallet, list(transactions.all())
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("Hamyon ma’lumotlarini olib bo‘lmadi.") from None


async def get_payment_history_page(page: int = 0) -> AdminPage:
    if type(page) is not int or page < 0:
        raise AdminShopValidationError("Sahifa raqami noto‘g‘ri.")
    try:
        async with get_sessionmaker()() as session:
            total = await session.scalar(select(func.count(PaymentOrder.id))) or 0
            page_count = (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE
            page = min(page, max(page_count - 1, 0))
            rows = await session.execute(
                select(
                    PaymentOrder.id,
                    PaymentOrder.telegram_user_id,
                    CoinPackage.name,
                    PaymentOrder.status,
                    PaymentOrder.price_stars,
                    PaymentOrder.coins,
                    PaymentOrder.created_at,
                    PaymentOrder.paid_at,
                )
                .join(CoinPackage, CoinPackage.id == PaymentOrder.coin_package_id)
                .order_by(PaymentOrder.created_at.desc(), PaymentOrder.id.desc())
                .limit(ADMIN_PAGE_SIZE)
                .offset(page * ADMIN_PAGE_SIZE)
            )
            entries = [
                PaymentHistoryEntry(
                    order_id=row.id,
                    telegram_user_id=row.telegram_user_id,
                    package_name=row.name,
                    status=row.status,
                    price_stars=row.price_stars,
                    coins=row.coins,
                    created_at=row.created_at,
                    paid_at=row.paid_at,
                )
                for row in rows.all()
            ]
            return AdminPage(entries, page, page_count, total)
    except SQLAlchemyError as error:
        _log_error(error)
        raise AdminShopStorageError("To‘lovlar tarixini olib bo‘lmadi.") from None
