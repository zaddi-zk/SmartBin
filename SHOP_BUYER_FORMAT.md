# 💎 SMARTBIN Shop Buyer Format

## Overview

Optimized for card buyers in vendor shops. The buyer already knows the brand, bank, and level from the listing. The bot's job is to answer what the shop hides:

- **Does it die?** (verdict + flags)
- **Will it fight checkout?** (3DS/AVS)
- **Does the address match?** (MATCH row)
- **How often does this BIN pass?** (HIT rate)

---

## Three States

### 🟩 BUY — Clean, ready to purchase
```
🧬 SMARTBIN · 51945700                     🟩 CORROBORATED
🟩 LIVE · APPROVED · 412ms · G03
⚡ BUY — clean geo, no kill flags
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💳 MASTERCARD · standard · credit · Citibank N.A.
📍 GEO    NEW YORK NY 10019 → BIN US 🟢
🗺️ MATCH  zip 🟢 · state 🟢 · city 🟢
🔐 3DS    🟦 NON-VBV* · 2 reads · conf 78
🔒 AVS    YES · attach full address
🪙 KIND   prepaid NO · comm NO · debit NO
📏 PAN    16 · age 14y
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚩 FLAGS  none
📊 HIT    41/57 (72%) · 30d
📊 CONF   brand 92 · bank 84 · level 61🟨 · geo 95
📖 SOURCE binlist · antipublic · seed · sample 3
🕐 SEEN   today · live · G03
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧾 SMARTBIN v7.2.1 · 2026-11-10 · @SmartBin_Bot
```

**Decision:** BUY
- ✅ Live and approved
- ✅ Clean geo (address matches BIN country)
- ✅ No kill flags
- ✅ 72% hit rate (good)
- ⚠️ Level confidence low (61%) but acceptable

---

### 🟨 MAYBE — Caution, some issues
```
🧬 SMARTBIN · 51945700                     🟨 PARTIAL
🟩 LIVE · CVV-FAIL · 512ms · G03
⚡ MAYBE — CVV mismatch · 3DS enrolled
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💳 VISA · platinum · credit · Chase
📍 GEO    HOUSTON TX 77002 → BIN US 🟢
🗺️ MATCH  zip 🟢 · state 🟤 MISMATCH · city 🟢
🔐 3DS    🔐 VBV · 3 reads · conf 81
🔒 AVS    YES · attach full address
🪙 KIND   prepaid NO · comm NO · debit NO
📏 PAN    16 · age 3y
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚩 FLAGS  🟠 CVV mismatch · 🟠 3DS enrolled · 🟠 state mismatch
📊 HIT    22/58 (38%) · 30d
📊 CONF   brand 92 · bank 90 · level 71 · geo 95
📖 SOURCE binlist · antipublic · seed · sample 4
🕐 SEEN   today · live · G03
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧾 SMARTBIN v7.2.1 · 2026-11-10 · @SmartBin_Bot
```

**Decision:** MAYBE
- ⚠️ CVV wrong (can be fixed)
- ⚠️ 3DS/VBV enrolled (will challenge)
- ⚠️ State mismatch (billing state ≠ BIN state)
- ⚠️ 38% hit rate (low but usable)

**Action:** Buy only if price is low, expect 3DS challenge

---

### 🟥 SKIP — Don't waste money
```
🧬 SMARTBIN · 51945700                     🟩 CORROBORATED
🟥 DEAD · EXPIRED · 341ms · G01
🛑 SKIP — expired
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💳 MASTERCARD · standard · prepaid · GreenDot
📍 GEO    MIAMI FL 33101 → BIN US 🟢
🗺️ MATCH  zip 🟢 · state 🟢 · city 🟢
🔐 3DS    ❔ UNKNOWN · 0 reads · conf 0
🔒 AVS    YES · strict
🪙 KIND   prepaid YES · comm NO · debit NO
📏 PAN    16 · age 1y
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚩 FLAGS  🔴 expired · 🔴 prepaid on AVS-strict
📊 HIT    4/31 (13%) · 30d
📊 CONF   brand 92 · bank 88 · level 64 · geo 95
📖 SOURCE binlist · antipublic · seed · sample 2
🕐 SEEN   today · dead · G01
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧾 SMARTBIN v7.2.1 · 2026-11-10 · @SmartBin_Bot
```

**Decision:** SKIP
- ❌ Expired (03/25)
- ❌ Prepaid on AVS-strict gate (guaranteed decline)
- ❌ 13% hit rate (terrible)

**Action:** Don't buy, will decline

---

## The Flag Law

**Flags drive everything. Empty flags is the only path to BUY.**

### 🔴 KILL Flags → SKIP
```
expired        → Card expired
invalid        → Invalid card number
pickup         → Card marked for pickup
lost-stolen    → Card reported lost/stolen
prepaid+AVS-strict → Prepaid on strict AVS gate (guaranteed decline)
country mismatch → Billing country ≠ BIN country (no proxy match)
multiple hard declines → 2+ hard declines on clean gates
```

### 🟠 WARN Flags → MAYBE
```
CVV mismatch       → Wrong CVV (can be fixed)
3DS enrolled       → VBV/3DS will challenge
state mismatch     → Billing state ≠ BIN state
commercial         → Commercial card (may have restrictions)
new BIN            → BIN age < 30 days (unproven)
low confidence     → Any field < 60% confidence
low hit rate       → < 40% at n ≥ 20 checks
```

### — NONE → BUY
```
clean geo, no kill flags
```

---

## Line-by-Line Breakdown

### Line 1: Header
```
🧬 SMARTBIN · 51945700                     🟩 CORROBORATED
```
- BIN number (8 digits)
- Status badge:
  - 🟩 CORROBORATED = Live+approved OR Dead+confirmed
  - 🟨 PARTIAL = Live but soft decline (CVV-FAIL, INSUFFICIENT)

### Line 2: Verdict
```
🟩 LIVE · APPROVED · 412ms · G03
```
- Live status (🟩 LIVE / 🟥 DEAD)
- Verdict (APPROVED, CVV-FAIL, EXPIRED, etc.)
- Latency in ms
- Gate label (G01, G02, G03)

### Line 3: Call (THE DECISION)
```
⚡ BUY — clean geo, no kill flags
```
- **🛑 SKIP** — Kill flags present
- **⚡ MAYBE** — Warn flags present
- **⚡ BUY** — No flags, clean

### Line 4: Separator
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Line 5: Card Identity (ONE LINE)
```
💳 MASTERCARD · standard · credit · Citibank N.A.
```
- Brand · Level · Type · Bank
- All on ONE line (shop already showed this)

### Line 6-7: Geographic & Address Match
```
📍 GEO    NEW YORK NY 10019 → BIN US 🟢
🗺️ MATCH  zip 🟢 · state 🟢 · city 🟢
```
- Billing address → BIN country
- Match indicators:
  - 🟢 = Match
  - 🟤 = Mismatch
  - — = Unknown

### Line 8: 3DS/2FA
```
🔐 3DS    🟦 NON-VBV* · 2 reads · conf 78
```
- Method with glyph:
  - 🟦 NON-VBV* = Single-gate, no 3DS
  - 🟦 NON-VBV = Two-gate, no 3DS
  - ✅ FRICTIONLESS = 3DS auto-pass
  - 🔐 VBV/CHALLENGED = Will challenge
  - ❔ UNKNOWN = No data
- Read count
- Confidence %

### Line 9: AVS
```
🔒 AVS    YES · attach full address
```
- AVS support: YES/NO
- Posture: strict / attach full address

### Line 10: Kind
```
🪙 KIND   prepaid NO · comm NO · debit NO
```
- Prepaid: YES/NO
- Commercial: YES/NO
- Debit/ATM: YES/NO

### Line 11: PAN
```
📏 PAN    16 · age 14y
```
- Card length
- BIN age in years

### Line 12: Separator
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Line 13: Flags
```
🚩 FLAGS  none
```
or
```
🚩 FLAGS  🔴 expired · 🔴 prepaid on AVS-strict
```
or
```
🚩 FLAGS  🟠 CVV mismatch · 🟠 3DS enrolled
```

### Line 14: Hit Rate
```
📊 HIT    41/57 (72%) · 30d
```
- Approved/Total (percentage) · 30 days
- If n < 5: `cold (n=3)`
- If n = 0: `no data`

### Line 15: Confidence
```
📊 CONF   brand 92 · bank 84 · level 61🟨 · geo 95
```
- Field confidence scores
- 🟨 = 60-79% (medium)
- 🔴 = <60% (low)

### Line 16: Sources
```
📖 SOURCE binlist · antipublic · seed · sample 3
```
- Data sources (first 3)
- Sample count

### Line 17: Seen
```
🕐 SEEN   today · live · G03
```
- Last seen: today / recent / old
- Status: live / dead
- Gate used

### Line 18: Separator
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Line 19: Footer
```
🧾 SMARTBIN v7.2.1 · 2026-11-10 · @SmartBin_Bot
```
- Byte-exact watermark

---

## Key Rules

### 1. Call is Always Line 3
```
Line 1: Header
Line 2: Verdict
Line 3: CALL (BUY/MAYBE/SKIP) ← THE DECISION
```

### 2. Flags in Two Places
- Line 3: `⚡ MAYBE — CVV mismatch · 3DS enrolled`
- Line 13: `🚩 FLAGS  🟠 CVV mismatch · 🟠 3DS enrolled`

**Why?** First is the decision, second is the audit.

### 3. MATCH Row is Critical
```
🗺️ MATCH  zip 🟢 · state 🟢 · city 🟢
```
- **zip** — Valid format for BIN country (5-digit US, 6-char UK)
- **state** — Billing state vs BIN state (— if unknown)
- **city** — Sanity check against state only

### 4. HIT is Your Number, Not Theirs
```
📊 HIT    41/57 (72%) · 30d
```
- From **your** checks table on that BIN
- Never show percentage if n < 5
- `cold (n=3)` for small samples

### 5. Don't Repeat the Shop
```
💳 MASTERCARD · standard · credit · Citibank N.A.
```
- Brand/bank/level on ONE line
- Shop already showed this
- Don't waste rows

### 6. No ✅ ❌
- Use YES/NO instead
- Cleaner, more professional

### 7. Prepaid Never Reaches BUY on AVS-Strict
```
🔒 AVS    YES · strict
🪙 KIND   prepaid YES · ...
🛑 SKIP — prepaid on AVS-strict
```
- Guaranteed decline
- Always SKIP

### 8. Total Lines: 14-17
- Compact, scannable
- No fluff

---

## Comparison: Old vs New

| Feature | Old Format | Shop Buyer Format |
|---------|-----------|-------------------|
| **Decision** | Hidden in verdict | Line 3: BUY/MAYBE/SKIP |
| **Flags** | Not shown | Two places (call + audit) |
| **Address Match** | AVS only | MATCH row (zip/state/city) |
| **Hit Rate** | Fleet stats | Your checks (41/57 (72%)) |
| **Brand/Bank** | Multiple lines | ONE line |
| **Lines Total** | 15-20 | 14-17 |
| **Focus** | All data | Buyer decisions |
| **Prepaid+AVS** | Not flagged | Always SKIP |

---

## Decision Flow

```
┌─────────────────────────────────────┐
│         Check Card                  │
└──────────────┬──────────────────────┘
               │
               ▼
    ┌──────────────────────┐
    │  Extract Flags       │
    │  (kill, warn, none)  │
    └──────────┬───────────┘
               │
    ┌──────────┴───────────┐
    │                      │
    ▼                      ▼
┌─────────┐          ┌─────────┐
│ KILL    │          │ WARN    │
│ Flags   │          │ Flags   │
└────┬────┘          └────┬────┘
     │                    │
     ▼                    ▼
🛑 SKIP            ⚡ MAYBE
     │                    │
     └────────┬───────────┘
              │
              ▼
       ┌──────────────┐
       │  NO FLAGS    │
       └──────────────┘
              │
              ▼
          ⚡ BUY
```

---

## Examples by Verdict

### APPROVED → BUY
```
🟩 LIVE · APPROVED · 412ms · G03
⚡ BUY — clean geo, no kill flags
```

### CVV-FAIL → MAYBE
```
🟩 LIVE · CVV-FAIL · 512ms · G03
⚡ MAYBE — CVV mismatch
```

### INSUFFICIENT → BUY
```
🟩 LIVE · INSUFFICIENT · 389ms · G02
⚡ BUY — clean geo, no kill flags
```
*(Live but no funds = still BUY)*

### DO-NOT-HONOR → MAYBE
```
🟩 LIVE · DO-NOT-HONOR · 445ms · G01
⚡ MAYBE — issuer decline
```

### EXPIRED → SKIP
```
🟥 DEAD · EXPIRED · 341ms · G01
🛑 SKIP — expired
```

### INVALID → SKIP
```
🟥 DEAD · INVALID · 298ms · G03
🛑 SKIP — invalid
```

---

## Status

**Version:** 7.2.1  
**Format:** Shop Buyer Optimized  
**Lines:** 14-17  
**Decision:** Line 3 (BUY/MAYBE/SKIP)  
**Flags:** Kill → SKIP, Warn → MAYBE, None → BUY  
**Status:** ✅ Deployed

**Made for buyers who need to decide FAST.** 💎
