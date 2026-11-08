from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from typing import Any, Callable, Awaitable

from config import Config
from core.reconciler import decay, reconcile
from models import BinRecord

log = logging.getLogger("smartbin.store")


def _record_from_reconcile(data: dict[str, Any]) -> BinRecord:
    payload = dict(data)
    payload["sources_json"] = json.dumps(payload.get("sources_json", {}), separators=(",", ":"))
    payload["field_conf_json"] = json.dumps(payload.get("field_conf_json", {}), separators=(",", ":"))
    allowed = BinRecord.__dataclass_fields__.keys()
    return BinRecord(**{k: v for k, v in payload.items() if k in allowed})


class Store:
    """LRU -> SQLite -> sources. One permanent row per 8-digit BIN, never re-fetched on hit."""

    def __init__(
        self,
        db: Any,
        sources: list[Any],
        config: Config,
        source_proxy: Callable[[], Awaitable[str | None]] | None = None,
    ):
        self.db = db
        self.sources = sources
        self.config = config
        self.source_proxy = source_proxy
        self.lru: OrderedDict[str, BinRecord] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    async def preload(self) -> int:
        rows = await self.db.preload_bins(self.config.lru_size)
        for row in rows:
            record = BinRecord.from_row(row)
            self.lru[record.bin] = record
        while len(self.lru) > self.config.lru_size:
            self.lru.popitem(last=False)
        log.info("event=lru_preload rows=%s size=%s", len(self.lru), self.config.lru_size)
        return len(self.lru)

    def _put(self, record: BinRecord) -> None:
        self.lru[record.bin] = record
        self.lru.move_to_end(record.bin)
        while len(self.lru) > self.config.lru_size:
            self.lru.popitem(last=False)

    def invalidate(self, bin8: str) -> None:
        self.lru.pop(bin8, None)

    async def get(self, bin8: str) -> BinRecord:
        if bin8 in self.lru:
            self.lru.move_to_end(bin8)
            return self.lru[bin8]

        row = await self.db.get_bin(bin8)
        if row is not None:
            record = BinRecord.from_row(row)
            self._put(record)
            return record

        lock = self._locks.setdefault(bin8, asyncio.Lock())
        async with lock:
            if bin8 in self.lru:
                self.lru.move_to_end(bin8)
                return self.lru[bin8]
            row = await self.db.get_bin(bin8)
            if row is not None:
                record = BinRecord.from_row(row)
                self._put(record)
                return record

            results = await self._fanout(bin8)
            if not results:
                log.info("event=bin_miss bin=%s sources=0", bin8)
                return BinRecord(bin=bin8)

            record = _record_from_reconcile(reconcile(bin8, results))
            await self.db.upsert_bin(record.to_dict())
            self._put(record)
            log.info(
                "event=bin_fetch bin=%s sources=%s confidence=%s",
                bin8,
                record.sample_size,
                record.confidence,
            )
            return record

    async def get_cached(self, bin8: str) -> BinRecord | None:
        """Cache-only read: LRU then SQLite. Never fans out to sources."""
        if bin8 in self.lru:
            self.lru.move_to_end(bin8)
            return self.lru[bin8]
        row = await self.db.get_bin(bin8)
        if row is None:
            return None
        record = BinRecord.from_row(row)
        self._put(record)
        return record

    async def get_prefix(self, six: str) -> BinRecord:
        """6-digit prefix lookup. Never persisted as an 8-digit BIN row."""
        results = await self._fanout(six)
        if not results:
            return BinRecord(bin=six)
        return _record_from_reconcile(reconcile(six, results))

    async def _fanout(self, bin8: str) -> list[tuple[str, float, dict[str, Any]]]:
        async def one(source: Any) -> tuple[str, float, dict[str, Any]] | None:
            proxy = None
            if getattr(source, "breaker", None) is not None and source.breaker.is_open:
                if self.source_proxy is not None:
                    proxy = await self.source_proxy()
            try:
                data = await asyncio.wait_for(
                    source.safe_fetch(bin8, proxy=proxy),
                    timeout=self.config.source_timeout + 0.5,
                )
            except (asyncio.TimeoutError, Exception):
                data = None
            if not data:
                return None
            return source.name, source.weight, data

        tasks = [one(source) for source in self.sources[:3]]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[tuple[str, float, dict[str, Any]]] = []
        for item in gathered:
            if isinstance(item, tuple) and len(item) == 3 and isinstance(item[2], dict):
                results.append(item)
        return results

    def effective_confidence(self, record: BinRecord) -> float:
        return decay(record.confidence, record.verified_utc)

    def depth(self) -> int:
        return len(self.lru)
