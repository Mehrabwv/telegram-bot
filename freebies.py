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
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

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
        images = {}
        for image in item.get("keyImages") or []:
            images.setdefault(image.get("type"), []).append(image.get("url"))
        about = (item.get("description") or "").strip()
        games.append({
            "key": "epic:" + plain_key(item["title"]),
            "title": item["title"],
            "where": "Epic",
            "worth": price.get("fmtPrice", {}).get("originalPrice", ""),
            "ends": parse_time(offer.get("endDate", "")),
            "url": f"https://store.epicgames.com/p/{slug}" if slug else "https://store.epicgames.com/free-games",
            # For the dashboard, if Steam doesn't know the game
            "about": about if about != item["title"] else "",
            "pictures": [u for kind in ("GalleryImage", "featuredMedia", "OfferImageWide")
                         for u in images.get(kind, []) if u and u.startswith("https://")][:8],
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
            "about": (item.get("description") or "").split("\n")[0].strip(),
            "pictures": [u for u in (item.get("image"),) if u and u.startswith("https://")],
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
        package = re.search(r"play\.google\.com/store/apps/details\?id=([\w.]+)",
                            html.unescape(entry.findtext(atom + "content") or ""))
        apps.append({
            "key": "play:" + plain_key(match.group(2)),
            "title": match.group(2),
            "where": "Android " + match.group(1).rstrip("s").lower(),  # "Android game", "Android app"
            "worth": match.group(3),
            "ends": None,
            "url": link.get("href") if link is not None else "",
            "package": package.group(1) if package else None,
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


# =====================================
# WHAT EACH GAME IS (for the dashboard and the messages)
# =====================================
# Steam knows most games, even ones given away on Epic or GOG: its description,
# screenshots and type, plus the review score from SteamSpy (Steam's own review
# numbers aren't public any more). Android apps come from their Play Store page.
# Looked up once per game and kept for DETAILS_KEEP_DAYS.

STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAMSPY_URL = "https://steamspy.com/api.php"
PLAY_URL = "https://play.google.com/store/apps/details"
BROWSER_HEADERS = {  # the Play Store sends its full page only to browsers
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
}
DETAILS_FILE = Path(__file__).resolve().parent / "freebies_details.json"
DETAILS_KEEP_DAYS = 14
PICTURES = 8  # screenshots kept per game
MODES = ("Single-player", "Multi-player", "Online Co-op", "Shared/Split Screen Co-op", "Online PvP")
# Steam tags that don't say what kind of game it is
VAGUE_KINDS = {"indie", "singleplayer", "multiplayer", "early access", "free to play", "great soundtrack",
               "2d", "3d", "colorful", "cute", "family friendly", "controller", "steam achievements"}
# Giveaway sites' sales talk, left out of the description
GIVEAWAY_WORDS = re.compile(r"\b(free|claim|giveaway|grab|download|save \d+%|don.t miss)\b", re.I)
EDITION = re.compile(r"\b(complete|definitive|deluxe|ultimate|gold|enhanced|remastered|anniversary|"
                     r"game of the year|goty|digital)\b|\bedition\b", re.I)

_spy_lock = asyncio.Lock()  # SteamSpy allows about one request a second


def name_keys(title):
    """'Model Builder: Complete Edition' -> {'modelbuildercompleteedition', 'modelbuilder'}:
    the same game whatever the edition is called."""
    base = clean_title(title)
    keys = {plain_key(base), plain_key(EDITION.sub("", base))}
    return {key for key in keys if len(key) >= 3}


def steam_rating(positive, negative):
    """Steam's own words for a review score, e.g. 'Very Positive'."""
    total = positive + negative
    if total < 10:
        return None
    percent = round(100 * positive / total)
    if percent >= 80:
        label = "Overwhelmingly Positive" if percent >= 95 and total >= 500 else (
            "Very Positive" if total >= 50 else "Positive")
    elif percent >= 70:
        label = "Mostly Positive"
    elif percent >= 40:
        label = "Mixed"
    elif percent >= 20:
        label = "Mostly Negative"
    else:
        label = "Negative"
    return {"label": label, "short": f"👍 {percent}%",
            "text": f"{label}: {percent}% of {total:,} Steam reviews"}


async def steam_app_id(client, title):
    """The Steam app with exactly this name (editions aside), or None."""
    response = await client.get(STEAM_SEARCH_URL, params={"term": clean_title(title), "cc": "US", "l": "en"})
    response.raise_for_status()
    found = [item for item in response.json().get("items") or [] if item.get("type") == "app"]
    exact = plain_key(clean_title(title))
    for item in found:
        if plain_key(item["name"]) == exact:
            return item["id"]
    wanted = name_keys(title)
    for item in found:
        if name_keys(item["name"]) & wanted:
            return item["id"]
    return None


async def steam_details(client, app_id):
    response = await client.get(STEAM_DETAILS_URL, params={"appids": app_id, "cc": "US", "l": "en"})
    response.raise_for_status()
    entry = response.json().get(str(app_id)) or {}
    if not entry.get("success"):
        return None
    data = entry["data"]

    spy = {}
    async with _spy_lock:
        try:
            spy = (await client.get(STEAMSPY_URL, params={"request": "appdetails", "appid": app_id})).json()
        except (httpx.HTTPError, ValueError):
            pass
        await asyncio.sleep(1)
    tags = spy.get("tags") if isinstance(spy.get("tags"), dict) else {}
    kinds = [name for name, _ in sorted(tags.items(), key=lambda pair: -pair[1])]
    kinds = kinds or [g["description"] for g in data.get("genres") or []]
    kinds = [kind for kind in kinds if kind.lower() not in VAGUE_KINDS][:5]

    return {
        "summary": html.unescape(data.get("short_description") or "").strip(),
        "kind": kinds,
        "modes": [c["description"] for c in data.get("categories") or [] if c["description"] in MODES][:3],
        "pictures": [s["path_thumbnail"] for s in data.get("screenshots") or []][:PICTURES],
        "cover": data.get("header_image"),
        "rating": steam_rating(int(spy.get("positive") or 0), int(spy.get("negative") or 0)),
        "developer": ", ".join((data.get("developers") or [])[:2]),
        "released": (data.get("release_date") or {}).get("date", ""),
        "reviews_url": f"https://store.steampowered.com/app/{app_id}/#app_reviews_hash",
        "reviews_label": "Steam reviews",
    }


async def play_details(client, package):
    response = await client.get(PLAY_URL, params={"id": package, "hl": "en_US", "gl": "US"},
                                headers=BROWSER_HEADERS)
    response.raise_for_status()
    page = response.text
    app = None
    for block in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "SoftwareApplication":
            app = data
            break
    if app is None:
        return None
    category = str(app.get("applicationCategory") or "")
    kind = category.removeprefix("GAME_").replace("_", " ").title()
    if category.startswith("GAME"):
        kind += " game"
    rating = None
    stars = app.get("aggregateRating") or {}
    try:
        value, count = float(stars["ratingValue"]), int(stars["ratingCount"])
        rating = {"label": f"{value:.1f}★", "short": f"★ {value:.1f}",
                  "text": f"{value:.1f}★ from {count:,} Play Store ratings"}
    except (KeyError, TypeError, ValueError):
        pass
    shots = re.findall(r'<img[^>]+src="(https://play-lh\.googleusercontent\.com/[^"]+)"[^>]*alt="Screenshot image"', page)
    return {
        "summary": html.unescape(str(app.get("description") or "")).strip(),
        "kind": [kind] if kind else [],
        "modes": [],
        "pictures": list(dict.fromkeys(shots))[:PICTURES],
        "cover": app.get("image") if str(app.get("image", "")).startswith("https://") else None,
        "rating": rating,
        "developer": (app.get("author") or {}).get("name", "") if isinstance(app.get("author"), dict) else "",
        "released": "",
        "reviews_url": f"{PLAY_URL}?id={package}#reviews",
        "reviews_label": "Play Store reviews",
    }


async def look_up(client, item):
    """Details for one game or app; falls back to what the giveaway itself says."""
    info = None
    try:
        if item.get("package"):
            info = await play_details(client, item["package"])
        else:
            app_id = await steam_app_id(client, item["title"])
            if app_id:
                info = await steam_details(client, app_id)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        print(f"Free games: details for {item['title']} failed ({type(error).__name__})")
        return None  # not saved: tried again next time
    if info is None:
        pictures = item.get("pictures") or []
        sentences = re.split(r"(?<=[.!?])\s+", item.get("about", ""))
        about = " ".join(s for s in sentences if s and not GIVEAWAY_WORDS.search(s))
        info = {"summary": about, "kind": [], "modes": [], "pictures": pictures,
                "cover": pictures[0] if pictures else None, "rating": None, "developer": "",
                "released": "", "reviews_url": None, "reviews_label": ""}
    if not info["reviews_url"]:
        info["reviews_url"] = "https://www.google.com/search?q=" + quote_plus(f'"{item["title"]}" game review')
        info["reviews_label"] = "Find reviews"
    info["video_url"] = "https://www.youtube.com/results?search_query=" + quote_plus(f"{item['title']} gameplay")
    return info


async def add_details(freebies):
    """Adds item["info"] (what the game is, pictures, reviews) to every game and app."""
    try:
        saved = json.loads(DETAILS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        saved = {}
    cutoff = (datetime.now() - timedelta(days=DETAILS_KEEP_DAYS)).date().isoformat()
    saved = {key: entry for key, entry in saved.items() if entry.get("at", "") >= cutoff}

    items = freebies["games"] + freebies["apps"]
    missing = [item for item in items if item["key"] not in saved]
    if missing:
        limit = asyncio.Semaphore(3)

        async def one(client, item):
            async with limit:
                return item["key"], await look_up(client, item)

        async with httpx.AsyncClient(timeout=20, headers=HEADERS, follow_redirects=True) as client:
            for key, info in await asyncio.gather(*(one(client, item) for item in missing)):
                if info is not None:
                    saved[key] = {"at": datetime.now().date().isoformat(), "info": info}
        try:
            DETAILS_FILE.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    for item in items:
        item["info"] = (saved.get(item["key"]) or {}).get("info")
    return freebies


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
        info = item.get("info") or {}
        about = [", ".join(info.get("kind", [])[:3])]
        if info.get("rating"):
            about.append(f'<a href="{html.escape(info["reviews_url"], quote=True)}">{info["rating"]["short"]}</a>')
        about = " · ".join(part for part in about if part)
        if about:
            text += f"\n    <i>{about}</i>"  # e.g. "City Builder, Colony Sim · 👍 77%"
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
