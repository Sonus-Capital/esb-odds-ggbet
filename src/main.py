#!/usr/bin/env python3
"""
GGBet (gg.bet) Esports Odds Scraper — v11 (2026-06-07)

Schema: SCHEMA-LOCK-2026-06-07.md — all actors must conform.
Changes in v11:
  - `game_normalised` → `game` (canonical name via normalise_game)
  - `match_start_time` now ISO 8601 UTC
  - Added `market_name` field

DOM structure (confirmed from live inspection):
  Page layout per match:
    DIV[data-test="sport-event-in-view-subscription"]
      DIV  <- tournament name (only present on first match of each tournament block)
      A[data-test="sport-event-row-body-link" href="/pl/esports/match/team1-vs-team2-DD-MM"]
        ...18 odd-buttons (6 markets × 3 outcomes)...

Tournament fix: read from sport-event-in-view-subscription first child DIV, carry last seen
Time fix: parse HH:MM from match innerText + DD-MM date from match URL href
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Tuple
import sys
from pathlib import Path

from apify import Actor
from playwright.async_api import async_playwright
from normalise import normalise_game

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ggbet-scraper")

GG_BET_URL = "https://gg.bet"
ESPORT_URL  = f"{GG_BET_URL}/esports"

PROXY_LIST = [
    "http://customer-sonus_TbxLY-cc-pl:gX~dawV=8MzVzA@pr.oxylabs.io:7777",
    "http://customer-sonus_TbxLY-cc-ie:gX~dawV=8MzVzA@pr.oxylabs.io:7777",
    "http://customer-sonus_TbxLY-cc-se:gX~dawV=8MzVzA@pr.oxylabs.io:7777",
    "http://numbnuts_9kOSG:~SWmnT7Qe~n7Fi@pr.oxylabs.io:7777",
]

ESPORT_GAMES_DEFAULT: List[Tuple[str, str]] = [
    ("CS2",           f"{GG_BET_URL}/counter-strike"),
    ("Dota 2",        f"{GG_BET_URL}/dota2"),
    ("Valorant",      f"{GG_BET_URL}/valorant"),
    ("LoL",           f"{GG_BET_URL}/league-of-legends"),
    ("MLBB",          f"{GG_BET_URL}/mobile-legends"),
    ("Overwatch 2",   f"{GG_BET_URL}/overwatch"),
    ("Rainbow Six",   f"{GG_BET_URL}/rainbow-six"),
    ("Call of Duty",  f"{GG_BET_URL}/call-of-duty"),
    ("Rocket League", f"{GG_BET_URL}/rocket-league"),
    ("StarCraft 2",   f"{GG_BET_URL}/starcraft2"),
]

VIRTUAL_RE = re.compile(
    r"EA FC|2x\d+ min|eSoccer|efootball|eFootball|NBA 2K|"
    r"Bundesliga.*EA|Serie.*EA|Premier.*EA|La Liga.*EA|"
    r"Virtual|marble|drone",
    re.IGNORECASE,
)

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
window.chrome = {runtime: {}};
"""

JS_DISCOVER_GAMES = """
() => {
    const seen = new Set();
    const slugs = ['counter-strike','cs2','dota2','valorant','league-of-legends',
        'mobile-legends','overwatch','rainbow-six','call-of-duty','rocket-league',
        'starcraft2','pubg','king-of-glory'];
    const results = [];
    document.querySelectorAll('a[href]').forEach(a => {
        const href = a.getAttribute('href') || '';
        const m = href.match(/^\\/([a-z0-9-]+)$/);
        if (m && slugs.includes(m[1]) && !seen.has(href)) {
            seen.add(href);
            results.push({text: a.textContent.trim() || m[1], url: 'https://gg.bet' + href});
        }
    });
    return results;
}
"""

JS_EXTRACT = """
() => {
    const records = [];
    const seen = new Set();

    const wrappers = document.querySelectorAll('[data-test="sport-event-in-view-subscription"]');
    let lastTournament = '';

    for (const wrapper of wrappers) {
        const children = Array.from(wrapper.children);
        const matchEl = wrapper.querySelector('a[data-test="sport-event-row-body-link"]');
        if (!matchEl) continue;

        for (const child of children) {
            if (child === matchEl || child.contains(matchEl)) break;
            const t = child.textContent.trim().split('\\n')[0].trim();
            if (t && t.length > 3 && t.length < 120 &&
                !/^\\d{1,2}:\\d{2}$/.test(t) &&
                !/^\\d+$/.test(t)) {
                lastTournament = t;
                break;
            }
        }
        const tournament = lastTournament;

        const rawHref = matchEl.getAttribute('href') || '';
        const href = rawHref.replace(/^\\/[a-z]{2}\\//, '/');
        const matchUrl = href ? 'https://gg.bet' + href : '';

        const allText = matchEl.innerText || '';
        const timeMatch = allText.match(/(\\d{1,2}:\\d{2})/);
        const startTime = timeMatch ? timeMatch[1] : '';

        const allOddBtns = Array.from(matchEl.querySelectorAll('[data-test*="odd-button"]'));
        const parsedBtns = [];
        const btnSeen = new Set();
        for (const btn of allOddBtns) {
            let label = '';
            let value = null;
            const walker = document.createTreeWalker(btn, NodeFilter.SHOW_TEXT, null);
            let node;
            while ((node = walker.nextNode())) {
                const t = node.textContent.trim();
                if (!t) continue;
                const v = parseFloat(t);
                if (!isNaN(v) && v >= 1.01 && v <= 500 && value === null) {
                    value = v;
                } else if (isNaN(parseFloat(t)) && t !== '-' && t.length > 0 && !label) {
                    label = t;
                }
            }
            if (value === null) continue;
            const k = label + ':' + value;
            if (!btnSeen.has(k)) { btnSeen.add(k); parsedBtns.push({label, value}); }
        }

        const invalidRe = /^[+\\-]?\\d+[.,]?\\d*$|powyżej|poniżej|over|under|^(yes|no|draw|x|remis)$/i;
        const mw = parsedBtns.filter(o => o.label && !invalidRe.test(o.label.trim()));
        if (mw.length < 2) continue;

        const team1 = mw[0].label.trim();
        const p1    = mw[0].value;
        let team2, p2, pDraw = null;
        if (mw.length >= 3 && /^(draw|x|remis)$/i.test(mw[1].label.trim())) {
            pDraw = mw[1].value;
            team2 = mw[2].label.trim();
            p2    = mw[2].value;
        } else {
            team2 = mw[1].label.trim();
            p2    = mw[1].value;
        }
        if (!team1 || !team2 || team1.toLowerCase() === team2.toLowerCase()) continue;

        const key = team1.toLowerCase() + '|' + team2.toLowerCase();
        if (!seen.has(key)) {
            seen.add(key);
            records.push({tournament, team1, team2, startTime, p1, p2, pDraw, matchUrl});
        }
    }
    return records;
}
"""

JS_SCROLL = """
async () => {
    const delay = ms => new Promise(r => setTimeout(r, ms));
    let prev = 0;
    for (let i = 0; i < 15; i++) {
        window.scrollTo(0, document.body.scrollHeight);
        await delay(1000);
        const cur = document.querySelectorAll('a[data-test="sport-event-row-body-link"]').length;
        if (cur === prev && i > 3) break;
        prev = cur;
    }
    window.scrollTo(0, 0);
    await delay(300);
    return document.querySelectorAll('a[data-test="sport-event-row-body-link"]').length;
}
"""

MATCH_DATE_RE = re.compile(r"-(\d{2})-(\d{2})$")


def enrich_start_time(start_time: str, match_url: str, scraped_at: str) -> str:
    """Combine HH:MM with DD-MM from match URL slug → ISO 8601 UTC string."""
    if not start_time:
        return ""
    m = MATCH_DATE_RE.search(match_url)
    if not m:
        return start_time
    day, month = m.group(1), m.group(2)
    year = scraped_at[:4]
    return f"{year}-{month}-{day}T{start_time}:00Z"


def is_virtual(tournament: str, game_label: str) -> bool:
    return bool(VIRTUAL_RE.search(tournament) or VIRTUAL_RE.search(game_label))


async def make_browser(pw, proxy_url: str):
    parts = proxy_url.replace("http://", "").split("@")
    user, pwd = parts[0].split(":", 1)
    server = "http://" + parts[1]
    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"],
        proxy={"server": server, "username": user, "password": pwd},
    )
    ctx = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        locale="en-US",
        viewport={"width": 1280, "height": 900},
    )
    await ctx.add_init_script(STEALTH_SCRIPT)
    return browser, ctx


async def scrape_game_page(page, game_label: str, url: str, now: str) -> List[Dict]:
    records = []
    logger.info(f"  Fetching {game_label}: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(10)
    except Exception as e:
        logger.warning(f"  Nav failed {game_label}: {e}")
        return records

    try:
        total = await page.evaluate(JS_SCROLL)
        logger.info(f"  {game_label}: {total} match links after scroll")
    except Exception as e:
        logger.warning(f"  Scroll error {game_label}: {e}")

    try:
        raw = await page.evaluate(JS_EXTRACT)
        logger.info(f"  {game_label}: {len(raw)} raw records")
        for item in raw:
            team1      = (item.get("team1") or "").strip()
            team2      = (item.get("team2") or "").strip()
            tournament = (item.get("tournament") or "").strip()
            p1, p2     = item.get("p1"), item.get("p2")
            match_url  = item.get("matchUrl") or ""
            if not team1 or not team2 or not p1 or not p2:
                continue
            if is_virtual(tournament, game_label):
                continue
            start_time = enrich_start_time(
                (item.get("startTime") or "").strip(), match_url, now
            )
            records.append({
                "bookmaker":        "ggbet",
                "game_raw":         game_label,
                "game":             normalise_game(game_label),
                "tournament_name":  tournament,
                "team1":            team1,
                "team2":            team2,
                "match_start_time": start_time,
                "match_url":        match_url,
                "market_name":      "Match Winner",
                "price_team1":      p1,
                "price_team2":      p2,
                "price_draw":       item.get("pDraw"),
                "scraped_at":       now,
            })
    except Exception as e:
        logger.error(f"  Extraction error {game_label}: {e}")
    return records


async def main() -> None:
    async with Actor() as actor:
        inp         = await actor.get_input() or {}
        max_matches = inp.get("max_matches", 1000)
        actor.log.info(f"GGBet DOM scraper v11 | schema-locked | max={max_matches}")

        now = datetime.now(timezone.utc).isoformat()
        all_records: List[Dict] = []
        seen_keys: set = set()

        async with async_playwright() as pw:
            browser = context = page = None
            for proxy_url in PROXY_LIST:
                try:
                    actor.log.info(f"Trying proxy: {proxy_url[:55]}...")
                    b, ctx = await make_browser(pw, proxy_url)
                    pg = await ctx.new_page()
                    await pg.goto(ESPORT_URL, wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(8)
                    title = await pg.title()
                    actor.log.info(f"Warm-up OK. Title: {title!r}")
                    browser, context, page = b, ctx, pg
                    break
                except Exception as e:
                    actor.log.warning(f"Proxy failed: {e}")
                    try:
                        await b.close()
                    except Exception:
                        pass

            if not page:
                actor.log.error("All proxies failed")
                return

            try:
                discovered = await page.evaluate(JS_DISCOVER_GAMES)
                game_list = [(r["text"], r["url"]) for r in discovered] if discovered and len(discovered) >= 3 else ESPORT_GAMES_DEFAULT
                actor.log.info(f"Game list: {[g for g, _ in game_list]}")
            except Exception:
                game_list = ESPORT_GAMES_DEFAULT

            for game_label, url in game_list:
                if len(all_records) >= max_matches:
                    break
                recs = await scrape_game_page(page, game_label, url, now)
                actor.log.info(f"  {game_label}: {len(recs)} valid records")
                for rec in recs:
                    key = f"{rec['team1'].lower()}|{rec['team2'].lower()}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_records.append(rec)
                await asyncio.sleep(2)

            await browser.close()

        actor.log.info(f"Total unique records: {len(all_records)}")
        for rec in all_records[:max_matches]:
            await actor.push_data(rec)
        await actor.push_data({
            "_meta": True, "bookmaker": "ggbet",
            "records_total": min(len(all_records), max_matches),
            "method": "playwright_dom_v11",
            "scraped_at": now,
        })
        actor.log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
