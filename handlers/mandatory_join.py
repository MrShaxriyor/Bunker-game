"""Mandatory membership gate for incoming user actions."""

import logging

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.exc import SQLAlchemyError

from database.community_access import (
    get_join_settings,
    get_user_access,
    record_chat_membership,
    register_user,
)
from handlers.admin import get_admin_ids

logger = logging.getLogger(__name__)


async def process_join_request_update(request) -> bool:
    """Identify a pending request without granting membership before approval."""
    if request.from_user.is_bot:
        return False
    settings = await get_join_settings()
    return settings.enabled and settings.chat_id == request.chat.id


async def process_chat_member_update(update: ChatMemberUpdated) -> bool:
    """Persist access after Telegram reports an accepted membership update."""
    member = update.new_chat_member
    if member.user.is_bot:
        return False
    is_member = member.status in {"creator", "administrator", "member"} or (
        member.status == "restricted" and bool(getattr(member, "is_member", False))
    )
    if not is_member:
        return False
    return await record_chat_membership(member.user.id, update.chat.id)


async def get_membership_status(bot, chat_id: int, user_id: int) -> bool:
    """Return the current Telegram membership state for the configured chat."""
    member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    if member.status in {"creator", "administrator", "member"}:
        return True
    return member.status == "restricted" and bool(getattr(member, "is_member", False))


def join_request_keyboard(invite_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📨 A’zo bo‘lish uchun so‘rov yuborish", url=invite_link)],
            [InlineKeyboardButton(text="✅ A’zolikni tekshirish", callback_data="join:check")],
        ]
    )


class MandatoryJoinMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None or user.is_bot:
            return await handler(event, data)

        try:
            await register_user(user.id)
        except (SQLAlchemyError, RuntimeError) as error:
            logger.error("Bot user registration failed: error_type=%s", type(error).__name__)

        if user.id in get_admin_ids():
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == "join:check":
            return await handler(event, data)

        try:
            settings, _previously_verified = await get_user_access(user.id)
        except (SQLAlchemyError, RuntimeError) as error:
            logger.error("Join gate lookup failed: error_type=%s", type(error).__name__)
            return await self._reject(
                event,
                "Kirish holatini tekshirib bo‘lmadi. Birozdan keyin qayta urinib ko‘ring.",
            )

        if not settings.enabled:
            return await handler(event, data)
        if not settings.chat_id:
            return await self._reject(
                event,
                "Majburiy a’zolik uchun chat ID sozlanmagan. Admin bilan bog‘laning.",
            )
        try:
            if await get_membership_status(event.bot, settings.chat_id, user.id):
                return await handler(event, data)
        except TelegramAPIError as error:
            logger.error("Chat membership check failed: error_type=%s", type(error).__name__)
            return await self._reject(
                event,
                "A’zolik holatini hozir tekshirib bo‘lmadi. Birozdan keyin qayta urinib ko‘ring.",
            )

        if not settings.invite_link:
            return await self._reject(
                event,
                "Siz belgilangan chat a’zosi emassiz va a’zolik havolasi sozlanmagan. Admin bilan bog‘laning.",
            )

        return await self._reject(
            event,
            "Botdan foydalanish uchun kanal/guruhga a’zo bo‘ling yoki yuborgan so‘rovingiz tasdiqlanishini kuting. So‘ng a’zolikni qayta tekshiring.",
            settings.invite_link,
        )

    @staticmethod
    async def _reject(event, message: str, invite_link: str | None = None):
        if isinstance(event, Message):
            await event.answer(message, reply_markup=join_request_keyboard(invite_link) if invite_link else None)
            return None
        if isinstance(event, CallbackQuery):
            await event.answer(message, show_alert=True)
            if invite_link and event.message is not None and event.message.chat.type == "private":
                await event.message.answer(message, reply_markup=join_request_keyboard(invite_link))
            return None
        return None


async def check_membership_callback(callback: CallbackQuery) -> None:
    try:
        settings = await get_join_settings()
        if not settings.enabled:
            await callback.answer("Majburiy a’zolik talabi o‘chirilgan.", show_alert=True)
            return
        if not settings.chat_id:
            await callback.answer("A’zolik sozlamasi to‘liq emas. Admin bilan bog‘laning.", show_alert=True)
            return
        if not await get_membership_status(
            callback.bot,
            settings.chat_id,
            callback.from_user.id,
        ):
            await callback.answer(
                "Hali a’zo emassiz yoki join request tasdiqlanishini kutyapti.",
                show_alert=True,
            )
            return
        await callback.answer("✅ A’zolik tasdiqlandi. Botdan foydalanishingiz mumkin.", show_alert=True)
    except (TelegramAPIError, SQLAlchemyError, RuntimeError) as error:
        logger.error("Membership recheck failed: error_type=%s", type(error).__name__)
        await callback.answer(
            "A’zolik holatini hozir tekshirib bo‘lmadi. Birozdan keyin qayta urinib ko‘ring.",
            show_alert=True,
        )
