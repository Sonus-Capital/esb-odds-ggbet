#!/usr/bin/env python3
"""Test GGBet BFF GraphQL endpoint via page.evaluate — same-origin, SPA-style."""
import asyncio, json
from playwright.async_api import async_playwright

GG_BET_GQL = "https://gg.bet/graphql"
ESPORT_URL = "https://gg.bet/esports"

JS_FETCH = """
async (args) => {
    const { url, query, variables, token } = args;
    const headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-language": "en",
        "Origin": "https://gg.bet",
        "Referer": "https://gg.bet/esports"
    };
    if (token) headers["Authorization"] = "Bearer " + token;
    const body = {query};
    if (variables) body.variables = variables;
    const r = await fetch(url, { method: "POST", headers, body: JSON.stringify(body), credentials: "include" });
    const text = await r.text();
    return { status: r.status, text };
}
"""

async def test_bff():
    print("Launching Playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print(f"Navigating to {ESPORT_URL}...")
        await page.goto(ESPORT_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(5000)

        # Step 1: GetBettingData from BFF
        print("\n--- Step 1: GetBettingData ---")
        r1 = await page.evaluate(JS_FETCH, {
            "url": GG_BET_GQL,
            "query": "query GetBettingData { bettingData { token endpoint: publicServiceUrl } }"
        })
        print(f"Status: {r1['status']}")
        if r1['status'] != 200:
            print(f"Failed: {r1['text'][:300]}")
            return False
        data = json.loads(r1['text'])
        token = data['data']['bettingData']['token']
        sb_endpoint = data['data']['bettingData']['endpoint']
        print(f"Token: {token[:40]}...")
        print(f"SB endpoint: {sb_endpoint}")

        # Step 2: Sports via BFF (not sportsbook directly!)
        print("\n--- Step 2: Sports via gg.bet/graphql ---")
        r2 = await page.evaluate(JS_FETCH, {
            "url": GG_BET_GQL,
            "query": "query { sports { id name slug tags } }",
            "token": token
        })
        print(f"Status: {r2['status']}")
        if r2['status'] == 200:
            try:
                data2 = json.loads(r2['text'])
                if "data" in data2:
                    sports = data2["data"].get("sports", [])
                    print(f"Found {len(sports)} sports")
                    for s in sports:
                        is_esport = "esports" in (s.get("tags") or []) or "esport" in s.get("slug", "")
                        mark = " **ESPORT**" if is_esport else ""
                        print(f"  {s['name']} ({s['slug']}) id={s['id']}{mark}")
                elif "errors" in data2:
                    print(f"GQL errors: {data2['errors']}")
                else:
                    print(f"Unexpected: {r2['text'][:300]}")
            except json.JSONDecodeError:
                print(f"Not JSON: {r2['text'][:200]}")
        else:
            print(f"Failed: {r2['text'][:300]}")

        # Step 3: Events via BFF
        print("\n--- Step 3: Events via gg.bet/graphql ---")
        r3 = await page.evaluate(JS_FETCH, {
            "url": GG_BET_GQL,
            "query": """query GetSportEventListByFilters(
                $offset: Int!, $limit: Int!, $sportEventTypes: [SportEventType!]
            ) {
                matches: sportEventListByFilters(
                    offset: $offset, limit: $limit, sportEventTypes: $sportEventTypes
                ) {
                    count
                    sportEvents {
                        id slug
                        fixture { startTime status type
                            sport { name slug tags }
                            tournament { name slug }
                            competitors { name }
                        }
                        markets(top: true, limit: 1) { name type
                            odds { name type value status }
                        }
                    }
                }
            }""",
            "variables": {"offset": 0, "limit": 50, "sportEventTypes": ["PREMATCH"]},
            "token": token
        })
        print(f"Status: {r3['status']}")
        if r3['status'] == 200:
            try:
                data3 = json.loads(r3['text'])
                if "errors" in data3:
                    print(f"GQL errors: {json.dumps(data3['errors'])[:500]}")
                    return False
                if "data" not in data3:
                    print(f"No data key: {r3['text'][:500]}")
                    return False
                events = data3["data"]["matches"]["sportEvents"]
                print(f"Got {len(events)} events")
                for ev in events[:10]:
                    f = ev["fixture"]
                    sport = f["sport"]["name"]
                    teams = [c["name"] for c in f["competitors"]]
                    markets = ev.get("markets", [])
                    odds_str = ""
                    if markets:
                        odds = ", ".join([f"{o['name']}={o['value']}" for o in markets[0].get("odds", [])[:3]])
                        odds_str = f" | {markets[0].get('name', '?')}: {odds}"
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
    result = asyncio.run(test_bff())
    print(f"\n>>> {'SUCCESS' if result else 'FAILED'}")
