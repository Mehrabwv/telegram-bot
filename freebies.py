"""Free games and apps (free to keep, for a limited time), for the morning digest.

Sources (all free, no keys):
- Epic Games Store: its own list of this week's free games
- GamerPower (gamerpower.com): giveaways on Steam, Epic, GOG and Android/iOS
- r/googleplaydeals: paid Android apps and games that are free right now

Only paid games and apps you can claim for $0 and keep for good are listed: no
discounts, no games that are always free, and no free trials, subscriptions,
in-app purchases or promo codes. Each one is reported once; ones you've already
seen come back only as a reminder on their last day.
"""

import asyncio
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import httpx

EPIC_URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
GAMERPOWER_URL = "https://www.gamerpower.com/api/filter"
GAMERPOWER_PLATFORMS = "steam.epic-games-store.gog.android.ios"
PLAY_DEALS_URL = "https://www.reddit.com/r/googleplaydeals/new/.rss?limit=50"
# Play Store posts that aren't a paid app made free to keep: trials, subscriptions,
# in-app purchases, promo codes, betas
NOT_FREE_TO_KEEP = re.compile(
    r"\b(iap|in-app|trials?|subscriptions?|sub|promo|codes?|beta|first (month|year)|unlock)\b", re.I
)
HEADERS = {"User-Agent": "TelegramNewsBot/1.0 (personal bot)"}

MAX_GAMES = 6  # in the morning digest, the most valuable first (/free shows all)
MAX_APPS = 5
APP_MAX_AGE_HOURS = 30  # Play Store deals posted since about yesterday morning
SEEN_KEEP_DAYS = 30


def plain_key(title):
    """'Flotsam (Epic Games) Giveaway' and 'Flotsam' -> 'flotsam' (to spot duplicates)."""
    return re.sub(r"[^a-z0-9]", "", clean_title(title).lower())


def clean_title(title):
    title = re.sub(r"\s*\([^)]*\)\s*(Giveaways?|Key Giveaway)?\s*$", "", title.strip())
    return re.sub(r"\s+(Giveaways?|Key Giveaway)$", "", title).strip()


def short_platform(platforms):
    text = platforms.lower()
    if "steam" in text:
        return "Steam"
    if "epic" in text:
        return "Epic"
    if "gog" in text:
        return "GOG"
    if "android" in text and "ios" in text:
        return "Android/iOS"
    if "android" in text:
        return "Android"
    if "ios" in text:
        return "iOS"
    return platforms.split(",")[0].strip()


def parse_time(text):
    """'2026-11-08 23:59:00', '2026-11-13T16:00:00.000Z' or '...+00:00' -> local time."""
    try:
        moment = datetime.fromisoformat(text.strip())
    except (AttributeError, ValueError):
        return None
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


async def epic_games(client):
    response = await client.get(EPIC_URL, params={"locale": "en-US", "country": "US"})
    response.raise_for_status()
    games = []
    for item in response.json()["data"]["Catalog"]["searchStore"]["elements"]:
        price = (item.get("price") or {}).get("totalPrice") or {}
        offers = ((item.get("promotions") or {}).get("promotionalOffers") or [])
        if not offers or price.get("discountPrice") != 0 or not price.get("originalPrice"):
            continue  # not free right now, or always free
        offer = offers[0]["promotionalOffers"][0]
        slug = item.get("productSlug") or (item.get("catalogNs", {}).get("mappings") or [{}])[0].get("pageSlug")
        games.append({
            "key": "epic:" + plain_key(item["title"]),
            "title": item["title"],
            "where": "Epic",
            "worth": price.get("fmtPrice", {}).get("originalPrice", ""),
            "ends": parse_time(offer.get("endDate", "")),
            "url": f"https://store.epicgames.com/p/{slug}" if slug else "https://store.epicgames.com/free-games",
        })
    return games


async def gamerpower_games(client):
    response = await client.get(GAMERPOWER_URL, params={"platform": GAMERPOWER_PLATFORMS, "type": "game"})
    if response.status_code == 201:  # "no giveaways right now"
        return []
    response.raise_for_status()
    games = []
    for item in response.json():
        if item.get("status", "Active") != "Active":
            continue
        where = short_platform(item.get("platforms", ""))
        games.append({
            "key": f"{'epic' if where == 'Epic' else 'gp'}:{plain_key(item['title'])}",
            "title": clean_title(item["title"]),
            "where": where,
            "worth": item.get("worth") if item.get("worth") not in (None, "N/A") else "",
            "ends": parse_time(item.get("end_date", "")),
            "url": item.get("open_giveaway_url") or item.get("gamerpower_url", ""),
        })
    return games


async def play_store_freebies(client):
    response = await client.get(PLAY_DEALS_URL)
    response.raise_for_status()
    atom = "{http://www.w3.org/2005/Atom}"
    apps, now = [], datetime.now()
    for entry in ET.fromstring(response.text).iter(atom + "entry"):
        title = entry.findtext(atom + "title") or ""
        match = re.match(r"\[([^\]]+)\]\s*(.+?)\s*\(([^)]*?)\s*->\s*free\)", title, re.I)
        if not match or NOT_FREE_TO_KEEP.search(title):
            continue  # a discount, or free only for a while / inside the app
        posted = parse_time(entry.findtext(atom + "updated") or "")
        if posted and now - posted > timedelta(hours=APP_MAX_AGE_HOURS):
            continue
        link = entry.find(atom + "link")
        apps.append({
            "key": "play:" + plain_key(match.group(2)),
            "title": match.group(2),
            "where": "Android " + match.group(1).rstrip("s").lower(),  # "Android game", "Android app"
            "worth": match.group(3),
            "ends": None,
            "url": link.get("href") if link is not None else "",
        })
    return apps[:MAX_APPS]


async def get_freebies():
    """{'games': [...], 'apps': [...]}: everything free right now (a source that
    fails is skipped)."""
    async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True) as client:
        results = await asyncio.gather(epic_games(client), gamerpower_games(client),
                                       play_store_freebies(client), return_exceptions=True)
    for name, result in zip(("Epic", "GamerPower", "Play Store deals"), results):
        if isinstance(result, Exception):
            print(f"Free games/apps: {name} failed ({type(result).__name__})")
    epic, gamerpower, apps = [r if isinstance(r, list) else [] for r in results]

    games, keys = [], set()
    for game in epic + gamerpower:  # Epic's own list first; GamerPower repeats some
        if game["key"] not in keys:
            keys.add(game["key"])
            games.append(game)
    games.sort(key=lambda g: -worth_value(g["worth"]))
    return {"games": games, "apps": apps}


def worth_value(worth):
    match = re.search(r"[\d.]+", worth.replace(",", "") if worth else "")
    return float(match.group()) if match else 0


def ends_text(moment, now):
    if moment is None:
        return ""
    if moment.date() == now.date():
        return "ends today"
    if moment.date() == (now + timedelta(days=1)).date():
        return "ends tomorrow"
    return "until " + moment.strftime("%b %d").replace(" 0", " ")


def section(freebies, seen, now=None, max_games=MAX_GAMES):
    """(Telegram HTML or None, keys reported). seen: {key: date first reported}.
    New giveaways are listed; already-reported ones only on their last day."""
    now = now or datetime.now()
    games, apps = [], []
    for game in freebies["games"]:
        last_day = game["ends"] is not None and game["ends"].date() == now.date()
        if game["key"] not in seen or last_day:
            games.append(game)
    apps = [app for app in freebies["apps"] if app["key"] not in seen]
    more = len(games) - max_games  # shown the next morning, or now with /free
    games = games[:max_games]
    if not games and not apps:
        return None, []

    def line(item):
        url = html.escape(item["url"], quote=True)
        text = f'• <a href="{url}">{html.escape(item["title"])}</a> ({html.escape(item["where"])}'
        if item["worth"]:
            text += f", normally {html.escape(item['worth'])}"
        text += ")"
        ends = ends_text(item["ends"], now)
        if ends:
            text += f" · {ends}" + (" ⏰" if item["key"] in seen else "")
        return text

    lines = ["🎁 <b>Free to keep</b>"] + [line(g) for g in games] + [line(a) for a in apps]
    if more > 0:
        lines.append(f"…and {more} more: send /free")
    return "\n".join(lines), [item["key"] for item in games + apps]


def remember(seen, keys, now=None):
    """Adds the reported keys (with today's date) and forgets old ones."""
    now = now or datetime.now()
    seen.update({key: seen.get(key, now.date().isoformat()) for key in keys})
    cutoff = (now - timedelta(days=SEEN_KEEP_DAYS)).date().isoformat()
    return {key: day for key, day in seen.items() if day >= cutoff}
