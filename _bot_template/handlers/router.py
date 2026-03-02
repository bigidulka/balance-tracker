"""
Handlers для бота
Роутеры и обработчики сообщений/callback
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession
from config import Settings

# Импорт из общих модулей
from _core.database.user_crud import UserCrud

# Локальные импорты
import messages as msg
import keyboards as kb

router = Router(name="main")
logger = logging.getLogger(__name__)


# ============================================
# FSM СОСТОЯНИЯ (если нужны)
# ============================================
# class YourState(StatesGroup):
#     """Состояния для формы"""
#     waiting_input = State()


# ============================================
# СТАРТ И МЕНЮ
# ============================================


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession):
    """Обработка команды /start"""
    # Создаём/обновляем пользователя в БД
    await UserCrud.get_or_create(
        session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )

    await message.answer(
        msg.WELCOME,
        reply_markup=kb.main_menu(),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, config: Settings):
    """Команда /menu - показать главное меню"""
    await message.answer(
        f"{msg.MAIN_MENU}\n\n🤖 {config.bot_name}",
        reply_markup=kb.main_menu(),
    )


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery):
    """Callback кнопки меню"""
    await cb.message.edit_text(
        msg.MAIN_MENU,
        reply_markup=kb.main_menu(),
    )
    await cb.answer()


# ============================================
# ПРИМЕР ОБРАБОТЧИКОВ
# ============================================


@router.callback_query(F.data == "action_1")
async def cb_action_1(cb: CallbackQuery, session: AsyncSession):
    """Пример обработчика callback"""
    # Ваша логика здесь
    await cb.answer("Действие 1 выполнено!")


@router.callback_query(F.data == "action_2")
async def cb_action_2(cb: CallbackQuery, session: AsyncSession):
    """Пример обработчика callback"""
    await cb.answer("Действие 2 выполнено!")


# ============================================
# ПРИМЕР FSM
# ============================================
# @router.callback_query(F.data == "start_form")
# async def start_form(cb: CallbackQuery, state: FSMContext):
#     """Начало формы"""
#     await state.set_state(YourState.waiting_input)
#     await cb.message.edit_text("Введите значение:")
#     await cb.answer()
#
#
# @router.message(YourState.waiting_input)
# async def process_input(message: Message, state: FSMContext, session: AsyncSession):
#     """Обработка ввода"""
#     value = message.text
#     await state.clear()
#     await message.answer(f"Вы ввели: {value}")
