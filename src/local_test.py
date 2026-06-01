#!/usr/bin/env python3
"""Local test for GGBet GraphQL endpoint — no Apify needed."""
import json, urllib.parse
from datetime import datetime, timezone

# Try curl_cffi first
try:
    from curl_cffi import requests as curl_requests
    CURL_OK = True
except ImportError:
    CURL_OK = False

# Also try basic requests
import requests

GG_BET_URL = "https://gg.bet"
GG_BET_GQL = "https://gg.bet/graphql"
ESPORT_URL = f"{GG_BET_URL}/esports"

# Multiple proxy formats to try
# Sonus accounts from TOOLS.md
PROXIES = []

# Try without proxy first
PROXIES.append(("direct", None))

# Stake uses these successfully:
# customer-sonus_TbxLY-cc-ca-city-edmonton:gX~dawV=8MzVzA@pr.oxylabs.io:7777
# customer-sonus_TbxLY-cc-gb:gX~dawV=8MzVzA@pr.oxylabs.io:7777
PROXIES.append(("oxylabs-sonus-pl", {"https": "http://customer-sonus_TbxLY-cc-pl:gX~dawV=8MzVzA@pr.oxylabs.io:7777", "http": "http://customer-sonus_TbxLY-cc-pl:gX~dawV=8MzVzA@pr.oxylabs.io:7777"}))
PROXIES.append(("oxylabs-sonus-ie", {"https": "http://customer-sonus_TbxLY-cc-ie:gX~dawV=8MzVzA@pr.oxylabs.io:7777", "http": "http://customer-sonus_TbxLY-cc-ie:gX~dawV=8MzVzA@pr.oxylabs.io:7777"}))

# General numbnuts account
PROXIES.append(("oxylabs-numbnuts", {"https": "http://numbnuts_9kOSG:~SWmnT7Qe~n7Fi@pr.oxylabs.io:7777", "http": "http://numbnuts_9kOSG:~SWmnT7Qe~n7Fi@pr.oxylabs.io:7777"}))

def test_endpoint(session, label, proxies=None):
    pfx = f"[{label}]"
    try:
        # Step 1: Hit /esports to get cookies
        print(f"{pfx} Fetching gg.bet/esports...")
        kw = {"timeout": 20, "headers": {
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }}
        if proxies:
            kw["proxies"] = proxies
        r = session.get(ESPORT_URL, **kw)
        print(f"{pfx}   Page status: {r.status_code} | cookies: {list(session.cookies.keys())}")

        if r.status_code != 200:
            print(f"{pfx}   Page blocked, skipping GQL test")
            return False

        # Step 2: GetBettingData → token
        bff_h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": ESPORT_URL,
            "Origin": GG_BET_URL,
            "x-language": "en",
        }

        q = '{"query": "query GetBettingData { bettingData { token endpoint: publicServiceUrl } }"}'
        kw2 = {"headers": bff_h, "timeout": 15, "data": q}
        if proxies:
            kw2["proxies"] = proxies
        r2 = session.post(GG_BET_GQL, **kw2)
        print(f"{pfx}   GetBettingData: {r2.status_code}")

        if r2.status_code != 200:
            print(f"{pfx}   Raw: {r2.text[:200]}")
            return False

        try:
            data = r2.json()
        except:
            print(f"{pfx}   Not JSON: {r2.text[:200]}")
            return False

        print(f"{pfx}   Response: {json.dumps(data)[:500]}")

        if "data" not in data or not data["data"] or not data["data"].get("bettingData"):
            print(f"{pfx}   No bettingData in response")
            return False

        bdata = data["data"]["bettingData"]
        token = bdata["token"]
        sb_endpoint = "https:" + bdata["endpoint"]
        print(f"{pfx}   Token: {token[:30]}...")
        print(f"{pfx}   SB endpoint: {sb_endpoint}")

        # Step 3: List sports
        sb_gql = f"{sb_endpoint}/graphql"
        sb_h = {**bff_h, "Authorization": f"Bearer {token}"}

        q2 = '{"query": "query { sports { id name slug tags } }"}'
        kw3 = {"headers": sb_h, "timeout": 15, "data": q2}
        if proxies:
            kw3["proxies"] = proxies
        r3 = session.post(sb_gql, **kw3)
        print(f"{pfx}   Sports: {r3.status_code}")
        if r3.status_code == 200:
            try:
                d3 = r3.json()
                sports = d3.get("data", {}).get("sports", [])
                print(f"{pfx}   Found {len(sports)} sports")
                for s in sports[:10]:
                    print(f"{pfx}     {s['name']} ({s['slug']}, id={s['id']})")
            except:
                print(f"{pfx}   Sports raw: {r3.text[:200]}")

        # Step 4: Get some events
        q3 = {
            "query": """query GetSportEventListByFilters($offset:Int!,$limit:Int!,$sportEventTypes:[SportEventType!]){
                matches:sportEventListByFilters(offset:$offset,limit:$limit,sportEventTypes:$sportEventTypes){
                    count
                    sportEvents{
                        id slug
                        fixture{
                            startTime status type
                            sport{name slug tags}
                            tournament{name slug}
                            competitors{name}
                        }
                        markets(top:true,limit:1){name type odds{name type value status}}
                    }
                }
            }""",
            "variables": {"offset":0,"limit":10,"sportEventTypes":["PREMATCH"]}
        }
        kw4 = {"headers": sb_h, "timeout": 20, "json": q3}
        if proxies:
            kw4["proxies"] = proxies
        r4 = session.post(sb_gql, **kw4)
        print(f"{pfx}   Events: {r4.status_code}")
        if r4.status_code == 200:
            try:
                d4 = r4.json()
                if "data" in d4:
                    events = d4["data"]["matches"]["sportEvents"]
                    print(f"{pfx}   Got {len(events)} events")
                    for ev in events[:3]:
                        f = ev["fixture"]
                        sport = f["sport"]["name"]
                        teams = [c["name"] for c in f["competitors"]]
                        print(f"{pfx}     [{sport}] {' vs '.join(teams)}")
                    return True
                elif "errors" in d4:
                    print(f"{pfx}   GQL errors: {d4['errors']}")
            except:
                print(f"{pfx}   Events raw: {r4.text[:200]}")
        return False

    except Exception as e:
        print(f"{pfx}   ERROR: {e}")
        return False

# Test each proxy with requests
print("=" * 60)
print("Testing GGBet endpoints")
print("=" * 60)

for proxy_name, proxy_cfg in PROXIES:
    print(f"\n--- Testing with {proxy_name} ---")
    result = test_endpoint(requests.Session(), f"requests/{proxy_name}", proxy_cfg)
    if result:
        print(f">>> SUCCESS with {proxy_name}")
        break

# If no success yet, try with curl_cffi
if CURL_OK and not result:
    print("\n--- Trying curl_cffi (Chrome TLS) ---")
    for proxy_name, proxy_cfg in PROXIES:
        if proxy_cfg is None:
            sess = curl_requests.Session(impersonate="chrome")
        else:
            sess = curl_requests.Session(impersonate="chrome", proxies=proxy_cfg)
        result = test_endpoint(sess, f"curl_cffi/{proxy_name}", proxy_cfg)
        if result:
            print(f">>> SUCCESS with curl_cffi/{proxy_name}")
            break

if not result:
    print("\n>>> ALL ATTEMPTS FAILED")
