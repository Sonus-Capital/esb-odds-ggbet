#!/usr/bin/env python3
"""
GGBet (gg.bet) Esports Odds Scraper — v5 (2026-06-01)

Method: Playwright + stealth + Oxylabs residential proxy
  1. Warm-up on gg.bet/esports with stealth browser (CF bypass)
  2. Discover game URLs from footer nav
  3. For each game URL navigate + wait 12s for SPA hydration
  4. Extract teams + Match Winner odds from rendered DOM
  5. Filter virtual/sim games, push unified schema records

Proxy: Oxylabs residential, PL geo (gg.bet allows PL)
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from apify import Actor
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ggbet-scraper")

GG_BET_URL = "https://gg.bet"
ESPORT_URL = f"{GG_BET_URL}/esports"

# Oxylabs residential proxies — PL geo (gg.bet allows PL)
# Rotate if one fails
PROXY_LIST = [
    "http://customer-sonus_TbxLY-cc-pl:gX~dawV=8MzVzA@pr.oxylabs.io:7777",
    "http://customer-sonus_TbxLY-cc-ie:gX~dawV=8MzVzA@pr.oxylabs.io:7777",
    "http://customer-sonus_TbxLY-cc-se:gX~dawV=8MzVzA@pr.oxylabs.io:7777",
    "http://numbnuts_9kOSG:~SWmnT7Qe~n7Fi@pr.oxylabs.io:7777",
]

# Real game URLs from gg.bet footer (confirmed from live DOM 2026-06-01)
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

VIRTUAL_TOURNAMENT_RE = re.compile(
    r"EA FC|2x4 min|2x\d+ min|eSoccer|efootball|eFootball|NBA 2K|"
    r"Bundesliga.*EA|Serie.*EA|Premier.*EA|La Liga.*EA|Ligue.*EA|"
    r"Virtual|virtual|marble|Marble|drone|Drone",
    re.IGNORECASE,
)
VIRTUAL_GAME_RE = re.compile(
    r"efootball|esoccer|ebasket|etennis|efight|ecricket|marble|drone|nba2k|fifa",
    re.IGNORECASE,
)

# Stealth init script — mask webdriver fingerprint
STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
window.chrome = {runtime: {}};
"""

# Discover real game slug URLs from footer/nav
JS_DISCOVER_GAMES = """
() => {
    const games = [];
    const seen = new Set();
    const esportSlugs = [
        'counter-strike','cs2','dota2','dota-2','valorant',
        'league-of-legends','mobile-legends','overwatch',
        'rainbow-six','call-of-duty','rocket-league','starcraft2',
        'starcraft-2','pubg','king-of-glory','crossfire',
        'wild-rift','hearthstone','halo','apex-legends'
    ];
    document.querySelectorAll('a[href]').forEach(a => {
        const href = a.getAttribute('href') || '';
        const text = a.textContent.trim();
        const m = href.match(/^\\/([a-z0-9-]+)$/);
        if (m && esportSlugs.includes(m[1]) && !seen.has(href)) {
            seen.add(href);
            games.push({text: text || m[1], href, url: 'https://gg.bet' + href});
        }
    });
    return games;
}
"""

# Extract match odds from rendered DOM
JS_EXTRACT_MATCHES = """
() => {
    const records = [];
    const seen = new Set();
    const oddBtns = Array.from(document.querySelectorAll('[data-test*="odd-button"]'));

    const matchMap = new Map();
    for (const btn of oddBtns) {
        let matchEl = null;
        let cur = btn.parentElement;
        for (let i = 0; i < 10 && cur; i++) {
            const dt = (cur.getAttribute('data-test') || '').toLowerCase();
            const cls = (cur.className || '').toLowerCase();
            if (dt.includes('match') || dt.includes('event-row') || dt.includes('game-row') ||
                cls.includes('matchcard') || cls.includes('match-row') || cls.includes('eventcard') ||
                cls.includes('event-row')) {
                matchEl = cur;
                break;
            }
            cur = cur.parentElement;
        }
        if (!matchEl) matchEl = btn.parentElement?.parentElement?.parentElement || btn.parentElement;
        if (!matchEl) continue;
        if (!matchMap.has(matchEl)) matchMap.set(matchEl, []);
        matchMap.get(matchEl).push(btn);
    }

    for (const [matchEl, btns] of matchMap.entries()) {
        if (btns.length < 2) continue;

        const parsedOdds = [];
        const btnSeen = new Set();
        for (const btn of btns) {
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
            const key = label + ':' + value;
            if (!btnSeen.has(key)) { btnSeen.add(key); parsedOdds.push({label, value}); }
        }

        if (parsedOdds.length < 2) continue;

        const matchWinner = parsedOdds.filter(o => {
            const l = o.label;
            return l && !l.match(/^[+-]\\d/) &&
                !/^(over|under|yes|no|total|handicap)$/i.test(l) &&
                !/^\\d+\\.\\d+$/.test(l);
        });
        if (matchWinner.length < 2) continue;

        const team1 = matchWinner[0].label;
        const p1    = matchWinner[0].value;
        let team2, p2, pDraw = null;

        if (matchWinner.length >= 3 && /draw|^x$/i.test(matchWinner[1].label)) {
            pDraw = matchWinner[1].value;
            team2 = matchWinner[2].label;
            p2    = matchWinner[2].value;
        } else {
            team2 = matchWinner[1].label;
            p2    = matchWinner[1].value;
        }
        if (!team1 || !team2) continue;

        let tournament = '';
        let cur = matchEl.parentElement;
        for (let i = 0; i < 8 && cur; i++) {
            const h = cur.querySelector('h1,h2,h3,h4,[class*="tournament"],[class*="league"],[class*="section-title"],[class*="group-title"]');
            if (h) { tournament = h.textContent.trim().split('\\n')[0].trim(); break; }
            cur = cur.parentElement;
        }

        let startTime = '';
        const timeEl = matchEl.querySelector('[class*="time"],[class*="Time"],time,[data-test*="time"]');
        if (timeEl) startTime = timeEl.textContent.trim();

        let matchPath = '';
        const linkEl = matchEl.querySelector('a[href*="/esports/match/"]');
        if (linkEl) matchPath = linkEl.getAttribute('href');

        const dedupKey = team1.toLowerCase() + '|' + team2.toLowerCase();
        if (!seen.has(dedupKey)) {
            seen.add(dedupKey);
            records.push({tournament, team1, team2, startTime, p1, p2, pDraw, matchPath});
        }
    }
    return records;
}
"""


def is_virtual(tournament: str, game_label: str) -> bool:
    return bool(VIRTUAL_TOURNAMENT_RE.search(tournament) or VIRTUAL_GAME_RE.search(game_label))


async def make_browser(pw, proxy_url: str):
    """Launch stealth Chromium with Oxylabs proxy."""
    proxy_parts = proxy_url.replace("http://", "").split("@")
    creds, server_port = proxy_parts[0], proxy_parts[1]
    user, pwd = creds.split(":", 1)

    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-web-security",
        ],
        proxy={"server": f"http://{server_port}", "username": user, "password": pwd},
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        viewport={"width": 1280, "height": 900},
    )
    await context.add_init_script(STEALTH_SCRIPT)
    return browser, context


async def discover_game_urls(page) -> List[Tuple[str, str]]:
    try:
        raw = await page.evaluate(JS_DISCOVER_GAMES)
        if raw and len(raw) >= 3:
            found = [(r["text"] or r["href"].strip("/"), r["url"]) for r in raw]
            logger.info(f"Discovered {len(found)} game URLs from DOM")
            return found
    except Exception as e:
        logger.warning(f"Discovery failed: {e}")
    logger.info("Using default game URL list")
    return ESPORT_GAMES_DEFAULT


async def scrape_game_page(page, game_label: str, url: str, now: str) -> List[Dict]:
    records = []
    logger.info(f"  Fetching {game_label}: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(12)  # CF challenge + SPA hydration
    except Exception as e:
        logger.warning(f"  Nav failed for {game_label}: {e}")
        return records

    try:
        raw = await page.evaluate(JS_EXTRACT_MATCHES)
        logger.info(f"  {game_label}: {len(raw)} raw records")
        for item in raw:
            team1      = (item.get("team1") or "").strip()
            team2      = (item.get("team2") or "").strip()
            tournament = (item.get("tournament") or "").strip()
            if not team1 or not team2 or not item.get("p1") or not item.get("p2"):
                continue
            if is_virtual(tournament, game_label):
                continue
            match_path = (item.get("matchPath") or "").strip()
            records.append({
                "bookmaker":        "ggbet",
                "game_raw":         game_label,
                "game_normalised":  game_label,
                "tournament_name":  tournament,
                "team1":            team1,
                "team2":            team2,
                "match_start_time": (item.get("startTime") or "").strip(),
                "match_url":        (GG_BET_URL + match_path) if match_path else "",
                "market_name":      "Match Winner",
                "price_team1":      item["p1"],
                "price_team2":      item["p2"],
                "price_draw":       item.get("pDraw"),
                "scraped_at":       now,
            })
    except Exception as e:
        logger.error(f"  Extraction error {game_label}: {e}")
    return records


async def main() -> None:
    async with Actor() as actor:
        inp         = await actor.get_input() or {}
        max_matches = inp.get("max_matches", 500)
        proxy_idx   = 0

        actor.log.info(f"GGBet DOM scraper v5 | Oxylabs+stealth | max={max_matches}")

        now = datetime.now(timezone.utc).isoformat()
        all_records: List[Dict] = []
        seen_keys: set = set()

        async with async_playwright() as pw:
            # Try proxies in order until warm-up succeeds
            browser = context = page = None
            for proxy_url in PROXY_LIST:
                try:
                    actor.log.info(f"Trying proxy: {proxy_url[:50]}...")
                    b, ctx = await make_browser(pw, proxy_url)
                    pg = await ctx.new_page()
                    actor.log.info("Warming up on gg.bet/esports ...")
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
                actor.log.error("All proxies failed — aborting")
                return

            # Discover game URLs from rendered footer
            game_list = await discover_game_urls(page)

            # Scrape each game
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
            "_meta":         True,
            "bookmaker":     "ggbet",
            "records_total": min(len(all_records), max_matches),
            "method":        "playwright_dom_v5_oxylabs_stealth",
            "scraped_at":    now,
        })
        actor.log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
