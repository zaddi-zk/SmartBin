# SMARTBIN Verdict Reference Guide

## How the Bot Determines LIVE vs DEAD

The bot uses the `live` field in the `CheckOutcome` to determine card status:

```python
# From smartbin/core/verdicts.py

# LIVE = 1 (Card is active)
# DEAD = 0 (Card is inactive/dead)
```

## Verdict Mapping Table

### 🟢 LIVE CARDS (live=1)

| Network Code | Stripe Code | Verdict | Live | Meaning |
|--------------|-------------|---------|------|---------|
| `00` | - | `APPROVED` | **1** | Authenticated successfully |
| `82`, `N7` | `incorrect_cvc` | `CVV-FAIL` | **1** | Card LIVE, CVV wrong |
| `51` | `insufficient_funds` | `INSUFFICIENT` | **1** | Card LIVE, no funds |
| - | `authentication_required` | `3DS-CHALLENGE` | **1** | Card LIVE, needs 3DS |
| `57`, `62`, `Z3` | - | `MERCHANT-RESTRICTED` | **1** | Card LIVE, merchant limits |
| - | `card_not_supported` | `LIVE-RESTRICTED` | **1** | Card LIVE, restricted |
| - | `restricted_card` | `LIVE-RESTRICTED` | **1** | Card LIVE, restricted |

### 🔴 DEAD CARDS (live=0)

| Network Code | Stripe Code | Verdict | Live | Meaning |
|--------------|-------------|---------|------|---------|
| `05` | `do_not_honor` | `DO-NOT-HONOR` | **0** | Issuer decline |
| `14`, `56` | `incorrect_number` | `INVALID` | **0** | Invalid card |
| `14`, `56` | `invalid_number` | `INVALID` | **0** | Invalid card |
| `54` | `expired_card` | `EXPIRED` | **0** | Card expired |
| `41`, `43` | `pickup_card` | `PICKUP` | **0** | Lost/Stolen |
| - | `lost_card` | `PICKUP` | **0** | Lost/Stolen |
| - | `stolen_card` | `PICKUP` | **0** | Lost/Stolen |
| - | `generic_decline` | `DECLINED` | **0** | Generic decline |
| - | `fraudulent` | `DECLINED` | **0** | Fraud detected |
| - | `security_violation` | `DECLINED` | **0** | Security issue |

### 🟡 AMBIGUOUS (live varies)

| Verdict | Live | Action | Meaning |
|---------|------|--------|---------|
| `INCONCLUSIVE` | 0 | `retry-or-degrade` | Timeout or error |
| `AMBIGUOUS` | 0 | `second-gate-required` | Needs verification |

## Quick Decision Tree

```
Card Check Result
    │
    ├─ APPROVED → LIVE ✅
    ├─ CVV-FAIL → LIVE ✅ (wrong CVV)
    ├─ INSUFFICIENT → LIVE ✅ (no funds)
    ├─ 3DS-CHALLENGE → LIVE ✅ (needs 3DS)
    ├─ LIVE-RESTRICTED → LIVE ✅ (has limits)
    │
    ├─ DO-NOT-HONOR → DEAD ❌
    ├─ INVALID → DEAD ❌
    ├─ EXPIRED → DEAD ❌
    ├─ PICKUP → DEAD ❌
    ├─ DECLINED → DEAD ❌
    │
    └─ INCONCLUSIVE → RETRY 🔄
```

## Bot Output Format

When you check a card, the bot returns:

```
BIN: 51780081
Verdict: APPROVED
Live: YES
Action: approved
Gate: stripe
Latency: 1234ms
Note: auth-and-void clean
```

**Key fields to watch:**
- `Verdict`: The decision (APPROVED, DO-NOT-HONOR, etc.)
- `Live`: YES = LIVE card, NO = DEAD card
- `Action`: What to do next (approved, final, retry-once-clean-gate, etc.)

## Testing Your Cards

### Individual Test
```
Command: /check 5178008141113983|08|27|256

Expected LIVE response:
Verdict: APPROVED
Live: YES

Expected DEAD response:
Verdict: DO-NOT-HONOR
Live: NO
```

### Bulk Test
```
Command: /bulk
Then paste cards from test_bulk_cards.txt

Output format:
51780081 APPROVED           LIVE   approved
54910186 DO-NOT-HONOR       DEAD   final
51945700 CVV-FAIL           LIVE   live-cvv-wrong
```

## Common Issues

| Issue | Solution |
|-------|----------|
| `INCONCLUSIVE` | Retry with different proxy/gate |
| `AMBIGUOUS` | Bot will try second gate automatically |
| `timeout` | Network issue, retry later |
| `canary-window-failed` | Gate health issue, wait and retry |

## Code Reference

The verdict mapping is in `smartbin/core/verdicts.py`:

```python
NETWORK_CODES = {
    "00": ("APPROVED", 1, "approved"),
    "05": ("DO-NOT-HONOR", 0, "retry-once-clean-gate"),
    "51": ("INSUFFICIENT", 1, "no-funds"),
    "14": ("INVALID", 0, "dead"),
    # ... more codes
}

STRIPE_CODES = {
    "generic_decline": ("DECLINED", 0, "final"),
    "insufficient_funds": ("INSUFFICIENT", 1, "no-funds"),
    "incorrect_cvc": ("CVV-FAIL", 1, "live-cvv-wrong"),
    # ... more codes
}
```

The second value in each tuple is the `live` flag:
- `1` = LIVE
- `0` = DEAD
