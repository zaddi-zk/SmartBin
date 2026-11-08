from __future__ import annotations

from typing import Any

from .base import BaseSource


def _pick(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


class BinsSource(BaseSource):
    name = "antipublic"
    weight = 0.8
    base_url = "https://bins.antipublic.in/bins"

    async def fetch(self, bin8: str, proxy: str | None = None) -> dict[str, Any] | None:
        prefix = bin8[:6]
        payload = await self._get_json(f"{self.base_url}/{prefix}", proxy=proxy)
        if isinstance(payload, list):
            payload = payload[0] if payload else None
        if not isinstance(payload, dict):
            return None
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict) or not data:
            return None

        prepaid = bool(_pick(data, "prepaid", "is_prepaid", default=False))
        commercial = bool(_pick(data, "commercial", "is_commercial", default=False))
        return {
            "brand": str(_pick(data, "brand", "scheme", "network", default="UNKNOWN")),
            "level": str(_pick(data, "level", "card_level", "type", default="UNKNOWN")),
            "bank": str(_pick(data, "bank", "bank_name", "issuer", default="UNKNOWN")),
            "phone": str(_pick(data, "phone", "bank_phone", default="")),
            "site": str(_pick(data, "site", "url", "bank_url", default="")),
            "country": str(_pick(data, "country", "country_name", default="UNKNOWN")),
            "iso2": str(_pick(data, "iso2", "country_code", "alpha2", default="")),
            "currency": str(_pick(data, "currency", "currency_code", default="")),
            "prepaid": 1 if prepaid else 0,
            "commercial": 1 if commercial else 0,
        }
