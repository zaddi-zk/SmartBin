#!/usr/bin/env python3
"""Credential rotation manager for payment gateways."""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Credential pools - ADD YOUR KEYS HERE
CREDENTIALS = {
    'stripe': {
        'keys': [
            # Add your Stripe keys here
            # 'sk_live_YOUR_KEY_HERE',
        ],
        'rotation': 'round_robin',  # round_robin, random, least_used
    },
    'authnet': {
        'accounts': [
            # Add AuthNet credentials
            # {'login': 'your_login', 'key': 'your_key'},
        ],
        'rotation': 'random',
    },
    'adyen': {
        'accounts': [
            # Add Adyen credentials
            # {'key': 'AyShY...', 'merchant': 'YourMerchant'},
        ],
        'rotation': 'round_robin',
    },
    'cybersource': {
        'accounts': [
            # {'merchant_id': '...', 'api_key': '...', 'api_secret': '...'}
        ],
        'rotation': 'random',
    },
    'checkout': {
        'keys': [],
        'rotation': 'random',
    },
    'worldpay': {
        'accounts': [],
        'rotation': 'random',
    },
    'shopify': {
        'keys': [],
        'rotation': 'random',
    },
    'paypal': {
        'accounts': [],
        'rotation': 'random',
    },
    'braintree': {
        'accounts': [],
        'rotation': 'random',
    },
    'square': {
        'keys': [],
        'rotation': 'random',
    },
}

# Track usage
USAGE_STATS = {}

class CredentialManager:
    def __init__(self):
        self.usage = {}
        self.load_stats()
    
    def load_stats(self):
        """Load usage stats from file"""
        stats_file = Path('credential_usage.json')
        if stats_file.exists():
            try:
                self.usage = json.loads(stats_file.read_text())
            except:
                self.usage = {}
    
    def save_stats(self):
        """Save usage stats"""
        stats_file = Path('credential_usage.json')
        stats_file.write_text(json.dumps(self.usage, indent=2))
    
    def get_credential(self, gate: str) -> dict[str, Any] | None:
        """Get next credential for gate based on rotation strategy"""
        if gate not in CREDENTIALS:
            return None
        
        config = CREDENTIALS[gate]
        rotation = config.get('rotation', 'random')
        
        # Get available credentials
        if 'keys' in config:
            creds = [{'key': k} for k in config['keys']]
        elif 'accounts' in config:
            creds = config['accounts']
        else:
            return None
        
        if not creds:
            return None
        
        # Apply rotation strategy
        if rotation == 'round_robin':
            return self._round_robin(gate, creds)
        elif rotation == 'least_used':
            return self._least_used(gate, creds)
        else:  # random
            return self._random(gate, creds)
    
    def _round_robin(self, gate: str, creds: list) -> dict | None:
        """Round robin rotation"""
        key = f"{gate}_round_robin_index"
        index = self.usage.get(key, 0)
        
        # Update index
        self.usage[key] = (index + 1) % len(creds)
        self.save_stats()
        
        return creds[index]
    
    def _least_used(self, gate: str, creds: list) -> dict | None:
        """Use least used credential"""
        # Find credential with lowest usage
        min_usage = float('inf')
        selected = None
        
        for cred in creds:
            cred_id = json.dumps(cred)
            usage = self.usage.get(cred_id, 0)
            if usage < min_usage:
                min_usage = usage
                selected = cred
        
        # Increment usage
        if selected:
            cred_id = json.dumps(selected)
            self.usage[cred_id] = self.usage.get(cred_id, 0) + 1
            self.save_stats()
        
        return selected
    
    def _random(self, gate: str, creds: list) -> dict | None:
        """Random selection"""
        selected = random.choice(creds)
        
        # Track usage
        cred_id = json.dumps(selected)
        self.usage[cred_id] = self.usage.get(cred_id, 0) + 1
        self.save_stats()
        
        return selected
    
    def record_success(self, gate: str, cred: dict):
        """Record successful transaction"""
        cred_id = json.dumps(cred)
        if cred_id not in self.usage:
            self.usage[cred_id] = {'success': 0, 'failure': 0}
        self.usage[cred_id]['success'] += 1
        self.save_stats()
    
    def record_failure(self, gate: str, cred: dict, reason: str = ''):
        """Record failed transaction"""
        cred_id = json.dumps(cred)
        if cred_id not in self.usage:
            self.usage[cred_id] = {'success': 0, 'failure': 0, 'last_error': ''}
        self.usage[cred_id]['failure'] += 1
        self.usage[cred_id]['last_error'] = reason
        self.save_stats()
    
    def get_stats(self, gate: str = None) -> dict:
        """Get usage statistics"""
        if gate:
            return {k: v for k, v in self.usage.items() if gate in k}
        return self.usage
    
    def add_credential(self, gate: str, cred: dict):
        """Add new credential to pool"""
        if gate not in CREDENTIALS:
            CREDENTIALS[gate] = {'keys': [], 'rotation': 'random'}
        
        if 'keys' in CREDENTIALS[gate]:
            CREDENTIALS[gate]['keys'].append(cred.get('key', cred))
        else:
            CREDENTIALS[gate]['accounts'].append(cred)
        
        # Save updated config
        self._save_config()
    
    def _save_config(self):
        """Save credential config"""
        config_file = Path('credentials_config.json')
        config_file.write_text(json.dumps(CREDENTIALS, indent=2))
    
    def remove_dead_credential(self, gate: str, cred: dict):
        """Remove dead credential from pool"""
        if gate not in CREDENTIALS:
            return
        
        if 'keys' in CREDENTIALS[gate]:
            CREDENTIALS[gate]['keys'] = [k for k in CREDENTIALS[gate]['keys'] 
                                         if k != cred.get('key', cred)]
        else:
            CREDENTIALS[gate]['accounts'] = [a for a in CREDENTIALS[gate]['accounts'] 
                                            if a != cred]
        
        self._save_config()

# Global instance
cred_manager = CredentialManager()

# Example usage
if __name__ == '__main__':
    # Add some test credentials
    cred_manager.add_credential('stripe', {'key': 'sk_live_TEST1'})
    cred_manager.add_credential('stripe', {'key': 'sk_live_TEST2'})
    cred_manager.add_credential('stripe', {'key': 'sk_live_TEST3'})
    
    # Get rotating credential
    for i in range(5):
        cred = cred_manager.get_credential('stripe')
        print(f"Request {i+1}: {cred}")
    
    # Show stats
    print("\nUsage stats:")
    print(cred_manager.get_stats('stripe'))
