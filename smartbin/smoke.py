from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Config  # noqa: E402
from core.canaries import CanaryDaemon, percentile  # noqa: E402
from db import DB  # noqa: E402
from handlers.common import free_watermark, quota_decision  # noqa: E402
import ui  # noqa: E402
from miniapp.server import server_port, start_server  # noqa: E402
from models import Card, utc_date  # noqa: E402
from render import WATERMARK, card_block  # noqa: E402
from runtime import build_runtime, close_runtime  # noqa: E402
from store import Store  # noqa: E402

log = logging.getLogger("smartbin.smoke")
ROOT = Path(__file__).resolve().parent


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="ts=%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s",
    )


def check(condition: bool, name: str, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} failed {detail}")
    log.info("event=smoke_pass name=%s %s", name, detail)


def make_init_data(token: str) -> str:
    pairs = {
        "auth_date": str(int(time.time())),
        "query_id": "AAH-smoke",
        "user": json.dumps({"id": 1, "first_name": "Smoke"}, separators=(",", ":")),
    }
    data_check = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**pairs, "hash": digest})


async def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smartbin-smoke-", dir="/tmp/opencode"))
    config = Config(
        bot_token="test-token",
        admin_id=42,
        db_path=str(tmp / "smoke.db"),
        miniapp_port=0,
        miniapp_base="http://127.0.0.1",
        stripe_test_mode=True,
        free_bins_per_day=3,
        free_checks_per_day=2,
    )
    runtime = await build_runtime(config, offline=True)
    runner = None
    try:
        await runtime.db.add_proxy("http://127.0.0.1:9", scope="check", country="US")
        await runtime.canary.fire_window("stripe")
        gate = await runtime.db.get_gate("stripe")
        check(runtime.canary.window_state(gate) == 1, "canary_clean_window")

        # Warm BIN lookup p95
        await runtime.store.get("45717360")
        latencies = []
        for _ in range(30):
            started = time.perf_counter()
            await runtime.store.get("45717360")
            latencies.append((time.perf_counter() - started) * 1000.0)
        p95 = percentile(latencies, 95)
        check(p95 < 400.0, "warm_lookup_p95", f"p95={p95:.3f}ms")
        log.info("event=smoke_metric name=warm_lookup_p95 value_ms=%.3f", p95)

        # Miss path + LOW-CONF render
        missing = await runtime.store.get("99999999")
        missing_text = card_block(missing, free=True)
        check("UNKNOWN" in missing_text, "miss_path_unknown")
        check(await runtime.db.get_bin("99999999") is None, "miss_not_persisted")
        seed_record = await runtime.store.get("45717360")
        seed_text = card_block(seed_record, free=True)
        check("LOW-CONF" in seed_text, "low_conf_render")
        check(WATERMARK in seed_text, "free_watermark")

        # All seven Stripe test-card verdicts
        expected = {
            "4242424242424242|12|2030|123": "APPROVED",
            "4000000000000002|12|2030|123": "AMBIGUOUS",
            "4000000000009995|12|2030|123": "INSUFFICIENT",
            "4000000000009987|12|2030|123": "PICKUP",
            "4000000000000069|12|2030|123": "EXPIRED",
            "4000000000000127|12|2030|123": "CVV-FAIL",
            "4000000000003220|12|2030|123": "3DS-CHALLENGE",
        }
        before_checks = await runtime.db.count("checks")
        for raw, verdict in expected.items():
            outcome = await runtime.runner.run(1, raw)
            check(outcome.verdict == verdict, f"verdict_{verdict}", f"got={outcome.verdict}")
            if verdict == "3DS-CHALLENGE":
                check(bool(outcome.acs_host), "acs_host_captured", outcome.acs_host)
        after_checks = await runtime.db.count("checks")
        check(after_checks >= before_checks + 7, "checks_writeback")

        # Gate disabled mid-window -> INCONCLUSIVE + row
        await runtime.db.update_gate("stripe", enabled=0)
        before_disabled = await runtime.db.count("checks")
        disabled = await runtime.runner.run(1, "4242424242424242|12|2030|123")
        after_disabled = await runtime.db.count("checks")
        check(disabled.verdict == "INCONCLUSIVE", "gate_disabled_inconclusive")
        check(after_disabled > before_disabled, "gate_disabled_writeback")
        await runtime.db.update_gate("stripe", enabled=1)
        await runtime.canary.fire_window("stripe")

        # Paywall on 4th lookup, never the 1st
        user = {"free_bin": 0, "free_check": 0, "credits": 0, "premium_until": ""}
        for index in range(3):
            decision = quota_decision(user, "bin", config)
            check(decision["allowed"], f"paywall_lookup_{index + 1}")
            user["free_bin"] += 1
        fourth = quota_decision(user, "bin", config)
        check(not fourth["allowed"], "paywall_fourth_lookup")
        premium = {"free_bin": 99, "free_check": 99, "credits": 0,
                   "premium_until": "2099-01-01T00:00:00Z"}
        check(quota_decision(premium, "bin", config)["allowed"], "premium_unlimited")
        check(free_watermark(user), "free_tier_watermark")
        check(not free_watermark(premium), "premium_no_watermark")

        # Admin surface invisible to non-admin
        plain = [b.text for row in ui.main_menu(False).keyboard for b in row]
        admin = [b.text for row in ui.main_menu(True).keyboard for b in row]
        check("🎛 Admin Panel" not in plain, "admin_button_hidden")
        check("🎛 Admin Panel" in admin, "admin_button_visible")
        check(all(len(row) <= 2 for row in ui.main_menu(True).keyboard), "menu_two_columns")
        dash = ui.dashboard_kb(True, "https://example.test")
        callbacks = [
            button.callback_data
            for row in dash.inline_keyboard
            for button in row
            if button.callback_data
        ]
        for expected_cb in ("flow:bin", "flow:check", "flow:bulk", "flow:status", "flow:fix", "flow:premium"):
            check(expected_cb in callbacks, f"dashboard_{expected_cb}")
        check(any(button.web_app for row in dash.inline_keyboard for button in row), "dashboard_miniapp")
        check("Send a 6 or 8 digit BIN" in ui.bin_prompt(), "bin_prompt")
        check("number|MM|YY|CVV" in ui.check_prompt(), "check_prompt")
        check(ui.cancel_kb().inline_keyboard[0][0].callback_data == "flow:cancel", "cancel_kb")
        check(ui.confirm_check_kb().inline_keyboard[0][0].callback_data == "flow:runcheck", "confirm_check_kb")
        check(ui.bulk_confirm_kb().inline_keyboard[0][0].callback_data == "flow:runbulk", "bulk_confirm_kb")
        check(ui.bin_kb("45717360").inline_keyboard[0][0].callback_data == "flow:checkbin:45717360", "bin_kb")
        check(ui.verdict_kb("42424242").inline_keyboard[0][1].callback_data == "flow:binof:42424242", "verdict_kb")
        check(ui.status_kb().inline_keyboard[0][0].callback_data == "flow:refresh", "status_kb")
        check(ui.premium_kb(config).inline_keyboard[0][0].callback_data == "buy:day", "premium_kb")
        check(
            ui.main_menu(True).keyboard[0][0].text == ui.BTN_BIN
            and ui.main_menu(True).keyboard[0][1].text == ui.BTN_CHECK,
            "menu_order",
        )
        from states import Flow as FlowStates

        for name in (
            "bin", "check", "check_confirm", "correct_bin", "correct_pick",
            "correct_value", "bulk_input", "bulk_confirm",
        ):
            check(hasattr(FlowStates, name), f"state_{name}")
        import re as _re

        pre_body = seed_text.split("<pre>", 1)[1].split("</pre>", 1)[0]
        check(not _re.search(r"[\U0001F300-\U0001FAFF]", pre_body), "no_emoji_in_data")
        check("-- @SmartBin_Bot" in seed_text, "watermark_exact")

        # Mini App: unsigned 401, signed 200
        runner = await start_server(runtime, host="127.0.0.1", port=0)
        port = server_port(runner)
        check(port > 0, "miniapp_port", str(port))
        base = f"http://127.0.0.1:{port}"
        init_data = make_init_data(config.bot_token)
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base}/api/bin?bin=45717360") as response:
                check(response.status == 401, "miniapp_unsigned_401", str(response.status))
            async with session.get(
                f"{base}/api/bin?bin=45717360",
                headers={"X-Telegram-Init-Data": init_data},
            ) as response:
                check(response.status == 200, "miniapp_signed_200", str(response.status))
                payload = await response.json()
                check(payload.get("bin") == "45717360", "miniapp_card_payload")
            async with session.get(
                f"{base}/api/status", headers={"X-Telegram-Init-Data": init_data}
            ) as response:
                check(response.status == 200, "miniapp_status_200", str(response.status))
        await runner.cleanup()
        runner = None

        # Idle canary cadence, zero user traffic
        daemon = CanaryDaemon(runtime.db, config, runtime.gates, idle_seconds=0.1)
        daemon.start()
        await asyncio.sleep(0.35)
        await daemon.stop()
        gate = await runtime.db.get_gate("stripe")
        check(bool(gate["last_canary_utc"]), "canary_idle_fired")
        check(daemon.idle_seconds <= 900, "canary_idle_bound")

        # Receipts idempotent + WAL + LRU rehydrate across restarts
        first = await runtime.db.insert_receipt("smoke-charge", 1, 300, "day")
        second = await runtime.db.insert_receipt("smoke-charge", 1, 300, "day")
        check(first and not second, "receipt_idempotent")
        check(await runtime.db.count("receipts") == 1, "receipt_count")
        await runtime.canary.stop()
        await runtime.db.close()

        for round_no in (1, 2):
            db = await DB(config.db_path).init()
            journal = await db.scalar("PRAGMA journal_mode")
            check(str(journal).lower() == "wal", f"wal_round_{round_no}", str(journal))
            check(await db.count("receipts") == 1, f"receipts_round_{round_no}")
            store = Store(db, [], config)
            await store.preload()
            check(store.depth() > 0, f"lru_rehydrate_round_{round_no}", str(store.depth()))
            await db.close()

        # Unit suites
        import unittest

        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=0).run(suite)
        check(result.wasSuccessful(), "unit_tests", f"tests={result.testsRun}")

        check((ROOT / "DECISIONS.md").exists(), "decisions_exists")
        check((ROOT / "README.md").exists(), "readme_exists")
        check(utc_date() != "", "utc_date")
        check(Card.parse("4242424242424242|12|2030|123").bin8 == "42424242", "card_parse")

        log.info("event=smoke_pass version=%s", config.display_version)
        return 0
    finally:
        if runner is not None:
            await runner.cleanup()
        try:
            await close_runtime(runtime)
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    setup_logging()
    try:
        sys.exit(asyncio.run(run()))
    except Exception as exc:
        log.exception("event=smoke_fail error=%s", exc)
        sys.exit(1)
