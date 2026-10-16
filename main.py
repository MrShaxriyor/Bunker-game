
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from dotenv import load_dotenv
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from database.engine import close_engine, get_sessionmaker
from database.models import Game, GamePlayer, Para, ParaMember
from handlers.admin import (
    admin_router,
    get_admin_ids,
    stop_broadcast_on_shutdown,
)
from handlers.admin_shop import router as admin_shop_router
from handlers.group_command_cleanup import GroupCommandCleanupMiddleware
from handlers.lobby import router as lobby_router
from handlers.para import router as para_router
from handlers.payments import router as payments_router
from handlers.shop import router as shop_router
from ui.start_menu import (
    guide_keyboard,
    guide_text,
    main_menu_keyboard,
    main_menu_text,
    profile_keyboard,
    profile_text,
)
from ui.texts import para_gifts_unconfigured_text



load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN .env faylida topilmadi!")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

command_cleanup = GroupCommandCleanupMiddleware()
for command_router in (
    para_router,
    shop_router,
    payments_router,
    admin_shop_router,
    lobby_router,
    admin_router,
):
    command_router.message.outer_middleware(command_cleanup)

dp.include_router(para_router)
dp.include_router(shop_router)
dp.include_router(payments_router)
dp.include_router(admin_shop_router)
dp.include_router(lobby_router)
dp.include_router(admin_router)
dp.message.outer_middleware(command_cleanup)
dp.shutdown.register(stop_broadcast_on_shutdown)

@dp.message(CommandStart())
async def cmd_start(message: Message):
    bot_info = await bot.get_me()
    if not bot_info.username:
        raise RuntimeError("Bot username Telegram API tomonidan qaytarilmadi.")

    await message.answer(
        main_menu_text(message.from_user.first_name),
        reply_markup=main_menu_keyboard(
            bot_info.username,
            os.getenv("PARA_GIFTS_WEBAPP_URL"),
        ),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "menu:guide")
async def show_guide(callback: CallbackQuery):
    if not isinstance(callback.message, Message):
        await callback.answer(
            "⚠️ Qo‘llanmani bu xabardan ochib bo‘lmadi. "
            "/start buyrug‘ini qayta yuboring.",
            show_alert=True,
        )
        return

    try:
        await callback.message.answer(
            guide_text(),
            reply_markup=guide_keyboard(),
            parse_mode="HTML",
        )
    except TelegramAPIError:
        logging.exception("Qo‘llanma xabarini yuborib bo‘lmadi")
        await callback.answer(
            "Qo‘llanmani yuborib bo‘lmadi. Birozdan keyin qayta urinib ko‘ring.",
            show_alert=True,
        )
        return

    await callback.answer()


@dp.callback_query(F.data == "menu:gifts_unconfigured")
async def show_unconfigured_para_gifts(callback: CallbackQuery):
    await callback.answer(para_gifts_unconfigured_text(), show_alert=True)


@dp.callback_query(F.data == "menu:home")
async def return_to_main_menu(callback: CallbackQuery):
    if not isinstance(callback.message, Message):
        await callback.answer(
            "⚠️ Asosiy menyuga qaytib bo‘lmadi. "
            "/start buyrug‘ini qayta yuboring.",
            show_alert=True,
        )
        return

    try:
        bot_info = await callback.bot.get_me()
        if not bot_info.username:
            logging.error("Bot username Telegram API tomonidan qaytarilmadi")
            await callback.answer(
                "⚠️ Bot havolasini aniqlab bo‘lmadi. Keyinroq urinib ko‘ring.",
                show_alert=True,
            )
            return

        await callback.message.edit_text(
            main_menu_text(callback.from_user.first_name),
            reply_markup=main_menu_keyboard(
                bot_info.username,
                os.getenv("PARA_GIFTS_WEBAPP_URL"),
            ),
            parse_mode="HTML",
        )
    except TelegramAPIError:
        logging.exception("Asosiy menyuga qaytishda Telegram API xatosi")
        await callback.answer(
            "Menyuni ochib bo‘lmadi. Birozdan keyin qayta urinib ko‘ring.",
            show_alert=True,
        )
        return

    await callback.answer()


@dp.callback_query(F.data == "menu:profile")
async def show_profile(callback: CallbackQuery):
    if not isinstance(callback.message, Message):
        await callback.answer(
            "⚠️ Profilni bu xabardan ochib bo‘lmadi. "
            "/start buyrug‘ini qayta yuboring.",
            show_alert=True,
        )
        return

    user = callback.from_user
    try:
        profile_data = await _get_profile_data(user.id)
    except (SQLAlchemyError, RuntimeError) as error:
        logging.error(
            "Profil ma’lumotlarini DB’dan o‘qishda xato: error_type=%s",
            type(error).__name__,
        )
        await callback.answer(
            "Profil ma’lumotlarini hozir olib bo‘lmadi. Keyinroq qayta urinib ko‘ring.",
            show_alert=True,
        )
        return

    try:
        await callback.message.answer(
            profile_text(
                user.full_name,
                user.username,
                user.id,
                total_games=profile_data[0],
                wins=profile_data[1],
                latest_game=profile_data[2],
                para_partner=profile_data[3],
            ),
            reply_markup=profile_keyboard(),
            parse_mode="HTML",
        )
    except TelegramAPIError:
        logging.exception("Profil xabarini yuborib bo‘lmadi")
        await callback.answer(
            "⚠️ Profilni yuborib bo‘lmadi. "
            "Birozdan keyin qayta urinib ko‘ring.",
            show_alert=True,
        )
        return

    await callback.answer()


async def _get_profile_data(
    telegram_user_id: int,
) -> tuple[
    int,
    int,
    tuple[str, int] | None,
    tuple[str, str | None] | None,
]:
    partner_member = aliased(ParaMember)

    async with get_sessionmaker()() as session:
        total_games = await session.scalar(
            select(func.count(func.distinct(GamePlayer.game_id))).where(
                GamePlayer.telegram_user_id == telegram_user_id
            )
        )
        wins = await session.scalar(
            select(func.count(Game.id)).where(
                Game.status == "completed",
                or_(
                    Game.first_winner_id == telegram_user_id,
                    Game.second_winner_id == telegram_user_id,
                ),
            )
        )
        latest_game = (
            await session.execute(
                select(Game.status, Game.current_round)
                .join(GamePlayer, GamePlayer.game_id == Game.id)
                .where(GamePlayer.telegram_user_id == telegram_user_id)
                .order_by(Game.created_at.desc(), Game.id.desc())
                .limit(1)
            )
        ).one_or_none()
        para_partner = (
            await session.execute(
                select(partner_member.display_name, partner_member.username)
                .select_from(ParaMember)
                .join(Para, Para.id == ParaMember.para_id)
                .join(
                    partner_member,
                    (partner_member.para_id == ParaMember.para_id)
                    & (partner_member.telegram_user_id == ParaMember.partner_id),
                )
                .where(
                    Para.status == "active",
                    ParaMember.telegram_user_id == telegram_user_id,
                    ParaMember.ended_at.is_(None),
                    partner_member.ended_at.is_(None),
                )
                .limit(1)
            )
        ).one_or_none()

    return (
        total_games or 0,
        wins or 0,
        (latest_game.status, latest_game.current_round)
        if latest_game is not None
        else None,
        (para_partner.display_name, para_partner.username)
        if para_partner is not None
        else None,
    )


async def main():
    try:
        get_admin_ids()
        print("Bunker TG ishga tushmoqda...")
        await dp.start_polling(bot)
    finally:
        try:
            await bot.session.close()
        finally:
            await close_engine()


if __name__ == "__main__":
    asyncio.run(main())
