#!/usr/bin/env python3
"""
GGBet (gg.bet) Esports Odds Scraper — v6 (2026-06-01)

Fixes vs v5:
  - Scroll to bottom repeatedly to trigger lazy-load of all matches
  - Stricter team name validation (no numeric labels, no "powyżej X", no same-team-both-sides)
  - Match URL extracted from anchor href inside match element
  - Start time extracted from time/date elements
  - Tournament name: strip PL locale prefix ("Zakłady na ", "Obstawianie ")
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

# PL locale tournament name prefixes to strip
TOURNEY_STRIP_RE = re.compile(
    r"^(Zakłady na |Obstawianie |Esport zakłady|Bet on )",
    re.IGNORECASE,
)

# Labels that are NOT team names — filter these out of match winner candidates
INVALID_LABEL_RE = re.compile(
    r"^[+-]?\d+\.?\d*$|"           # pure numbers / handicap lines like -1.5
    r"powyżej|poniżej|over|under|"  # total lines
    r"^(yes|no|draw|x)$|"          # other markets
    r"remis|bukmacher",             # PL words
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
    const esportSlugs = [
        'counter-strike','cs2','dota2','dota-2','valorant',
        'league-of-legends','mobile-legends','overwatch',
        'rainbow-six','call-of-duty','rocket-league','starcraft2',
        'starcraft-2','pubg','king-of-glory'
    ];
    const results = [];
    document.querySelectorAll('a[href]').forEach(a => {
        const href = a.getAttribute('href') || '';
        const m = href.match(/^\\/([a-z0-9-]+)$/);
        if (m && esportSlugs.includes(m[1]) && !seen.has(href)) {
            seen.add(href);
            results.push({text: a.textContent.trim() || m[1], url: 'https://gg.bet' + href});
        }
    });
    return results;
}
"""

# Scroll to load all lazy-loaded matches then extract
JS_SCROLL_AND_COUNT = """
async () => {
    // Scroll in steps to trigger virtual-list rendering
    const delay = ms => new Promise(r => setTimeout(r, ms));
    let prev = 0;
    for (let i = 0; i < 20; i++) {
        window.scrollTo(0, document.body.scrollHeight);
        await delay(800);
        const cur = document.querySelectorAll('[data-test*="odd-button"]').length;
        if (cur === prev && i > 3) break;
        prev = cur;
    }
    window.scrollTo(0, 0);
    await delay(500);
    return document.querySelectorAll('[data-test*="odd-button"]').length;
}
"""

JS_EXTRACT_MATCHES = """
() => {
    const records = [];
    const seen = new Set();
    const oddBtns = Array.from(document.querySelectorAll('[data-test*="odd-button"]'));

    // Group odd buttons by closest match-level parent
    const matchMap = new Map();
    for (const btn of oddBtns) {
        let matchEl = null;
        let cur = btn.parentElement;
        for (let i = 0; i < 12 && cur && cur !== document.body; i++) {
            const dt  = (cur.getAttribute('data-test') || '').toLowerCase();
            const cls = (cur.className || '').toLowerCase();
            if (
                dt.includes('match') || dt.includes('event-row') || dt.includes('game-row') ||
                cls.includes('matchcard') || cls.includes('match-row') || cls.includes('eventcard') ||
                cls.includes('event-row') || cls.includes('sportsevent') || cls.includes('sports-event')
            ) {
                matchEl = cur;
                break;
            }
            cur = cur.parentElement;
        }
        if (!matchEl) matchEl = btn.parentElement?.parentElement?.parentElement || btn.parentElement;
        if (!matchEl || matchEl === document.body) continue;
        if (!matchMap.has(matchEl)) matchMap.set(matchEl, []);
        matchMap.get(matchEl).push(btn);
    }

    for (const [matchEl, btns] of matchMap.entries()) {
        if (btns.length < 2) continue;

        // Parse each button — extract first numeric value + first string label
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

        // Keep only Match Winner candidates — strip handicap/total/draw labels
        const invalidRe = /^[+\-]?\d+\.?\d*$|powyżej|poniżej|over|under|^(yes|no|draw|x|remis)$/i;
        const mw = parsedOdds.filter(o => o.label && !invalidRe.test(o.label.trim()));
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

        // Skip if team names are the same (bad parse)
        if (!team1 || !team2 || team1.toLowerCase() === team2.toLowerCase()) continue;

        // Tournament name — walk up to find heading
        let tournament = '';
        let cur2 = matchEl.parentElement;
        for (let i = 0; i < 10 && cur2 && cur2 !== document.body; i++) {
            const h = cur2.querySelector(
                'h1,h2,h3,h4,[class*="tournament"],[class*="league"],[class*="group-title"],[class*="section-title"],[class*="sport-title"]'
            );
            if (h) { tournament = h.textContent.trim().split('\\n')[0].trim(); break; }
            cur2 = cur2.parentElement;
        }

        // Start time — look for time/date elements
        let startTime = '';
        const timeEl = matchEl.querySelector(
            'time,[class*="start-time"],[class*="StartTime"],[class*="match-time"],[class*="MatchTime"],[data-test*="time"]'
        );
        if (timeEl) {
            startTime = timeEl.getAttribute('datetime') || timeEl.textContent.trim();
        }

        // Match URL — find anchor linking to /esports/match/
        let matchUrl = '';
        const linkEl = matchEl.querySelector('a[href*="/esports/match/"]') ||
                       matchEl.closest('a[href*="/esports/match/"]');
        if (linkEl) matchUrl = 'https://gg.bet' + linkEl.getAttribute('href');

        // Also try the whole match element as a link
        if (!matchUrl) {
            const selfHref = matchEl.getAttribute('href');
            if (selfHref && selfHref.includes('/esports/match/')) {
                matchUrl = 'https://gg.bet' + selfHref;
            }
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


def clean_tournament(name: str) -> str:
    return TOURNEY_STRIP_RE.sub("", name).strip()


def is_virtual(tournament: str, game_label: str) -> bool:
    return bool(VIRTUAL_RE.search(tournament) or VIRTUAL_RE.search(game_label))


def is_valid_team(name: str) -> bool:
    if not name or len(name) < 2:
        return False
    if INVALID_LABEL_RE.search(name):
        return False
    return True


async def make_browser(pw, proxy_url: str):
    parts  = proxy_url.replace("http://", "").split("@")
    creds  = parts[0]
    server = "http://" + parts[1]
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
        proxy={"server": server, "username": user, "password": pwd},
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


async def scrape_game_page(page, game_label: str, url: str, now: str) -> List[Dict]:
    records = []
    logger.info(f"  Fetching {game_label}: {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(10)  # CF + initial SPA hydration
    except Exception as e:
        logger.warning(f"  Nav failed {game_label}: {e}")
        return records

    # Scroll repeatedly to trigger lazy-load until odd-button count stabilises
    try:
        count_before = await page.evaluate(
            "() => document.querySelectorAll('[data-test*=\"odd-button\"]').length"
        )
        total_btns = await page.evaluate(JS_SCROLL_AND_COUNT)
        logger.info(f"  {game_label}: odd-buttons before={count_before} after scroll={total_btns}")
    except Exception as e:
        logger.warning(f"  Scroll error {game_label}: {e}")

    try:
        raw = await page.evaluate(JS_EXTRACT_MATCHES)
        logger.info(f"  {game_label}: {len(raw)} raw records extracted")
        for item in raw:
            team1      = (item.get("team1") or "").strip()
            team2      = (item.get("team2") or "").strip()
            tournament = clean_tournament((item.get("tournament") or "").strip())
            p1         = item.get("p1")
            p2         = item.get("p2")

            if not is_valid_team(team1) or not is_valid_team(team2):
                logger.info(f"  Skip invalid teams: '{team1}' / '{team2}'")
                continue
            if team1.lower() == team2.lower():
                logger.info(f"  Skip same-team duplicate: '{team1}'")
                continue
            if not p1 or not p2:
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

        actor.log.info(f"GGBet DOM scraper v6 | Oxylabs+stealth+scroll | max={max_matches}")

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
                actor.log.error("All proxies failed — aborting")
                return

            # Discover game URLs from footer
            try:
                discovered = await page.evaluate(JS_DISCOVER_GAMES)
                if discovered and len(discovered) >= 3:
                    game_list = [(r["text"], r["url"]) for r in discovered]
                    actor.log.info(f"Discovered {len(game_list)} games from DOM")
                else:
                    game_list = ESPORT_GAMES_DEFAULT
                    actor.log.info("Using default game list")
            except Exception:
                game_list = ESPORT_GAMES_DEFAULT

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
            "method":        "playwright_dom_v6_scroll",
            "scraped_at":    now,
        })
        actor.log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
