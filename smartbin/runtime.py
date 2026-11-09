from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import Config
from core.canaries import CanaryDaemon
from core.check_runner import CheckRunner
from core.gate import GateManager
from db import DB
from sources import build_sources
from store import Store


@dataclass
class Runtime:
    config: Config
    db: DB
    sources: list[Any]
    store: Store
    gates: GateManager
    canary: CanaryDaemon
    runner: CheckRunner
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bot: Any = None
    miniapp_runner: Any = None

    def uptime(self) -> str:
        delta = datetime.now(timezone.utc) - self.started_at
        minutes = int(delta.total_seconds() // 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h {minutes}m"


RUNTIME: Runtime | None = None


async def _seed_proxies(db: DB, path: str = "us_proxies.txt") -> int:
    """Sync scraped proxies into the pool on boot, pruning dead seed rows."""
    p = Path(path)
    if not p.exists():
        return 0
    urls: set[str] = set()
    added = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split()
        if not parts or parts[0].startswith("#"):
            continue
        url = parts[0]
        scope = parts[1] if len(parts) > 1 else "check"
        country = parts[2].upper() if len(parts) > 2 else ""
        note = ("seed " + " ".join(parts[3:])).strip()
        urls.add(url)
        await db.add_proxy(url, scope=scope, country=country, note=note)
        await db.execute("UPDATE proxies SET enabled=1 WHERE url=?", (url,))
        added += 1
    # Disable previously seeded rows that are no longer in the file.
    rows = await db.fetchall("SELECT id, url, note FROM proxies")
    for row in rows:
        note = str(row["note"] or "")
        seeded = note.startswith("seed") or note in ("fast", "medium", "slow")
        if seeded and str(row["url"]) not in urls:
            await db.update_proxy(int(row["id"]), enabled=0)
    return added


async def build_runtime(config: Config, offline: bool = False) -> Runtime:
    global RUNTIME
    db = await DB(config.db_path, database_url=config.database_url).init()
    await _seed_proxies(db)
    sources = build_sources(offline=offline)

    async def source_proxy() -> str | None:
        rows = await db.list_proxies(enabled_only=True)
        for row in rows:
            if str(row["scope"]) in ("source", "both"):
                return str(row["url"])
        return None

    store = Store(db, sources, config, source_proxy=source_proxy)
    await store.preload()
    gates = GateManager(config)
    canary = CanaryDaemon(db, config, gates)
    runner = CheckRunner(db, store, gates, canary, config)
    RUNTIME = Runtime(
        config=config,
        db=db,
        sources=sources,
        store=store,
        gates=gates,
        canary=canary,
        runner=runner,
    )
    return RUNTIME


def get_runtime() -> Runtime:
    if RUNTIME is None:
        raise RuntimeError("runtime not built")
    return RUNTIME


async def close_runtime(runtime: Runtime | None = None) -> None:
    global RUNTIME
    rt = runtime or RUNTIME
    if rt is None:
        return
    await rt.canary.stop()
    await rt.db.close()
    RUNTIME = None
