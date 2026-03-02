FROM python:3.11-slim

WORKDIR /app

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Python зависимости
COPY _template/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Общие модули (_core и _shared)
COPY _core /app/_core
COPY _shared /app/_shared

# Код приложения
COPY _template /app/

# Переменные окружения
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Запуск
CMD ["python", "main.py"]
