#!/usr/bin/env python3
"""
Quick card testing script for SMARTBIN bot.
Tests cards individually or in bulk mode.
"""

import asyncio
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# Card data
INDIVIDUAL_CARDS_FILE = Path("test_individual_cards.txt")
BULK_CARDS_FILE = Path("test_bulk_cards.txt")


def load_cards(filepath: Path) -> list[str]:
    """Load cards from file."""
    if not filepath.exists():
        log.error(f"File not found: {filepath}")
        return []
    
    with open(filepath, 'r') as f:
        return [line.strip() for line in f if line.strip()]


def display_test_commands(cards: list[str], mode: str = "individual"):
    """Display commands for testing cards."""
    if mode == "individual":
        log.info("\n" + "=" * 60)
        log.info("INDIVIDUAL TEST COMMANDS")
        log.info("=" * 60)
        log.info("Send these commands to the bot one by one:\n")
        
        for i, card in enumerate(cards, 1):
            log.info(f"{i}. /check {card}")
        
        log.info(f"\nTotal: {len(cards)} cards")
    
    elif mode == "bulk":
        log.info("\n" + "=" * 60)
        log.info("BULK TEST COMMAND")
        log.info("=" * 60)
        log.info("1. Send: /bulk")
        log.info("2. Paste all cards below:\n")
        
        for card in cards:
            log.info(card)
        
        log.info(f"\nTotal: {len(cards)} cards")


def analyze_cards(cards: list[str]):
    """Analyze card data for testing."""
    log.info("\n" + "=" * 60)
    log.info("CARD ANALYSIS")
    log.info("=" * 60)
    
    bins = set()
    exp_dates = {}
    
    for card in cards:
        parts = card.split('|')
        if len(parts) == 4:
            number, month, year, cvv = parts
            bin8 = number[:8]
            bins.add(bin8)
            
            exp_key = f"{month}/{year}"
            exp_dates[exp_key] = exp_dates.get(exp_key, 0) + 1
    
    log.info(f"\nUnique BINs: {len(bins)}")
    for bin8 in sorted(bins):
        log.info(f"  - {bin8}")
    
    log.info(f"\nExpiry distribution:")
    for exp, count in sorted(exp_dates.items()):
        log.info(f"  - {exp}: {count} card(s)")


async def main():
    """Main testing function."""
    log.info("=" * 60)
    log.info("SMARTBIN Card Test Runner")
    log.info("=" * 60)
    
    # Load cards
    individual_cards = load_cards(INDIVIDUAL_CARDS_FILE)
    bulk_cards = load_cards(BULK_CARDS_FILE)
    
    if not individual_cards and not bulk_cards:
        log.error("No cards found. Run test_cards.py first.")
        return
    
    # Display options
    log.info("\nChoose test mode:")
    log.info("  1. Individual tests (5 cards - saves tokens)")
    log.info("  2. Bulk tests (all cards)")
    log.info("  3. Show analysis")
    log.info("  4. All of the above")
    
    # For demo, show all
    if individual_cards:
        display_test_commands(individual_cards, "individual")
    
    if bulk_cards:
        display_test_commands(bulk_cards, "bulk")
    
    if bulk_cards:
        analyze_cards(bulk_cards)
    
    log.info("\n" + "=" * 60)
    log.info("TESTING TIPS")
    log.info("=" * 60)
    log.info("1. Start with individual tests to verify bot behavior")
    log.info("2. Watch for 'Live: YES' (LIVE) vs 'Live: NO' (DEAD)")
    log.info("3. Bulk test last to save tokens")
    log.info("4. Record results to compare expected vs actual")


if __name__ == "__main__":
    asyncio.run(main())
