# SMARTBIN v1.0.0

Telegram card intelligence engine: cache-first BIN metadata, measured 2FA, canary-monitored
auth-and-void checks, Stars payments, and a single-process aiohttp Mini App.

One repo, one process, one entrypoint: `bot.py`.

## Requirements

- Python 3.11+
- SQLite with FTS5 (the stock Python build is enough) **or** any Postgres 14+
  database such as Supabase
- No Docker required

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp smartbin/.env.example smartbin/.env
# edit smartbin/.env: BOT_TOKEN and ADMIN_ID are required
python bot.py
```

The process runs Telegram polling, the Mini App HTTP server, and the canary daemon together.

## Database: SQLite or Supabase/Postgres

By default the bot uses the SQLite file in `DB_PATH`. Set `DATABASE_URL` and the
same code runs on Postgres/Supabase instead — no other changes needed. The schema
is created automatically on first boot.

```bash
# Supabase dashboard -> Connect -> Session pooler / direct
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

On Render, add `DATABASE_URL` as a secret environment variable and redeploy.
To move the existing SQLite rows (users, payments/receipts, checks, bins, gates,
proxies, corrections, stats) into Supabase:

```bash
DATABASE_URL='postgresql://...' python smartbin/tools/migrate_to_postgres.py \
  --sqlite smartbin/smartbin.db
```

The migration is idempotent: re-running only fills in missing rows.

## Environment

| Key | Meaning |
| --- | --- |
| `BOT_TOKEN` | Telegram bot token |
| `ADMIN_ID` | Numeric Telegram user id with admin access |
| `DB_PATH` | SQLite file (default `smartbin.db`) |
| `DATABASE_URL` | Supabase/Postgres URL; when set it overrides `DB_PATH` |
| `MINIAPP_PORT` / `MINIAPP_BASE` | aiohttp bind port and public URL |
| `STRIPE_API_KEY` | Stripe secret key for live mode |
| `STRIPE_TEST_MODE` | `1` = deterministic test cards, no money |
| `CANARY_NONVBV` / `CANARY_VBV` / `CANARY_DEAD` | `number\|MM\|YY\|CVV` canary cards |
| `FREE_BINS_PER_DAY` / `FREE_CHECKS_PER_DAY` | Daily free quota |
| `STARS_DAY` / `STARS_MONTH` / `STARS_CREDITS_500` | XTR prices |
| `CORRECTION_REWARD` | Credits for an applied correction |
| `LRU_SIZE` | In-process BIN cache size |
| `BULK_FREE_LINES` / `BULK_PREMIUM_LINES` | Bulk caps |
| `USDT_TRC20` | Display-only wallet |
| `PREMIUM_EMOJI_ID` | Optional custom emoji id for headers |

## Button-first UX

The reply keyboard is the main interface. Tap a button, then send the input:

| Button | What happens |
| --- | --- |
| 🃏 BIN Lookup | Prompt for a 6/8 digit BIN, then render the card with inline actions |
| ⚡ Live Check | Prompt for `number\|MM\|YY\|CVV`, show a masked confirmation, then dispatch |
| 📦 Bulk / CSV | Prompt for one card per line, confirm, then return fixed-width results and CSV |
| 📊 Status | Live uptime, latency, cache depth, and anonymized gate telemetry |
| 🐞 Fix Record | BIN -> field picker -> value -> queued correction |
| 🔒 Premium | Stars store and credit packs |
| 🎲 Play | Animated dice |
| 🎛 Admin Panel | Admin dashboard (only visible to `ADMIN_ID`) |

You can also send a BIN or card directly without pressing a button. Slash commands still work.
Every check confirms the masked card before dispatch, and every path writes a `checks` row.

## Commands

- `/start` main menu, premium emoji header, live dice, admin-only button
- `/bin <6 or 8 digits>` BIN card; inline mode `@SmartBlN_Bot <bin>`
- `/check <number|MM|YY|CVV>` full check state machine
- `/bulk` paste cards, get fixed-width output plus CSV
- `/status`, `/why <bin>`, `/gates`, `/correct <bin> <field> <value>`
- `/premium` Stars store (day, monthly subscription, 500 credits)
- `/admin` dashboard, proxies, gates, corrections, credits, broadcast, refunds

Free results carry `-- @SmartBlN_Bot`. Data blocks are fixed-width `<pre>`, no emoji or bold.
Unknown fields render `UNKNOWN`; fields below 60% confidence render `NAME LOW-CONF`.

Check verdicts lead with a status banner outside the data block: `🟢 LIVE`, `🔴 DEAD`, or
`🟡 INCONCLUSIVE / CORE DEGRADED`, followed by the gate health (`gate-1 HEALTHY · canary 100%`).
The block itself stays ASCII and includes `ROUTE auto-route · ISO2 first`. Gate identity is
anonymized for users and visible by name only in the admin panel.

## Admin flow

1. Set `ADMIN_ID` to your numeric Telegram id.
2. `/admin` opens the dashboard.
3. Add a check proxy before live checks: Proxy Pool -> Add -> `url scope country note`.
   `scope=check` for dispatch, `scope=source` for source retries, `both` for either.
4. Gates: toggle, run canary now, reset breaker. Canary history is stored per gate.
5. Corrections: approve applies the field, increments the BIN correction count, and grants credits.
6. Credits: manual USDT fulfillment. Broadcast: text to every user. Refund: Stars charge id.

## Live vs test mode

`STRIPE_TEST_MODE=1` is the default and uses the seven public Stripe test cards. It never
moves money and never needs a Stripe key. To go live:

1. Set `STRIPE_API_KEY` to a live secret key.
2. Set `STRIPE_TEST_MODE=0`.
3. Add check proxies (live checks refuse to run from the bare server IP).
4. Replace canary cards in `.env` with live non-VBV, VBV, and known-dead cards.
5. Restart. The first canary window runs at boot; user checks stay `INCONCLUSIVE` until it passes.

## Smoke test

```bash
cd smartbin
python smoke.py
```

`smoke.py` is offline: temporary SQLite DB, seed-only sources, deterministic Stripe test
cards, no Telegram calls. It verifies the verdict matrix, warm lookup p95, unknown/LOW-CONF
rendering, gate-disabled write-back, paywall position, admin visibility, Mini App 401/200,
receipt idempotency, WAL, LRU rehydration, canary cadence, and both unit suites.

## Unit tests

```bash
cd smartbin
python -m unittest discover -s tests -v
```

`tests/test_reconciler.py` covers agreement, disagreement penalty, decay at 30/90 days, and
level normalization. `tests/test_verdicts.py` covers the network/Stripe matrices and all ten
M1 2FA rules.

## Mini App

`GET /` serves `miniapp/static/index.html` (single file, no CDN). `GET /api/bin` and
`GET|POST /api/status` require Telegram `initData` HMAC-SHA256 verification; unsigned requests
get `401`. The bot sets the chat menu button to the Mini App at startup.

## Notes

- BINs are always stored as 8 digits. 6-digit input is a prefix lookup and is not persisted.
- Every check path writes a `checks` row, including parse failures and inconclusive results.
- Gate identity is private on user surfaces; `/status` labels them `gate-1`, `gate-2`.
- No `print()`: logs are structured `key=value` lines on stdout.
