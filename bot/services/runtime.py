"""Shared bot runtime state backed by Redis with in-memory fallback."""

from __future__ import annotations

import base64
import contextvars
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

from bot.config import settings
from bot.i18n import DEFAULT_LOCALE, normalize_locale

logger = logging.getLogger(__name__)
BACKEND_AUTH_SESSION_VERSION = 2

_current_telegram_user_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "bot_current_telegram_user_id",
    default=None,
)
_current_backend_auth: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "bot_current_backend_auth",
    default=None,
)


@dataclass(frozen=True)
class BackendAuthSession:
    version: int
    telegram_user_id: int
    access_token: str
    organization_id: int
    backend_user_id: int
    organization_name: str | None = None
    full_name: str | None = None
    username: str | None = None
    role: str | None = None
    is_platform_admin: bool | None = None
    resolved_at: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "telegram_user_id": self.telegram_user_id,
            "access_token": self.access_token,
            "organization_id": self.organization_id,
            "backend_user_id": self.backend_user_id,
            "organization_name": self.organization_name,
            "full_name": self.full_name,
            "username": self.username,
            "role": self.role,
            "is_platform_admin": self.is_platform_admin,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "BackendAuthSession | None":
        try:
            version = int(payload.get("version"))
            telegram_user_id = int(payload.get("telegram_user_id"))
            organization_id = int(payload.get("organization_id"))
            backend_user_id = int(payload.get("backend_user_id"))
            access_token = str(payload.get("access_token") or "").strip()
        except (TypeError, ValueError):
            return None
        if version != BACKEND_AUTH_SESSION_VERSION:
            return None
        if not access_token:
            return None
        return cls(
            version=version,
            telegram_user_id=telegram_user_id,
            access_token=access_token,
            organization_id=organization_id,
            backend_user_id=backend_user_id,
            organization_name=str(payload.get("organization_name") or "") or None,
            full_name=str(payload.get("full_name") or "") or None,
            username=str(payload.get("username") or "") or None,
            role=str(payload.get("role") or "") or None,
            is_platform_admin=payload.get("is_platform_admin"),
            resolved_at=str(payload.get("resolved_at") or "") or None,
        )

_DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "language": DEFAULT_LOCALE,
    "hide_small": False,
    "tx_type": "all",
    "tx_status": "all",
    "tx_service": "all",
    "tx_since_hours": 24,
}

_user_settings_fallback: dict[int, dict[str, Any]] = {}
_subscribers_fallback: set[int] = set()
_auth_sessions_fallback: dict[int, dict[str, Any]] = {}
_notification_balances_fallback: dict[int, dict[str, dict[str, float]]] = {}
_notification_changes_fallback: dict[int, dict[str, dict[str, list[list[Any]]]]] = {}
_notification_transactions_fallback: dict[int, dict[str, str]] = {}
_redis_client: Redis | None = None
_redis_failed = False


def _user_settings_key(user_id: int) -> str:
    return f"bot:settings:{user_id}"


def _subscriber_key() -> str:
    return "bot:subscribers"


def _notification_balances_key(user_id: int) -> str:
    return f"bot:notifications:balances:{user_id}"


def _notification_changes_key(user_id: int) -> str:
    return f"bot:notifications:changes:{user_id}"


def _notification_transactions_key(user_id: int) -> str:
    return f"bot:notifications:transactions:{user_id}"


def _auth_session_key(user_id: int) -> str:
    return f"bot:auth:{user_id}"


def set_current_telegram_user_id(user_id: int | None) -> contextvars.Token[int | None]:
    return _current_telegram_user_id.set(user_id)


def get_current_telegram_user_id() -> int | None:
    return _current_telegram_user_id.get()


def set_current_backend_auth_session(session: dict[str, Any] | None) -> contextvars.Token[dict[str, Any] | None]:
    return _current_backend_auth.set(dict(session) if isinstance(session, dict) else None)


def get_current_backend_auth_session() -> dict[str, Any] | None:
    payload = _current_backend_auth.get()
    return dict(payload) if isinstance(payload, dict) else None


def _jwt_expired(token: str, *, skew_seconds: int = 60) -> bool:
    raw = str(token or "").strip()
    if not raw:
        return True
    try:
        parts = raw.split(".", 2)
        if len(parts) != 3:
            return True
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8"))
        exp = int(payload.get("exp") or 0)
    except Exception:
        return True
    now_ts = int(datetime.now(timezone.utc).timestamp())
    return exp <= now_ts + max(int(skew_seconds), 0)


def _sanitize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(_DEFAULT_USER_SETTINGS)
    if not isinstance(raw, dict):
        return payload
    for key in payload:
        if key in raw:
            payload[key] = raw[key]
    payload["language"] = normalize_locale(payload.get("language"))
    payload["hide_small"] = bool(payload.get("hide_small"))
    if str(payload.get("tx_type")) not in {"all", "deposit", "withdrawal"}:
        payload["tx_type"] = "all"
    if str(payload.get("tx_status")) not in {"all", "pending", "ok", "failed"}:
        payload["tx_status"] = "all"
    if str(payload.get("tx_service")) != "all":
        payload["tx_service"] = "all"
    try:
        tx_since_hours = int(payload.get("tx_since_hours", 24) or 24)
    except (TypeError, ValueError):
        tx_since_hours = 24
    payload["tx_since_hours"] = min(max(tx_since_hours, 1), 24 * 30)
    return payload


async def get_runtime_redis() -> Redis | None:
    global _redis_client, _redis_failed
    if _redis_failed:
        return None
    if _redis_client is None:
        try:
            client = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
            await client.ping()
            _redis_client = client
        except Exception as exc:
            _redis_failed = True
            logger.warning("Redis runtime unavailable, using in-memory fallback: %s", exc)
            return None
    return _redis_client


async def close_runtime() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


async def get_backend_auth_session(telegram_user_id: int) -> dict[str, Any] | None:
    current = get_current_backend_auth_session()
    if current and int(current.get("telegram_user_id") or 0) == int(telegram_user_id):
        return dict(current)

    redis = await get_runtime_redis()
    if redis is None:
        cached = _auth_sessions_fallback.get(telegram_user_id)
        if cached is None:
            return None
        session = BackendAuthSession.from_payload(cached)
        return session.to_payload() if session else None

    payload = await redis.get(_auth_session_key(telegram_user_id))
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    session = BackendAuthSession.from_payload(parsed)
    return session.to_payload() if session else None


async def save_backend_auth_session(telegram_user_id: int, session: dict[str, Any]) -> dict[str, Any]:
    normalized = BackendAuthSession.from_payload({**dict(session), "telegram_user_id": telegram_user_id})
    if normalized is None:
        raise ValueError("Invalid backend auth session payload")
    payload = normalized.to_payload()

    redis = await get_runtime_redis()
    if redis is None:
        _auth_sessions_fallback[telegram_user_id] = dict(payload)
        return payload

    await redis.set(_auth_session_key(telegram_user_id), json.dumps(payload))
    return payload


async def clear_backend_auth_session(telegram_user_id: int) -> None:
    current_user_id = get_current_telegram_user_id()
    if current_user_id == telegram_user_id:
        set_current_backend_auth_session(None)

    redis = await get_runtime_redis()
    if redis is None:
        _auth_sessions_fallback.pop(telegram_user_id, None)
        return

    await redis.delete(_auth_session_key(telegram_user_id))


async def ensure_backend_auth_session(
    *,
    telegram_user_id: int,
    username: str | None = None,
    full_name: str | None = None,
) -> dict[str, Any]:
    existing = await get_backend_auth_session(telegram_user_id)
    normalized_username = str(username or "").strip() or None
    normalized_full_name = str(full_name or "").strip() or None
    needs_profile_refresh = False
    if existing is not None:
        if normalized_username and str(existing.get("username") or "").strip() != normalized_username:
            needs_profile_refresh = True
        if normalized_full_name and str(existing.get("full_name") or "").strip() != normalized_full_name:
            needs_profile_refresh = True
    if existing is not None and not needs_profile_refresh:
        token = str(existing.get("access_token") or "").strip()
        if not _jwt_expired(token):
            set_current_backend_auth_session(existing)
            return existing
        logger.info(
            "Backend auth session expired for telegram_user_id=%s, refreshing",
            telegram_user_id,
        )

    if settings.no_backend_ui_mode:
        payload = BackendAuthSession(
            version=BACKEND_AUTH_SESSION_VERSION,
            telegram_user_id=telegram_user_id,
            access_token=f"preview-{telegram_user_id}",
            organization_id=1,
            backend_user_id=telegram_user_id,
            organization_name="UI Preview Org",
            full_name=normalized_full_name,
            username=normalized_username,
            role="owner",
            is_platform_admin=telegram_user_id in settings.admin_users,
            resolved_at=datetime.now(timezone.utc).isoformat(),
        ).to_payload()
        await save_backend_auth_session(telegram_user_id, payload)
        set_current_backend_auth_session(payload)
        return payload

    from bot.api_client import api_client

    display_name = normalized_full_name or normalized_username or f"Telegram User {telegram_user_id}"
    bootstrap = await api_client.bootstrap_telegram_identity(
        telegram_user_id=telegram_user_id,
        telegram_username=normalized_username,
        telegram_full_name=display_name,
    )
    payload = BackendAuthSession(
        version=BACKEND_AUTH_SESSION_VERSION,
        telegram_user_id=telegram_user_id,
        access_token=str(bootstrap.get("access_token") or ""),
        organization_id=int(bootstrap.get("organization_id") or 0),
        backend_user_id=int(bootstrap.get("user_id") or telegram_user_id),
        organization_name=str(bootstrap.get("organization_name") or "") or None,
        full_name=display_name,
        username=normalized_username,
        role=str(bootstrap.get("role") or "owner"),
        is_platform_admin=bool(bootstrap.get("is_platform_admin")),
        resolved_at=datetime.now(timezone.utc).isoformat(),
    ).to_payload()
    await save_backend_auth_session(telegram_user_id, payload)
    set_current_backend_auth_session(payload)
    return payload


async def get_user_settings(user_id: int) -> dict[str, Any]:
    redis = await get_runtime_redis()
    if redis is None:
        cached = _user_settings_fallback.get(user_id)
        if cached is None:
            cached = dict(_DEFAULT_USER_SETTINGS)
            _user_settings_fallback[user_id] = cached
        return dict(cached)

    payload = await redis.get(_user_settings_key(user_id))
    if not payload:
        settings_payload = dict(_DEFAULT_USER_SETTINGS)
        await redis.set(_user_settings_key(user_id), json.dumps(settings_payload))
        return settings_payload

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        parsed = {}
    sanitized = _sanitize_settings(parsed)
    if sanitized != parsed:
        await redis.set(_user_settings_key(user_id), json.dumps(sanitized))
    return sanitized


async def save_user_settings(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_settings(payload)
    redis = await get_runtime_redis()
    if redis is None:
        _user_settings_fallback[user_id] = dict(sanitized)
        return sanitized

    await redis.set(_user_settings_key(user_id), json.dumps(sanitized))
    return sanitized


async def register_subscriber(user_id: int) -> None:
    redis = await get_runtime_redis()
    if redis is None:
        _subscribers_fallback.add(user_id)
        return
    await redis.sadd(_subscriber_key(), str(user_id))


async def get_notification_recipients() -> list[int]:
    redis = await get_runtime_redis()
    if redis is None:
        if _subscribers_fallback:
            return sorted(_subscribers_fallback)
        return []

    members = await redis.smembers(_subscriber_key())
    parsed = sorted(int(member) for member in members if str(member).isdigit())
    return parsed


async def is_local_user_allowed(user_id: int) -> bool:
    if settings.no_backend_ui_mode:
        return True
    if user_id in settings.admin_users:
        return True
    if settings.allowed_users and user_id in settings.allowed_users:
        return True
    return (await get_backend_auth_session(user_id)) is not None


async def is_local_user_admin(user_id: int) -> bool:
    return user_id in settings.admin_users


async def load_notification_balances(user_id: int) -> dict[str, dict[str, float]]:
    redis = await get_runtime_redis()
    if redis is None:
        cached = _notification_balances_fallback.get(user_id, {})
        return {
            service: dict(assets)
            for service, assets in cached.items()
        }

    payload = await redis.get(_notification_balances_key(user_id))
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, dict[str, float]] = {}
    for service, assets in parsed.items():
        if not isinstance(service, str) or not isinstance(assets, dict):
            continue
        result[service] = {
            str(coin): float(amount)
            for coin, amount in assets.items()
            if isinstance(coin, str)
        }
    return result


async def save_notification_balances(user_id: int, payload: dict[str, dict[str, float]]) -> None:
    normalized = {
        str(service): {str(coin): float(amount) for coin, amount in assets.items()}
        for service, assets in payload.items()
        if isinstance(assets, dict)
    }
    redis = await get_runtime_redis()
    if redis is None:
        _notification_balances_fallback[user_id] = {
            service: dict(assets) for service, assets in normalized.items()
        }
        return
    await redis.set(_notification_balances_key(user_id), json.dumps(normalized))


async def load_notification_changes(user_id: int) -> dict[str, dict[str, list[list[Any]]]]:
    redis = await get_runtime_redis()
    if redis is None:
        cached = _notification_changes_fallback.get(user_id, {})
        return {
            service: {
                coin: [list(item) for item in history]
                for coin, history in coins.items()
            }
            for service, coins in cached.items()
        }

    payload = await redis.get(_notification_changes_key(user_id))
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, dict[str, list[list[Any]]]] = {}
    for service, coins in parsed.items():
        if not isinstance(service, str) or not isinstance(coins, dict):
            continue
        result[service] = {}
        for coin, history in coins.items():
            if not isinstance(coin, str) or not isinstance(history, list):
                continue
            normalized_history: list[list[Any]] = []
            for item in history:
                if not isinstance(item, list) or len(item) != 3:
                    continue
                normalized_history.append([str(item[0]), float(item[1]), float(item[2])])
            result[service][coin] = normalized_history
    return result


async def save_notification_changes(user_id: int, payload: dict[str, dict[str, list[list[Any]]]]) -> None:
    normalized: dict[str, dict[str, list[list[Any]]]] = {}
    for service, coins in payload.items():
        if not isinstance(service, str) or not isinstance(coins, dict):
            continue
        normalized[service] = {}
        for coin, history in coins.items():
            if not isinstance(coin, str) or not isinstance(history, list):
                continue
            normalized[service][coin] = []
            for item in history:
                if not isinstance(item, (list, tuple)) or len(item) != 3:
                    continue
                normalized[service][coin].append(
                    [str(item[0]), float(item[1]), float(item[2])]
                )

    redis = await get_runtime_redis()
    if redis is None:
        _notification_changes_fallback[user_id] = {
            service: {
                coin: [list(item) for item in history]
                for coin, history in coins.items()
            }
            for service, coins in normalized.items()
        }
        return
    await redis.set(_notification_changes_key(user_id), json.dumps(normalized))


async def load_processed_transactions(user_id: int) -> dict[str, str]:
    redis = await get_runtime_redis()
    if redis is None:
        return dict(_notification_transactions_fallback.get(user_id, {}))

    payload = await redis.get(_notification_transactions_key(user_id))
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(tx_key): str(service)
        for tx_key, service in parsed.items()
        if isinstance(tx_key, str)
    }


async def save_processed_transactions(user_id: int, payload: dict[str, str]) -> None:
    normalized = {
        str(tx_key): str(service)
        for tx_key, service in payload.items()
        if isinstance(tx_key, str)
    }
    redis = await get_runtime_redis()
    if redis is None:
        _notification_transactions_fallback[user_id] = dict(normalized)
        return
    await redis.set(_notification_transactions_key(user_id), json.dumps(normalized))
