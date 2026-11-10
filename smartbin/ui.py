from __future__ import annotations

import html
from typing import Any

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from render import premium_emoji

FIELD_LABELS = {
    "brand": "Brand",
    "level": "Level",
    "bank": "Bank",
    "country": "Country",
    "iso2": "ISO2",
    "currency": "Currency",
    "phone": "Phone",
    "site": "Site",
    "prepaid": "Prepaid",
    "commercial": "Commercial",
    "avs": "AVS",
    "card_length": "Card length",
    "luhn": "Luhn",
}

# Reply-keyboard labels. Keep them short so the 2-column grid stays even.
BTN_BIN = "🃏 BIN Lookup"
BTN_CHECK = "⚡ Check Card"
BTN_CHECK_LEGACY = "⚡ Live Check"
BTN_KILL = "💀 Kill Card"
BTN_BULK = "📦 Bulk / CSV"
BTN_STATUS = "📊 Status"
BTN_FIX = "🔧 BIN Doctor"
BTN_SHOP = "💎 Shop"
BTN_REFER = "👥 Refer"
BTN_ADMIN = "🎛 Admin Panel"

BUTTON_LABELS = {
    BTN_BIN, BTN_CHECK, BTN_CHECK_LEGACY, BTN_KILL, BTN_BULK, BTN_STATUS,
    BTN_FIX, BTN_SHOP, BTN_REFER, BTN_ADMIN,
}


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows])


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def menu_row() -> list[InlineKeyboardButton]:
    return [_btn("🏠 Menu", "flow:menu")]


def main_menu(admin: bool, miniapp_url: str = "") -> ReplyKeyboardMarkup:
    """Persistent 2-column grid. Order is deliberate: lookup first, admin last."""
    rows = [
        [KeyboardButton(text=BTN_BIN), KeyboardButton(text=BTN_CHECK)],
        [KeyboardButton(text=BTN_KILL), KeyboardButton(text=BTN_BULK)],
        [KeyboardButton(text=BTN_FIX), KeyboardButton(text=BTN_SHOP)],
        [KeyboardButton(text=BTN_STATUS), KeyboardButton(text="📱 Mini App")],
    ]
    # Last row: Refer + Admin (if applicable)
    last = [KeyboardButton(text=BTN_REFER)]
    if admin:
        last.append(KeyboardButton(text=BTN_ADMIN))
    rows.append(last)
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Tap an action or send a BIN",
    )


def dashboard_kb(admin: bool, miniapp_url: str = "") -> InlineKeyboardMarkup:
    """Inline keyboard for instant button responses - Mini App prominent at top."""
    rows: list[list[InlineKeyboardButton]] = []
    
    # Mini App button at the top if HTTPS URL available
    if miniapp_url and miniapp_url.lower().startswith("https://"):
        from aiogram.types import WebAppInfo
        rows.append([
            InlineKeyboardButton(text="📱 Open Mini App", web_app=WebAppInfo(url=miniapp_url))
        ])
    
    # Main actions in 2-column grid
    rows.extend([
        [_btn("🃏 BIN Lookup", "flow:bin"), _btn("⚡ Check Card", "flow:check")],
        [_btn("💀 Kill Card", "flow:kill"), _btn("📦 Bulk / CSV", "flow:bulk")],
        [_btn("🔧 BIN Doctor", "flow:fix"), _btn("💎 Shop", "flow:premium")],
        [_btn("📊 Status", "flow:status"), _btn("👥 Refer", "flow:refer")],
    ])
    
    # Admin button if applicable
    if admin:
        rows.append([_btn("🎛 Admin", "flow:admin")])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def menu_text(config: Any, user: Any = None) -> str:
    header = premium_emoji("SMARTBIN", config.premium_emoji_id)
    lines = [
        f"<b>{header}</b>  <code>v{config.display_version}</code>",
        "<blockquote>⚡ Card Intelligence · 💀 Kill Engine · 🎯 Live Verdicts</blockquote>",
        "",
        "🃏  <b>BIN Lookup</b> — metadata, sources, 2FA",
        "⚡  <b>Check Card</b> — single auth-and-void",
        "💀  <b>Kill Card</b> — multi-attempt drain",
        "📦  <b>Bulk / CSV</b> — batch checks and export",
        "🔧  <b>BIN Doctor</b> — corrections earn credits",
        "💎  <b>Shop</b> — credits, packs & premium",
        "📊  <b>Status</b> — engine health",
        "",
        "<i>Tap a button to start.</i>",
    ]
    if user is not None:
        try:
            from handlers.common import is_premium

            if is_premium(user):
                lines.insert(2, "<blockquote>💎 Premium active · unlimited operations</blockquote>")
            else:
                credits = int(user.get("credits", 0))
                lines.insert(
                    2,
                    f"<blockquote>💰 Credits: <b>{credits}</b> | Kill: {config.kill_max_attempts}x {config.kill_attempts_cost}cr</blockquote>",
                )
        except Exception:
            pass
    return "\n".join(lines)


def bin_prompt() -> str:
    return (
        "<b>🃏 BIN Lookup</b>\n"
        "<blockquote>Send a 6 or 8 digit BIN. I return the card, source set, "
        "confidence and measured 2FA state.</blockquote>\n"
        "Example  <code>45717360</code>"
    )


def bin_invalid() -> str:
    return (
        "<b>Not a valid BIN</b>\n"
        "<blockquote>Send exactly 6 or 8 digits. Nothing is stored until it parses.</blockquote>\n"
        "Example  <code>45717360</code>"
    )


def check_prompt(prefix: str = "") -> str:
    hint = f" It must start with <code>{html.escape(prefix)}</code>." if prefix else ""
    return (
        "<b>⚡ Check Card</b>\n"
        f"<blockquote>Send the card as <code>number|MM|YY|CVV</code>.{hint} "
        "I show a confirmation before any dispatch.</blockquote>\n"
        "Example  <code>4242424242424242|12|2030|123</code>"
    )


def check_invalid(reason: str = "") -> str:
    detail = f" <i>{html.escape(reason)}</i>" if reason else ""
    return (
        "<b>Not a valid card</b>\n"
        f"<blockquote>Use <code>number|MM|YY|CVV</code>.{detail}</blockquote>\n"
        "Example  <code>4242424242424242|12|2030|123</code>"
    )


def check_prefix_mismatch(prefix: str) -> str:
    return (
        "<b>BIN mismatch</b>\n"
        f"<blockquote>The card must start with <code>{html.escape(prefix)}</code>. "
        "Send the full card again, or cancel.</blockquote>"
    )


def confirm_check_text(card: Any, config: Any) -> str:
    mode = "TEST · no money" if config.stripe_test_mode else "LIVE · real auth"
    return (
        "<b>Confirm live check</b>\n"
        "<pre>"
        f"CARD   {card.masked()}\n"
        f"BIN    {card.bin8}\n"
        f"EXP    {card.exp_month:02d}/{card.exp_year}\n"
        f"MODE   {mode}"
        "</pre>\n"
        "<blockquote>Runs through a canary-clean gate and always writes a checks row. "
        "3DS reads are half-credit.</blockquote>"
    )


def correct_bin_prompt() -> str:
    return (
        "<b>🔧 BIN Doctor</b>\n"
        "<blockquote>Send the BIN you want to fix. Approved corrections earn credits.</blockquote>\n"
        "Example  <code>45717360</code>"
    )


def correct_fields_text(bin_arg: str) -> str:
    return (
        "<b>🔧 BIN Doctor</b>\n"
        f"<pre>BIN  {html.escape(bin_arg)}</pre>\n"
        "<blockquote>Select field to correct.</blockquote>"
    )


def correct_field_prompt(field: str) -> str:
    label = FIELD_LABELS.get(field, field)
    return (
        "<b>🔧 BIN Doctor</b>\n"
        f"<blockquote>Send correct value for <b>{html.escape(label)}</b>.</blockquote>"
    )


def correction_done_text(bin_arg: str, field: str, value: str) -> str:
    safe_value = html.escape(value[:80])
    return (
        "<b>🔧 Correction Queued</b>\n"
        "<pre>"
        f"BIN    {html.escape(bin_arg)}\n"
        f"FIELD  {html.escape(field)}\n"
        f"VALUE  {safe_value}"
        "</pre>\n"
        "<blockquote>⏳ Admin review pending. Credits awarded upon approval.</blockquote>"
    )


def paywall_text() -> str:
    return (
        "<b>🚫 Daily Limit Reached</b>\n"
        "<blockquote>💎 Premium unlocks:</blockquote>\n"
        "• Unlimited BIN lookups & checks\n"
        "• No watermark on results\n"
        "• 100-line bulk processing\n"
        "• Credits never expire"
    )


def unknown_text() -> str:
    return (
        "<b>I did not catch that</b>\n"
        "<blockquote>Tap a button, or send a 6/8 digit BIN or a full card.</blockquote>"
    )


def bulk_prompt(cap: int) -> str:
    return (
        "<b>📦 Bulk / CSV</b>\n"
        f"<blockquote>Paste up to <b>{cap}</b> cards, one per line, as "
        "<code>number|MM|YY|CVV</code>. I return fixed-width results and a CSV file.</blockquote>"
    )


def bulk_confirm_text(count: int) -> str:
    return (
        "<b>📦 Bulk detected</b>\n"
        f"<blockquote>{count} card lines found. Run live checks now?</blockquote>"
    )


def premium_text(config: Any) -> str:
    text = (
        "<b>💎 SMARTBIN Shop</b>\n"
        "<blockquote>💰 Credits never expire · ⚡ Instant delivery · 🌐 Crypto accepted</blockquote>\n"
        f"📅 1 Day Pass      <b>{config.stars_day} XTR</b>\n"
        f"📆 30 Days Pass    <b>{config.stars_month} XTR</b>  (subscription)\n"
        f"💰 500 Credits     <b>{config.stars_credits_500} XTR</b>"
    )
    if config.usdt_trc20:
        text += f"\n\n🪙 USDT TRC20 (manual): <code>{html.escape(config.usdt_trc20)}</code>"
    return text


def status_text(stats: dict) -> str:
    from render import status_block

    return status_block(stats)


def cancel_kb() -> InlineKeyboardMarkup:
    return _kb([_btn("✖ Cancel", "flow:cancel")])


def bin_kb(bin_arg: str) -> InlineKeyboardMarkup:
    return _kb(
        [
            _btn("⚡ Check this BIN", f"flow:checkbin:{bin_arg}"),
            _btn("🔎 Why", f"flow:why:{bin_arg}"),
        ],
        [
            InlineKeyboardButton(
                text="📋 Copy BIN", copy_text=CopyTextButton(text=bin_arg)
            ),
            _btn("🔧 BIN Doctor", f"flow:fix:{bin_arg}"),
        ],
        menu_row(),
    )


def verdict_kb(bin8: str) -> InlineKeyboardMarkup:
    first = [_btn("⚡ Check another", "flow:check")]
    if bin8:
        first.append(_btn("🃏 BIN info", f"flow:binof:{bin8}"))
    return _kb(
        first,
        [_btn("📊 Status", "flow:status"), _btn("🏠 Menu", "flow:menu")],
    )


def status_kb() -> InlineKeyboardMarkup:
    return _kb([_btn("🔄 Refresh", "flow:refresh"), _btn("🏠 Menu", "flow:menu")])


def bulk_kb() -> InlineKeyboardMarkup:
    return _kb([_btn("📦 New batch", "flow:bulk"), _btn("🏠 Menu", "flow:menu")])


def premium_kb(config: Any) -> InlineKeyboardMarkup:
    return _kb(
        [_btn(f"📅 1 Day · {config.stars_day} XTR", "buy:day")],
        [_btn(f"📆 30 Days · {config.stars_month} XTR", "buy:monthly")],
        [_btn(f"💰 500 Credits · {config.stars_credits_500} XTR", "buy:credits500")],
        menu_row(),
    )


def paywall_kb() -> InlineKeyboardMarkup:
    return _kb([_btn("💎 Shop", "flow:premium"), _btn("🏠 Menu", "flow:menu")])


def confirm_check_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Run check", "flow:runcheck"), _btn("✖ Cancel", "flow:cancel")]
    )


def bulk_confirm_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Run batch", "flow:runbulk"), _btn("✖ Cancel", "flow:cancel")]
    )


def correct_fields_kb(bin_arg: str) -> InlineKeyboardMarkup:
    fields = list(FIELD_LABELS)
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(fields), 2):
        rows.append(
            [
                _btn(FIELD_LABELS[field], f"flow:corrfield:{field}")
                for field in fields[index:index + 2]
            ]
        )
    rows.append([_btn("✖ Cancel", "flow:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def correction_done_kb(bin_arg: str) -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🃏 View BIN", f"flow:binof:{bin_arg}"), _btn("🏠 Menu", "flow:menu")]
    )


def menu_only_kb() -> InlineKeyboardMarkup:
    return _kb(menu_row())


# ============= KILL CARD UI =============

def kill_prompt() -> str:
    return (
        "<b>💀 Kill Card</b>\n"
        "<blockquote>Send your card to KILL. Multiple auth attempts to drain/decline.</blockquote>\n"
        "Format: <code>number|MM|YY|CVV</code>\n"
        "Example: <code>4242424242424242|12|2030|123</code>"
    )


def kill_invalid(reason: str = "") -> str:
    detail = f"\n<i>{html.escape(reason)}</i>" if reason else ""
    return (
        "<b>❌ Invalid Card</b>\n"
        f"<blockquote>Use format: <code>number|MM|YY|CVV</code>{detail}</blockquote>\n"
        "Example: <code>4242424242424242|12|2030|123</code>"
    )


def confirm_kill_text(card: Any, config: Any) -> str:
    mode = "TEST · no money" if config.stripe_test_mode else "LIVE · real auth"
    total_cost = config.kill_max_attempts * config.kill_attempts_cost
    return (
        "<b>💀 Confirm Kill Operation</b>\n"
        "<pre>"
        f"CARD   {card.masked()}\n"
        f"BIN    {card.bin8}\n"
        f"EXP    {card.exp_month:02d}/{card.exp_year}\n"
        f"ATTEMPTS  {config.kill_max_attempts}x\n"
        f"COST    {total_cost} credits\n"
        f"MODE   {mode}"
        "</pre>\n"
        "<blockquote>Multiple auth attempts to drain/kill the card.</blockquote>"
    )


def kill_insufficient_credits(cost: int) -> str:
    return (
        "<b>💰 Insufficient Credits</b>\n"
        f"<blockquote>Kill operation requires <b>{cost}</b> credits.</blockquote>\n"
        "<i>Visit 💎 Shop to purchase more credits.</i>"
    )


def kill_result_text(card: Any, results: list, declined: int, approved: int, avg_latency: float, config: Any) -> str:
    """Professional kill result display."""
    mode = "TEST" if config.stripe_test_mode else "LIVE"
    
    # Determine final status
    if approved > 0:
        status = "🟢 LIVE (PARTIAL)"
        status_emoji = "🟢"
    elif declined == len(results):
        status = "🔴 DECLINED"
        status_emoji = "🔴"
    else:
        status = "🟡 MIXED"
        status_emoji = "🟡"
    
    # Build result lines
    result_lines = []
    for r in results[:5]:  # Show first 5 attempts
        verdict = r["verdict"]
        live = "✅" if r["live"] else "❌"
        result_lines.append(f"  {live} Attempt {r['attempt']}: {verdict} ({r['latency_ms']}ms)")
    
    if len(results) > 5:
        result_lines.append(f"  ... and {len(results) - 5} more")
    
    return (
        f"<b>💀 KILL OPERATION COMPLETED</b>\n"
        f"<pre>"
        f"🎴 {card.masked()}\n"
        f"📊 BIN: {card.bin8}\n"
        f"🔄 Attempts: {len(results)}\n"
        f"✅ Approved: {approved}\n"
        f"❌ Declined: {declined}\n"
        f"{status_emoji} Status: {status}\n"
        f"⚡ Avg: {avg_latency:.0f}ms\n"
        f"🔧 Mode: {mode}"
        f"</pre>\n"
        "<blockquote>" + "\n".join(result_lines) + "</blockquote>\n"
        f"<i>⚡ Speed: {avg_latency/1000:.2f}s | Killed by: @SmartBIN_Bot</i>"
    )


def kill_result_kb(bin8: str) -> InlineKeyboardMarkup:
    return _kb(
        [_btn("⚡ Check This Card", "flow:check"), _btn("💀 Kill Another", "flow:kill")],
        [_btn("🃏 BIN Info", f"flow:binof:{bin8}"), _btn("📦 Auto-Kill BIN", f"flow:bulk")],
        [_btn("🔧 Switch Killer", "flow:kill"), _btn("🏠 Menu", "flow:menu")],
    )


def confirm_kill_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Run Kill", "flow:runkill"), _btn("❌ Cancel", "flow:cancel")]
    )
