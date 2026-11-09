from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_HEADERS = {
    "User-Agent": "SMARTBIN/1.0.0 (+https://t.me/SmartBlN_Bot)",
    "Accept": "application/json",
}


def make_client(proxy: str | None = None, timeout: float = 6.0, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    """Proxy-aware httpx client. trust_env is off so ambient proxies never leak in."""
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(timeout),
        "follow_redirects": True,
        "trust_env": False,
        "headers": merged,
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


async def fetch_json(
    url: str,
    proxy: str | None = None,
    timeout: float = 6.0,
    headers: dict[str, str] | None = None,
) -> Any:
    async with make_client(proxy=proxy, timeout=timeout, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def timed_get(url: str, proxy: str | None = None, timeout: float = 6.0) -> tuple[int, int]:
    """Admin proxy test: returns (status_code, latency_ms)."""
    import time

    started = time.perf_counter()
    try:
        async with make_client(proxy=proxy, timeout=timeout) as client:
            response = await client.get(url)
        return response.status_code, int((time.perf_counter() - started) * 1000)
    except Exception:
        return 0, int((time.perf_counter() - started) * 1000)


def proxy_host(proxy: str) -> str:
    try:
        return urlsplit(proxy).hostname or ""
    except Exception:
        return ""


def url_host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except Exception:
        return ""
