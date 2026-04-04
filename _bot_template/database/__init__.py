"""
Database модуль бота
Экспорт моделей и CRUD операций
"""

# Импорт из общих модулей (_core)
from _core.database.models import Base, User
from _core.database.user_crud import UserCrud

# Локальные модели и CRUD (если есть)
# from .models import YourModel
# from .crud import YourCrud

__all__ = [
    # Общие
    "Base",
    "User",
    "UserCrud",
    # Локальные
    # "YourModel",
    # "YourCrud",
]
