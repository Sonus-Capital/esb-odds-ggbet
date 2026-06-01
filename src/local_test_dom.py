#!/usr/bin/env python3
"""Extract match data from GGBet's rendered DOM."""
import asyncio, json
from playwright.async_api import async_playwright

GG_BET_URL = "https://gg.bet"
ESPORT_URL = f"{GG_BET_URL}/esports"

async def test_dom():
    print("Launching Playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print(f"Navigating to {ESPORT_URL}...")
        await page.goto(ESPORT_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)

        # Find all match elements by various selectors
        print("\n=== Looking for match elements ===")
        selectors = {
            "data-test*=match": '[data-test*="match"]',
            "data-testid*=match": '[data-testid*="match"]',
            "class*=MatchCard": '[class*="MatchCard"]',
            "class*=EventCard": '[class*="EventCard"]',
            "class*=match-card": '[class*="match-card"]',
        }

        best_selector = None
        best_count = 0
        for name, sel in selectors.items():
            try:
                count = await page.locator(sel).count()
                if count > best_count:
                    best_count = count
                    best_selector = sel
                print(f"  {name}: {count} elements")
            except Exception as e:
                print(f"  {name}: error - {e}")

        if not best_selector:
            print("No match elements found!")
            await browser.close()
            return False

        print(f"\nUsing selector: {best_selector} ({best_count} elements)")

        # Extract data from each match element
        print("\n=== Extracting match data ===")
        matches = []
        for i in range(min(best_count, 10)):
            try:
                el = page.locator(best_selector).nth(i)
                html = await el.inner_html()
                text = await el.inner_text()

                # Try to find team names (look for "vs" patterns)
                clean_text = " ".join(text.split())  # normalize whitespace
                print(f"\nMatch {i+1}:")
                print(f"  Text: {clean_text[:200]}")

                # Look for child elements with specific patterns
                child_selectors = {
                    "link": 'a',
                    "teams": '[class*="team"], [class*="Team"], [class*="opponent"], [class*="Opponent"]',
                    "odds": '[class*="odd"], [class*="Odd"], [class*="price"], [class*="Price"], [class*="coef"], [class*="Coef"]',
                    "time": '[class*="time"], [class*="Time"], [class*="date"], [class*="Date"]',
                    "tournament": '[class*="tournament"], [class*="Tournament"], [class*="league"], [class*="League"]',
                }

                for child_name, child_sel in child_selectors.items():
                    try:
                        child_count = await el.locator(child_sel).count()
                        if child_count > 0:
                            child_texts = await el.locator(child_sel).all_inner_texts()
                            print(f"  {child_name} ({child_count}): {child_texts[:5]}")
                    except Exception as e:
                        pass

            except Exception as e:
                print(f"Match {i+1}: error - {e}")

        # Try a broader approach: look for any text containing "vs"
        print("\n=== All 'vs' matches on page ===")
        all_text = await page.inner_text("body")
        for line in all_text.split('\n'):
            line = line.strip()
            if ' vs ' in line.lower() and len(line) > 10 and len(line) < 200:
                print(f"  {line}")

        await browser.close()
        return True

if __name__ == "__main__":
    result = asyncio.run(test_dom())
    print(f"\n{'SUCCESS' if result else 'FAILED'}")
