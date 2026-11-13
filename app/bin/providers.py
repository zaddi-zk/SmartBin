"""Two independent BIN sources, normalised to one field set (section 17)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

BINLIST_URL = "https://lookup.binlist.net"
ANTIPUBLIC_URL = "https://bins.antipublic.in/bins"

FIELDS: tuple[str, ...] = ("brand", "type", "bank", "country", "iso2")


def _text(value: object) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _pick(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(data.get(key))
        if value:
            return value
    return ""


async def binlist(client: httpx.AsyncClient, bin6: str) -> dict[str, str] | None:
    try:
        response = await client.get(f"{BINLIST_URL}/{bin6}", headers={"Accept-Version": "3"})
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, Mapping) or not data:
        return None
    country: Mapping[str, Any] = (
        data["country"] if isinstance(data.get("country"), Mapping) else {}
    )
    bank: Mapping[str, Any] = data["bank"] if isinstance(data.get("bank"), Mapping) else {}
    return {
        "brand": _pick(data, "brand", "scheme"),
        "type": _pick(data, "type"),
        "bank": _text(bank.get("name")),
        "country": _text(country.get("name")),
        "iso2": _text(country.get("alpha2")),
        "prepaid": "1" if data.get("prepaid") else "0",
        "commercial": "1" if _pick(data, "type").lower() == "business" else "0",
    }


async def antipublic(client: httpx.AsyncClient, bin6: str) -> dict[str, str] | None:
    try:
        response = await client.get(f"{ANTIPUBLIC_URL}/{bin6}")
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    if not isinstance(data, Mapping) or not data:
        return None
    return {
        "brand": _pick(data, "brand", "scheme", "network"),
        "type": _pick(data, "type", "card_type"),
        "bank": _pick(data, "bank", "bank_name", "issuer"),
        "country": _pick(data, "country", "country_name"),
        "iso2": _pick(data, "iso2", "country_code", "alpha2"),
        "prepaid": "1" if _pick(data, "prepaid", "is_prepaid") in ("1", "true", "True") else "0",
        "commercial": "1"
        if _pick(data, "commercial", "is_commercial") in ("1", "true", "True")
        else "0",
    }
