#!/usr/bin/env python3
"""Extract match + odds data from GGBet's rendered DOM."""
import asyncio, json, re
from playwright.async_api import async_playwright

GG_BET_URL = "https://gg.bet"
ESPORT_URL = f"{GG_BET_URL}/esports"

async def test_odds_dom():
    print("Launching Playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print(f"Navigating to {ESPORT_URL}...")
        await page.goto(ESPORT_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)

        # Get full page HTML and look for odds patterns
        print("\n=== Scanning for odds/coef values in DOM ===")

        # Look for numeric values that look like odds (1.00-50.00)
        html = await page.content()
        odds_pattern = re.compile(r'>([1-9]\.\d{2})<')
        odds_found = odds_pattern.findall(html)
        if odds_found:
            print(f"Odds-like values in HTML: {odds_found[:20]}")

        # Look for specific odds container selectors
        odds_selectors = [
            '[class*="odds"]', '[class*="Odds"]', '[class*="coef"]', '[class*="Coef"]',
            '[class*="price"]', '[class*="Price"]', '[class*="rate"]', '[class*="Rate"]',
            '[class*="market"]', '[class*="Market"]',
            '[data-test*="odd"]', '[data-test*="coef"]', '[data-test*="price"]',
        ]

        for sel in odds_selectors:
            count = await page.locator(sel).count()
            if count > 0:
                print(f"\n  Found {count} elements with '{sel}'")
                for i in range(min(count, 3)):
                    el = page.locator(sel).nth(i)
                    text = await el.inner_text()
                    html_snippet = await el.inner_html()
                    print(f"    [{i+1}] text='{text.strip()[:60]}' html='{html_snippet[:200]}'")

        # Deep dive: look at the structure around match elements
        print("\n=== Deep dive on match elements ===")
        match_sel = '[data-test*="match"]'
        match_count = await page.locator(match_sel).count()
        print(f"Match elements: {match_count}")

        for i in range(min(match_count, 5)):
            el = page.locator(match_sel).nth(i)
            text = await el.inner_text()
            print(f"\nMatch {i+1} full text: {text.strip()[:200]}")

            # Try to find all child text nodes with numbers
            all_child_text = await el.locator("*").all_inner_texts()
            for child_text in all_child_text:
                child_text = child_text.strip()
                if child_text and len(child_text) < 50:
                    # Check if it looks like an odds value
                    try:
                        val = float(child_text)
                        if 1.0 <= val <= 50.0:
                            print(f"  => ODDS VALUE: {val}")
                    except ValueError:
                        if 'vs' not in child_text.lower() and len(child_text) > 1:
                            print(f"  => text: '{child_text}'")

        # Try clicking a game filter to get a specific game's page
        print("\n=== Looking for game filter links ===")
        game_links = []
        for sel in ['a[href*="cs"]', 'a[href*="dota"]', 'a[href*="valorant"]', 'a[href*="league"]',
                    '[class*="sport"]', '[class*="Sport"]', '[class*="game"]', '[class*="Game"]']:
            els = await page.locator(sel).all()
            for el in els:
                text = await el.inner_text()
                href = await el.get_attribute("href")
                if text and href and any(g in text.lower() + href.lower() for g in ['cs', 'dota', 'valorant', 'league', 'overwatch', 'rainbow']):
                    game_links.append((text.strip()[:30], href))

        # Deduplicate
        game_links = list(dict.fromkeys(game_links))
        print(f"Found {len(game_links)} game links:")
        for text, href in game_links[:10]:
            print(f"  {text} -> {href}")

        await browser.close()
        return True

if __name__ == "__main__":
    result = asyncio.run(test_odds_dom())
    print(f"\n{'SUCCESS' if result else 'FAILED'}")
