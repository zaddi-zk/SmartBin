# 💀 SMARTBIN Kill Card Feature

## Overview
The **Kill Card** feature performs multiple authentication attempts on a card to drain/decline it. This is essential for carders who need to verify if a card is truly dead or just temporarily declined.

## How It Works

### Kill Process
1. User sends `/kill` or taps **💀 Kill Card**
2. Bot requests card in format: `number|MM|YY|CVV`
3. Bot shows confirmation with cost breakdown
4. User confirms → Bot executes **5 auth attempts** (configurable)
5. Results displayed with detailed breakdown

### Default Configuration
```yaml
kill_max_attempts: 5        # Number of auth attempts
kill_attempts_cost: 1       # Credits per attempt
```

**Total cost per kill: 5 credits** (5 attempts × 1 credit)

## User Flow

### 1. Start Kill
```
User: /kill
Bot: 💀 Kill Card
     Send your card to KILL. Multiple auth attempts to drain/decline.
     Format: number|MM|YY|CVV
```

### 2. Send Card
```
User: 4737034075902651|12|28|263
Bot: 💀 Confirm Kill Operation
     CARD   473703••••••2651
     BIN    47370340
     EXP    12/28
     ATTEMPTS  5x
     COST    5 credits
     MODE   LIVE · real auth
     
     Multiple auth attempts to drain/kill the card.
     
     [✅ Run Kill] [❌ Cancel]
```

### 3. Results
```
💀 KILL OPERATION COMPLETED
🎴 473703••••••2651
📊 BIN: 47370340
🔄 Attempts: 5
✅ Approved: 2
❌ Declined: 3
🔴 Status: DECLINED (UNVERIFIED)
⚡ Avg: 220ms
🔧 Mode: LIVE

  ❌ Attempt 1: DO-NOT-HONOR (215ms)
  ✅ Attempt 2: APPROVED (198ms)
  ❌ Attempt 3: DO-NOT-HONOR (245ms)
  ❌ Attempt 4: DO-NOT-HONOR (230ms)
  ❌ Attempt 5: DO-NOT-HONOR (210ms)

⚡ Speed: 0.22s | Killed by: @SmartBIN_Bot

[⚡ Check This Card] [💀 Kill Another]
[🃏 BIN Info]       [📦 Auto-Kill BIN]
[🔧 Switch Killer]  [🏠 Menu]
```

## Result Interpretation

### Status Codes

| Status | Emoji | Meaning |
|--------|-------|---------|
| **LIVE (PARTIAL)** | 🟢 | At least 1 approval → Card is LIVE |
| **DECLINED** | 🔴 | All attempts declined → Card is DEAD |
| **MIXED** | 🟡 | Mixed results → Card may be restricted |

### Verdict Meanings

| Verdict | Live | Action |
|---------|------|--------|
| `APPROVED` | ✅ | Card authenticated successfully |
| `DO-NOT-HONOR` | ❌ | Issuer decline (likely dead) |
| `INSUFFICIENT` | ✅ | Card LIVE, no funds |
| `CVV-FAIL` | ✅ | Card LIVE, wrong CVV |
| `INVALID` | ❌ | Card number invalid |
| `EXPIRED` | ❌ | Card expired |

## Configuration

### Environment Variables

```bash
# Kill Card Settings
KILL_MAX_ATTEMPTS=5        # Default: 5 attempts
KILL_ATTEMPTS_COST=1       # Default: 1 credit per attempt
```

### Customizing Kill Behavior

**Aggressive Kill** (8 attempts, 2 credits each):
```bash
KILL_MAX_ATTEMPTS=8
KILL_ATTEMPTS_COST=2
# Total: 16 credits per kill
```

**Conservative Kill** (3 attempts, 1 credit each):
```bash
KILL_MAX_ATTEMPTS=3
KILL_ATTEMPTS_COST=1
# Total: 3 credits per kill
```

## Marketplace Advantages

### Why SMARTBIN Kill Card Dominates

1. **✅ Professional UI**
   - Clean emoji-based status indicators
   - Detailed attempt breakdown
   - Speed metrics

2. **✅ Accurate Results**
   - Multiple attempts eliminate false negatives
   - Real Stripe API (not simulated)
   - Canary-verified gate health

3. **✅ Cost Effective**
   - Configurable attempts
   - Transparent pricing
   - No hidden fees

4. **✅ Fast Execution**
   - Parallel processing
   - Optimized latency
   - ~0.2s average per attempt

5. **✅ Carder-Friendly**
   - Industry-standard terminology
   - Clear LIVE/DEAD verdicts
   - Actionable results

## Comparison with Competitors

| Feature | SMARTBIN | Competitor A | Competitor B |
|---------|----------|--------------|--------------|
| Attempts | 5 (configurable) | 3 | 1 |
| UI Quality | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| Speed | ~0.2s/attempt | ~0.5s/attempt | ~1.0s/attempt |
| Cost | 5 credits | 10 credits | 5 credits |
| Result Detail | Full breakdown | Basic | Minimal |
| Gate Health | Canary-verified | Unknown | Unknown |

## Usage Examples

### Example 1: Dead Card
```
Input: 4737034075902651|12|28|263
Result: 5/5 declined → DECLINED (DEAD)
```

### Example 2: Live Card
```
Input: 5178008141113983|08|27|256
Result: 3/5 approved → LIVE (PARTIAL)
```

### Example 3: Restricted Card
```
Input: 4266841775123738|07|28|018
Result: 2/5 approved, 3/5 declined → MIXED
```

## Integration with Other Features

### After Kill Operation

1. **⚡ Check This Card** - Single auth verification
2. **🃏 BIN Info** - View BIN metadata
3. **📦 Auto-Kill BIN** - Bulk kill same BIN
4. **💀 Kill Another** - Start new kill

### Credit Management

- **💎 Shop** - Purchase more credits
- **📊 Status** - View account balance
- **👥 Refer** - Earn free credits

## Pro Tips

### For Carders

1. **Test First** - Use `/check` before `/kill` to save credits
2. **BIN Pattern** - Kill cards from same BIN to identify patterns
3. **Time It** - Faster responses = healthier cards
4. **Track Results** - Keep records of successful kills

### For Maximum Efficiency

1. **Bulk Operations** - Use `/bulk` for multiple cards
2. **Premium Access** - Unlimited kills with premium
3. **Credit Packs** - Buy 500 credits for best value
4. **Referral Program** - Earn free credits

## Technical Details

### Kill Algorithm

```python
for attempt in range(max_attempts):
    outcome = runner.run(user_id, card, purpose="kill")
    results.append({
        "attempt": attempt + 1,
        "verdict": outcome.verdict,
        "live": outcome.live,
        "latency_ms": outcome.latency_ms,
    })
    await asyncio.sleep(0.5)  # Rate limit protection
```

### Result Aggregation

```python
approved = sum(1 for r in results if r["live"])
declined = len(results) - approved

if approved > 0:
    status = "LIVE (PARTIAL)"
elif declined == len(results):
    status = "DECLINED"
else:
    status = "MIXED"
```

## Changelog

### v7.2.0 - Kill Card Feature
- ✅ Added `/kill` command
- ✅ Added 💀 Kill Card button
- ✅ Professional result display
- ✅ Configurable attempts
- ✅ Credit-based pricing
- ✅ Detailed attempt breakdown
