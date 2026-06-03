FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY frontend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY frontend/app ./app
COPY frontend/app/emoji_catalog.py ./app/emoji_catalog.py
COPY frontend/app/assets ./app/assets

CMD ["python", "-m", "app.main"]
