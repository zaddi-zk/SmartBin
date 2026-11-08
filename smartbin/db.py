SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

-- ============ SCHEMA v1.0.0 · SMARTBIN · single SQLite file · no server ============

-- FRONT: BIN intelligence, one permanent row per BIN, never re-fetched on hit
CREATE TABLE IF NOT EXISTS bins (
  bin             TEXT PRIMARY KEY,           -- ALWAYS 8 digits, ISO/IEC 7812. Never truncate.
  brand           TEXT DEFAULT 'UNKNOWN',
  level           TEXT DEFAULT 'UNKNOWN',    -- CLASSIC|STANDARD|GOLD|PLATINUM|SIGNATURE|INFINITE|WORLD|BUSINESS|COMMERCIAL|CORPORATE|PREPAID|GOVERNMENT
  bank            TEXT DEFAULT 'UNKNOWN',
  phone           TEXT DEFAULT '',
  site            TEXT DEFAULT '',
  country        TEXT DEFAULT 'UNKNOWN',
  iso2           TEXT DEFAULT '',
  currency       TEXT DEFAULT '',
  prepaid        INTEGER DEFAULT 0,
  commercial     INTEGER DEFAULT 0,
  bin_age        INTEGER DEFAULT 0,           -- years since first seen
  card_length    INTEGER DEFAULT 16,
  luhn          INTEGER DEFAULT 1,
  dob            INTEGER DEFAULT 0,          -- 1 if DOB known-asked by issuer flow
  ssn            INTEGER DEFAULT 0,
  mmn            INTEGER DEFAULT 0,
  avs            INTEGER DEFAULT 1,
  sources_json   TEXT DEFAULT '{}',          -- per-field: {"bank":{"binlist":"Chase","antipublic":"JPMorgan"}}
  field_conf_json TEXT DEFAULT '{}',         -- {"bank":82.5,"level":55.0} → <60 renders LOW-CONF
  confidence     REAL DEFAULT 0.0,          -- computed, never guessed
  verified_utc   TEXT DEFAULT '',
  sample_size    INTEGER DEFAULT 0,
  corrections    INTEGER DEFAULT 0,
  twofa          TEXT DEFAULT 'UNKNOWN',     -- NON-VBV|NON-VBV*|VBV|PARTIAL|LOW-CONF|INCONCLUSIVE|UNKNOWN
  twofa_conf     REAL DEFAULT 0.0,
  twofa_reads    INTEGER DEFAULT 0,
  last_twofa_utc TEXT DEFAULT '',
  amt_tested     INTEGER DEFAULT 0          -- minor units of enrollment test (3100 = €31.00)
);

-- FTS5 search on bank/brand/country
CREATE VIRTUAL TABLE IF NOT EXISTS bins_fts USING fts5(
  bin UNINDEXED, brand, level, bank, country
);
-- ON UPSERT: DELETE FROM bins_fts WHERE bin=?; then INSERT. Coder implements.

CREATE TABLE IF NOT EXISTS users (
  user_id      INTEGER PRIMARY KEY,
  username     TEXT DEFAULT '',
  first_name   TEXT DEFAULT '',
  joined_utc   TEXT DEFAULT '',
  source_tag   TEXT DEFAULT '',              -- startapp/start deeplynk attribution. THE funnel.
  credits      INTEGER DEFAULT 0,
  premium_until TEXT DEFAULT '',             -- ISO; sub renewals extend via receipts
  quota_date   TEXT DEFAULT '',             -- UTC date for daily free quota reset
  free_bin     INTEGER DEFAULT 0,
  free_check   INTEGER DEFAULT 0,
  stars_total  INTEGER DEFAULT 0,
  corrections  INTEGER DEFAULT 0,
  is_banned    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_users_premium ON users(premium_until);

-- Idempotent by charge_id. Replay = UPDATE on existing PK, never INSERT dup.
CREATE TABLE IF NOT EXISTS receipts (
  charge_id  TEXT PRIMARY KEY,              -- telegram_payment_charge_id
  user_id    INTEGER,
  amount_xtr INTEGER DEFAULT 0,
  product    TEXT,                          -- day | monthly | credits500
  paid_utc   TEXT DEFAULT '',
  state      TEXT DEFAULT 'paid'             -- paid | refunded
);

CREATE TABLE IF NOT EXISTS checks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER,
  bin         TEXT,
  gate        TEXT,
  attempt     INTEGER DEFAULT 1,            -- 1 or 2 (retry matrix)
  verdict     TEXT DEFAULT '',              -- APPROVED|CVV-FAIL|DO-NOT-HONOR|ISSUER-BLOCK|AMBIGUOUS|INSUFFICIENT|INVALID|EXPIRED|3DS-CHALLENGE|MERCHANT-RESTRICTED|PICKUP|INCONCLUSIVE
  action      TEXT DEFAULT '',
  live        INTEGER DEFAULT 0,
  raw_code    TEXT DEFAULT '',              -- network code (05, 82...)
  decline_code TEXT DEFAULT '',             -- Stripe scope (generic_decline...)
  twofa_read  TEXT DEFAULT '',              -- what THIS check taught the 2FA table
  amount_band INTEGER DEFAULT 0,            -- minor units
  canary_ok   INTEGER DEFAULT -1,           -- window state at dispatch: 1 pass / 0 fail / -1 stale
  acs_host    TEXT DEFAULT '',
  latency_ms  INTEGER DEFAULT 0,
  proxy_id    INTEGER DEFAULT 0,
  note        TEXT DEFAULT '',               -- why AMBIGUOUS, why INCONCLUSIVE
  created_utc TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_checks_bin ON checks(bin);
CREATE INDEX IF NOT EXISTS idx_checks_user ON checks(user_id);

CREATE TABLE IF NOT EXISTS gates (
  name            TEXT PRIMARY KEY,
  kind            TEXT DEFAULT 'stripe',
  enabled         INTEGER DEFAULT 1,
  breaker_fails   INTEGER DEFAULT 0,
  breaker_until   TEXT DEFAULT '',
  canary_log      TEXT DEFAULT '[]',         -- JSON ring ≤30: {"ts","card":"nonvbv|vbv|dead","pass":0|1,"latency_ms"}
  last_canary_utc TEXT DEFAULT '',
  window_checks   INTEGER DEFAULT 0          -- user checks since last canary window
);

CREATE TABLE IF NOT EXISTS proxies (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  url            TEXT UNIQUE,               -- scheme://user:pass@host:port
  scope          TEXT DEFAULT 'check',       -- check | source | both
  country        TEXT DEFAULT '',           -- scrape from proxy IP geo; used for ISO2 match
  enabled        INTEGER DEFAULT 1,
  ok_count       INTEGER DEFAULT 0,
  fail_count     INTEGER DEFAULT 0,
  last_latency_ms INTEGER DEFAULT 0,
  note           TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS corrections (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER,
  bin         TEXT,
  field       TEXT,
  value       TEXT,
  status      TEXT DEFAULT 'pending',        -- pending | applied | rejected
  created_utc TEXT DEFAULT '',
  decided_utc TEXT DEFAULT '',
  reward      INTEGER DEFAULT 0              -- credits granted on apply
);

-- Funnel + daily counters. Key convention: 'start','first_bin','first_check','first_payment','day:YYYY-MM-DD:bins'
CREATE TABLE IF NOT EXISTS stats (
  key   TEXT PRIMARY KEY,
  value INTEGER DEFAULT 0
);
"""

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import aiosqlite

from models import BIN_FIELDS

JSON_FIELDS = ("sources_json", "field_conf_json")


def _jsonify(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    for key in JSON_FIELDS:
        if key in out and not isinstance(out[key], str):
            out[key] = json.dumps(out[key], separators=(",", ":"))
    return out


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class DB:
    """One SQLite file. WAL + FTS5. Single async connection, serialized by aiosqlite."""

    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("DB not initialized")
        return self._conn

    async def init(self) -> "DB":
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.execute(
            "INSERT OR IGNORE INTO gates(name, kind, enabled) VALUES('stripe','stripe',1)"
        )
        await self._conn.commit()
        return self

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.commit()
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Cursor:
        cur = await self.conn.execute(sql, tuple(params))
        await self.conn.commit()
        return cur

    async def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> aiosqlite.Cursor:
        cur = await self.conn.executemany(sql, seq)
        await self.conn.commit()
        return cur

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        cur = await self.conn.execute(sql, tuple(params))
        row = await cur.fetchone()
        await cur.close()
        return row

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        await cur.close()
        return rows

    async def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = 0) -> Any:
        row = await self.fetchone(sql, params)
        if row is None:
            return default
        return row[0]

    # ---------- bins ----------
    async def upsert_bin(self, record: Mapping[str, Any]) -> None:
        data = _jsonify(dict(record))
        cols = [c for c in BIN_FIELDS if c in data]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "bin")
        sql = (
            f"INSERT INTO bins ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(bin) DO UPDATE SET {updates}"
        )
        await self.conn.execute(sql, tuple(data[c] for c in cols))
        await self.conn.execute("DELETE FROM bins_fts WHERE bin=?", (data.get("bin", ""),))
        await self.conn.execute(
            "INSERT INTO bins_fts(bin, brand, level, bank, country) VALUES (?,?,?,?,?)",
            (
                data.get("bin", ""),
                data.get("brand", "UNKNOWN"),
                data.get("level", "UNKNOWN"),
                data.get("bank", "UNKNOWN"),
                data.get("country", "UNKNOWN"),
            ),
        )
        await self.conn.commit()

    async def get_bin(self, bin8: str) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM bins WHERE bin=?", (bin8,))

    async def update_bin_fields(self, bin8: str, fields: Mapping[str, Any]) -> None:
        if not fields:
            return
        data = _jsonify(dict(fields))
        sets = ",".join(f"{k}=?" for k in data)
        await self.execute(f"UPDATE bins SET {sets} WHERE bin=?", (*data.values(), bin8))
        row = await self.get_bin(bin8)
        if row is not None:
            await self.conn.execute("DELETE FROM bins_fts WHERE bin=?", (bin8,))
            await self.conn.execute(
                "INSERT INTO bins_fts(bin, brand, level, bank, country) VALUES (?,?,?,?,?)",
                (bin8, row["brand"], row["level"], row["bank"], row["country"]),
            )
            await self.conn.commit()

    async def preload_bins(self, limit: int = 5000) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM bins ORDER BY verified_utc DESC LIMIT ?", (limit,)
        )

    async def search_bins(self, query: str, limit: int = 10) -> list[aiosqlite.Row]:
        tokens = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if t]
        if not tokens:
            return []
        match = " OR ".join(f"{t}*" for t in tokens)
        try:
            return await self.fetchall(
                "SELECT b.* FROM bins b JOIN bins_fts f ON b.bin=f.bin "
                "WHERE bins_fts MATCH ? LIMIT ?",
                (match, limit),
            )
        except Exception:
            like = f"%{query}%"
            return await self.fetchall(
                "SELECT * FROM bins WHERE bank LIKE ? OR brand LIKE ? OR country LIKE ? LIMIT ?",
                (like, like, like, limit),
            )

    # ---------- users ----------
    async def ensure_user(
        self, user_id: int, username: str = "", first_name: str = "", source_tag: str = ""
    ) -> aiosqlite.Row:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self.conn.execute(
            "INSERT OR IGNORE INTO users(user_id, username, first_name, joined_utc, source_tag, quota_date) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, username, first_name, now, source_tag, today_utc()),
        )
        if username or first_name:
            await self.conn.execute(
                "UPDATE users SET username=?, first_name=? WHERE user_id=?",
                (username, first_name, user_id),
            )
        if source_tag:
            await self.conn.execute(
                "UPDATE users SET source_tag=? WHERE user_id=? AND (source_tag='' OR source_tag IS NULL)",
                (source_tag, user_id),
            )
        await self.conn.commit()
        row = await self.fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))
        assert row is not None
        return row

    async def get_user(self, user_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))

    async def reset_quota_if_needed(self, user_id: int) -> aiosqlite.Row:
        row = await self.get_user(user_id)
        if row is None:
            return await self.ensure_user(user_id)
        if row["quota_date"] != today_utc():
            await self.execute(
                "UPDATE users SET quota_date=?, free_bin=0, free_check=0 WHERE user_id=?",
                (today_utc(), user_id),
            )
            row = await self.get_user(user_id)
        assert row is not None
        return row

    async def update_user(self, user_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        await self.execute(f"UPDATE users SET {sets} WHERE user_id=?", (*fields.values(), user_id))

    # ---------- checks ----------
    async def add_check(self, row: Mapping[str, Any]) -> int:
        data = dict(row)
        cols = list(data.keys())
        sql = f"INSERT INTO checks ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
        cur = await self.execute(sql, tuple(data[c] for c in cols))
        return int(cur.lastrowid or 0)

    async def recent_twofa_reads(self, bin8: str, limit: int = 20) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT created_utc, gate, twofa_read, amount_band, canary_ok, acs_host "
            "FROM checks WHERE bin=? AND twofa_read!='' ORDER BY id DESC LIMIT ?",
            (bin8, limit),
        )

    async def checks_for_bin(self, bin8: str, limit: int = 5) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM checks WHERE bin=? ORDER BY id DESC LIMIT ?", (bin8, limit)
        )

    # ---------- gates ----------
    async def get_gate(self, name: str = "stripe") -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM gates WHERE name=?", (name,))

    async def list_gates(self) -> list[aiosqlite.Row]:
        return await self.fetchall("SELECT * FROM gates ORDER BY name")

    async def update_gate(self, name: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        await self.execute(f"UPDATE gates SET {sets} WHERE name=?", (*fields.values(), name))

    # ---------- proxies ----------
    async def list_proxies(self, enabled_only: bool = False) -> list[aiosqlite.Row]:
        sql = "SELECT * FROM proxies"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id"
        return await self.fetchall(sql)

    async def add_proxy(self, url: str, scope: str = "check", country: str = "", note: str = "") -> int:
        cur = await self.execute(
            "INSERT OR IGNORE INTO proxies(url, scope, country, note) VALUES (?,?,?,?)",
            (url, scope, country, note),
        )
        return int(cur.lastrowid or 0)

    async def update_proxy(self, proxy_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        await self.execute(f"UPDATE proxies SET {sets} WHERE id=?", (*fields.values(), proxy_id))

    async def delete_proxy(self, proxy_id: int) -> None:
        await self.execute("DELETE FROM proxies WHERE id=?", (proxy_id,))

    async def check_proxies(self, iso2: str = "") -> list[aiosqlite.Row]:
        rows = await self.fetchall(
            "SELECT * FROM proxies WHERE enabled=1 AND (scope='check' OR scope='both') ORDER BY id"
        )
        if iso2:
            rows.sort(key=lambda r: (r["country"] or "").upper() != iso2.upper())
        return rows

    # ---------- corrections ----------
    async def add_correction(
        self, user_id: int, bin8: str, field: str, value: str
    ) -> int:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = await self.execute(
            "INSERT INTO corrections(user_id, bin, field, value, status, created_utc) "
            "VALUES (?,?,?,?, 'pending', ?)",
            (user_id, bin8, field, value, now),
        )
        return int(cur.lastrowid or 0)

    async def list_corrections(self, status: str = "pending", limit: int = 20) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT * FROM corrections WHERE status=? ORDER BY id LIMIT ?", (status, limit)
        )

    async def decide_correction(self, correction_id: int, status: str, reward: int = 0) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self.execute(
            "UPDATE corrections SET status=?, decided_utc=?, reward=? WHERE id=?",
            (status, now, reward, correction_id),
        )

    # ---------- receipts ----------
    async def insert_receipt(
        self, charge_id: str, user_id: int, amount_xtr: int, product: str, state: str = "paid"
    ) -> bool:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = await self.execute(
            "INSERT OR IGNORE INTO receipts(charge_id, user_id, amount_xtr, product, paid_utc, state) "
            "VALUES (?,?,?,?,?,?)",
            (charge_id, user_id, amount_xtr, product, now, state),
        )
        return cur.rowcount > 0

    async def get_receipt(self, charge_id: str) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM receipts WHERE charge_id=?", (charge_id,))

    async def mark_receipt_refunded(self, charge_id: str) -> None:
        await self.execute("UPDATE receipts SET state='refunded' WHERE charge_id=?", (charge_id,))

    # ---------- stats ----------
    async def stats_bump(self, key: str, amount: int = 1) -> None:
        await self.execute(
            "INSERT INTO stats(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=value+?",
            (key, amount, amount),
        )

    async def stats_get(self, key: str, default: int = 0) -> int:
        value = await self.scalar("SELECT value FROM stats WHERE key=?", (key,), default)
        return int(value)

    async def stats_all(self) -> dict[str, int]:
        rows = await self.fetchall("SELECT key, value FROM stats")
        return {r["key"]: int(r["value"]) for r in rows}

    async def count(self, table: str, where: str = "", params: Iterable[Any] = ()) -> int:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(await self.scalar(sql, params, 0))

    async def verdict_distribution(self) -> dict[str, int]:
        rows = await self.fetchall(
            "SELECT verdict, COUNT(*) AS n FROM checks GROUP BY verdict ORDER BY n DESC"
        )
        return {r["verdict"] or "UNKNOWN": int(r["n"]) for r in rows}

    async def cache_depth(self) -> int:
        return int(await self.scalar("SELECT COUNT(*) FROM bins", (), 0))

