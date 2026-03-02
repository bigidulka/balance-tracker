import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import get_settings

settings = get_settings()


class SecurityError(Exception):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
    return f"pbkdf2_sha256${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, salt_b64, digest_b64 = password_hash.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
    except Exception:
        return False

    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
    return hmac.compare_digest(digest, expected)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))


def create_access_token(
    user_id: int,
    organization_id: int | None = None,
    expires_minutes: int | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(
        minutes=expires_minutes or settings.jwt_access_token_expire_minutes
    )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if organization_id is not None:
        payload["org"] = int(organization_id)

    header = {"alg": settings.jwt_algorithm, "typ": "JWT"}

    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url_encode(json.dumps(payload, separators=(',', ':')).encode())}"
    )

    signature = hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".", 2)
    except ValueError as exc:
        raise SecurityError("Invalid token format") from exc

    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception as exc:
        raise SecurityError("Invalid token payload") from exc

    if header.get("alg") != settings.jwt_algorithm:
        raise SecurityError("Unsupported token algorithm")

    signing_input = f"{header_b64}.{payload_b64}"
    expected_signature = hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    try:
        token_signature = _b64url_decode(signature_b64)
    except Exception as exc:
        raise SecurityError("Invalid token signature encoding") from exc

    if not hmac.compare_digest(expected_signature, token_signature):
        raise SecurityError("Invalid token signature")

    exp = payload.get("exp")
    if not isinstance(exp, int):
        raise SecurityError("Token has invalid expiration")

    if datetime.now(timezone.utc).timestamp() >= exp:
        raise SecurityError("Token expired")

    return payload
