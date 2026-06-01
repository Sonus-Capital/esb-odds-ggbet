#!/usr/bin/env python3
"""Capture GGBet GQL responses via Playwright route interception BEFORE SPA reads body."""
import asyncio, json
from playwright.async_api import async_playwright

GG_BET_URL = "https://gg.bet"
ESPORT_URL = f"{GG_BET_URL}/esports"

async def test_route():
    print("Launching Playwright with route interception...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        intercepted = {"bettingData": None, "sports": [], "events": [], "count": 0}

        async def handle_route(route):
            req = route.request
            if "graphql" in req.url:
                response = await route.fetch()
                body = await response.body()
                intercepted["count"] += 1
                print(f"  [INTERCEPT #{intercepted['count']}] {req.method} {req.url[:60]}... | status={response.status} | body_len={len(body)}")

                if len(body) > 0:
                    try:
                        data = json.loads(body)
                        if data.get("data", {}).get("bettingData"):
                            intercepted["bettingData"] = data["data"]["bettingData"]
                            print(f"    >> bettingData intercepted!")
                        if data.get("data", {}).get("sports"):
                            intercepted["sports"] = data["data"]["sports"]
                            print(f"    >> sports intercepted: {len(intercepted['sports'])} items")
                        events = data.get("data", {}).get("matches", {}).get("sportEvents", [])
                        if events:
                            intercepted["events"].extend(events)
                            print(f"    >> events intercepted: {len(events)} matches")
                    except json.JSONDecodeError:
                        print(f"    >> NOT JSON (binary?)")

                await route.fulfill(response=response)
            else:
                await route.continue_()

        await page.route("**/*graphql*", handle_route)

        print(f"Navigating to {ESPORT_URL}...")
        await page.goto(ESPORT_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)

        # Scroll to trigger lazy loads
        print("\nScrolling...")
        for _ in range(3):
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)

        await browser.close()

        print(f"\n{'='*60}")
        print(f"Total intercepted GQL calls: {intercepted['count']}")
        print(f"bettingData: {'✅' if intercepted['bettingData'] else '❌'}")
        print(f"Sports: {len(intercepted['sports'])}")
        print(f"Events: {len(intercepted['events'])}")

        for ev in intercepted["events"][:5]:
            f = ev.get("fixture", {})
            sport = f.get("sport", {}).get("name", "?")
            teams = [c["name"] for c in f.get("competitors", [])]
            markets = ev.get("markets", [])
            odds = ""
            if markets:
                odds = ", ".join([f"{o['name']}={o['value']}" for o in markets[0].get("odds", [])[:3]])
            print(f"  [{sport}] {' vs '.join(teams)} | {odds}")

        return len(intercepted["events"]) > 0

if __name__ == "__main__":
    result = asyncio.run(test_route())
    print(f"\n>>> {'SUCCESS' if result else 'FAILED'}")
