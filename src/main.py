#!/usr/bin/env python3
"""
GGBet (gg.bet) Esports Odds Scraper — v3 (2026-06-01)

Method: Playwright DOM extraction
  1. Load gg.bet/esports page — SPA renders match cards incl. odds
  2. For each esports game, navigate /esports?sportId=<id>
  3. Extract teams + Match Winner odds from rendered DOM
  4. Push unified schema records to Apify dataset

Allowed geos: PL, IE, CA, SE, AT, LV, LT, EE, FI, DK, NO, NZ, HU, SK, RO, PH
(Antigua/AU/US/GB/DE all blocked by GGBet geo-restriction)

Game filter URLs (confirmed working from local tests):
  Dota 2      /esports?sportId=esports_dota_2
  Valorant    /esports?sportId=esports_valorant
  LoL         /esports?sportId=esports_league_of_legends
  CS2         /esports?sportId=esports_cs2  (or /counter-strike)
  MLBB        /esports?sportId=esports_mobile_legends
  Overwatch   /esports?sportId=esports_overwatch
  R6S         /esports?sportId=esports_rainbow_six
  CoD         /esports?sportId=esports_call_of_duty
  Rocket Lg   /esports?sportId=esports_rocket_league
  PUBG        /esports?sportId=esports_pubg
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from apify import Actor
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ggbet-scraper")

GG_BET_URL = "https://gg.bet"
ESPORT_URL = f"{GG_BET_URL}/esports"

# Confirmed working game filter URLs
ESPORT_GAMES = [
    ("CS2",          f"{ESPORT_URL}?sportId=esports_cs2"),
    ("Dota 2",       f"{ESPORT_URL}?sportId=esports_dota_2"),
    ("Valorant",     f"{ESPORT_URL}?sportId=esports_valorant"),
    ("LoL",          f"{ESPORT_URL}?sportId=esports_league_of_legends"),
    ("MLBB",         f"{ESPORT_URL}?sportId=esports_mobile_legends"),
    ("Overwatch 2",  f"{ESPORT_URL}?sportId=esports_overwatch"),
    ("Rainbow Six",  f"{ESPORT_URL}?sportId=esports_rainbow_six"),
    ("Call of Duty", f"{ESPORT_URL}?sportId=esports_call_of_duty"),
    ("Rocket League",f"{ESPORT_URL}?sportId=esports_rocket_league"),
    ("PUBG",         f"{ESPORT_URL}?sportId=esports_pubg"),
    ("King of Glory",f"{ESPORT_URL}?sportId=esports_king_of_glory"),
    ("StarCraft 2",  f"{ESPORT_URL}?sportId=esports_starcraft_2"),
]

# JS extractor — runs inside the browser page context
# Returns list of {team1, team2, tournament, start_time, price_team1, price_team2, price_draw}
JS_EXTRACT = """
() => {
    const results = [];
    // Each match group holds a tournament name and 1+ match rows
    // data-test="match" marks a match container; it has:
    //   team names as text nodes, start time, and sibling odd-buttons

    // Approach: walk every element with data-test containing "match"
    // and look for the two adjacent odd-button elements for that match
    const matchEls = document.querySelectorAll('[data-test*="event-row"], [data-test*="match-row"], [data-test*="event-item"]');

    if (matchEls.length === 0) {
        // Fallback: use [data-test*="match"] we found in testing
        const matches = document.querySelectorAll('[data-test*="match"]');
        for (const m of matches) {
            try {
                // Get text nodes directly in this element (not nested)
                const texts = [];
                m.querySelectorAll('[data-test*="odd-button"]').forEach(btn => {
                    const title = btn.querySelector('[data-test*="title"], [class*="title"]');
                    const val = btn.querySelector('[class*="odd"], [class*="coeff"], [class*="value"]');
                    if (title && val) {
                        const ttext = title.textContent.trim();
                        const vtext = val.textContent.trim();
                        if (ttext && vtext && !isNaN(parseFloat(vtext))) {
                            texts.push({name: ttext, value: parseFloat(vtext)});
                        }
                    }
                });
                if (texts.length >= 2) {
                    results.push({raw: texts, source: 'match-element'});
                }
            } catch(e) {}
        }
        return results;
    }

    for (const m of matchEls) {
        try {
            const oddBtns = m.querySelectorAll('[data-test*="odd-button"]');
            const texts = [];
            oddBtns.forEach(btn => {
                const titleEl = btn.querySelector('[data-test*="title"], [class*="title"]');
                const valEl = btn.querySelector('[class*="odd"], [class*="coeff"], [class*="value"]');
                const titleText = titleEl ? titleEl.textContent.trim() : btn.textContent.trim();
                const valText = valEl ? valEl.textContent.trim() : '';
                if (valText && !isNaN(parseFloat(valText))) {
                    texts.push({name: titleText, value: parseFloat(valText)});
                }
            });
            if (texts.length >= 2) {
                results.push({raw: texts, source: 'event-row'});
            }
        } catch(e) {}
    }
    return results;
}
"""

# Better JS: extract structured match data from the groups we found
JS_EXTRACT_GROUPS = """
() => {
    const records = [];

    // Find all group/section containers
    const groups = document.querySelectorAll('[class*="EventsGroup"], [class*="events-group"], [class*="SportSection"], [class*="sport-section"]');
    const containers = groups.length > 0 ? groups : [document.body];

    for (const container of containers) {
        // Tournament name: first heading or title element in container
        let tournamentName = '';
        const tourEl = container.querySelector('[class*="tournament"], [class*="Tournament"], [class*="league"], [class*="League"], h3, h4, [class*="heading"]');
        if (tourEl) tournamentName = tourEl.textContent.trim().split('\\n')[0].trim();

        // Find all match rows — odd buttons come in pairs (or triples with draw)
        const allOddBtns = Array.from(container.querySelectorAll('[data-test*="odd-button"]'));

        // Each match has 2 or 3 odd buttons
        // Strategy: group consecutive odd buttons by their parent match container
        const matchMap = new Map();
        allOddBtns.forEach(btn => {
            // Walk up to find common match parent
            let parent = btn.parentElement;
            let depth = 0;
            while (parent && depth < 6) {
                // Check if this parent is a match-level container
                const cls = parent.className || '';
                const testAttr = parent.getAttribute('data-test') || '';
                if (/match|event|fixture|game-row/i.test(cls + testAttr)) {
                    break;
                }
                parent = parent.parentElement;
                depth++;
            }
            if (!parent) return;

            const key = parent;
            if (!matchMap.has(key)) matchMap.set(key, []);
            matchMap.get(key).push(btn);
        });

        for (const [matchEl, btns] of matchMap.entries()) {
            if (btns.length < 2) continue;

            // Extract time from match element
            let startTime = '';
            const timeEl = matchEl.querySelector('[class*="time"], [class*="Time"], [data-test*="time"], time');
            if (timeEl) startTime = timeEl.textContent.trim();

            // Extract team names from match element (not from odd buttons)
            let team1 = '', team2 = '';
            const teamEls = matchEl.querySelectorAll('[class*="competitor"], [class*="team-name"], [class*="TeamName"], [class*="opponent"]');
            if (teamEls.length >= 2) {
                team1 = teamEls[0].textContent.trim();
                team2 = teamEls[1].textContent.trim();
            }

            // Parse odds from buttons
            const parsedOdds = [];
            for (const btn of btns) {
                // Find the numeric value
                let val = null;
                btn.querySelectorAll('*').forEach(el => {
                    if (el.children.length === 0) {
                        const t = el.textContent.trim();
                        const v = parseFloat(t);
                        if (!isNaN(v) && v >= 1.01 && v <= 500) {
                            val = v;
                        }
                    }
                });

                // Find the label (team name or "Draw")
                let label = '';
                btn.querySelectorAll('*').forEach(el => {
                    if (el.children.length === 0) {
                        const t = el.textContent.trim();
                        if (t && isNaN(parseFloat(t)) && t.length > 0 && t !== '-') {
                            label = t;
                        }
                    }
                });

                if (val !== null) {
                    parsedOdds.push({label, value: val});
                }
            }

            if (parsedOdds.length >= 2) {
                // If we don't have team names yet, use odd button labels
                if (!team1 && parsedOdds[0].label) team1 = parsedOdds[0].label;
                if (!team2 && parsedOdds[parsedOdds.length - 1].label) team2 = parsedOdds[parsedOdds.length - 1].label;

                const p1 = parsedOdds[0].value;
                const p2 = parsedOdds[parsedOdds.length === 3 ? 2 : 1].value;
                const pDraw = parsedOdds.length === 3 ? parsedOdds[1].value : null;

                records.push({
                    tournament: tournamentName,
                    team1,
                    team2,
                    startTime,
                    p1,
                    p2,
                    pDraw,
                });
            }
        }
    }
    return records;
}
"""

# Simplest working extraction based on the DOM structure we found:
# - odd-buttons contain team name + odds value
# - match elements group odd-buttons together
JS_EXTRACT_SIMPLE = """
() => {
    const records = [];
    const seen = new Set();

    // Get all odd-button elements
    const oddBtns = Array.from(document.querySelectorAll('[data-test*="odd-button"]'));

    // Build a map: parent match element -> list of {label, value}
    const matchMap = new Map();

    for (const btn of oddBtns) {
        // Walk up max 8 levels to find the match-level container
        let matchEl = null;
        let cur = btn.parentElement;
        for (let i = 0; i < 8 && cur; i++) {
            const dt = (cur.getAttribute('data-test') || '').toLowerCase();
            const cls = (cur.className || '').toLowerCase();
            if (dt.includes('match') || dt.includes('event') || dt.includes('game-row') ||
                cls.includes('matchcard') || cls.includes('eventcard') || cls.includes('match-row')) {
                matchEl = cur;
                break;
            }
            cur = cur.parentElement;
        }
        if (!matchEl) matchEl = btn.parentElement?.parentElement?.parentElement;
        if (!matchEl) continue;

        // Extract label and value from this button
        let label = '';
        let value = null;

        // Walk all leaf text nodes
        const walker = document.createTreeWalker(btn, NodeFilter.SHOW_TEXT, null);
        const texts = [];
        let node;
        while ((node = walker.nextNode())) {
            const t = node.textContent.trim();
            if (t) texts.push(t);
        }

        for (const t of texts) {
            const v = parseFloat(t);
            if (!isNaN(v) && v >= 1.01 && v <= 500 && value === null) {
                value = v;
            } else if (isNaN(parseFloat(t)) && t !== '-' && t.length > 0 && !label) {
                label = t;
            }
        }

        if (value === null) continue;

        if (!matchMap.has(matchEl)) matchMap.set(matchEl, []);
        matchMap.get(matchEl).push({label, value});
    }

    // For each match, extract teams + odds
    for (const [matchEl, odds] of matchMap.entries()) {
        if (odds.length < 2) continue;

        // Dedupe odds (same label+value appears twice due to DOM nesting)
        const deduped = [];
        const oddsKeys = new Set();
        for (const o of odds) {
            const k = `${o.label}:${o.value}`;
            if (!oddsKeys.has(k)) {
                oddsKeys.add(k);
                deduped.push(o);
            }
        }
        if (deduped.length < 2) continue;

        // Filter out handicap/total lines (those have numeric labels like "-8.5", "+8.5", "Over", "Under")
        const matchWinner = deduped.filter(o => {
            const l = o.label;
            return l && !l.startsWith('-') && !l.startsWith('+') &&
                   !/^(over|under|yes|no|\\d+\\.\\d)$/i.test(l) &&
                   !/^\\d+$/.test(l);
        });

        if (matchWinner.length < 2) continue;

        const team1 = matchWinner[0].label;
        const p1 = matchWinner[0].value;

        // Draw check: if 3 outcomes, middle is draw
        let p2, pDraw;
        if (matchWinner.length === 3) {
            const drawCandidate = matchWinner[1];
            const isDraw = /draw|x/i.test(drawCandidate.label);
            if (isDraw) {
                pDraw = drawCandidate.value;
                p2 = matchWinner[2].value;
            } else {
                p2 = matchWinner[1].value;
                pDraw = null;
            }
        } else {
            p2 = matchWinner[1].value;
            pDraw = null;
        }
        const team2 = matchWinner[matchWinner.length >= 3 && pDraw !== null ? 2 : 1].label;

        // Get tournament name: walk up to find a heading or named container
        let tournament = '';
        let startTime = '';
        let cur = matchEl.parentElement;
        for (let i = 0; i < 6 && cur; i++) {
            const heading = cur.querySelector('h1,h2,h3,h4,[class*="tournament"],[class*="league"],[class*="section-title"]');
            if (heading) {
                tournament = heading.textContent.trim().split('\\n')[0].trim();
                break;
            }
            cur = cur.parentElement;
        }

        // Get start time
        const timeEl = matchEl.querySelector('[class*="time"],[class*="Time"],time,[data-test*="time"]');
        if (timeEl) startTime = timeEl.textContent.trim();

        const key = `${team1.toLowerCase()}|${team2.toLowerCase()}`;
        if (!seen.has(key)) {
            seen.add(key);
            records.push({tournament, team1, team2, startTime, p1, p2, pDraw});
        }
    }

    return records;
}
"""

VIRTUAL_FILTER = re.compile(
    r"efootball|esoccer|ebasket|etennis|virtual|efight|ecricket|marble|drone|sim|nba2k",
    re.IGNORECASE,
)

def is_real_esport(game_label: str, team1: str, team2: str) -> bool:
    combined = f"{game_label} {team1} {team2}".lower()
    return not VIRTUAL_FILTER.search(combined)


async def scrape_page(page, url: str, game_label: str, now: str, timeout_ms: int = 30000) -> List[Dict]:
    """Navigate to a game page and extract match data."""
    logger.info(f"Loading {game_label}: {url}")
    records = []

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(8000)  # wait for SPA hydration
    except Exception as e:
        logger.warning(f"Navigation error for {game_label}: {e}")
        return records

    try:
        raw = await page.evaluate(JS_EXTRACT_SIMPLE)
        logger.info(f"  {game_label}: extracted {len(raw)} raw records from DOM")

        for item in raw:
            team1 = (item.get("team1") or "").strip()
            team2 = (item.get("team2") or "").strip()

            if not team1 or not team2:
                continue
            if not is_real_esport(game_label, team1, team2):
                continue

            p1 = item.get("p1")
            p2 = item.get("p2")
            if not p1 or not p2:
                continue

            tournament = (item.get("tournament") or "").strip()
            start_time = (item.get("startTime") or "").strip()
            slug = f"{team1.lower().replace(' ', '-')}-vs-{team2.lower().replace(' ', '-')}"
            match_url = f"{GG_BET_URL}/esports/{slug}"

            records.append({
                "bookmaker": "ggbet",
                "game_raw": game_label,
                "game_normalised": game_label,
                "tournament_name": tournament,
                "team1": team1,
                "team2": team2,
                "match_start_time": start_time,
                "match_url": match_url,
                "market_name": "Match Winner",
                "price_team1": p1,
                "price_team2": p2,
                "price_draw": item.get("pDraw"),
                "scraped_at": now,
            })

    except Exception as e:
        logger.error(f"  Extraction error for {game_label}: {e}")

    return records


async def main() -> None:
    async with Actor() as actor:
        inp = await actor.get_input() or {}
        max_matches = inp.get("max_matches", 500)
        proxy_country = inp.get("proxy_country", "PL")
        debug = inp.get("debug", False)
        wait_ms = inp.get("wait_ms", 8000)
        games = inp.get("games", None)  # None = all games

        actor.log.info(f"GGBet DOM scraper v3 | proxy_country={proxy_country} | max_matches={max_matches}")

        # Build proxy URL
        proxy_url = None
        proxy_cfg = await actor.create_proxy_configuration(
            actor_proxy_input=inp.get("proxyConfiguration") or {
                "useApifyProxy": True,
                "apifyProxyGroups": ["RESIDENTIAL"],
                "apifyProxyCountry": proxy_country,
            }
        )
        if proxy_cfg:
            proxy_url = await proxy_cfg.new_url()
            actor.log.info(f"Proxy: {proxy_url[:40]}...")

        now = datetime.now(timezone.utc).isoformat()
        all_records: List[Dict] = []
        seen_keys: set = set()

        async with async_playwright() as pw:
            browser_opts = {"headless": True}
            if proxy_url:
                browser_opts["proxy"] = {"server": proxy_url}

            browser = await pw.chromium.launch(**browser_opts)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
                locale="en-US",
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            # Warm up — load main esports page first to establish session
            actor.log.info("Warming up session on gg.bet/esports...")
            try:
                await page.goto(ESPORT_URL, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(5000)
                actor.log.info(f"Session established. Title: {await page.title()}")
            except Exception as e:
                actor.log.warning(f"Warm-up failed: {e}")

            # Scrape each game
            game_list = ESPORT_GAMES if not games else [(g, u) for g, u in ESPORT_GAMES if g in games]
            for game_label, url in game_list:
                if len(all_records) >= max_matches:
                    break

                recs = await scrape_page(page, url, game_label, now, timeout_ms=30000)
                actor.log.info(f"  {game_label}: {len(recs)} valid records")

                for rec in recs:
                    key = f"{rec['team1'].lower()}|{rec['team2'].lower()}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_records.append(rec)

                await asyncio.sleep(1.5)

            await browser.close()

        actor.log.info(f"Total unique records: {len(all_records)}")

        # Push to dataset
        for rec in all_records[:max_matches]:
            await actor.push_data(rec)

        # Meta record
        await actor.push_data({
            "_meta": True,
            "bookmaker": "ggbet",
            "records_total": min(len(all_records), max_matches),
            "method": "playwright_dom",
            "scraped_at": now,
        })

        actor.log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
