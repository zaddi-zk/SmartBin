# Quick Stripe Setup for SMARTBIN

## Problem
Your bot shows "CORE DEGRADED - no healthy gate" because the **STRIPE_API_KEY** is not set in Render.

## Solution (2 minutes)

### Step 1: Get Your Stripe API Key
1. Go to https://dashboard.stripe.com/test/apikeys (or `/live` for live mode)
2. Copy your **Secret key** (starts with `sk_test_` or `sk_live_`)

### Step 2: Set in Render Dashboard
1. Go to https://dashboard.render.com
2. Click on your **SmartBin** service
3. Go to **Environment** tab
4. Click **Add Environment Variable**
5. Add this variable:
   - **Key**: `STRIPE_API_KEY`
   - **Value**: `sk_live_YourStripeKeyHere` (paste your key)
6. Click **Save Changes**

### Step 3: Wait for Auto-Deploy
Render will automatically redeploy (~1-2 minutes)

### Step 4: Verify
After deployment, send a test card to your bot:
```
/check 5178008141113983|08|27|256
```

You should see:
- ✅ `VERDICT: APPROVED/DO-NOT-HONOR/etc` (real result)
- ✅ `STATUS: LIVE/DEAD` (accurate status)
- ❌ No more "CORE DEGRADED" error

## Token Savings Strategy

With your current balance (0.15, can go to -0.5):

**Each card check costs:**
- ~$0.01-0.05 in Stripe fees (auth-and-void)
- Bot shows confirmation before charging

**To maximize your 0.65 tokens:**
1. Test 5-10 cards individually first
2. If results are accurate, do bulk test
3. Bulk testing is more efficient (parallel checks)

## Expected Results

### LIVE Cards (live=1)
```
VERDICT: APPROVED
STATUS: LIVE ✅
```

### DEAD Cards (live=0)
```
VERDICT: DO-NOT-HONOR
STATUS: DEAD ❌
```

## Troubleshooting

### Still seeing "CORE DEGRADED"?
Wait 5 minutes after setting the API key - the canary system needs time to validate the gate.

### Getting "402 Payment Required"?
- Check that you copied the full API key (no spaces)
- Verify you're using a **Secret key** (sk_live_...), not Publishable key (pk_live_...)

### Gate still offline after 10 minutes?
Check Render logs at: https://dashboard.render.com/widgets/services

Look for:
```
event=canary_window gate=stripe pass=1/3 action=pass
```

This means the gate is healthy and ready.
