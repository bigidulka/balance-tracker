"""
Модели базы данных (специфичные для этого бота)
Общие модели импортируются из _core.database.models
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    BigInteger,
    String,
    Boolean,
    DateTime,
    Integer,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Импорт общих моделей
from _core.database.models import Base, User


class TimestampMixin:
    """Миксин для created_at и updated_at"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )


# ============================================
# ПРИМЕР МОДЕЛИ
# ============================================
# class YourModel(Base, TimestampMixin):
#     """
#     Ваша модель
#
#     Пример таблицы, связанной с пользователем
#     """
#
#     __tablename__ = "your_table_name"
#     __table_args__ = (
#         Index("ix_your_table_user", "user_id"),
#     )
#
#     id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
#     user_id: Mapped[int] = mapped_column(
#         "user_id",
#         Integer,
#         ForeignKey("users.id", ondelete="CASCADE"),
#         nullable=False,
#         index=True,
#     )
#
#     # Ваши поля
#     name: Mapped[str] = mapped_column(String(64), nullable=False)
#     is_active: Mapped[bool] = mapped_column(Boolean, default=True)
#
#     # Связи
#     user: Mapped["User"] = relationship("User", lazy="raise")
