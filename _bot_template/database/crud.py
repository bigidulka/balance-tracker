"""
CRUD операции (специфичные для этого бота)
Общие CRUD импортируются из _core.database
"""

from typing import Optional, List
from sqlalchemy import select, update, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession

# Импорт общих CRUD
from _core.database.models import User
from _core.database.user_crud import UserCrud

# Локальные модели
# from .models import YourModel

import logging

logger = logging.getLogger(__name__)


# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================


async def _get_user_id(session: AsyncSession, telegram_id: int) -> Optional[int]:
    """Получить users.id по telegram_id"""
    stmt = select(User.id).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ============================================
# ПРИМЕР CRUD
# ============================================
# class YourCrud:
#     @staticmethod
#     async def create(
#         session: AsyncSession,
#         telegram_id: int,
#         name: str,
#     ) -> YourModel:
#         """Создать запись для пользователя"""
#         user_id = await _get_user_id(session, telegram_id)
#         if not user_id:
#             raise ValueError(f"User not found: {telegram_id}")
#
#         item = YourModel(
#             user_id=user_id,
#             name=name,
#         )
#         session.add(item)
#         await session.flush()
#         return item
#
#     @staticmethod
#     async def get_by_user(session: AsyncSession, telegram_id: int) -> List[YourModel]:
#         """Получить все записи пользователя"""
#         user_id = await _get_user_id(session, telegram_id)
#         if not user_id:
#             return []
#
#         stmt = (
#             select(YourModel)
#             .where(YourModel.user_id == user_id)
#             .order_by(YourModel.created_at.desc())
#         )
#         result = await session.execute(stmt)
#         return list(result.scalars().all())
#
#     @staticmethod
#     async def delete(session: AsyncSession, item_id: int) -> bool:
#         """Удалить запись"""
#         stmt = delete(YourModel).where(YourModel.id == item_id)
#         result = await session.execute(stmt)
#         return result.rowcount > 0
