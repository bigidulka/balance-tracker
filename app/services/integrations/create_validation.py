from __future__ import annotations

from fastapi import HTTPException


CRYPTOBOT_PROVIDER_CODES = {"cryptobot", "cryptobot_apps"}


def validate_integration_create_payload(payload) -> None:
    provider = str(payload.provider or "").strip().lower()
    kind = str(payload.kind or "").strip().lower()

    if kind == "cex":
        if provider in CRYPTOBOT_PROVIDER_CODES:
            if not str(payload.api_token or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "validation_error",
                        "message": "api_token is required for cryptobot provider",
                    },
                )
            if not str(payload.account_ref or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "validation_error",
                        "message": "account_ref is required for cryptobot provider",
                    },
                )
            payload.provider = "cryptobot"
            payload.exchange_code = "cryptobot"
            return

        if not str(payload.exchange_code or "").strip() or not str(payload.account_ref or "").strip():
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "validation_error",
                    "message": "exchange_code and account_ref are required for cex kind",
                },
            )
        return

    if kind == "dex":
        if not str(payload.wallet_address or "").strip() or not str(payload.chain or "").strip():
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "validation_error",
                    "message": "wallet_address and chain are required for dex kind",
                },
            )
