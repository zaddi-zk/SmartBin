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
  country         TEXT DEFAULT 'UNKNOWN',
  iso2            TEXT DEFAULT '',
  currency        TEXT DEFAULT '',
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
  last_seen_utc TEXT DEFAULT '',             -- UTC timestamp of last interaction (monthly active)
  source_tag   TEXT DEFAULT '',              -- startapp/start deeplynk attribution. THE funnel.
  credits      INTEGER DEFAULT 0,
  premium_until TEXT DEFAULT '',             -- ISO; sub renewals extend via receipts
  quota_date   TEXT DEFAULT '',             -- UTC date for daily free quota reset
  free_bin     INTEGER DEFAULT 0,
  free_check   INTEGER DEFAULT 0,
  bonus_check  INTEGER DEFAULT 0,           -- referral/promo checks, spend before credits
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

CREATE TABLE IF NOT EXISTS bin_hits (
  bin          TEXT PRIMARY KEY,
  attempts     INTEGER DEFAULT 0,
  approved     INTEGER DEFAULT 0,
  users        INTEGER DEFAULT 0,
  last_hit_utc TEXT DEFAULT ''
);

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

-- Per-user, per-day, per-item dedupe. Inline mode re-sends the same query
-- while the user types; one BIN must never be charged twice in a day.
CREATE TABLE IF NOT EXISTS usage (
  user_id INTEGER NOT NULL,
  day     TEXT NOT NULL,
  kind    TEXT NOT NULL,
  key     TEXT NOT NULL,
  PRIMARY KEY (user_id, day, kind, key)
);

-- One referral reward per referee, ever. Replaying /start ref_<id> must not
-- mint unlimited bonus checks.
CREATE TABLE IF NOT EXISTS referrals (
  referee_id  INTEGER PRIMARY KEY,
  referrer_id INTEGER NOT NULL,
  created_utc TEXT DEFAULT ''
);
"""

# Postgres / Supabase schema. Same columns as the SQLite schema, in order,
# minus the FTS5 virtual table (search uses ILIKE instead).
PG_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS bins (
      bin              TEXT PRIMARY KEY,
      brand            TEXT DEFAULT 'UNKNOWN',
      level            TEXT DEFAULT 'UNKNOWN',
      bank             TEXT DEFAULT 'UNKNOWN',
      phone            TEXT DEFAULT '',
      site             TEXT DEFAULT '',
      country          TEXT DEFAULT 'UNKNOWN',
      iso2             TEXT DEFAULT '',
      currency         TEXT DEFAULT '',
      prepaid          INTEGER DEFAULT 0,
      commercial       INTEGER DEFAULT 0,
      bin_age          INTEGER DEFAULT 0,
      card_length      INTEGER DEFAULT 16,
      luhn             INTEGER DEFAULT 1,
      dob              INTEGER DEFAULT 0,
      ssn              INTEGER DEFAULT 0,
      mmn              INTEGER DEFAULT 0,
      avs              INTEGER DEFAULT 1,
      sources_json     TEXT DEFAULT '{}',
      field_conf_json  TEXT DEFAULT '{}',
      confidence       DOUBLE PRECISION DEFAULT 0.0,
      verified_utc     TEXT DEFAULT '',
      sample_size      INTEGER DEFAULT 0,
      corrections      INTEGER DEFAULT 0,
      twofa            TEXT DEFAULT 'UNKNOWN',
      twofa_conf       DOUBLE PRECISION DEFAULT 0.0,
      twofa_reads      INTEGER DEFAULT 0,
      last_twofa_utc   TEXT DEFAULT '',
      amt_tested       INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS users (
      user_id       BIGINT PRIMARY KEY,
      username      TEXT DEFAULT '',
      first_name    TEXT DEFAULT '',
      joined_utc    TEXT DEFAULT '',
      last_seen_utc TEXT DEFAULT '',
      source_tag    TEXT DEFAULT '',
      credits       INTEGER DEFAULT 0,
      premium_until TEXT DEFAULT '',
      quota_date    TEXT DEFAULT '',
      free_bin      INTEGER DEFAULT 0,
      free_check    INTEGER DEFAULT 0,
      bonus_check   INTEGER DEFAULT 0,
      stars_total   INTEGER DEFAULT 0,
      corrections   INTEGER DEFAULT 0,
      is_banned     INTEGER DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_users_premium ON users(premium_until)",
    """CREATE TABLE IF NOT EXISTS receipts (
      charge_id  TEXT PRIMARY KEY,
      user_id    BIGINT,
      amount_xtr INTEGER DEFAULT 0,
      product    TEXT,
      paid_utc   TEXT DEFAULT '',
      state      TEXT DEFAULT 'paid'
    )""",
    """CREATE TABLE IF NOT EXISTS checks (
      id           BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
      user_id      BIGINT,
      bin          TEXT,
      gate         TEXT,
      attempt      INTEGER DEFAULT 1,
      verdict      TEXT DEFAULT '',
      action       TEXT DEFAULT '',
      live         INTEGER DEFAULT 0,
      raw_code     TEXT DEFAULT '',
      decline_code TEXT DEFAULT '',
      twofa_read   TEXT DEFAULT '',
      amount_band  INTEGER DEFAULT 0,
      canary_ok    INTEGER DEFAULT -1,
      acs_host     TEXT DEFAULT '',
      latency_ms   INTEGER DEFAULT 0,
      proxy_id     INTEGER DEFAULT 0,
      note         TEXT DEFAULT '',
      created_utc  TEXT DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS idx_checks_bin ON checks(bin)",
    "CREATE INDEX IF NOT EXISTS idx_checks_user ON checks(user_id)",
    """CREATE TABLE IF NOT EXISTS bin_hits (
      bin          TEXT PRIMARY KEY,
      attempts     INTEGER DEFAULT 0,
      approved     INTEGER DEFAULT 0,
      users        INTEGER DEFAULT 0,
      last_hit_utc TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS gates (
      name            TEXT PRIMARY KEY,
      kind            TEXT DEFAULT 'stripe',
      enabled         INTEGER DEFAULT 1,
      breaker_fails   INTEGER DEFAULT 0,
      breaker_until   TEXT DEFAULT '',
      canary_log      TEXT DEFAULT '[]',
      last_canary_utc TEXT DEFAULT '',
      window_checks   INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS proxies (
      id              BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
      url             TEXT UNIQUE,
      scope           TEXT DEFAULT 'check',
      country         TEXT DEFAULT '',
      enabled         INTEGER DEFAULT 1,
      ok_count        INTEGER DEFAULT 0,
      fail_count      INTEGER DEFAULT 0,
      last_latency_ms INTEGER DEFAULT 0,
      note            TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS corrections (
      id          BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
      user_id     BIGINT,
      bin         TEXT,
      field       TEXT,
      value       TEXT,
      status      TEXT DEFAULT 'pending',
      created_utc TEXT DEFAULT '',
      decided_utc TEXT DEFAULT '',
      reward      INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS stats (
      key   TEXT PRIMARY KEY,
      value INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS usage (
      user_id BIGINT NOT NULL,
      day     TEXT NOT NULL,
      kind    TEXT NOT NULL,
      key     TEXT NOT NULL,
      PRIMARY KEY (user_id, day, kind, key)
    )""",
    """CREATE TABLE IF NOT EXISTS referrals (
      referee_id  BIGINT PRIMARY KEY,
      referrer_id BIGINT NOT NULL,
      created_utc TEXT DEFAULT ''
    )""",
]

import json
from datetime import datetime, timedelta, timezone
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


def _rowcount(result: Any) -> int:
    """UPDATE/DELETE affected-row count across SQLite cursor and psycopg int."""
    if isinstance(result, int):
        return result
    try:
        return int(result.rowcount)  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return 0


def _pg_sql(sql: str) -> str:
    """Translate the codebase's qmark placeholders to psycopg's %s."""
    return sql.replace("?", "%s")


class DB:
    """SQLite file by default; Supabase/Postgres when DATABASE_URL is set.

    Both backends expose the same async API, so handlers never care which
    one is active. Set DATABASE_URL to switch without touching any other code.
    """

    def __init__(self, path: str, database_url: str = ""):
        self.path = path
        self.database_url = (database_url or "").strip()
        self.is_postgres = bool(self.database_url)
        self._conn: aiosqlite.Connection | None = None
        self._pool: Any = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("DB not initialized")
        return self._conn

    async def init(self) -> "DB":
        if self.is_postgres:
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool

            self._pool = AsyncConnectionPool(
                conninfo=self.database_url,
                min_size=1,
                max_size=5,
                open=False,
                kwargs={
                    "autocommit": True,
                    "row_factory": dict_row,
                    "prepare_threshold": None,
                    "connect_timeout": 15,
                },
            )
            await self._pool.open()
            await self._pool.wait(timeout=30)
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    for statement in PG_SCHEMA:
                        await cur.execute(statement)
            await self.execute(
                "INSERT INTO gates(name, kind, enabled) VALUES('stripe','stripe',1) "
                "ON CONFLICT DO NOTHING"
            )
            await self.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_utc TEXT DEFAULT ''"
            )
            await self.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_check INTEGER DEFAULT 0"
            )
            await self.execute(
                "UPDATE users SET last_seen_utc=joined_utc "
                "WHERE COALESCE(last_seen_utc,'')='' AND COALESCE(joined_utc,'')<>''"
            )
            return self

        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._migrate_sqlite()
        await self._conn.execute(
            "INSERT INTO gates(name, kind, enabled) VALUES('stripe','stripe',1) "
            "ON CONFLICT DO NOTHING"
        )
        await self._conn.commit()
        return self

    async def _migrate_sqlite(self) -> None:
        """Add columns introduced after the first release, in place."""
        cur = await self._conn.execute("PRAGMA table_info(users)")
        cols = {row[1] for row in await cur.fetchall()}
        await cur.close()
        if "last_seen_utc" not in cols:
            await self._conn.execute(
                "ALTER TABLE users ADD COLUMN last_seen_utc TEXT DEFAULT ''"
            )
        if "bonus_check" not in cols:
            await self._conn.execute(
                "ALTER TABLE users ADD COLUMN bonus_check INTEGER DEFAULT 0"
            )
        await self._conn.execute(
            "UPDATE users SET last_seen_utc=joined_utc "
            "WHERE COALESCE(last_seen_utc,'')='' AND COALESCE(joined_utc,'')<>''"
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        if self._conn is not None:
            await self._conn.commit()
            await self._conn.close()
            self._conn = None

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        if self.is_postgres:
            async with self._pool.connection() as conn:
                cur = await conn.execute(_pg_sql(sql), tuple(params))
                count = cur.rowcount
                await cur.close()
            return count
        cur = await self.conn.execute(sql, tuple(params))
        await self.conn.commit()
        return cur

    async def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> Any:
        if self.is_postgres:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.executemany(
                        _pg_sql(sql), [tuple(row) for row in seq]
                    )
            return None
        cur = await self.conn.executemany(sql, seq)
        await self.conn.commit()
        return cur

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> Any:
        if self.is_postgres:
            async with self._pool.connection() as conn:
                cur = await conn.execute(_pg_sql(sql), tuple(params))
                row = await cur.fetchone()
                await cur.close()
                return row
        cur = await self.conn.execute(sql, tuple(params))
        row = await cur.fetchone()
        await cur.close()
        return row

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[Any]:
        if self.is_postgres:
            async with self._pool.connection() as conn:
                cur = await conn.execute(_pg_sql(sql), tuple(params))
                rows = await cur.fetchall()
                await cur.close()
                return rows
        cur = await self.conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        await cur.close()
        return rows

    async def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = 0) -> Any:
        row = await self.fetchone(sql, params)
        if row is None:
            return default
        if isinstance(row, Mapping):
            for value in row.values():
                return value
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
        await self.execute(sql, tuple(data[c] for c in cols))
        if not self.is_postgres:
            await self.execute("DELETE FROM bins_fts WHERE bin=?", (data.get("bin", ""),))
            await self.execute(
                "INSERT INTO bins_fts(bin, brand, level, bank, country) VALUES (?,?,?,?,?)",
                (
                    data.get("bin", ""),
                    data.get("brand", "UNKNOWN"),
                    data.get("level", "UNKNOWN"),
                    data.get("bank", "UNKNOWN"),
                    data.get("country", "UNKNOWN"),
                ),
            )

    async def get_bin(self, bin8: str) -> Any:
        return await self.fetchone("SELECT * FROM bins WHERE bin=?", (bin8,))

    async def update_bin_fields(self, bin8: str, fields: Mapping[str, Any]) -> None:
        if not fields:
            return
        data = _jsonify(dict(fields))
        sets = ",".join(f"{k}=?" for k in data)
        await self.execute(f"UPDATE bins SET {sets} WHERE bin=?", (*data.values(), bin8))
        if self.is_postgres:
            return
        row = await self.get_bin(bin8)
        if row is not None:
            await self.execute("DELETE FROM bins_fts WHERE bin=?", (bin8,))
            await self.execute(
                "INSERT INTO bins_fts(bin, brand, level, bank, country) VALUES (?,?,?,?,?)",
                (bin8, row["brand"], row["level"], row["bank"], row["country"]),
            )

    async def preload_bins(self, limit: int = 5000) -> list[Any]:
        return await self.fetchall(
            "SELECT * FROM bins ORDER BY verified_utc DESC LIMIT ?", (limit,)
        )

    async def search_bins(self, query: str, limit: int = 10) -> list[Any]:
        if self.is_postgres:
            like = f"%{query}%"
            return await self.fetchall(
                "SELECT * FROM bins WHERE bin ILIKE ? OR brand ILIKE ? OR level ILIKE ? "
                "OR bank ILIKE ? OR country ILIKE ? ORDER BY bin LIMIT ?",
                (like, like, like, like, like, limit),
            )
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
    ) -> Any:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self.execute(
            "INSERT INTO users(user_id, username, first_name, joined_utc, source_tag, quota_date, last_seen_utc) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (user_id, username, first_name, now, source_tag, today_utc(), now),
        )
        await self.execute(
            "UPDATE users SET last_seen_utc=? WHERE user_id=?", (now, user_id)
        )
        if username or first_name:
            await self.execute(
                "UPDATE users SET username=?, first_name=? WHERE user_id=?",
                (username, first_name, user_id),
            )
        if source_tag:
            await self.execute(
                "UPDATE users SET source_tag=? WHERE user_id=? AND (source_tag='' OR source_tag IS NULL)",
                (source_tag, user_id),
            )
        row = await self.fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))
        assert row is not None
        return row

    async def get_user(self, user_id: int) -> Any:
        return await self.fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))

    async def monthly_users(self, days: int = 30) -> int:
        """Users who used the bot in the last N days.

        Rows created before activity tracking existed have an empty
        last_seen_utc, so fall back to joined_utc instead of dropping them.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return await self.count(
            "users",
            "COALESCE(NULLIF(last_seen_utc, ''), joined_utc) >= ?",
            (cutoff,),
        )

    async def touch_user(
        self, user_id: int, username: str = "", first_name: str = ""
    ) -> None:
        """Record activity, throttled to at most one write per hour per user."""
        now_dt = datetime.now(timezone.utc)
        now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        cutoff = (now_dt - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self.execute(
            "INSERT INTO users(user_id, username, first_name, joined_utc, source_tag, quota_date, last_seen_utc) "
            "VALUES (?,?,?,?,'',?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_seen_utc=excluded.last_seen_utc "
            "WHERE users.last_seen_utc='' OR users.last_seen_utc < ?",
            (user_id, username, first_name, now, today_utc(), now, cutoff),
        )

    async def reset_quota_if_needed(self, user_id: int) -> Any:
        """Roll the daily counters atomically when the UTC date changes."""
        await self.reset_free_quota(user_id)
        row = await self.get_user(user_id)
        if row is None:
            return await self.ensure_user(user_id)
        return row

    async def reset_free_quota(self, user_id: int) -> None:
        today = today_utc()
        await self.execute(
            "UPDATE users SET quota_date=?, free_bin=0, free_check=0 "
            "WHERE user_id=? AND COALESCE(quota_date,'')<>?",
            (today, user_id, today),
        )

    # ---------- quota claims (single conditional UPDATE = race-free) ----------
    async def claim_free(self, user_id: int, kind: str, limit: int) -> bool:
        """Take one free unit only if today's allowance still has room.

        Bonus checks are a separate finite balance (`claim_bonus_check`) so a
        one-off referral reward can never turn into a permanent higher limit.
        """
        if kind not in ("bin", "check"):
            raise ValueError(f"unknown quota kind: {kind}")
        limit = max(0, int(limit))
        col = f"free_{kind}"
        result = await self.execute(
            f"UPDATE users SET {col}={col}+1 "
            f"WHERE user_id=? AND quota_date=? AND {col} < ?",
            (user_id, today_utc(), limit),
        )
        return _rowcount(result) == 1

    async def claim_bonus_check(self, user_id: int) -> bool:
        """Spend one referral/promo check from the finite bonus balance."""
        result = await self.execute(
            "UPDATE users SET bonus_check=bonus_check-1 "
            "WHERE user_id=? AND bonus_check>0",
            (user_id,),
        )
        return _rowcount(result) == 1

    async def claim_credit(self, user_id: int) -> bool:
        """Spend one credit only if the balance is still positive."""
        result = await self.execute(
            "UPDATE users SET credits=credits-1 WHERE user_id=? AND credits>0",
            (user_id,),
        )
        return _rowcount(result) == 1

    async def refund_free(self, user_id: int, kind: str) -> None:
        if kind not in ("bin", "check"):
            raise ValueError(f"unknown quota kind: {kind}")
        col = f"free_{kind}"
        await self.execute(
            f"UPDATE users SET {col}=CASE WHEN {col}>0 THEN {col}-1 ELSE 0 END "
            f"WHERE user_id=? AND quota_date=?",
            (user_id, today_utc()),
        )

    async def refund_credit(self, user_id: int) -> None:
        await self.execute(
            "UPDATE users SET credits=credits+1 WHERE user_id=?", (user_id,)
        )

    async def refund_bonus_check(self, user_id: int) -> None:
        await self.execute(
            "UPDATE users SET bonus_check=bonus_check+1 WHERE user_id=?", (user_id,)
        )

    async def mark_daily_usage(
        self, user_id: int, kind: str, key: str, day: str | None = None
    ) -> bool:
        """Return True only the first time this item is seen for the user today."""
        result = await self.execute(
            "INSERT INTO usage(user_id, day, kind, key) VALUES (?,?,?,?) "
            "ON CONFLICT DO NOTHING",
            (user_id, day or today_utc(), kind, key),
        )
        return _rowcount(result) == 1

    async def unmark_daily_usage(self, user_id: int, kind: str, key: str) -> None:
        await self.execute(
            "DELETE FROM usage WHERE user_id=? AND day=? AND kind=? AND key=?",
            (user_id, today_utc(), kind, key),
        )

    async def grant_referral_bonus(self, referee_id: int, referrer_id: int) -> bool:
        """Grant 5 bonus checks to both sides, once per referee.

        Bonus checks live outside `free_check` so they survive the daily reset
        and are never mistaken for "already used the free allowance". A replay
        of `/start ref_<id>` is rejected by the `referrals` primary key.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        inserted = await self.execute(
            "INSERT INTO referrals(referee_id, referrer_id, created_utc) VALUES (?,?,?) "
            "ON CONFLICT DO NOTHING",
            (referee_id, referrer_id, now),
        )
        if _rowcount(inserted) != 1:
            return False
        for uid in {referee_id, referrer_id}:
            await self.execute(
                "UPDATE users SET bonus_check=bonus_check+5 WHERE user_id=?",
                (uid,),
            )
        return True

    async def update_user(self, user_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        await self.execute(f"UPDATE users SET {sets} WHERE user_id=?", (*fields.values(), user_id))

    async def user_spend(self, user_id: int, amount: int = 1) -> bool:
        """Atomically spend credits; never drives the balance negative."""
        amount = max(0, int(amount))
        if amount == 0:
            return True
        result = await self.execute(
            "UPDATE users SET credits=credits-? WHERE user_id=? AND credits>=?",
            (amount, user_id, amount),
        )
        return _rowcount(result) == 1

    async def user_grant(self, user_id: int, amount: int = 1) -> None:
        amount = max(0, int(amount))
        if amount:
            await self.execute(
                "UPDATE users SET credits=credits+? WHERE user_id=?", (amount, user_id)
            )

    # ---------- checks ----------
    async def add_check(self, row: Mapping[str, Any]) -> int:
        data = dict(row)
        cols = list(data.keys())
        placeholders = ",".join("?" for _ in cols)
        if self.is_postgres:
            sql = (
                f"INSERT INTO checks ({','.join(cols)}) VALUES ({placeholders}) "
                "RETURNING id"
            )
            result = await self.fetchone(sql, tuple(data[c] for c in cols))
            return int(result["id"]) if result is not None else 0
        sql = f"INSERT INTO checks ({','.join(cols)}) VALUES ({placeholders})"
        cur = await self.execute(sql, tuple(data[c] for c in cols))
        return int(cur.lastrowid or 0)

    async def recent_twofa_reads(self, bin8: str, limit: int = 20) -> list[Any]:
        return await self.fetchall(
            "SELECT created_utc, gate, twofa_read, amount_band, canary_ok, acs_host "
            "FROM checks WHERE bin=? AND twofa_read!='' ORDER BY id DESC LIMIT ?",
            (bin8, limit),
        )

    async def checks_for_bin(self, bin8: str, limit: int = 5) -> list[Any]:
        return await self.fetchall(
            "SELECT * FROM checks WHERE bin=? ORDER BY id DESC LIMIT ?", (bin8, limit)
        )

    async def get_bin_hits(self, bin8: str) -> Any:
        return await self.fetchone(
            "SELECT * FROM bin_hits WHERE bin=?", (bin8[:8],)
        )

    async def bump_bin_hits(self, bin8: str, approved: bool, user_id: int = 0) -> None:
        """One attempt per dispatch. Only the executor calls this."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self.execute(
            "INSERT INTO bin_hits(bin, attempts, approved, users, last_hit_utc) "
            "VALUES(?,1,?,?,?) ON CONFLICT(bin) DO UPDATE SET "
            "attempts=bin_hits.attempts+1, "
            "approved=bin_hits.approved+excluded.approved, "
            "users=CASE WHEN bin_hits.users=0 AND excluded.users>0 "
            "THEN excluded.users ELSE bin_hits.users END, "
            "last_hit_utc=excluded.last_hit_utc",
            (bin8[:8], 1 if approved else 0, 1 if user_id else 0, now),
        )

    async def twofa_challenges(self, bin8: str) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) AS n FROM checks WHERE bin=? AND twofa_read='VBV'",
            (bin8[:8],),
        )
        if row is None:
            return 0
        try:
            return int(row["n"])
        except (KeyError, TypeError):
            return int(row[0])

    async def last_twofa_state(self, bin8: str) -> str:
        row = await self.fetchone(
            "SELECT twofa_read FROM checks WHERE bin=? AND twofa_read!='' "
            "ORDER BY id DESC LIMIT 1",
            (bin8[:8],),
        )
        if row is None:
            return ""
        try:
            value = str(row["twofa_read"])
        except (KeyError, TypeError, IndexError):
            value = str(row[0])
        if value == "VBV":
            return "YES"
        if value in ("NON-VBV", "NON-VBV*", "LOW-CONF", "PARTIAL"):
            return "NO"
        return ""

    # ---------- gates ----------
    async def get_gate(self, name: str = "stripe") -> Any:
        return await self.fetchone("SELECT * FROM gates WHERE name=?", (name,))

    async def list_gates(self) -> list[Any]:
        return await self.fetchall("SELECT * FROM gates ORDER BY name")

    async def update_gate(self, name: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        await self.execute(f"UPDATE gates SET {sets} WHERE name=?", (*fields.values(), name))

    # ---------- proxies ----------
    async def list_proxies(self, enabled_only: bool = False) -> list[Any]:
        sql = "SELECT * FROM proxies"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id"
        return await self.fetchall(sql)

    async def add_proxy(self, url: str, scope: str = "check", country: str = "", note: str = "") -> int:
        sql = "INSERT INTO proxies(url, scope, country, note) VALUES (?,?,?,?) ON CONFLICT DO NOTHING"
        params = (url, scope, country, note)
        if self.is_postgres:
            result = await self.fetchone(sql + " RETURNING id", params)
            return int(result["id"]) if result is not None else 0
        cur = await self.execute(sql, params)
        return int(cur.lastrowid or 0)

    async def update_proxy(self, proxy_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        await self.execute(f"UPDATE proxies SET {sets} WHERE id=?", (*fields.values(), proxy_id))

    async def delete_proxy(self, proxy_id: int) -> None:
        await self.execute("DELETE FROM proxies WHERE id=?", (proxy_id,))

    async def check_proxies(self, iso2: str = "") -> list[Any]:
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
        sql = (
            "INSERT INTO corrections(user_id, bin, field, value, status, created_utc) "
            "VALUES (?,?,?,?, 'pending', ?)"
        )
        params = (user_id, bin8, field, value, now)
        if self.is_postgres:
            result = await self.fetchone(sql + " RETURNING id", params)
            return int(result["id"]) if result is not None else 0
        cur = await self.execute(sql, params)
        return int(cur.lastrowid or 0)

    async def list_corrections(self, status: str = "pending", limit: int = 20) -> list[Any]:
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
        sql = (
            "INSERT INTO receipts(charge_id, user_id, amount_xtr, product, paid_utc, state) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING"
        )
        params = (charge_id, user_id, amount_xtr, product, now, state)
        if self.is_postgres:
            return await self.fetchone(sql + " RETURNING charge_id", params) is not None
        cur = await self.execute(sql, params)
        return cur.rowcount > 0

    async def get_receipt(self, charge_id: str) -> Any:
        return await self.fetchone("SELECT * FROM receipts WHERE charge_id=?", (charge_id,))

    async def mark_receipt_refunded(self, charge_id: str) -> None:
        await self.execute("UPDATE receipts SET state='refunded' WHERE charge_id=?", (charge_id,))

    # ---------- stats ----------
    async def stats_bump(self, key: str, amount: int = 1) -> None:
        await self.execute(
            "INSERT INTO stats(key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=stats.value+?",
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
