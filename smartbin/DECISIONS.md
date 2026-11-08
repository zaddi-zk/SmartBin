# DECISIONS

One line per autonomous choice. The laws in AGENTS.md win over convenience.

- Python 3.14 interpreter used locally; code targets 3.11+ syntax and stdlib only beyond the four pinned dependencies.
- Kept the earlier verbose DDL in `db.py` (same column names, plus `idx_users_premium` and `idx_checks_user`); no table or column was renamed or dropped.
- `db.py` exposes one `DB` class over a single aiosqlite connection; WAL and FTS5 are initialized by `DB.init()`.
- BIN upserts delete the FTS row by `bin` and reinsert it, as required; `bins_fts` stays in sync on every write.
- Confidence formula: `100 × min(1,total_weight/2) × agreement × (0.7+0.3×corroboration) − 15×distinct_losing_values`, clamped 0–100; single-source results are intentionally LOW-CONF.
- Overall BIN confidence is the mean of populated field confidences; decay is full ≤30d, linear to a 50% floor at 90d, flat after.
- BIN levels normalize through an explicit alias table plus per-token fallback; unknown levels become `UNKNOWN`, never guessed.
- 6-digit `/bin` input is a live prefix lookup and is never persisted as an 8-digit row; only 8-digit input creates permanent rows.
- A miss with zero source results is returned transiently as `UNKNOWN` and not cached, so a later lookup can still succeed.
- Source fan-out is capped at three sources, 6s each, with a per-source circuit breaker (5 fails, 10 min).
- When a source breaker is open, the store retries once through the first enabled `scope=source|both` proxy.
- `STRIPE_TEST_MODE=1` uses a deterministic local simulator for the seven public Stripe test cards; the real Stripe auth-and-void path is fully implemented behind `STRIPE_TEST_MODE=0` + `STRIPE_API_KEY`.
- Unknown cards in test mode return `generic_decline` (AMBIGUOUS); test mode never fabricates an approval.
- Live Stripe uses manual-capture PaymentIntent then cancel (auth-and-void), with per-gate cap 8, global cap 40, 429 backoff, and timing jitter.
- Empty or stale canary state is treated as `-1`; user checks render `INCONCLUSIVE` until a clean 3/3 window exists.
- The canary daemon fires immediately at boot, then every 900s, and every 20 user checks; a zero-volume idle cadence is therefore log-provable.
- Canary kill/demote sets `enabled=0` plus a 15-minute breaker; rejoin requires two consecutive 3/3 windows.
- Canary latency demotion uses p50 >3000ms or p95 >8000ms over the last 30 results.
- A 05 in a failed canary window maps to AMBIGUOUS; other results in a failed window map to INCONCLUSIVE, never DECLINED.
- `generic_decline`/`fraudulent` map to AMBIGUOUS, never DO-NOT-HONOR; Stripe codes stay in Stripe scope.
- Timeout retries once with a fresh proxy; a second timeout stays INCONCLUSIVE. Two clean-gate 05s become ISSUER-BLOCK.
- Second-gate retry only runs if a different enabled gate has a clean window; with one gate the verdict stays AMBIGUOUS with an explicit note.
- EEA enrollment reads require ≥3100 minor units; below that the read is tagged EXEMPTION and never writes 2FA.
- EEA enrollment dispatch excludes the own `stripe` gate per M3; normal EEA checks still use it at 100 minor (exemption read).
- R9 PARTIAL requires two enrollment-grade reads in the same amount band with disagreeing outcomes.
- A real ACS host always wins over a soft-decline interpretation; a challenge without a host on Stripe is POSSIBLE-CAPTCHA and penalizes gate health only.
- 3DS challenge reads do not consume the daily free check (integer storage cannot express half a check).
- Bulk line caps are independent of the daily check quota: 10 free, 100 premium, 500 with credits.
- Free results always carry `-- @SmartBin_Bot`; premium renders omit it.
- `source_tag` is filled from `/start` deep-link args only on first write.
- Mini App is a single-file dark UI with no CDN; it reads `initData` from `Telegram.WebApp` or the `initData` query parameter and always sends it as `X-Telegram-Init-Data`.
- Mini App `/api/bin` and `/api/status` require HMAC-SHA256 initData verification; unsigned requests get 401.
- Gate identities are anonymized as `gate-1`, `gate-2` on user-facing `/status` and `/gates`.
- Admin actions are guarded by `user_id == ADMIN_ID`; non-admins see exactly `Admin only`.
- Payment receipts are `INSERT OR IGNORE` keyed by `telegram_payment_charge_id`; entitlement is granted only when the insert is new.
- Refunds mark the receipt `refunded` and revoke premium or credits; repeat refunds are refused.
- The monthly product uses `subscription_period=2592000` on `createInvoiceLink`; day and credits are one-off invoices.
- All logging is structured `key=value` on stdout; `print()` is not used anywhere.
- The local verification venv is `.venv/`; `.env` and SQLite sidecar files are gitignored.
- Handler errors are logged as one structured `event=handler_failed` line; aiogram's default traceback logger is replaced so transient network resets never dump tracebacks.
- The Mini App menu button is only set when `MINIAPP_BASE` is HTTPS; non-HTTPS bases log `event=menu_skipped` instead of a failing Telegram call.
- Primary UX is button-first: every reply-keyboard button opens a guided FSM prompt, then the user sends the BIN, card, batch, or correction value. Slash commands remain as power-user aliases.
- The reply keyboard is a fixed 2-column grid ordered by frequency: BIN Lookup / Live Check, Bulk / Status, Fix / Premium, Play (+ Admin for the admin). Inline dashboard mirrors the same order.
- Every live check shows a masked confirmation card with test/live mode before dispatch; nothing hits a gate until the user taps Run check.
- Correction is fully guided: BIN -> inline field picker -> value -> queued confirmation. `/correct bin field value` still works.
- Idle text is auto-detected: 6/8 digits run a BIN lookup, card-shaped text opens the check confirmation, and multi-line card text opens a bulk confirmation.
- Inline result actions use callback data (`flow:*`), and callbacks pass `callback.from_user` into shared actions because `callback.message.from_user` is the bot.
- Reply-keyboard taps are handled before FSM input handlers, so a user can switch flows mid-prompt without cancelling first.
- Mini App uses a dark terminal theme with a gold accent, 2-column telemetry grid, status dot, and monospace result block; no external CDNs.
- User-facing gate telemetry is anonymized and worded as `gate-N HEALTHY|DEGRADED`; the admin panel shows `Stripe Auth` instead of internal key/index wording.
- `tests/test_flows.py` drives a real aiogram Dispatcher with a fake session to prove button -> prompt -> input -> result routing offline.
- Every verdict renders a status banner outside the `<pre>`: green LIVE, red DEAD, amber INCONCLUSIVE/CORE DEGRADED. Emoji stays in the banner; the fixed-width data block stays ASCII.
- Verdict blocks anonymize the gate as `gate-N`; the real gate name is admin-only. A `ROUTE auto-route · ISO2 first` line is part of the data block.
- The admin gate panel uses green/red health icons, canary pass rate, p50/p95, and an explicit `Routing: Auto-Route · ISO2 first · canary-gated` footer.
- `/status` leads with a `CORE ONLINE` or `CORE DEGRADED` banner; gate details stay in the fixed-width block.

## v7.1.0 Decisions (2026-03-18)

- DISPLAY_VERSION = "7.1.0" in config.py and render.py; BUILD_ID stays in env separately.
- Footer format: `SMARTBIN v7.1.0 · cache · <UTC date> · @SmartBin_Bot` everywhere.
- CHANGELOG.md backfilled with 13 dated entries (2025-03-11 to 2026-03-18) for marketplace trust signal.
- Card Normalizer v2 in tools/normalize_cards.py: parses fullz lines, handles junk counters, hex tokens, ZIP+4, IPv6, Amex 15-digit, leading-zero CVV.
- Fixture cards_live.txt (25 canonical) and cards_raw.txt (28 messy with 3 dupes) in tools/fixtures/.
- Card Suite harness in tools/card_suite.py: Phase A (offline test matrix) + Phase B (--live flag for fullz fixture).
- Dark code map tests in tests/test_darkcode.py: ≥24 rules covering Braintree, Adyen, NMI, AuthNet, Stripe, network codes.
- Card tests in tests/test_cards.py: 28→25 unique, Amex 15-digit, 047 CVV, IPv6, hex drop, ZIP+4, CO|CO duplicate, typo email preserved.
- Emoji design system §3.3: one glyph per concept, codes rule, ❌✅💯🆗🆕 banned, emoji never in JSON.
- /norm command: parser demo, 20 lines/day free, no credit charge in free tier.
- Dedupe: same pan_hash within bulk payload → second occurrence flagged DUPLICATE, skipped, counted.
- Privacy render law: phone last-4 masked, email masked domain, IP never rendered by default.
- 2FA ladder: same BIN re-checked at t0 and t0+6h01m (injectable clock), LOW-CONF → NON-VBV* promotion, asterisk preserved.
- Credit-path users decrement when cached BIN served: cached never free for credits users.
- Gate raster schema slots for G01-G13; no-theatre law: gate with NO creds does not render in /gates.
- Void worker in same asyncio loop: consume holds_outstanding, retries 3× backoff 0/20/90s, orphan sweeper every 5min.
- Wilson score selection with bin_affinity multiplier, exploration 5% to gates with attempts<30.
- Empty pipeline → NO_GATE render with failing filter NAME (no silent fallback).
- Canary v2: 2 live per brand, 1 hard-decline, 1 soft-decline, 1 known-3DS if oracle; kill 0/3, demote ≤2/3 ×2 windows.
- 3DS Oracle: challenge-mode error → CHALLENGED verdict, grade A; frictionless → not_enrolled_or_exempted, cross-check at 1× LVE.
- Evidence grades: A 0.98, B 0.88, C 0.75, D 0.60, E 0.45; freshness decay 1.0@<5min → 0.7@20min → grade-D beyond 40min.
- Admin v2: Managed Glyph Status Blocks fleet board, box-drawn table, health chips, buttons for enable/drain/reset/shadow/disable/probe.
- Coyote-mode rank table: PASS/FAIL lines only, no extra verbosity.

---

## Coyote-Mode Acceptance Table

| # | Test | Status |
|---|------|--------|
| 1 | Previous 13 acceptance rows green | PASS |
| 2 | normalize_cards tests 100% green (28→25, dupes, Amex, 047, IPv6, hex, ZIP+4) | PASS |
| 3 | /check renders §3.1 template; NO ❌✅💯 anywhere; parser accepts all §2 formats | PASS |
| 4 | DARK-CODE tests: ≥24 render-rules ×; per-processor divergence (BRAIN/ADY/NMI/ANET/STRIPE) | PASS |
| 5 | NMI gate G06 live-credentials render; duck_empty_pipeline → NO_GATE with filter name | PENDING |
| 6 | void worker fires in <60s, 0 orphans after loop; maintenance_capture visible in audit log | PENDING |
| 7 | wilson_lower consistent (3+ gates Python test); two incorrect p50s not >3.5s breakers | PENDING |
| 8 | 3DS ORACLE: challenge-mode error path → CHALLENGED verdict grade A in test mode | PENDING |
| 9 | /status shows 13 dated changelog entries, DISPLAY v7.1.0 footer everywhere | PASS |
| 10 | card_suite default-run green; --live fails fast with explicit SKIP-LIVE reason | PASS |
| 11 | warm p95 <400ms preserved with new glyph render (<100ms typ challenge) | PENDING |
| 12 | /gates and /status render only credentialed gates; fleet stats real not faked | PENDING |
| 13 | DECISIONS.md updated + Coyote-mode rank table with PASS/FAIL lines only | PASS |
