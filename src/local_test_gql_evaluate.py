#!/usr/bin/env python3
"""Test GGBet GraphQL via page.evaluate — same-origin fetch with browser cookies."""
import asyncio, json
from playwright.async_api import async_playwright

GG_BET_URL = "https://gg.bet"
ESPORT_URL = f"{GG_BET_URL}/esports"

GET_BETTING_DATA = '{"query": "query GetBettingData { bettingData { token endpoint: publicServiceUrl } }"}'
GET_SPORTS = '{"query": "query { sports { id name slug tags } }"}'
GET_EVENTS = """{"query": "query GetSportEventListByFilters($offset: Int!, $limit: Int!, $sportEventTypes: [SportEventType!]) { matches: sportEventListByFilters(offset: $offset, limit: $limit, sportEventTypes: $sportEventTypes) { count sportEvents { id slug fixture { startTime status type sport { name slug tags } tournament { name slug } competitors { name } } markets(top: true, limit: 1) { name type odds { name type value status } } } } }", "variables": {"offset": 0, "limit": 50, "sportEventTypes": ["PREMATCH"]}}"""

JS_FETCH = """
async (args) => {
    const { url, body, needsAuth } = args;
    const headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-language": "en",
        "Origin": "https://gg.bet",
        "Referer": "https://gg.bet/esports"
    };
    if (needsAuth && window.__GGBET_TOKEN__) {
        headers["Authorization"] = "Bearer " + window.__GGBET_TOKEN__;
    }
    const r = await fetch(url, {
        method: "POST",
        headers,
        body: typeof body === "string" ? body : JSON.stringify(body),
        credentials: "include"
    });
    const text = await r.text();
    return { status: r.status, text };
}
"""

async def test_gql_evaluate():
    print("Launching Playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print(f"Navigating to {ESPORT_URL}...")
        await page.goto(ESPORT_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)
        print("Page loaded.")

        # Step 1: Get token via same-origin fetch
        print("\n--- Step 1: GetBettingData ---")
        r1 = await page.evaluate(JS_FETCH, {
            "url": "https://gg.bet/graphql",
            "body": GET_BETTING_DATA,
            "needsAuth": False
        })
        print(f"Status: {r1['status']}")
        if r1['status'] == 200:
            data = json.loads(r1['text'])
            token = data['data']['bettingData']['token']
            sb_endpoint = "https:" + data['data']['bettingData']['endpoint']
            print(f"Token: {token[:40]}...")
            print(f"SB endpoint: {sb_endpoint}")

            # Save token to window for subsequent calls
            await page.evaluate(f"() => {{ window.__GGBET_TOKEN__ = '{token}'; }}")
        else:
            print(f"Failed: {r1['text'][:300]}")
            await browser.close()
            return False

        # Step 2: Sports query — try against gg.bet/graphql first (same-origin)
        print("\n--- Step 2: Sports via gg.bet/graphql ---")
        r2 = await page.evaluate(JS_FETCH, {
            "url": "https://gg.bet/graphql",
            "body": GET_SPORTS,
            "needsAuth": True
        })
        print(f"Status: {r2['status']}")
        if r2['status'] == 200:
            try:
                data = json.loads(r2['text'])
                sports = data.get("data", {}).get("sports", [])
                print(f"Found {len(sports)} sports")
                for s in sports[:10]:
                    print(f"  {s['name']} ({s['slug']}) id={s['id']}")
            except:
                print(f"Raw: {r2['text'][:500]}")
        else:
            print(f"Failed: {r2['text'][:300]}")

            # Try against sportsbook endpoint
            print("\n--- Step 2b: Sports via sportsbook endpoint ---")
            r2b = await page.evaluate(JS_FETCH, {
                "url": f"{sb_endpoint}/graphql",
                "body": GET_SPORTS,
                "needsAuth": True
            })
            print(f"Status: {r2b['status']}")
            if r2b['status'] == 200:
                data = json.loads(r2b['text'])
                sports = data.get("data", {}).get("sports", [])
                print(f"Found {len(sports)} sports")
                for s in sports[:10]:
                    print(f"  {s['name']} ({s['slug']}) id={s['id']}")
            else:
                print(f"Failed: {r2b['text'][:300]}")

        # Step 3: Events query
        print("\n--- Step 3: Events ---")
        r3 = await page.evaluate(JS_FETCH, {
            "url": f"{sb_endpoint}/graphql",
            "body": GET_EVENTS,
            "needsAuth": True
        })
        print(f"Status: {r3['status']}")
        if r3['status'] == 200:
            try:
                data = json.loads(r3['text'])
                if "errors" in data:
                    print(f"GQL errors: {data['errors']}")
                    return False
                events = data.get("data", {}).get("matches", {}).get("sportEvents", [])
                print(f"Got {len(events)} events")
                for ev in events[:5]:
                    f = ev["fixture"]
                    sport = f["sport"]["name"]
                    teams = [c["name"] for c in f["competitors"]]
                    markets = ev.get("markets", [])
                    odds_str = ""
                    if markets:
                        odds = [f"{o['name']}={o['value']}" for o in markets[0].get("odds", [])[:3]]
                        odds_str = f" | {markets[0].get('name', '?')}: {', '.join(odds)}"
                    print(f"  [{sport}] {' vs '.join(teams)}{odds_str}")
                return len(events) > 0
            except Exception as e:
                print(f"Parse error: {e}")
                print(f"Raw: {r3['text'][:500]}")
        else:
            print(f"Failed: {r3['text'][:300]}")

        await browser.close()
        return False

if __name__ == "__main__":
    result = asyncio.run(test_gql_evaluate())
    print(f"\n>>> {'SUCCESS' if result else 'FAILED'}")
