from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import BaseSource, CircuitBreaker, Source
from .binlist_source import BinlistSource
from .bins_source import BinsSource

SEED_PATH = Path(__file__).resolve().parents[1] / "seed_bins.json"


class SeedSource(BaseSource):
    name = "seed"
    weight = 0.4

    def __init__(self, path: Path | str = SEED_PATH):
        super().__init__()
        self.path = Path(path)
        self._data: dict[str, dict[str, Any]] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self._data = {}

    async def fetch(self, bin8: str, proxy: str | None = None) -> dict[str, Any] | None:
        for key in (bin8, bin8[:6]):
            if key in self._data:
                return dict(self._data[key])
        return None


REGISTRY: dict[str, type[BaseSource]] = {
    "binlist": BinlistSource,
    "antipublic": BinsSource,
    "seed": SeedSource,
}


def build_sources(offline: bool = False) -> list[Source]:
    if offline:
        return [SeedSource()]
    return [BinlistSource(), BinsSource(), SeedSource()]


__all__ = ["BaseSource", "CircuitBreaker", "Source", "SeedSource", "build_sources", "REGISTRY"]
