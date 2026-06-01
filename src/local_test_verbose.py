#!/usr/bin/env python3
"""Playwright GGBet test — verbose request/response logging to find data sources."""
import asyncio
import json
from playwright.async_api import async_playwright

GG_BET_URL = "https://gg.bet"
ESPORT_URL = f"{GG_BET_URL}/esports"

async def test_verbose():
    print("Launching Playwright (chromium, headless)...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        def log_request(req):
            print(f"  [REQ] {req.method} {req.url[:90]}")

        async def log_response(resp):
            url = resp.url
            status = resp.status
            if any(x in url for x in ["graphql", "api", "event", "match", "odd", "price"]):
                ct = resp.headers.get("content-type", "?")
                body_preview = ""
                try:
                    if ct.startswith("application/json"):
                        body = await resp.json()
                        if body and isinstance(body, dict):
                            body_preview = json.dumps(body)[:200]
                    else:
                        body_preview = (await resp.body())[:200].hex()
                except Exception:
                    body_preview = "(binary/shape-error)"
                print(f"  [RESP] {status} {url[:90]} | ct={ct[:40]} | body={body_preview}")

        page.on("request", log_request)
        page.on("response", log_response)

        print(f"Navigating to {ESPORT_URL}...")
        await page.goto(ESPORT_URL, wait_until="domcontentloaded", timeout=30000)
        print(f"domcontentloaded. Title: {await page.title()}")

        # Wait for JS load
        print("\nWaiting 15s for SPA hydration...")
        await page.wait_for_timeout(15000)
        print(f"networkidle approach: waiting for 2s idle...")
        await page.wait_for_load_state("networkidle", timeout=30000)
        print("networkidle reached.")

        # Check for a match list in the DOM
        print("\n--- DOM check ---")
        try:
            match_links = await page.locator("a[href*='match'], a[href*='event']").count()
            print(f"Match/event links found: {match_links}")
            team_names = await page.locator("text=/[A-Z][a-zA-Z ]+ vs [A-Z][a-zA-Z ]+/i").all_inner_texts()
            print(f"Team match texts: {team_names[:5]}")
        except Exception as e:
            print(f"DOM error: {e}")

        # Look for any matches on the page
        print("\n--- Looking for match widgets ---")
        for selector in ["[class*='match']", "[class*='event']", "[class*='fixture']", "[data-test*='match']"]:
            try:
                count = await page.locator(selector).count()
                if count > 0:
                    print(f"  Selector '{selector[:40]}': {count} elements")
            except:
                pass

        # Scroll to trigger lazy loading
        print("\n--- Scrolling to trigger more ---")
        for i in range(3):
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3000)
            print(f"  scroll {i+1}/3 done")

        await page.wait_for_timeout(3000)

        # Final DOM check
        print("\n--- Final DOM snapshot ---")
        try:
            body_text = await page.inner_text("body")
            if "vs" in body_text.lower():
                vs_matches = [line.strip() for line in body_text.split("\n") if "vs" in line.lower() and len(line.strip()) > 10]
                print(f"Lines containing 'vs': {len(vs_matches)}")
                for line in vs_matches[:10]:
                    print(f"  {line[:100]}")
            else:
                print("No 'vs' found in body text — probably still loading")
        except Exception as e:
            print(f"Body text error: {e}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(test_verbose())
