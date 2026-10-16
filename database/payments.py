"""Telegram Stars coin-package payment persistence and validation."""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from database.engine import get_sessionmaker
from database.models import (
    CoinPackage,
    PaymentOrder,
    PlayerWallet,
    WalletTransaction,
)
from database.wallet import WalletStorageError, get_or_create_wallet

logger = logging.getLogger(__name__)

STARS_CURRENCY = "XTR"


class PaymentError(Exception):
    """Base class for payment business and persistence errors."""


class PaymentRejected(PaymentError):
    """Raised when an invoice update is not valid for its order."""


class PaymentStorageError(PaymentError):
    """Raised when payment state cannot be persisted."""


@dataclass(frozen=True)
class CoinPackageOption:
    id: int
    name: str
    description: str | None
    coins: int
    price_stars: int


@dataclass(frozen=True)
class PaymentInvoice:
    order_id: int
    invoice_payload: str
    package_name: str
    package_description: str | None
    coins: int
    price_stars: int


@dataclass(frozen=True)
class CheckoutValidation:
    ok: bool
    error_message: str | None = None


@dataclass(frozen=True)
class PaymentSettlement:
    coins: int
    balance_coins: int
    replayed: bool


def _validate_user_id(telegram_user_id: int) -> None:
    if type(telegram_user_id) is not int or telegram_user_id <= 0:
        raise PaymentRejected("Foydalanuvchi aniqlanmadi.")


def _log_storage_error(error: SQLAlchemyError) -> None:
    logger.error(
        "Stars payment database operation failed: error_type=%s",
        type(error).__name__,
    )


async def get_active_coin_packages() -> list[CoinPackageOption]:
    try:
        async with get_sessionmaker()() as session:
            packages = await session.scalars(
                select(CoinPackage)
                .where(CoinPackage.is_active.is_(True))
                .order_by(CoinPackage.price_stars, CoinPackage.id)
            )
            return [
                CoinPackageOption(
                    id=package.id,
                    name=package.name,
                    description=package.description,
                    coins=package.coins,
                    price_stars=package.price_stars,
                )
                for package in packages.all()
            ]
    except SQLAlchemyError as error:
        _log_storage_error(error)
        raise PaymentStorageError("Coin paketlarini olib bo‘lmadi.") from None


async def create_payment_order(
    telegram_user_id: int,
    package_id: int,
) -> PaymentInvoice:
    _validate_user_id(telegram_user_id)
    if type(package_id) is not int or package_id <= 0:
        raise PaymentRejected("Coin paketi topilmadi.")

    try:
        await get_or_create_wallet(telegram_user_id)
        async with get_sessionmaker()() as session:
            async with session.begin():
                package = await session.scalar(
                    select(CoinPackage)
                    .where(
                        CoinPackage.id == package_id,
                        CoinPackage.is_active.is_(True),
                    )
                    .with_for_update()
                )
                if package is None or package.coins <= 0 or package.price_stars <= 0:
                    raise PaymentRejected(
                        "Coin paketi hozir sotuvda mavjud emas."
                    )

                payload = secrets.token_urlsafe(32)
                order = PaymentOrder(
                    telegram_user_id=telegram_user_id,
                    coin_package_id=package.id,
                    invoice_payload=payload,
                    price_stars=package.price_stars,
                    coins=package.coins,
                    status="pending",
                )
                session.add(order)
                await session.flush()
                return PaymentInvoice(
                    order_id=order.id,
                    invoice_payload=payload,
                    package_name=package.name,
                    package_description=package.description,
                    coins=package.coins,
                    price_stars=package.price_stars,
                )
    except PaymentRejected:
        raise
    except WalletStorageError as error:
        _log_storage_error(error)
        raise PaymentStorageError("To‘lov hamyonini tayyorlab bo‘lmadi.") from None
    except SQLAlchemyError as error:
        _log_storage_error(error)
        raise PaymentStorageError("To‘lov buyurtmasini yaratib bo‘lmadi.") from None


async def mark_invoice_failed(
    telegram_user_id: int,
    invoice_payload: str,
) -> None:
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                order = await session.scalar(
                    select(PaymentOrder)
                    .where(
                        PaymentOrder.invoice_payload == invoice_payload,
                        PaymentOrder.telegram_user_id == telegram_user_id,
                    )
                    .with_for_update()
                )
                if order is not None and order.status == "pending":
                    order.status = "failed"
    except SQLAlchemyError as error:
        _log_storage_error(error)


async def validate_pre_checkout(
    telegram_user_id: int,
    invoice_payload: str,
    currency: str,
    total_amount: int,
) -> CheckoutValidation:
    _validate_user_id(telegram_user_id)
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                order = await session.scalar(
                    select(PaymentOrder)
                    .where(PaymentOrder.invoice_payload == invoice_payload)
                    .with_for_update()
                )
                if order is None or order.telegram_user_id != telegram_user_id:
                    return CheckoutValidation(False, "To‘lov buyurtmasi topilmadi.")
                if order.status != "pending":
                    return CheckoutValidation(
                        False,
                        "Bu to‘lov buyurtmasi endi faol emas.",
                    )
                if currency != STARS_CURRENCY or total_amount != order.price_stars:
                    return CheckoutValidation(
                        False,
                        "To‘lov valyutasi yoki summasi buyurtmaga mos emas.",
                    )

                package = await session.scalar(
                    select(CoinPackage)
                    .where(CoinPackage.id == order.coin_package_id)
                    .with_for_update()
                )
                if (
                    package is None
                    or not package.is_active
                    or package.price_stars != order.price_stars
                ):
                    return CheckoutValidation(
                        False,
                        "Coin paketi o‘zgargan yoki sotuvdan olingan.",
                    )
                return CheckoutValidation(True)
    except SQLAlchemyError as error:
        _log_storage_error(error)
        raise PaymentStorageError(
            "To‘lovni hozir tekshirib bo‘lmadi."
        ) from None


async def settle_successful_payment(
    telegram_user_id: int,
    invoice_payload: str,
    currency: str,
    total_amount: int,
    telegram_payment_charge_id: str,
) -> PaymentSettlement:
    _validate_user_id(telegram_user_id)
    if not invoice_payload or not telegram_payment_charge_id:
        raise PaymentRejected("To‘lov ma’lumotlari to‘liq emas.")

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                order = await session.scalar(
                    select(PaymentOrder)
                    .where(PaymentOrder.invoice_payload == invoice_payload)
                    .with_for_update()
                )
                if order is None or order.telegram_user_id != telegram_user_id:
                    raise PaymentRejected("To‘lov buyurtmasi mos kelmadi.")
                if currency != STARS_CURRENCY or total_amount != order.price_stars:
                    raise PaymentRejected("Tasdiqlangan to‘lov summasi mos kelmadi.")

                if order.status == "paid":
                    if order.telegram_payment_charge_id != telegram_payment_charge_id:
                        raise PaymentRejected("Bu buyurtma boshqa to‘lov bilan yopilgan.")
                    wallet = await session.scalar(
                        select(PlayerWallet)
                        .where(
                            PlayerWallet.telegram_user_id == telegram_user_id
                        )
                        .with_for_update()
                    )
                    if wallet is None:
                        raise PaymentStorageError("To‘lov hamyoni topilmadi.")
                    return PaymentSettlement(
                        coins=order.coins,
                        balance_coins=wallet.balance_coins,
                        replayed=True,
                    )
                if order.status != "pending":
                    raise PaymentRejected("To‘lov buyurtmasi faol emas.")

                charge_order_id = await session.scalar(
                    select(PaymentOrder.id).where(
                        PaymentOrder.telegram_payment_charge_id
                        == telegram_payment_charge_id
                    )
                )
                if charge_order_id is not None:
                    raise PaymentRejected("Bu Telegram to‘lovi avval qayd etilgan.")

                wallet = await session.scalar(
                    select(PlayerWallet)
                    .where(PlayerWallet.telegram_user_id == telegram_user_id)
                    .with_for_update()
                )
                if wallet is None:
                    raise PaymentStorageError("To‘lov hamyoni topilmadi.")

                idempotency_key = f"telegram-stars:{invoice_payload}"
                existing_transaction = await session.scalar(
                    select(WalletTransaction).where(
                        WalletTransaction.idempotency_key == idempotency_key
                    )
                )
                if existing_transaction is not None:
                    raise PaymentRejected("Coin bu buyurtma uchun avval berilgan.")

                wallet.balance_coins += order.coins
                session.add(
                    WalletTransaction(
                        telegram_user_id=telegram_user_id,
                        direction="credit",
                        amount_coins=order.coins,
                        reason="payment",
                        idempotency_key=idempotency_key,
                    )
                )
                order.status = "paid"
                order.telegram_payment_charge_id = telegram_payment_charge_id
                order.paid_at = datetime.now(timezone.utc)
                await session.flush()
                return PaymentSettlement(
                    coins=order.coins,
                    balance_coins=wallet.balance_coins,
                    replayed=False,
                )
    except (PaymentRejected, PaymentStorageError):
        raise
    except SQLAlchemyError as error:
        _log_storage_error(error)
        raise PaymentStorageError(
            "To‘lovni saqlashda vaqtinchalik xato yuz berdi."
        ) from None


async def mark_payment_failed(
    invoice_payload: str,
    telegram_payment_charge_id: str,
) -> None:
    """Record a Telegram-confirmed failed payment without crediting coins."""
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                order = await session.scalar(
                    select(PaymentOrder)
                    .where(PaymentOrder.invoice_payload == invoice_payload)
                    .with_for_update()
                )
                if order is not None and order.status == "pending":
                    order.status = "failed"
                    if telegram_payment_charge_id:
                        order.telegram_payment_charge_id = (
                            telegram_payment_charge_id
                        )
    except SQLAlchemyError as error:
        _log_storage_error(error)