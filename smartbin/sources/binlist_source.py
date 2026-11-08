from __future__ import annotations

from typing import Any

from .base import BaseSource


class BinlistSource(BaseSource):
    name = "binlist"
    weight = 1.0
    base_url = "https://lookup.binlist.net"

    async def fetch(self, bin8: str, proxy: str | None = None) -> dict[str, Any] | None:
        prefix = bin8[:6]
        data = await self._get_json(
            f"{self.base_url}/{prefix}",
            proxy=proxy,
            headers={"Accept-Version": "3"},
        )
        if not isinstance(data, dict) or not data:
            return None
        country = data.get("country") or {}
        bank = data.get("bank") or {}
        prepaid = bool(data.get("prepaid"))
        level = "PREPAID" if prepaid else str(data.get("level") or "UNKNOWN")
        return {
            "brand": str(data.get("brand") or data.get("scheme") or "UNKNOWN"),
            "level": level,
            "bank": str(bank.get("name") or "UNKNOWN"),
            "phone": str(bank.get("phone") or ""),
            "site": str(bank.get("url") or ""),
            "country": str(country.get("name") or "UNKNOWN"),
            "iso2": str(country.get("alpha2") or ""),
            "currency": str(country.get("currency") or ""),
            "prepaid": 1 if prepaid else 0,
            "commercial": 1 if str(data.get("type", "")).lower() in ("business", "commercial") else 0,
        }
