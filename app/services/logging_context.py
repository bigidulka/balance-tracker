import logging
from typing import Optional


def request_log_context(
    request_id: str,
    organization_id: int,
    user_id: Optional[int] = None,
) -> dict[str, str | int | None]:
    return {
        "request_id": request_id,
        "organization_id": organization_id,
        "user_id": user_id,
    }


def get_request_logger(base_logger: logging.Logger, context: dict) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(base_logger, extra={"context": context})
