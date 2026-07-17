from __future__ import annotations

import re
from urllib.parse import urlparse

import aiohttp
from aiohttp_socks import ProxyConnector

from app.core.config import get_settings

settings = get_settings()

# Hostnames that should never be routed through outbound proxy.
# Covers docker-compose service names, localhost, and link-local addresses.
_INTERNAL_HOST_RE = re.compile(
    r"^(?:"
    r"localhost|127\.[0-9]+\.[0-9]+\.[0-9]+|0\.0\.0\.0|"
    r"::1|\[::1\]|"
    r"debank-sdk|redis|api|worker|bot|db|postgres|"
    r"[a-z][a-z0-9-]*\.(?:internal|docker|local|svc)"
    r")$",
    re.IGNORECASE,
)


def _url_is_internal(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return bool(_INTERNAL_HOST_RE.match(host))


def build_proxy_url() -> str | None:
    return settings.proxy_url


def is_socks_proxy(url: str | None = None) -> bool:
    proxy = str(url or build_proxy_url() or "").strip().lower()
    return proxy.startswith(("socks5://", "socks4://", "socks4a://", "socks5h://"))


def is_http_proxy(url: str | None = None) -> bool:
    proxy = str(url or build_proxy_url() or "").strip().lower()
    return proxy.startswith(("http://", "https://"))


def build_connector(target_url: str | None = None) -> aiohttp.BaseConnector | None:
    proxy = build_proxy_url()
    if not proxy:
        return None
    if target_url and _url_is_internal(target_url):
        return None
    if is_socks_proxy(proxy):
        return ProxyConnector.from_url(proxy)
    return None


def session_kwargs(
    timeout: aiohttp.ClientTimeout | None = None,
    target_url: str | None = None,
) -> dict:
    kwargs: dict = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    connector = build_connector(target_url=target_url)
    if connector is not None:
        kwargs["connector"] = connector
    return kwargs


def request_proxy_kwargs(target_url: str | None = None) -> dict:
    proxy = build_proxy_url()
    if not proxy:
        return {}
    if target_url and _url_is_internal(target_url):
        return {}
    if is_http_proxy(proxy):
        return {"proxy": proxy}
    return {}
