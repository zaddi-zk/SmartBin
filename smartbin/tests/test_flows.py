from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import ui
from config import Config
from handlers import setup_routers
from render import verdict_block, verdict_header
from runtime import build_runtime, close_runtime


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list = []

    async def close(self):
        return None

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        return True

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""

    def sent(self):
        return [c for c in self.calls if type(c).__name__ == "SendMessage"]

    def last(self):
        sent = self.sent()
        return sent[-1] if sent else None

    def last_text(self) -> str:
        sent = self.sent()
        return (sent[-1].text or "") if sent else ""


def _user(uid: int) -> User:
    return User(id=uid, is_bot=False, first_name="Test", username="tester")


def _message(uid: int, text: str, mid: int = 1) -> Message:
    return Message(
        message_id=mid,
        date=datetime.now(timezone.utc),
        chat=Chat(id=uid, type="private"),
        from_user=_user(uid),
        text=text,
    )


def _callback(uid: int, data: str, mid: int = 99) -> CallbackQuery:
    return CallbackQuery(
        id=f"cb{mid}",
        from_user=_user(uid),
        chat_instance="ci",
        data=data,
        message=_message(uid, "origin", mid),
    )


class GuidedFlowTest(unittest.TestCase):
    """One dispatcher, one loop: proves button -> prompt -> input -> result."""

    def test_guided_flow_scenario(self):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._scenario())
            return
        # smoke.py runs unittest inside its own loop; give the scenario a clean one.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(lambda: asyncio.run(self._scenario())).result()

    async def _scenario(self):
        tmp = Path(tempfile.mkdtemp(prefix="smartbin-test-", dir="/tmp/opencode"))
        config = Config(
            bot_token="1:x",
            admin_id=8577755570,
            db_path=str(tmp / "flow.db"),
            miniapp_port=0,
            miniapp_base="http://127.0.0.1",
            stripe_test_mode=True,
            free_bins_per_day=3,
            free_checks_per_day=2,
        )
        runtime = await build_runtime(config, offline=True)
        await runtime.db.add_proxy("http://127.0.0.1:9", scope="check", country="US")
        await runtime.canary.fire_window("stripe")
        session = FakeSession()
        bot = Bot(token=config.bot_token, session=session)
        dp = Dispatcher(storage=MemoryStorage())
        dp["runtime"] = runtime
        setup_routers(dp)
        uid = 8577755570
        update_id = 0

        async def feed(update):
            session.calls.clear()
            await dp.feed_update(bot, update)

        def update(**kwargs):
            nonlocal update_id
            update_id += 1
            return Update(update_id=update_id, **kwargs)

        try:
            # /start -> single menu message with the inline dashboard
            await feed(update(message=_message(uid, "/start")))
            sent = session.sent()
            self.assertEqual(len(sent), 1)
            self.assertEqual(
                type(sent[0].reply_markup).__name__, "InlineKeyboardMarkup"
            )
            dashboard = [
                b.callback_data
                for row in sent[0].reply_markup.inline_keyboard
                for b in row
                if b.callback_data
            ]
            for cb in ("flow:bin", "flow:check", "flow:bulk", "flow:status"):
                self.assertIn(cb, dashboard)
            check_label = next(
                b.text
                for row in sent[0].reply_markup.inline_keyboard
                for b in row
                if b.callback_data == "flow:check"
            )
            self.assertEqual(check_label, "⚡ Check Card")

            # Button -> prompt -> BIN -> card with inline actions
            await feed(update(message=_message(uid, "🃏 BIN Lookup")))
            self.assertIn("BIN Lookup", session.last_text())
            await feed(update(message=_message(uid, "45717360")))
            text = session.last_text()
            self.assertIn("45717360", text)
            self.assertIn("SMARTBIN BIN INTELLIGENCE", text)
            self.assertIn("2FA", text)
            callbacks = [
                b.callback_data
                for row in session.last().reply_markup.inline_keyboard
                for b in row
                if b.callback_data
            ]
            self.assertIn("flow:checkbin:45717360", callbacks)

            # Check Card -> confirmation -> inline run -> verdict
            await feed(update(message=_message(uid, "⚡ Check Card")))
            self.assertIn("Check Card", session.last_text())
            await feed(
                update(message=_message(uid, "4242424242424242|12|2030|123"))
            )
            self.assertIn("Confirm live check", session.last_text())
            confirm = [
                b.callback_data
                for row in session.last().reply_markup.inline_keyboard
                for b in row
                if b.callback_data
            ]
            self.assertIn("flow:runcheck", confirm)
            await feed(update(callback_query=_callback(uid, "flow:runcheck")))
            verdict = session.last_text()
            self.assertIn("CORROBORATED", verdict)
            self.assertIn("APPROVED", verdict)
            self.assertIn("🟩", verdict)
            self.assertIn("GATE-1", verdict)
            self.assertNotIn("gate=stripe", verdict.lower())
            self.assertIn("SMARTBIN", verdict)
            self.assertNotIn("-- @SmartBlN_Bot", verdict)  # admin is unlimited: no watermark

            # Status
            await feed(update(message=_message(uid, "📊 Status")))
            self.assertIn("UPTIME", session.last_text())
            self.assertIn("ROUTING", session.last_text())

            # Fix record -> BIN -> field -> value
            await feed(update(message=_message(uid, ui.BTN_FIX)))
            self.assertIn("BIN Doctor", session.last_text())
            await feed(update(message=_message(uid, "45717360")))
            fields = [
                b.callback_data
                for row in session.last().reply_markup.inline_keyboard
                for b in row
                if b.callback_data
            ]
            self.assertIn("flow:corrfield:bank", fields)
            await feed(update(callback_query=_callback(uid, "flow:corrfield:bank")))
            self.assertIn("Bank", session.last_text())
            await feed(update(message=_message(uid, "Jyske Bank")))
            self.assertIn("Correction Queued", session.last_text())
            self.assertGreaterEqual(await runtime.db.count("corrections"), 1)

            # Idle card text -> confirmation, cancel, unknown fallback
            await feed(
                update(message=_message(uid, "4000000000000002|12|2030|123"))
            )
            self.assertIn("Confirm live check", session.last_text())
            await feed(update(callback_query=_callback(uid, "flow:cancel")))
            await feed(update(message=_message(uid, "hello there")))
            self.assertIn("did not catch that", session.last_text())

            # Admin panel visible to admin only
            await feed(update(message=_message(uid, "🎛 Admin Panel")))
            self.assertIn("ADMIN DASHBOARD", session.last_text())
            await feed(update(message=_message(1234, "🎛 Admin Panel")))
            self.assertIn("Admin only", session.last_text())

            # No-proxy path: honest CORE DEGRADED header, checks row still written
            for proxy in await runtime.db.list_proxies():
                await runtime.db.update_proxy(int(proxy["id"]), enabled=0)
            before = await runtime.db.count("checks")
            outcome = await runtime.runner.run(
                uid, "4242424242424242|12|2030|123"
            )
            after = await runtime.db.count("checks")
            self.assertEqual(outcome.action, "no-proxy")
            self.assertGreater(after, before)
            header = verdict_header(outcome, {}, "gate-1")
            self.assertIn("CORE DEGRADED", header)
            self.assertIn("proxy pool empty", header)
            body = verdict_block(outcome, None, free=True, gate_label="gate-1")
            self.assertIn("INCONCLUSIVE-NO-PROXY", body)
            self.assertNotIn("stripe", body.lower())
        finally:
            await close_runtime(runtime)
            await bot.session.close()


class CheckCardButtonTest(unittest.TestCase):
    """Renaming Live Check to Check Card must keep the exact same flow."""

    def test_new_and_legacy_labels_share_the_check_flow(self):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._scenario())
            return
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(lambda: asyncio.run(self._scenario())).result()

    async def _scenario(self):
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey

        from handlers import flows
        from states import Flow

        tmp = Path(tempfile.mkdtemp(prefix="smartbin-btn-", dir="/tmp/opencode"))
        config = Config(
            bot_token="1:x",
            admin_id=0,
            db_path=str(tmp / "btn.db"),
            miniapp_port=0,
            miniapp_base="http://127.0.0.1",
        )
        runtime = await build_runtime(config, offline=True)
        session = FakeSession()
        bot = Bot(token=config.bot_token, session=session)
        storage = MemoryStorage()
        uid = 777001
        key = StorageKey(bot_id=bot.id, chat_id=uid, user_id=uid)
        try:
            for label in ("⚡ Check Card", "⚡ Live Check"):
                await storage.set_state(key, None)
                session.calls.clear()
                context = FSMContext(storage=storage, key=key)
                await flows.kb_check(_message(uid, label).as_(bot), context)
                text = session.last_text()
                self.assertIn("Check Card", text)
                self.assertIn("number|MM|YY|CVV", text)
                self.assertEqual(
                    type(session.last().reply_markup).__name__, "InlineKeyboardMarkup"
                )
                self.assertEqual(await storage.get_state(key), Flow.check.state)
        finally:
            await close_runtime(runtime)
            await bot.session.close()


if __name__ == "__main__":
    unittest.main()
