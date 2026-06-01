#!/usr/bin/env python3
"""Playwright GGBet test — intercept SPA's own GQL responses."""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

GG_BET_URL = "https://gg.bet"
ESPORT_URL = f"{GG_BET_URL}/esports"

results = {"bettingData": None, "sports": [], "events": []}

async def test_intercept():
    print("Launching Playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Intercept response bodies for GQL calls
        async def handle_response(response):
            if "graphql" in response.url:
                try:
                    body = await response.json()
                    # Look for known structures
                    if body.get("data", {}).get("bettingData"):
                        results["bettingData"] = body["data"]["bettingData"]
                        print(f"  >> Intercepted bettingData — token obtained")
                    if body.get("data", {}).get("sports"):
                        results["sports"] = body["data"]["sports"]
                        print(f"  >> Intercepted sports — {len(results['sports'])} sports")
                    events = body.get("data", {}).get("matches", {}).get("sportEvents", [])
                    if events:
                        results["events"].extend(events)
                        print(f"  >> Intercepted events — {len(events)} matches")
                except Exception:
                    pass

        page.on("response", handle_response)

        print(f"Navigating to {ESPORT_URL}...")
        await page.goto(ESPORT_URL, wait_until="networkidle", timeout=30000)
        print(f"Page loaded: {await page.title()}")

        # Wait a few seconds for SPA to hydrate and make calls
        await page.wait_for_timeout(5000)

        # Scroll to trigger lazy loading if needed
        print("\nScrolling to trigger more data loads...")
        for _ in range(3):
            await page.evaluate("() => { window.scrollTo(0, document.body.scrollHeight); }")
            await page.wait_for_timeout(3000)

        await browser.close()

        # Report
        print(f"\n{'='*50}")
        print(f"Token obtained: {results['bettingData'] is not None}")
        if results['bettingData']:
            print(f"SB endpoint: https:{results['bettingData']['endpoint']}")
        print(f"Sports found: {len(results['sports'])}")
        for s in results['sports'][:10]:
            print(f"  {s['name']} ({s['slug']}) id={s['id']}")
        print(f"Events found: {len(results['events'])}")
        for ev in results['events'][:5]:
            f = ev["fixture"]
            sport = f["sport"]["name"]
            teams = [c["name"] for c in f["competitors"]]
            markets = ev.get("markets", [])
            odds = ""
            if markets:
                odds = f" | {markets[0].get('name', '?')}: " + ", ".join(
                    [f"{o['name']}={o['value']}" for o in markets[0].get("odds", [])[:3]]
                )
            print(f"  [{sport}] {' vs '.join(teams)}{odds}")

        return len(results['events']) > 0

if __name__ == "__main__":
    result = asyncio.run(test_intercept())
    print(f"\n>>> {'SUCCESS' if result else 'FAILED'}")
