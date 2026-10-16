"""Group para proposals and invitee response handlers."""

import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import aliased

from database.engine import get_sessionmaker
from database.models import Para, ParaMember
from ui.keyboards import para_invitation_keyboard

router = Router(name="para")
logger = logging.getLogger(__name__)

PARA_INVITATION_TTL = timedelta(hours=24)


async def _end_active_para(telegram_user_id: int) -> str:
    now = datetime.now(timezone.utc)
    async with get_sessionmaker()() as session:
        async with session.begin():
            para_id = await session.scalar(
                select(ParaMember.para_id)
                .join(Para, Para.id == ParaMember.para_id)
                .where(
                    Para.status == "active",
                    ParaMember.ended_at.is_(None),
                    ParaMember.telegram_user_id == telegram_user_id,
                )
                .limit(1)
            )
            if para_id is None:
                return "no_active"

            para = await session.scalar(
                select(Para)
                .where(Para.id == para_id)
                .with_for_update()
            )
            if para is None or para.status != "active":
                return "no_active"

            members = (
                await session.execute(
                    select(
                        ParaMember.telegram_user_id,
                        ParaMember.partner_id,
                    )
                    .where(
                        ParaMember.para_id == para.id,
                        ParaMember.ended_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
            partner_by_user = {
                member.telegram_user_id: member.partner_id
                for member in members
            }
            expected_users = {para.proposer_id, para.invitee_id}
            if (
                set(partner_by_user) != expected_users
                or any(
                    partner_by_user.get(user_id) != partner_id
                    for user_id, partner_id in (
                        (para.proposer_id, para.invitee_id),
                        (para.invitee_id, para.proposer_id),
                    )
                )
            ):
                raise RuntimeError("Faol para a’zoliklari mos kelmaydi")

            result = await session.execute(
                update(ParaMember)
                .where(
                    ParaMember.para_id == para.id,
                    ParaMember.ended_at.is_(None),
                )
                .values(ended_at=now)
            )
            if result.rowcount != 2:
                raise RuntimeError("Para a’zoliklarini tugatib bo‘lmadi")

            para.status = "cancelled"

    return "ended"


@router.message(Command("dpara"))
async def end_para(message: Message) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Bu buyruqni Telegram guruhida ishlating.")
        return
    if message.from_user is None:
        await message.answer("Foydalanuvchini aniqlab bo‘lmadi.")
        return

    try:
        outcome = await _end_active_para(message.from_user.id)
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Para juftligini tugatishda DB xatosi: error_type=%s",
            type(error).__name__,
        )
        await message.answer(
            "Para juftligini hozir tugatib bo‘lmadi. Keyinroq qayta urinib ko‘ring."
        )
        return

    if outcome == "no_active":
        await message.answer("💔 Sizda hozir faol para yo‘q.")
        return

    await message.answer(
        "💔 Para juftligingiz yakunlandi. O‘zingizga g‘amxo‘rlik qiling. 🌹"
    )
    await message.answer("💔 Guruhdagi bir para juftligi yakunlandi.")


@router.message(Command("mypara"))
async def show_my_para(message: Message) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Bu buyruqni Telegram guruhida ishlating.")
        return
    if message.from_user is None:
        await message.answer("Foydalanuvchini aniqlab bo‘lmadi.")
        return

    partner_member = aliased(ParaMember)
    try:
        async with get_sessionmaker()() as session:
            result = await session.execute(
                select(
                    ParaMember.display_name,
                    ParaMember.username,
                    partner_member.display_name,
                    partner_member.username,
                )
                .outerjoin(
                    partner_member,
                    (partner_member.para_id == ParaMember.para_id)
                    & (partner_member.telegram_user_id == ParaMember.partner_id)
                    & partner_member.ended_at.is_(None),
                )
                .where(
                    ParaMember.telegram_user_id == message.from_user.id,
                    ParaMember.ended_at.is_(None),
                )
                .limit(1)
            )
            membership = result.one_or_none()
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Faol para ma’lumotini o‘qishda DB xatosi: error_type=%s",
            type(error).__name__,
        )
        await message.answer(
            "Para ma’lumotini hozir olib bo‘lmadi. Keyinroq qayta urinib ko‘ring."
        )
        return

    if membership is None:
        await message.answer("💔 Hozircha sizning para juftingiz yo‘q. 💫")
        return

    display_name, username, partner_name, partner_username = membership
    if partner_name is None:
        logger.error("Faol para a’zoligida juft a’zolik topilmadi")
        await message.answer(
            "Para ma’lumotlari to‘liq emas. Keyinroq qayta urinib ko‘ring."
        )
        return

    member_label = display_name
    if username:
        member_label += f" (@{username.lstrip('@')})"
    partner_label = partner_name
    if partner_username:
        partner_label += f" (@{partner_username.lstrip('@')})"

    await message.answer(
        "💞 Sizning para juftingiz:\n"
        f"💍 {member_label} ❤️ {partner_label}",
        parse_mode=None,
    )


async def _cancel_undelivered_invitation(para_id: int) -> bool:
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                para = await session.scalar(
                    select(Para)
                    .where(Para.id == para_id)
                    .with_for_update()
                )
                if para is None or para.status != "pending":
                    return True
                para.status = "cancelled"
                para.responded_at = datetime.now(timezone.utc)
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Yetkazilmagan para taklifini bekor qilishda DB xatosi: "
            "error_type=%s",
            type(error).__name__,
        )
        return False
    return True


@router.message(Command("para"))
async def create_para_invitation(message: Message) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Bu buyruqni Telegram guruhida ishlating.")
        return
    if message.from_user is None:
        await message.answer("Taklif yuboruvchini aniqlab bo‘lmadi.")
        return

    replied_message = message.reply_to_message
    if replied_message is None:
        await message.answer(
            "Taklif yuborish uchun boshqa foydalanuvchining xabariga "
            "reply qilib /para yuboring."
        )
        return

    proposer = message.from_user
    invitee = replied_message.from_user
    if invitee is None or invitee.is_bot:
        await message.answer("Bot yoki noma’lum muallifga taklif yuborib bo‘lmaydi.")
        return
    if proposer.id == invitee.id:
        await message.answer("O‘zingizga para taklifini yubora olmaysiz.")
        return

    now = datetime.now(timezone.utc)
    expires_at = now + PARA_INVITATION_TTL
    para_id: int | None = None
    outcome = "created"
    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                active_membership = await session.scalar(
                    select(ParaMember.id)
                    .join(Para, Para.id == ParaMember.para_id)
                    .where(
                        Para.status == "active",
                        ParaMember.ended_at.is_(None),
                        ParaMember.telegram_user_id.in_(
                            (proposer.id, invitee.id)
                        ),
                    )
                    .limit(1)
                )
                if active_membership is not None:
                    outcome = "already_paired"
                else:
                    pair_filter = (
                        (
                            (Para.proposer_id == proposer.id)
                            & (Para.invitee_id == invitee.id)
                        )
                        | (
                            (Para.proposer_id == invitee.id)
                            & (Para.invitee_id == proposer.id)
                        )
                    )
                    await session.execute(
                        select(Para.id)
                        .where(
                            pair_filter,
                            Para.status == "pending",
                            Para.expires_at <= now,
                        )
                        .with_for_update()
                    )
                    await session.execute(
                        Para.__table__.update()
                        .where(
                            pair_filter,
                            Para.status == "pending",
                            Para.expires_at <= now,
                        )
                        .values(status="expired", responded_at=now)
                    )
                    pending_invitation = await session.scalar(
                        select(Para.id)
                        .where(
                            pair_filter,
                            Para.status == "pending",
                            Para.expires_at > now,
                        )
                        .with_for_update()
                        .limit(1)
                    )
                    if pending_invitation is not None:
                        outcome = "pending_exists"
                    else:
                        para = Para(
                            proposer_id=proposer.id,
                            invitee_id=invitee.id,
                            proposer_display_name=proposer.full_name,
                            proposer_username=proposer.username,
                            chat_id=message.chat.id,
                            status="pending",
                            expires_at=expires_at,
                        )
                        session.add(para)
                        await session.flush()
                        para_id = para.id
    except IntegrityError as error:
        logger.info(
            "Takroriy para taklifi DB constraint tomonidan to‘xtatildi: "
            "error_type=%s",
            type(error).__name__,
        )
        await message.answer(
            "Bu foydalanuvchilar o‘rtasida allaqachon amal qilayotgan taklif bor."
        )
        return
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Para taklifini DB’da saqlashda xato: error_type=%s",
            type(error).__name__,
        )
        await message.answer(
            "Taklifni saqlab bo‘lmadi. Keyinroq qayta urinib ko‘ring."
        )
        return

    if outcome == "already_paired":
        await message.answer(
            "Siz yoki tanlangan foydalanuvchi allaqachon faol para ichida."
        )
        return
    if outcome == "pending_exists":
        await message.answer(
            "Bu foydalanuvchilar o‘rtasida allaqachon javob kutilayotgan "
            "taklif bor."
        )
        return
    if para_id is None:
        await message.answer("Taklifni yaratib bo‘lmadi. Qayta urinib ko‘ring.")
        return

    invitation_text = (
        f"💌 {proposer.full_name} sizga {message.chat.title or 'guruh'} "
        "guruhida para bo‘lishni taklif qildi.\n\n"
        "Taklif 24 soat amal qiladi."
    )
    try:
        await message.bot.send_message(
            chat_id=invitee.id,
            text=invitation_text,
            reply_markup=para_invitation_keyboard(para_id),
            parse_mode=None,
        )
    except TelegramAPIError as error:
        logger.info(
            "Para taklifini shaxsiy chatga yetkazib bo‘lmadi: error_type=%s",
            type(error).__name__,
        )
        cancelled = await _cancel_undelivered_invitation(para_id)
        if cancelled:
            await message.answer(
                "Taklifni shaxsiy xabarda yuborib bo‘lmadi. Tanlangan "
                "foydalanuvchi avval botga /start yuborishi kerak; "
                "shundan so‘ng qayta urinib ko‘ring."
            )
        else:
            await message.answer(
                "Taklif xabari yetkazilmadi va holatini bazada yangilashda "
                "xato yuz berdi. Admin bilan bog‘laning."
            )
        return

    await message.answer(
        f"💌 {invitee.full_name}ga para taklifi yuborildi."
    )


async def _remove_invitation_buttons(
    callback: CallbackQuery,
    text: str,
) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(
            text,
            reply_markup=None,
            parse_mode=None,
        )
    except TelegramAPIError as error:
        logger.info(
            "Para taklif DM xabarini yangilab bo‘lmadi: error_type=%s",
            type(error).__name__,
        )


async def _respond_to_invitation(
    callback: CallbackQuery,
    *,
    accept: bool,
) -> None:
    try:
        _, action, para_id_value = callback.data.split(":")
        para_id = int(para_id_value)
        if action not in {"accept", "reject"} or para_id <= 0:
            raise ValueError
    except (AttributeError, ValueError):
        await callback.answer("Taklif tugmasi noto‘g‘ri.", show_alert=True)
        return

    now = datetime.now(timezone.utc)
    outcome = "missing"
    para_chat_id: int | None = None
    proposer_name: str | None = None
    invitee_name = callback.from_user.full_name

    try:
        async with get_sessionmaker()() as session:
            async with session.begin():
                para = await session.scalar(
                    select(Para)
                    .where(Para.id == para_id)
                    .with_for_update()
                )
                if para is None:
                    outcome = "missing"
                elif callback.from_user.id != para.invitee_id:
                    outcome = "wrong_user"
                elif para.status != "pending":
                    outcome = "closed"
                elif para.expires_at <= now:
                    para.status = "expired"
                    para.responded_at = now
                    outcome = "expired"
                elif not accept:
                    para.status = "rejected"
                    para.responded_at = now
                    outcome = "rejected"
                else:
                    active_membership = await session.scalar(
                        select(ParaMember.id)
                        .join(Para, Para.id == ParaMember.para_id)
                        .where(
                            Para.status == "active",
                            ParaMember.ended_at.is_(None),
                            ParaMember.telegram_user_id.in_(
                                (para.proposer_id, para.invitee_id)
                            ),
                        )
                        .limit(1)
                    )
                    if active_membership is not None:
                        para.status = "cancelled"
                        para.responded_at = now
                        outcome = "already_paired"
                    else:
                        para.status = "active"
                        para.responded_at = now
                        proposer_name = (
                            para.proposer_display_name
                            or str(para.proposer_id)
                        )
                        para_chat_id = para.chat_id
                        session.add_all(
                            (
                                ParaMember(
                                    para_id=para.id,
                                    telegram_user_id=para.proposer_id,
                                    partner_id=para.invitee_id,
                                    display_name=proposer_name,
                                    username=para.proposer_username,
                                ),
                                ParaMember(
                                    para_id=para.id,
                                    telegram_user_id=para.invitee_id,
                                    partner_id=para.proposer_id,
                                    display_name=invitee_name,
                                    username=callback.from_user.username,
                                ),
                            )
                        )
                        await session.flush()
                        outcome = "accepted"
    except IntegrityError as error:
        logger.info(
            "Para qabul qilish parallel DB constraint sabab rad etildi: "
            "error_type=%s",
            type(error).__name__,
        )
        await _remove_invitation_buttons(
            callback,
            "Bu taklif parallel so‘rov sabab qabul qilinmadi. "
            "Siz yoki boshqa ishtirokchi allaqachon faol para ichida.",
        )
        await callback.answer(
            "Faol para a’zoligi yaratilmadi. Holatni tekshirib qayta urinib ko‘ring.",
            show_alert=True,
        )
        return
    except (SQLAlchemyError, RuntimeError) as error:
        logger.error(
            "Para taklifiga javobni DB’da saqlashda xato: error_type=%s",
            type(error).__name__,
        )
        await callback.answer(
            "Javobni saqlab bo‘lmadi. Keyinroq qayta urinib ko‘ring.",
            show_alert=True,
        )
        return

    if outcome == "wrong_user":
        await callback.answer(
            "Bu taklif faqat taklif qilingan foydalanuvchi uchun.",
            show_alert=True,
        )
        return
    if outcome in {"missing", "closed", "expired", "already_paired"}:
        messages = {
            "missing": "Taklif topilmadi.",
            "closed": "Bu taklifga javob berilgan yoki u bekor qilingan.",
            "expired": "Taklif muddati tugagan.",
            "already_paired": (
                "Siz yoki taklif yuborgan odam allaqachon faol para ichida."
            ),
        }
        await _remove_invitation_buttons(callback, messages[outcome])
        await callback.answer(messages[outcome], show_alert=True)
        return

    if outcome == "rejected":
        await _remove_invitation_buttons(
            callback,
            "Siz para taklifini rad etdingiz.",
        )
        await callback.answer("Taklif rad etildi.")
        return

    if outcome == "accepted" and para_chat_id is not None:
        await _remove_invitation_buttons(
            callback,
            "💕 Siz para taklifini qabul qildingiz.",
        )
        await callback.answer("Taklif qabul qilindi.")
        try:
            await callback.bot.send_message(
                chat_id=para_chat_id,
                text=(
                    f"💕 {proposer_name} 💍 {invitee_name} — endi para! ❤️"
                ),
                parse_mode=None,
            )
        except TelegramAPIError as error:
            logger.error(
                "Para tuzilgani haqidagi guruh xabarini yuborib bo‘lmadi: "
                "error_type=%s",
                type(error).__name__,
            )


@router.callback_query(F.data.startswith("para:accept:"))
async def accept_para_invitation(callback: CallbackQuery) -> None:
    await _respond_to_invitation(callback, accept=True)


@router.callback_query(F.data.startswith("para:reject:"))
async def reject_para_invitation(callback: CallbackQuery) -> None:
    await _respond_to_invitation(callback, accept=False)
