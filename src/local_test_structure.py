#!/usr/bin/env python3
"""Deep DOM structure exploration for GGBet odds extraction."""
import asyncio, json
from playwright.async_api import async_playwright

GG_BET_URL = "https://gg.bet"

async def explore():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print("Loading gg.bet/esports...")
        await page.goto(f"{GG_BET_URL}/esports", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)

        # Strategy 1: Find all clickable elements that have a class containing "odd"
        # and trace up to find their sibling team name
        print("\n=== Odds buttons and their DOM context ===")

        # Find all odd-button elements
        odd_buttons = await page.locator('[data-test*="odd-button"]').all()
        print(f"Found {len(odd_buttons)} odd-button elements")

        for i, btn in enumerate(odd_buttons[:10]):
            try:
                # Get all text within the button
                texts = await btn.locator("*").all_inner_texts()
                texts = [t.strip() for t in texts if t.strip()]
                print(f"\nOdd button {i+1}: texts={texts}")

                # Try to get parent 3 levels up
                parent = btn
                for level in range(1, 6):
                    try:
                        parent = parent.locator("xpath=..")
                        parent_html = await parent.inner_html()
                        parent_text = await parent.inner_text()
                        # Look for "vs" in parent
                        if ' vs ' in parent_text.lower() or any(g in parent_text.lower() for g in ['cs', 'dota', 'valorant']):
                            print(f"  Parent level {level} contains match/tournament info!")
                            print(f"    Text (first 200 chars): {parent_text.strip()[:200]}")
                            break
                    except Exception as e:
                        print(f"  Parent level {level}: can't traverse ({e})")
                        break
            except Exception as e:
                print(f"Odd button {i+1}: error - {e}")

        # Strategy 2: Look for the section/group structure
        print("\n=== Looking for tournament/game sections ===")
        section_selectors = [
            '[class*="section"]', '[class*="group"]', '[class*="category"]',
            '[class*="tournament"]', '[class*="Tournament"]',
            '[class*="league"]', '[class*="League"]',
            '[class*="sport"]', '[class*="Sport"]',
        ]
        for sel in section_selectors:
            count = await page.locator(sel).count()
            if count > 0:
                print(f"\n{sel}: {count} elements")
                for i in range(min(count, 3)):
                    el = page.locator(sel).nth(i)
                    text = await el.inner_text()
                    print(f"  [{i+1}] {text.strip()[:150]}")

        # Strategy 3: Use page.evaluate to walk the DOM tree via JavaScript
        print("\n=== JavaScript DOM walk ===")
        dom_data = await page.evaluate("""
            () => {
                // Find all elements with odd/coef in class or data-test
                const odds = [];
                document.querySelectorAll('[data-test*="odd-button"]').forEach(el => {
                    const texts = [];
                    el.querySelectorAll('*').forEach(child => {
                        if (child.childNodes.length === 1 && child.childNodes[0].nodeType === 3) {
                            const t = child.textContent.trim();
                            if (t) texts.push(t);
                        }
                    });
                    // Find the closest parent that contains a tournament name or "vs"
                    let parent = el.parentElement;
                    let context = [];
                    for (let i = 0; i < 5 && parent; i++) {
                        const pt = parent.textContent;
                        if (pt.includes(' vs ') || /\\b(CS|Dota|Valorant|League|Overwatch|Rainbow)\\b/i.test(pt)) {
                            context.push(pt.slice(0, 300));
                            break;
                        }
                        parent = parent.parentElement;
                    }
                    odds.push({texts, context});
                });
                return odds;
            }
        """)
        for i, item in enumerate(dom_data[:10]):
            print(f"\nOdds button {i+1}:")
            print(f"  Texts: {item['texts']}")
            if item['context']:
                print(f"  Context: {item['context'][0][:200]}")

        # Strategy 4: Check for any JSON data embedded in script tags
        print("\n=== Checking for JSON in script tags ===")
        scripts = await page.locator("script").all()
        for i, script in enumerate(scripts[:20]):
            try:
                text = await script.inner_text()
                if text and len(text) > 100 and ("event" in text.lower() or "match" in text.lower() or "odd" in text.lower()):
                    print(f"Script {i+1} ({len(text)} chars): {text[:200]}")
            except:
                pass

        await browser.close()

if __name__ == "__main__":
    asyncio.run(explore())
