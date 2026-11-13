"""BIN sources, normalised to one field set (section 17.5).

Providers only reshape their own payload. They never compute confidence and
never decide what is true: the resolver merges field by field and records
disagreement as a conflict.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

BINLIST_URL = "https://lookup.binlist.net"
ANTIPUBLIC_URL = "https://bins.antipublic.in/bins"

FIELDS: tuple[str, ...] = (
    "brand",
    "type",
    "tier",
    "prepaid",
    "commercial",
    "debit",
    "bank_short",
    "bank_legal",
    "bank_phone",
    "bank_site",
    "country",
    "iso2",
    "currency",
    "pan_len",
    "range_lo",
    "range_hi",
)

BOOL_FIELDS = frozenset({"prepaid", "commercial", "debit"})
INT_FIELDS = frozenset({"pan_len"})

# Explicit trust order. The first name wins a field when no majority exists.
TRUST: tuple[str, ...] = ("binlist", "antipublic")

Fetch = Callable[[httpx.AsyncClient, str], Awaitable[dict[str, str] | None]]


def _text(value: object) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _pick(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(data.get(key))
        if value:
            return value
    return ""


def _truthy(value: object) -> str:
    return "1" if str(value).strip().lower() in ("1", "true", "yes", "y") else "0"


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


async def _get_json(
    client: httpx.AsyncClient, url: str, headers: Mapping[str, str] | None = None
) -> object:
    try:
        response = await client.get(url, headers=dict(headers or {}))
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        return response.json()
    except ValueError:
        return None


async def binlist(client: httpx.AsyncClient, bin6: str) -> dict[str, str] | None:
    payload = await _get_json(client, f"{BINLIST_URL}/{bin6}", {"Accept-Version": "3"})
    if not isinstance(payload, Mapping) or not payload:
        return None
    country = _mapping(payload.get("country"))
    bank = _mapping(payload.get("bank"))
    number = _mapping(payload.get("number"))
    card_type = _pick(payload, "type")
    brand = _pick(payload, "brand", "scheme")
    if not brand and not card_type:
        return None
    return {
        "brand": brand,
        "type": card_type,
        "tier": _pick(payload, "tier", "card_level"),
        "prepaid": _truthy(payload.get("prepaid")),
        "commercial": "1" if card_type.lower() == "business" else "0",
        "debit": "1" if card_type.lower() == "debit" else "0",
        "bank_short": _text(bank.get("name")),
        "bank_legal": _text(bank.get("name")),
        "bank_phone": _text(bank.get("phone")),
        "bank_site": _text(bank.get("url")),
        "country": _text(country.get("name")),
        "iso2": _text(country.get("alpha2")),
        "currency": _text(country.get("currency")),
        "pan_len": _text(number.get("length")),
    }


async def antipublic(client: httpx.AsyncClient, bin6: str) -> dict[str, str] | None:
    payload = await _get_json(client, f"{ANTIPUBLIC_URL}/{bin6}")
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, Mapping):
        return None
    data = _mapping(payload.get("data")) or payload
    if not data:
        return None
    brand = _pick(data, "brand", "scheme", "network")
    card_type = _pick(data, "type", "card_type")
    if not brand and not card_type:
        return None
    return {
        "brand": brand,
        "type": card_type,
        "tier": _pick(data, "tier", "card_level", "level"),
        "prepaid": _truthy(_pick(data, "prepaid", "is_prepaid")),
        "commercial": _truthy(_pick(data, "commercial", "is_commercial")),
        "debit": _truthy(_pick(data, "debit", "is_debit")),
        "bank_short": _pick(data, "bank", "bank_name", "issuer"),
        "bank_legal": _pick(data, "bank_legal", "legal_name", "issuer"),
        "bank_phone": _pick(data, "bank_phone", "phone"),
        "bank_site": _pick(data, "bank_url", "site", "website"),
        "country": _pick(data, "country", "country_name"),
        "iso2": _pick(data, "iso2", "country_code", "alpha2"),
        "currency": _pick(data, "currency", "currency_code"),
        "pan_len": _pick(data, "pan_len", "length", "digits"),
        "range_lo": _pick(data, "range_lo", "range_start", "low"),
        "range_hi": _pick(data, "range_hi", "range_end", "high"),
    }


@dataclass(frozen=True, slots=True)
class Provider:
    name: str
    fetch: Fetch


# Minimum two. A single success still renders, with sources_n = 1 and the
# advisory banner; it is never silently upgraded to "high".
PROVIDERS: tuple[Provider, ...] = (
    Provider(name="binlist", fetch=binlist),
    Provider(name="antipublic", fetch=antipublic),
)

PROVIDER_NAMES: tuple[str, ...] = tuple(provider.name for provider in PROVIDERS)
assert PROVIDER_NAMES == TRUST, "PROVIDERS must follow the explicit trust order"
