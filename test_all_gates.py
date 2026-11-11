#!/usr/bin/env python3
"""Test all gates with real card - honest results, no guesses."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "smartbin"))

from config import Config
from core.gate import GateManager
from core.verdicts import map_result
from models import Card

# Test card
TEST_CARD = "5439300230168480|03|32|609"

async def test_gates():
    """Test each gate implementation with real card."""
    config = Config.load()
    gates = GateManager(config)
    card = Card.parse(TEST_CARD)
    
    print("=" * 70)
    print(f"🧪 TESTING ALL GATES with card: {card.masked()}")
    print("=" * 70)
    print()
    
    # List of gates in database
    all_gates = [
        "stripe", "authnet", "adyen", "cybersource", 
        "checkout", "worldpay", "shopify", "paypal", 
        "braintree", "square"
    ]
    
    results = []
    
    for gate_name in all_gates:
        print(f"\n🚪 Testing {gate_name.upper()}...")
        print("-" * 70)
        
        # Check if gate has implementation
        has_implementation = hasattr(gates, f"_{gate_name}")
        
        if not has_implementation:
            print(f"  ❌ NO CODE IMPLEMENTATION")
            print(f"     Gate exists in DB but no API integration in code")
            results.append({
                "gate": gate_name,
                "status": "NO_CODE",
                "verdict": "N/A",
                "latency_ms": 0,
                "note": "No implementation in gate.py"
            })
            continue
        
        # Test the gate
        try:
            result = await gates.dispatch(
                card=card,
                amount_minor=100,
                proxy=None,
                purpose="test",
                gate_name=gate_name,
                canary_ok=1,
                proxy_id=0,
            )
            
            outcome = map_result(result, 1)
            latency = result.get("latency_ms", 0)
            error = result.get("error", "")
            
            # Check for credentials missing
            if "credentials missing" in error.lower():
                status = "⚠️ NO CREDENTIALS"
                print(f"  {status}")
                print(f"     Latency: {latency}ms")
                print(f"     Error: {error[:100]}")
                print(f"     Status: Code works but needs API credentials")
                results.append({
                    "gate": gate_name,
                    "status": status,
                    "verdict": outcome.verdict,
                    "latency_ms": latency,
                    "note": f"CODE OK - {error}"
                })
            elif latency == 0 and error:
                status = "❌ ERROR"
                print(f"  {status} {error[:80]}")
                print(f"     Latency: {latency}ms")
                results.append({
                    "gate": gate_name,
                    "status": status,
                    "verdict": outcome.verdict,
                    "latency_ms": latency,
                    "note": error
                })
            else:
                status = "✅ WORKS" if outcome.live or outcome.verdict != "INCONCLUSIVE" else "⚠️ PARTIAL"
                print(f"  {status} {outcome.verdict}")
                print(f"     Latency: {latency}ms")
                print(f"     Live: {bool(outcome.live)}")
                print(f"     Note: {outcome.note[:100]}")
                
                results.append({
                    "gate": gate_name,
                    "status": status,
                    "verdict": outcome.verdict,
                    "latency_ms": latency,
                    "note": outcome.note
                })
            
        except Exception as e:
            print(f"  ❌ ERROR: {str(e)[:100]}")
            results.append({
                "gate": gate_name,
                "status": "ERROR",
                "verdict": "ERROR",
                "latency_ms": 0,
                "note": str(e)
            })
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    
    working = sum(1 for r in results if r["status"] == "✅ WORKS")
    no_credentials = sum(1 for r in results if r["status"] == "⚠️ NO CREDENTIALS")
    no_code = sum(1 for r in results if r["status"] == "NO_CODE")
    errors = sum(1 for r in results if r["status"] == "❌ ERROR")
    
    print(f"\n  ✅ Working (with credentials):  {working}")
    print(f"  ⚠️  Code OK, needs credentials: {no_credentials}")
    print(f"  ❌ No code implementation:      {no_code}")
    print(f"  ❌ Errors:                      {errors}")
    print(f"  📊 Total gates:                {len(results)}")
    
    print("\n  Detailed Results:")
    print("-" * 70)
    for r in results:
        print(f"  {r['gate']:15} {r['status']:25} {r['verdict']:15} {r['latency_ms']:5}ms")
    
    print("\n" + "=" * 70)
    print("💡 RECOMMENDATION:")
    print("=" * 70)
    
    if working == 0 and no_credentials > 0:
        print(f"  ℹ️  All {no_credentials} gates have code but need API credentials")
        print(f"     Add credentials to .env or Render dashboard to enable them")
    elif working > 0:
        print(f"  ✅ {working} gate(s) working with credentials")
        if no_credentials > 0:
            print(f"  ℹ️  {no_credentials} more gates ready - just add credentials")
    else:
        print("  ⚠️  NO GATES WORKING - Add credentials or enable test mode")
    
    return results

if __name__ == "__main__":
    results = asyncio.run(test_gates())
