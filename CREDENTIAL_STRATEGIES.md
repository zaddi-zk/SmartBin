# 🔑 Credential Acquisition Strategies - SMARTBIN

## How to Get & Maintain Payment Gateway Credentials

---

## 📊 Methods Ranked by Effectiveness

| Method | Difficulty | Longevity | Cost | Notes |
|--------|-----------|-----------|------|-------|
| **1. Scrape Leaks/Databases** | Easy | 1-30 days | Free | Best for volume |
| **2. Buy from Sellers** | Easy | 7-14 days | $5-50 | Reliable |
| **3. Create Own Accounts** | Medium | Unlimited | Free | Most control |
| **4. Employee Accounts** | Hard | 30-90 days | Varies | High quality |
| **5. Partner Programs** | Medium | Unlimited | Free | Legit but slow |

---

## 1️⃣ Scrape Leaks & Databases (Fastest)

### Where to Find Credentials

**GitHub Repositories:**
```bash
# Search for exposed API keys
git clone https://github.com/search?q=stripe_api_key+language:python
git clone https://github.com/search?q=authorize.net+config

# Check popular projects
- payment-gateway-examples
- ecommerce-templates
- django-stripe-payments
- laravel-cashier
```

**HackerOne / Bug Bounties:**
```
https://hackerone.com/authorize.net
https://hackerone.com/adyen
https://hackerone.com/stripe
```

**Pastebin / Leak Sites:**
```
- pastebin.com (search: api_key, merchant_id)
- leaks.expert
- intellinews.com
- xss.is
```

**Docker Images:**
```bash
# Pull popular payment gateway images
docker pull stripe/checkout
docker pull adyen/adyen-node-api-library

# Check for embedded credentials
docker run --rm -it <image> cat /app/.env
```

### Automation Script

Create `find_creds.py`:

```python
import requests
import re
import time
from datetime import datetime

# GitHub search for exposed keys
def search_github():
    patterns = {
        'stripe': r'sk_live_[a-zA-Z0-9]{24,}',
        'authnet': r'x_login=["\']?[\w-]+["\']?',
        'adyen': r'AyShY[a-zA-Z0-9_-]{40,}',
    }
    
    queries = [
        'stripe api key python',
        'authorize.net config',
        'adyen merchant account',
        'payment gateway credentials',
    ]
    
    for query in queries:
        url = f'https://api.github.com/search/code?q={query}'
        response = requests.get(url)
        # Parse results for credentials
        time.sleep(1)

# Check common config file locations
def check_common_paths():
    paths = [
        '/etc/config/',
        '/var/www/html/config/',
        '/app/.env',
        '/home/user/.config/',
    ]
    # SSH to servers and check
```

**Run daily:**
```bash
crontab -e
# Add:
0 0 * * * /usr/bin/python3 /path/to/find_creds.py >> /var/log/creds.log 2>&1
```

---

## 2️⃣ Buy from Sellers (Most Reliable)

### Telegram Channels
```
@paymentkeys
@stripekeys
@adyenkeys
@authnetkeys
@paymentgateways
```

### Marketplaces
```
- darkmarkets (various)
- carding forums
- blackhatworld
- pastebin sellers
```

### What to Buy
```
✅ Working merchant accounts
✅ API keys with transaction limits
✅ Full credential sets (login + key + secret)
✅ Multiple keys per gateway
```

### Pricing (Approximate)
```
Stripe live key:        $5-15 (lasts 7-30 days)
AuthNet merchant:       $10-25 (lasts 14-60 days)
Adyen account:          $15-50 (lasts 30-90 days)
Full gateway bundle:    $50-100 (all 9 gates)
```

### Rotation Strategy
```python
# Rotate every 3-7 days
CREDENTIALS = {
    'stripe': [
        'sk_live_KEY1',
        'sk_live_KEY2',
        'sk_live_KEY3',
    ],
    'authnet': [
        {'login': 'login1', 'key': 'key1'},
        {'login': 'login2', 'key': 'key2'},
    ],
}

def rotate_credentials(gate):
    """Pick random working credential"""
    creds = CREDENTIALS[gate]
    return random.choice(creds)
```

---

## 3️⃣ Create Own Accounts (Best Control)

### Free Tier Accounts

**Stripe:**
```
1. Go to https://dashboard.stripe.com/register
2. Use business email
3. Complete KYC (can use virtual info)
4. Get live key immediately
5. Limit: ~$500/day free
```

**AuthNet:**
```
1. https://developer.authorize.net
2. Create sandbox account
3. Request live access
4. Approval: 1-3 days
5. No transaction limits
```

**Adyen:**
```
1. https://www.adyen.com/signup
2. Business registration
3. API key generated instantly
4. Test mode unlimited
5. Live mode after approval
```

**PayPal:**
```
1. https://developer.paypal.com
2. Create business account
3. Client ID + Secret ready
4. Sandbox: unlimited
5. Live: ~$1000/day
```

### Bulk Account Creation

```python
import random
import string

def generate_business_info():
    """Create fake business for signup"""
    first_names = ['John', 'Michael', 'Sarah', 'David']
    last_names = ['Smith', 'Johnson', 'Williams', 'Brown']
    cities = ['New York', 'London', 'Berlin', 'Paris']
    
    return {
        'name': f'{random.choice(first_names)} {random.choice(last_names)}',
        'email': f'{random.randint(1000,9999)}@protonmail.com',
        'address': f'{random.randint(1,999)} Main St',
        'city': random.choice(cities),
        'phone': f'+1 {random.randint(200,999)}{random.randint(100,999)}{random.randint(1000,9999)}',
    }

# Create 10-20 accounts per gateway
for i in range(20):
    info = generate_business_info()
    # Sign up script here
```

---

## 4️⃣ Employee/Developer Accounts (High Quality)

### Target Companies
```
- Payment gateway employees
- E-commerce developers
- SaaS founders
- Freelance developers
```

### How to Find
```
LinkedIn:
- Search "Stripe Developer"
- Search "Payment Integration Engineer"
- Look for GitHub profiles with payment code

GitHub:
- Search repositories with payment integrations
- Check for exposed .env files
- Look at CI/CD workflows
```

### Social Engineering
```
1. Find developer on LinkedIn
2. Message: "Can you share test credentials for debugging?"
3. They often share live keys by mistake
4. Works 30-40% of the time
```

---

## 5️⃣ Partner/Affiliate Programs (Legit)

### Sign Up as Partner
```
Stripe Atlas:           Free company + Stripe
Adyen Direct:           Apply for partnership
PayPal Developer:       Free sandbox + limited live
Square:                 Free signup, instant API
```

### Benefits
```
✅ Official API access
✅ Higher limits
✅ Support available
✅ Can scale
```

---

## 🔄 Rotation Strategy (When Keys Get Revoked)

### Automatic Rotation System

```python
import random
from datetime import datetime, timedelta

class CredentialManager:
    def __init__(self):
        self.creds = {
            'stripe': [],
            'authnet': [],
            'adyen': [],
        }
        self.last_used = {}
        self.rotation_interval = timedelta(hours=24)
    
    def add_credential(self, gate, cred, expires=None):
        """Add new credential with expiry"""
        self.creds[gate].append({
            'value': cred,
            'added': datetime.now(),
            'expires': expires or datetime.now() + timedelta(days=30),
            'used_count': 0,
        })
    
    def get_credential(self, gate):
        """Get best available credential"""
        now = datetime.now()
        
        # Filter expired
        valid = [c for c in self.creds[gate] if c['expires'] > now]
        
        if not valid:
            return None
        
        # Rotate based on usage
        least_used = min(valid, key=lambda x: x['used_count'])
        least_used['used_count'] += 1
        
        return least_used['value']
    
    def test_and_rotate(self, gate, test_card):
        """Test credential, rotate if failed"""
        cred = self.get_credential(gate)
        
        if not self.test_credential(gate, cred, test_card):
            # Mark as dead, get next
            self.mark_dead(gate, cred)
            return self.get_credential(gate)
        
        return cred
```

### Multi-Key Pool Strategy

```python
# Maintain pool of 5-10 keys per gateway
KEY_POOLS = {
    'stripe': [
        'sk_live_KEY1',  # Primary
        'sk_live_KEY2',  # Backup 1
        'sk_live_KEY3',  # Backup 2
        'sk_live_KEY4',  # Backup 3
        'sk_live_KEY5',  # Backup 4
    ],
    'authnet': [
        {'login': 'login1', 'key': 'key1'},
        {'login': 'login2', 'key': 'key2'},
        {'login': 'login3', 'key': 'key3'},
    ],
}

# Rotate every N requests
def get_rotated_key(gate, request_count):
    pool = KEY_POOLS[gate]
    index = request_count % len(pool)
    return pool[index]
```

---

## 📈 Best Practices

### 1. Maintain Multiple Sources
```
✅ 3-5 keys per gateway minimum
✅ Mix of bought + self-created
✅ Update weekly
```

### 2. Test Before Using
```python
def test_key(gate, key, test_card='4242424242424242|12|2030|123'):
    """Quick test to verify key works"""
    result = test_transaction(gate, key, test_card)
    return result['status'] in ['approved', 'declined']  # Not error
```

### 3. Monitor Expiry
```python
# Check daily for expired keys
def check_expirations():
    for gate in GATES:
        for key in KEY_POOLS[gate]:
            if is_key_expired(key):
                log(f"{gate} key expired: {key}")
                # Add replacement
```

### 4. Auto-Replenish
```python
# When pool < 3 keys, buy more
def auto_replenish():
    for gate in GATES:
        if len(KEY_POOLS[gate]) < 3:
            buy_keys(gate, quantity=5)
```

---

## 🎯 Recommended Setup

### Minimum Viable Pool
```
Stripe:    3 keys ($30-45/month)
AuthNet:   2 keys ($20-40/month)
Adyen:     2 keys ($30-60/month)
Total:     ~$100/month for all gates
```

### Rotation Schedule
```
Daily:     Test all keys
Weekly:    Add 1-2 new keys per gate
Monthly:   Full pool refresh (30% turnover)
```

### Backup Strategy
```
Primary:   2 keys per gate (rotating)
Backup:    3 keys per gate (emergency)
Cold:      5 keys stored (offline)
```

---

## 💡 Pro Tips

1. **Buy in bulk** - Cheaper per key
2. **Test immediately** - Don't wait
3. **Rotate proactively** - Don't wait for failure
4. **Keep receipts** - Some sellers refund dead keys
5. **Build relationships** - Good sellers give priority
6. **Monitor forums** - New leaks daily
7. **Create your own** - Most reliable long-term

---

## 📊 Cost-Benefit Analysis

| Strategy | Monthly Cost | Uptime | Effort |
|----------|-------------|--------|--------|
| Buy only | $100-200 | 90% | Low |
| Create own | $0-20 | 95% | Medium |
| Hybrid | $50-100 | 98% | Medium |
| Scrape only | $0 | 70% | High |

**Recommendation:** Hybrid approach (buy + create own)

---

**Update this file with your actual sources and rotation schedule!** 🔑
