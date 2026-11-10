# 🐛 Mini App Fixes & Troubleshooting

## Issues Fixed

### 1. "Cannot Initialize WebView" Error ✅
**Cause:** Missing Telegram WebApp initialization and CORS headers

**Fix:**
- Added `tg.ready()` before `tg.expand()`
- Added CORS middleware with `Access-Control-Allow-Origin: *`
- Added fallback for standalone mode (non-Telegram)

### 2. API Calls Failing ✅
**Cause:** Missing `X-Telegram-Init-Data` header

**Fix:**
- Added `getInitHeaders()` function
- All API calls now include init_data from URL params
- Added proper Content-Type headers

### 3. Missing /api/check Endpoint ✅
**Cause:** Endpoint didn't exist in server

**Fix:**
- Added full `/api/check` POST endpoint
- Supports card verification with credit deduction
- Returns proper JSON with live/approved status

### 4. Health Check Incomplete ✅
**Cause:** Missing canary stats

**Fix:**
- Updated `/api/health` to include:
  - `canary_24h.total`
  - `canary_24h.successful`
  - `latency_p50`

---

## Testing Checklist

### 1. Open Mini App
```
1. Open Telegram bot
2. Tap "📱 Mini App" button
3. Should load without errors
```

### 2. Check Console
```javascript
// Should see:
Telegram WebApp initialized
// OR (if standalone):
Telegram WebApp not available, running in standalone mode
```

### 3. Test BIN Lookup
```
1. Go to 🃏 BIN tab
2. Enter: 45717360
3. Tap "🔍 Lookup BIN"
4. Should show BIN details
```

### 4. Test Check Card
```
1. Go to ⚡ Check tab
2. Enter: 4242424242424242|12|2030|123
3. Tap "⚡ Run Check"
4. Should show result (requires STRIPE_API_KEY)
```

### 5. Test Kill Card
```
1. Go to 💀 Kill tab
2. Enter: 4737034075902651|12|28|263
3. Tap "💀 Execute Kill"
4. Should run 5 attempts (requires STRIPE_API_KEY + 5 credits)
```

---

## Common Issues & Solutions

### Issue: "Unauthorized" (401)
**Cause:** Missing or invalid init_data

**Solution:**
- Open Mini App from Telegram bot (not browser)
- Check URL has `?init_data=...` parameter
- Verify BOT_TOKEN is correct

### Issue: "Invalid Card Format"
**Cause:** Wrong card format

**Solution:**
- Use format: `number|MM|YY|CVV`
- Example: `4242424242424242|12|2030|123`
- No spaces or dashes

### Issue: "Insufficient Credits"
**Cause:** Not enough credits

**Solution:**
- Check credit balance in header
- Visit 💎 Shop to purchase credits
- Kill costs 5 credits, Check costs 1 credit

### Issue: "Check Failed" (503)
**Cause:** STRIPE_API_KEY not set or gate issues

**Solution:**
1. Set STRIPE_API_KEY in Render dashboard
2. Wait 5 minutes for canary to warm up
3. Check Render logs for errors

### Issue: Mini App Not Loading
**Cause:** Deployment still in progress

**Solution:**
1. Wait 2-3 minutes after deploy
2. Clear Telegram cache: Settings → Data and Storage → Clear Cache
3. Reopen bot

---

## Debug Mode

### View Network Requests
```javascript
// In browser console (if opened in browser):
fetch('https://smartbin-eurr.onrender.com/miniapp/api/health')
  .then(r => r.json())
  .then(console.log)
```

### Check Telegram Init Data
```javascript
// In browser console:
console.log(window.Telegram.WebApp.initDataUnsafe)
```

### Test API Directly
```bash
# Health check
curl https://smartbin-eurr.onrender.com/miniapp/api/health

# BIN lookup
curl "https://smartbin-eurr.onrender.com/miniapp/api/bin?bin=45717360" \
  -H "X-Telegram-Init-Data: YOUR_INIT_DATA"
```

---

## Server Logs

### Check Render Logs
```
1. Go to https://dashboard.render.com
2. Click SmartBin service
3. Click "View Logs"
4. Look for:
   - event=miniapp_verify_failed
   - event=miniapp_check_failed
   - event=miniapp_bin_failed
```

### Common Log Messages
```
✅ event=miniapp_index_served      # HTML loaded
✅ event=miniapp_touch_user        # User activity recorded
⚠️ event=miniapp_verify_failed     # Init data invalid
⚠️ event=miniapp_check_failed      # Card check error
❌ event=miniapp_unhandled         # Unexpected error
```

---

## CORS Configuration

### Headers Set
```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type, X-Telegram-Init-Data
Access-Control-Max-Age: 86400
```

### Cache Control
```
Cache-Control: no-store, no-cache, must-revalidate, max-age=0
Pragma: no-cache
Expires: 0
X-Content-Type-Options: nosniff
```

---

## API Endpoints

### GET /
Returns: `index.html` (Mini App)

### GET /api/health
Returns:
```json
{
  "status": "healthy",
  "version": "7.2.0",
  "canary_24h": { "total": 100, "successful": 95 },
  "latency_p50": 245
}
```

### GET /api/bin?bin=45717360
Headers: `X-Telegram-Init-Data`
Returns:
```json
{
  "bin": "45717360",
  "brand": "VISA",
  "bank": "Chase Bank",
  "country": "United States",
  ...
}
```

### POST /api/check
Headers: `X-Telegram-Init-Data`, `Content-Type: application/json`
Body:
```json
{
  "card": "4242424242424242|12|2030|123",
  "amount": 1.00
}
```
Returns:
```json
{
  "live": true,
  "verdict": "APPROVED",
  "latency_ms": 245,
  "credits": 45
}
```

### GET /api/status
Headers: `X-Telegram-Init-Data`
Returns: User stats and system status

### GET /api/reflink?user_id=123
Headers: `X-Telegram-Init-Data`
Returns:
```json
{
  "ref_link": "https://t.me/SmartBlN_Bot?start=ref_123"
}
```

---

## Performance Tips

### 1. Minimize API Calls
- Cache BIN results locally
- Batch bulk operations
- Use debouncing for search

### 2. Optimize Images
- Use SVG icons (inline)
- No external images
- Google Fonts (cached)

### 3. Reduce Bundle Size
- Vanilla JS (no frameworks)
- Single file (~50KB)
- CSS custom properties

### 4. Fast Loading
- Inline critical CSS
- Async JavaScript
- Lazy load sections

---

## Security

### Init Data Verification
```python
# Server validates Telegram init_data
def verify_init_data(init_data: str, bot_token: str):
    # HMAC-SHA256 check
    # Returns user data or None
```

### Credit Deduction
```python
# Atomic credit deduction
await runtime.db.user_spend(user_id, 1)
```

### Rate Limiting
```
- 1 check/second per user
- 20 cards max per bulk
- 5 attempts per kill
```

---

## Version History

### v7.2.1 (Current) - 2026-11-10
- ✅ Fixed WebView initialization
- ✅ Added CORS middleware
- ✅ Added /api/check endpoint
- ✅ Fixed init_data headers
- ✅ Improved error handling

### v7.2.0 - Previous
- Added Kill Card feature
- Professional gold Mini App
- UI overhaul

---

## Support

### Contact
- **Telegram:** @SmartBin_info
- **Bot:** @SmartBlN_Bot

### Resources
- **Render Dashboard:** https://dashboard.render.com
- **GitHub:** https://github.com/zaddi-zk/SmartBin
- **Mini App:** https://smartbin-eurr.onrender.com/miniapp

---

**Status:** ✅ All fixes deployed  
**Version:** 7.2.1  
**Last Updated:** 2026-11-10
