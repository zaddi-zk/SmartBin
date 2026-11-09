from __future__ import annotations

"""Copy every table from the legacy SQLite file into Supabase/Postgres.

Usage (from the repo root):
    DATABASE_URL='postgresql://...' python smartbin/tools/migrate_to_postgres.py
    python smartbin/tools/migrate_to_postgres.py --sqlite smartbin/smartbin.db \
        --database-url 'postgresql://...'

Safe to run more than once: existing rows are kept (ON CONFLICT DO NOTHING),
so re-running only fills in whatever is missing.
"""

import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import DB  # noqa: E402

# Parent tables first. bins_fts* and sqlite_sequence are SQLite internals.
TABLES = ["bins", "users", "receipts", "checks", "gates", "proxies", "corrections", "stats"]
IDENTITY = {"checks": "id", "proxies": "id", "corrections": "id"}


def _sqlite_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})")]


async def migrate(sqlite_path: Path, database_url: str, dry_run: bool) -> None:
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite file not found: {sqlite_path}")
    if not database_url:
        raise SystemExit("DATABASE_URL is empty (pass --database-url or set the env var)")

    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = sqlite3.Row

    db = await DB("", database_url=database_url).init()
    try:
        for table in TABLES:
            cols = _sqlite_columns(src, table)
            rows = src.execute(f"SELECT * FROM {table}").fetchall()
            print(f"{table:12} source rows: {len(rows)}")
            if not rows:
                continue
            if dry_run:
                print(f"{table:12} dry-run: would copy {len(rows)}")
                continue
            insert = (
                f"INSERT INTO {table} ({','.join(cols)}) "
                f"VALUES ({','.join('?' for _ in cols)}) ON CONFLICT DO NOTHING"
            )
            await db.executemany(insert, [tuple(row) for row in rows])
            copied = await db.count(table)
            print(f"{table:12} destination rows: {copied}")

        if not dry_run:
            for table, column in IDENTITY.items():
                await db.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}','{column}'), "
                    f"GREATEST(COALESCE((SELECT MAX({column}) FROM {table}), 0), 1), "
                    f"(SELECT COUNT(*) FROM {table}) > 0)"
                )
            await db.execute(
                "UPDATE users SET last_seen_utc=joined_utc "
                "WHERE COALESCE(last_seen_utc,'')='' AND COALESCE(joined_utc,'')<>''"
            )

        print("\nFinal counts:")
        for table in TABLES:
            print(f"  {table:12} {await db.count(table)}")
    finally:
        src.close()
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate SMARTBIN SQLite data to Postgres/Supabase")
    parser.add_argument(
        "--sqlite",
        default=str(ROOT / "smartbin.db"),
        help="path to the legacy SQLite file (default: smartbin/smartbin.db)",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="Postgres/Supabase connection URL (default: DATABASE_URL env var)",
    )
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    asyncio.run(migrate(Path(args.sqlite), args.database_url, args.dry_run))


if __name__ == "__main__":
    main()
