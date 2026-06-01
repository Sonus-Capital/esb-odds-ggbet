#!/usr/bin/env python3
"""
GGBet (gg.bet) Esports Odds Scraper — v8 (2026-06-01)

DOM structure (confirmed):
  Each match = <a data-test="sport-event-row-body-link" href="/pl/esports/match/...">
  Inside each match: 18 odd-buttons (6 markets × 3 outcomes)
  We want the first market (Match Winner = first 2 or 3 buttons)

Fixes vs v6:
  - Group by data-test="sport-event-row-body-link" anchor — exact match container
  - Strip /pl/ locale prefix from match URLs
  - Extract start time from within the match anchor
  - Extract tournament from the section heading above each group of matches
  - Match Winner = odd-buttons inside data-test="top-markets" only (first market block)
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from apify import Actor
from playwright.async_api import async_playwright

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

# Core extractor — uses confirmed DOM structure
JS_EXTRACT = """
() => {
    const records = [];
    const seen = new Set();

    // Each match is wrapped in <a data-test="sport-event-row-body-link">
    const matchLinks = document.querySelectorAll('a[data-test="sport-event-row-body-link"]');

    for (const matchEl of matchLinks) {
        const rawHref = matchEl.getAttribute('href') || '';
        // Strip locale prefix: /pl/esports/match/... -> /esports/match/...
        const href = rawHref.replace(/^\\/[a-z]{2}\\//, '/');
        const matchUrl = href ? 'https://gg.bet' + href : '';

        // Odd-buttons: only from the first market block (data-test="top-markets")
        // Each market block is a DIV[data-test="top-markets"]
        // The Match Winner market is the one with team names as labels
        const allOddBtns = Array.from(matchEl.querySelectorAll('[data-test*="odd-button"]'));

        // Extract leaf text from each button: label + numeric value
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

        // Filter to Match Winner candidates: reject numeric labels, PL locale words, draw/over/under
        const invalidRe = /^[+\-]?\d+[.,]?\d*$|powyżej|poniżej|over|under|^(yes|no|draw|x|remis)$/i;
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

        // Start time — first line of innerText is always "HH:MM" (confirmed from DOM)
        let startTime = '';
        const allText = matchEl.innerText || '';
        const timeMatch = allText.match(/(\d{1,2}:\d{2})/);
        if (timeMatch) startTime = timeMatch[1];

        // Tournament: walk up from matchEl to find a heading/label above the match group
        let tournament = '';
        let cur = matchEl.parentElement;
        for (let i = 0; i < 10 && cur && cur !== document.body; i++) {
            const h = cur.querySelector('h1,h2,h3,h4,[class*="sport-name"],[class*="SportName"],[class*="tournament"],[class*="league"],[class*="section-title"],[class*="group-title"]');
            if (h) {
                const t = h.textContent.trim().split('\\n')[0].trim();
                if (t && t.length > 1 && t.length < 100 && !t.includes('GGBET')) {
                    // Strip PL locale prefixes
                    tournament = t.replace(/^(Obstawianie |Zakłady na |Obstawiaj |Bet on |Apostas em |Apuestas de )/i, '').trim();
                    break;
                }
            }
            cur = cur.parentElement;
        }

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
            if not team1 or not team2 or not p1 or not p2:
                continue
            if is_virtual(tournament, game_label):
                continue
            records.append({
                "bookmaker":        "ggbet",
                "game_raw":         game_label,
                "game_normalised":  game_label,
                "tournament_name":  tournament,
                "team1":            team1,
                "team2":            team2,
                "match_start_time": (item.get("startTime") or "").strip(),
                "match_url":        item.get("matchUrl") or "",
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
        actor.log.info(f"GGBet DOM scraper v8 | sport-event-row-body-link | max={max_matches}")

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
            "method": "playwright_dom_v9_per_match_tournament",
            "scraped_at": now,
        })
        actor.log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
