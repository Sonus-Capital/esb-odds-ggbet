#!/usr/bin/env python3
"""Playwright-based test for GGBet — establishes browser context then calls GQL via page.evaluate()."""
import asyncio
import json
import sys

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: playwright not installed.")
    sys.exit(1)

GG_BET_URL = "https://gg.bet"
ESPORT_URL = f"{GG_BET_URL}/esports"

GET_BETTING_DATA = """query GetBettingData{bettingData{token endpoint:publicServiceUrl}}"""
GET_SPORTS = "query{sports{id name slug tags}}"
GET_EVENTS = """query GetSportEventListByFilters($offset:Int!,$limit:Int!,$sportEventTypes:[SportEventType!]){
    matches:sportEventListByFilters(offset:$offset,limit:$limit,sportEventTypes:$sportEventTypes){
        count sportEvents{
            id slug
            fixture{startTime status type sport{name slug tags} tournament{name slug} competitors{name}}
            markets(top:true,limit:1){name type odds{name type value status}}
        }}}"""

JS_FETCH = """
async (args) => {
    const { url, query, token, variables } = args;
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
    const r = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        credentials: "include"
    });
    return { status: r.status, text: await r.text() };
}
"""

async def test_pw():
    print("Launching Playwright (chromium, headless)...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        page.on("console", lambda msg: print(f"  [console] {msg.type}: {msg.text[:200]}") if "Hotjar" not in msg.text else None)
        page.on("response", lambda resp: print(f"  [response] {resp.status} {resp.url[:90]}") if "graphql" in resp.url else None)

        print(f"Navigating to {ESPORT_URL}...")
        await page.goto(ESPORT_URL, wait_until="networkidle", timeout=30000)
        print(f"Page loaded. Title: {await page.title()}")

        # The SPA already makes GQL calls — let it settle for a moment
        await page.wait_for_timeout(3000)

        # Step 1: GetBettingData
        print("\n--- Step 1: GetBettingData ---")
        r1 = await page.evaluate(JS_FETCH, {"url": "https://gg.bet/graphql", "query": GET_BETTING_DATA})
        print(f"HTTP {r1['status']}")
        if r1['status'] != 200:
            print(f"Raw: {r1['text'][:300]}")
            return False
        data = json.loads(r1['text'])
        token = data['data']['bettingData']['token']
        sb = "https:" + data['data']['bettingData']['endpoint']
        print(f"Token: {token[:40]}...")
        print(f"SB endpoint: {sb}")

        # Step 2: Sports query
        print("\n--- Step 2: Sports ---")
        sb_gql = f"{sb}/graphql"
        r2 = await page.evaluate(JS_FETCH, {"url": sb_gql, "query": GET_SPORTS, "token": token})
        print(f"HTTP {r2['status']}")
        if r2['status'] == 200:
            data2 = json.loads(r2['text'])
            sports = data2.get("data", {}).get("sports", [])
            print(f"Found {len(sports)} sports:")
            for s in sports:
                is_esport = "esports" in (s.get("tags") or []) or "esport" in s.get("slug", "")
                mark = " **ESPORT**" if is_esport else ""
                print(f"  {s['name']} ({s['slug']}) id={s['id']}{mark}")
        else:
            print(f"Raw: {r2['text'][:300]}")

        # Step 3: Events query
        print("\n--- Step 3: Events ---")
        r3 = await page.evaluate(JS_FETCH, {
            "url": sb_gql,
            "query": GET_EVENTS,
            "token": token,
            "variables": {"offset": 0, "limit": 20, "sportEventTypes": ["PREMATCH"]}
        })
        print(f"HTTP {r3['status']}")
        if r3['status'] == 200:
            data3 = json.loads(r3['text'])
            if "errors" in data3:
                print(f"GQL errors: {data3['errors']}")
                return False
            events = data3.get("data", {}).get("matches", {}).get("sportEvents", [])
            print(f"Got {len(events)} events")
            for ev in events[:5]:
                f = ev["fixture"]
                sport = f["sport"]["name"]
                teams = [c["name"] for c in f["competitors"]]
                markets = ev.get("markets", [])
                odds_str = ""
                if markets:
                    odds = [f"{o['name']}={o['value']}" for o in markets[0].get("odds", [])[:3]]
                    odds_str = f" | odds: {', '.join(odds)}"
                print(f"  [{sport}] {' vs '.join(teams)}{odds_str}")
            return True
        else:
            print(f"Raw: {r3['text'][:300]}")
            return False

        await browser.close()
        return False

if __name__ == "__main__":
    result = asyncio.run(test_pw())
    print(f"\n>>> {'SUCCESS' if result else 'FAILED'}")
