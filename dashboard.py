"""The dashboard: an app for your Windows computers (and any browser).

It runs inside the bot (bot.py starts it) and shows everything in more detail
than Telegram: the news ranked for you with 👍/👎, stocks with charts, the full
weather, free games and apps, and an inbox of everything the bot sent. Install it
from Edge (the "App available" icon in the address bar) and turn on notifications
in it: breaking news, digests and big stock moves then pop up in Windows.

Open it on this computer: http://localhost:8765
From your other devices: through Tailscale (see README.md, "Dashboard app").

The server only listens on this computer (127.0.0.1). Tailscale passes your
other devices' requests through to it, so nothing is opened to the network.

Try it without the bot: py dashboard.py
(downloads the headlines, uses copies of your news ratings and inbox)
"""

import asyncio
import html
import json
import shutil
import socket
import sys
import tempfile
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

import curation
import display
import freebies
import webpush
from news import get_news, latest_news, section_label
from stocks import WATCHLIST, get_chart, get_quotes, is_recent, stock_stories
from weather import (
    accuracy_summary,
    compare_sources,
    confidence_note,
    describe,
    get_weather,
    hour,
)

# =====================================
# SETTINGS
# =====================================

HOST = "127.0.0.1"  # this computer only; Tailscale brings your other devices in
PORT = 8765
# Names this app may be opened by (protects against other websites reaching it).
# Tailscale names end in .ts.net. On the Raspberry Pi later, add its name here.
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", socket.gethostname().lower()}
ALLOWED_HOST_ENDINGS = (".ts.net",)

FOLDER = Path(__file__).resolve().parent
APP_FOLDER = FOLDER / "dashboard"
INBOX_FILE = FOLDER / "dashboard_inbox.json"
STATE_FILE = FOLDER / "state.json"
INBOX_KEEP = 200  # notifications kept in the inbox

# How long fetched data is reused before it's downloaded again (minutes)
WEATHER_MINUTES = 10
QUOTES_MINUTES = 2
CHART_MINUTES = 5
FREEBIES_MINUTES = 60

_runner = None
_cache = {}  # name -> (time, value)
_locks = {}
_preview = False


# =====================================
# CACHED DATA
# =====================================

async def cached(name, minutes, fetch):
    """fetch() at most every `minutes`; a failed fetch (None) keeps the older value."""
    lock = _locks.setdefault(name, asyncio.Lock())
    async with lock:
        saved = _cache.get(name)
        if saved and time.time() - saved[0] < minutes * 60:
            return saved[1]
        try:
            value = await fetch()
        except Exception as error:
            print(f"Dashboard: getting {name} failed ({type(error).__name__})")
            value = None
        if value is None:
            return saved[1] if saved else None
        _cache[name] = (time.time(), value)
        return value


def clock(moment):
    """'2:05 PM', or 'Wed 2:05 PM' for another day."""
    text = moment.strftime("%I:%M %p").lstrip("0")
    if moment.date() != datetime.now().date():
        text = moment.strftime("%a ") + text
    return text


# =====================================
# NEWS
# =====================================

def story_json(article, memory, ratings, tickers=()):
    story_id = curation.article_id(article["url"])
    score = curation.score_of(memory, article)
    return {
        "id": story_id,
        "title": article["title"],
        "url": article["url"],
        "section": section_label(article.get("section") or "News"),
        "summary": article.get("summary") or "",
        "published": (article.get("published") or "")[:16],
        "category": score["category"],
        "label": curation.LABELS[score["category"]],
        "importance": score["importance"],
        "interest": score["interest"],
        "rated_by_ai": not score.get("guess"),
        "why": score.get("why", ""),
        "new": curation.is_new(memory, article),
        "rating": ratings.get(story_id, 0),
        "tickers": list(tickers),
    }


def ratings_map(memory):
    return {r["id"]: r["rating"] for r in memory["ratings"]}


def news_json():
    news = latest_news()
    memory = curation.load()
    ratings = ratings_map(memory)
    tickers = {article["url"]: t for t, article in stock_stories(news.get("all") or [])}
    stories = [story_json(a, memory, ratings, tickers.get(a["url"], ()))
               for a in curation.ranked(memory, news.get("ranked") or [])]
    liked, disliked = curation.rating_counts()
    return {
        "updated": clock(news["time"]) if news.get("time") else None,
        "stories": stories,
        "ratings": {"liked": liked, "disliked": disliked},
    }


def find_story(story_id):
    """A story by id: from the current headlines, else from the bot's memory."""
    for article in latest_news().get("ranked") or []:
        if curation.article_id(article["url"]) == story_id:
            return article
    return curation.article_by_id(story_id)


def stories_by_id(ids):
    memory = curation.load()
    ratings = ratings_map(memory)
    found = []
    for story_id in ids:
        article = find_story(story_id)
        if article and article.get("url"):
            found.append(story_json(article, memory, ratings))
    return found


# =====================================
# STOCKS
# =====================================

async def stocks_json():
    quotes = await cached("quotes", QUOTES_MINUTES, get_quotes) or {}
    news = latest_news()
    stories = [(t, a) for t, a in stock_stories(news.get("all") or []) if is_recent(a)]
    items = []
    for ticker, names in WATCHLIST:
        quote = quotes.get(ticker)
        items.append({
            "ticker": ticker,
            "name": (quote or {}).get("name") or (names[0] if names else ticker),
            "price": quote["price"] if quote else None,
            "change": quote["change"] if quote else None,
            "previous": (quote or {}).get("previous"),
            "high": (quote or {}).get("high"),
            "low": (quote or {}).get("low"),
            "open": bool(quote and quote["open"]),
            "time": clock(quote["time"]) if quote and quote["time"] else None,
            "news": [{"id": curation.article_id(a["url"]), "title": a["title"], "url": a["url"],
                      "published": a["published"][:10]} for t, a in stories if ticker in t][:5],
        })
    return {"market": "Open" if any(i["open"] for i in items) else "Closed", "items": items}


# =====================================
# WEATHER
# =====================================

def weather_json(weather):
    brief = display.build_brief(weather, "now")
    current, hourly, daily = weather["current"], weather["hourly"], weather["daily"]
    now = datetime.fromisoformat(current["time"]).replace(minute=0)

    hours = []
    for i, text in enumerate(hourly["time"]):
        moment = datetime.fromisoformat(text)
        if now < moment <= now + timedelta(hours=24) and hourly["temperature_2m"][i] is not None:
            value = lambda key: (hourly.get(key) or [None] * (i + 1))[i]
            hours.append({
                "time": hour(moment),
                "temp": round(hourly["temperature_2m"][i]),
                "feels": round(value("apparent_temperature")) if value("apparent_temperature") is not None else None,
                "rain": round(value("precipitation_probability") or 0),
                "wind": round(value("wind_speed_10m")) if value("wind_speed_10m") is not None else None,
                "uv": round(value("uv_index")) if value("uv_index") is not None else None,
                "icon": display.icon_for_code(value("weather_code")),
                "day": display.is_daytime(weather, moment),
            })

    days = []
    for i, text in enumerate(daily["time"]):
        day = datetime.fromisoformat(text)
        pick = lambda key: (daily.get(key) or [None] * (i + 1))[i]
        if pick("temperature_2m_max") is None:
            continue
        days.append({
            "name": "Today" if day.date() == now.date() else day.strftime("%a"),
            "date": day.strftime("%b %d").replace(" 0", " "),
            "high": round(pick("temperature_2m_max")),
            "low": round(pick("temperature_2m_min")) if pick("temperature_2m_min") is not None else None,
            "rain": round(pick("precipitation_probability_max") or 0),
            "uv": round(pick("uv_index_max")) if pick("uv_index_max") is not None else None,
            "icon": display.icon_for_code(pick("weather_code")),
            "text": describe(pick("weather_code")).capitalize() if pick("weather_code") is not None else "",
            "sunrise": clock(datetime.fromisoformat(pick("sunrise"))) if pick("sunrise") else None,
            "sunset": clock(datetime.fromisoformat(pick("sunset"))) if pick("sunset") else None,
        })

    comparison = compare_sources(weather)
    weights = weather.get("weights") or {}
    total = sum(weights.values()) or 1
    return {
        "place": weather["place"],
        "updated": clock(datetime.fromisoformat(current["time"])),
        "now": {
            **brief["now"],
            "humidity": current.get("relative_humidity_2m"),
            "wind": current.get("wind_speed_10m"),
            "gusts": current.get("wind_gusts_10m"),
        },
        "high": brief["high"],
        "low": brief["low"],
        "for": brief["for"],
        "items": brief["items"],
        "confidence": brief["confidence"],
        "confidence_note": confidence_note(comparison).split(". ", 1)[-1] if comparison else "",
        "station": (weather.get("station") or {}).get("name"),
        "sources": {name: round(100 * share / total) for name, share in weights.items()},
        "weights_kind": weather.get("weights_kind"),
        "accuracy": accuracy_summary() if weather.get("near_home") else "",
        "hours": hours,
        "days": days,
    }


# =====================================
# FREE GAMES AND APPS
# =====================================

def safe_info(info):
    """A game's details for the app, with only https links and pictures."""
    if not info:
        return None
    https = lambda url: url if isinstance(url, str) and url.startswith("https://") else None
    return {
        **{key: info.get(key) for key in ("summary", "kind", "modes", "developer", "released", "reviews_label")},
        "rating": info.get("rating"),
        "pictures": [url for url in info.get("pictures") or [] if https(url)],
        "cover": https(info.get("cover")),
        "reviews_url": https(info.get("reviews_url")),
        "video_url": https(info.get("video_url")),
    }


async def freebies_with_details():
    return await freebies.add_details(await freebies.get_freebies())


async def free_json():
    found = await cached("freebies", FREEBIES_MINUTES, freebies_with_details)
    if not found:
        return {"items": []}
    now = datetime.now()
    try:
        seen = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("freebies_seen", {})
    except (FileNotFoundError, ValueError):
        seen = {}
    items = []
    for item in found["games"] + found["apps"]:
        items.append({
            "title": item["title"],
            "where": item["where"],
            "worth": item["worth"],
            "ends": freebies.ends_text(item["ends"], now),
            "last_day": bool(item["ends"] and item["ends"].date() == now.date()),
            "url": item["url"] if item["url"].startswith("https://") else "",
            "kind": "app" if item["key"].startswith("play:") else "game",
            "new": item["key"] not in seen,
            "info": safe_info(item.get("info")),
        })
    return {"items": items}


# =====================================
# INBOX + NOTIFICATIONS (called by bot.py)
# =====================================

def load_inbox():
    try:
        inbox = json.loads(INBOX_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return inbox if isinstance(inbox, list) else []


def has_listeners():
    """True if any device gets the app's notifications."""
    return webpush.device_count() > 0


async def notify(kind, title, body, html_text="", stories=(), urgent=False, open_story=None):
    """Save a message in the app's inbox and pop it up on every device with
    notifications on. kind: alert, morning, stocks, night, weather, display, test.
    stories: the articles it lists (shown with 👍/👎 in the app)."""
    entry = {
        "id": f"{int(time.time() * 1000):x}",
        "at": datetime.now().isoformat(timespec="minutes"),
        "kind": kind,
        "title": title,
        "body": body,
        "html": html_text,
        "stories": [curation.article_id(a["url"]) for a in stories],
    }
    try:
        inbox = ([entry] + load_inbox())[:INBOX_KEEP]
        INBOX_FILE.write_text(json.dumps(inbox, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as error:
        print(f"Dashboard inbox not saved: {type(error).__name__}")
    url = f"/#story/{curation.article_id(open_story['url'])}" if open_story else f"/#inbox/{entry['id']}"
    try:
        sent = await webpush.send({"title": title, "body": body, "url": url, "tag": kind}, urgent=urgent)
        if sent:
            print(f"Dashboard: notified {sent} device(s): {title}")
    except Exception as error:
        print(f"Dashboard notification failed: {type(error).__name__}")
    return entry


# =====================================
# THE WEB SERVER
# =====================================

def host_allowed(request):
    host = (request.host or "").lower()
    name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    return name in ALLOWED_HOSTS or name.endswith(ALLOWED_HOST_ENDINGS)


@web.middleware
async def guard(request, handler):
    """Only this app's own pages may use it: no other website (or a renamed address)
    can read your data or change your ratings."""
    if not host_allowed(request):
        return web.Response(status=403, text="Not allowed from this address.")
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("Origin")
        # Tailscale passes requests on with the address you typed in X-Forwarded-Host
        own = {(request.host or "").lower(), request.headers.get("X-Forwarded-Host", "").lower()}
        if origin and urlsplit(origin).netloc.lower() not in own:
            return web.Response(status=403, text="Not allowed from another site.")
        if request.content_type != "application/json":
            return web.Response(status=415, text="JSON only.")
    response = await handler(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: https:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    )
    return response


def app_file(name, content_type):
    async def handler(request):
        response = web.FileResponse(APP_FOLDER / name, headers={"Cache-Control": "no-cache"})
        response.content_type = content_type
        return response
    return handler


async def body_of(request):
    try:
        data = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="Bad JSON.")
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Bad JSON.")
    return data


async def api_news(request):
    return web.json_response(news_json())


async def api_stocks(request):
    return web.json_response(await stocks_json())


async def api_chart(request):
    ticker = request.query.get("ticker", "")
    span = request.query.get("range", "1d")
    if ticker not in {t for t, _ in WATCHLIST}:
        raise web.HTTPNotFound(text="Not on the watchlist.")
    chart = await cached(f"chart {ticker} {span}", CHART_MINUTES, lambda: get_chart(ticker, span))
    if chart is None:
        raise web.HTTPServiceUnavailable(text="Yahoo didn't answer.")
    return web.json_response(chart)


async def api_weather(request):
    weather = await cached("weather", WEATHER_MINUTES, get_weather)
    if weather is None:
        raise web.HTTPServiceUnavailable(text="The weather services didn't answer.")
    return web.json_response(weather_json(weather))


async def api_free(request):
    return web.json_response(await free_json())


async def api_inbox(request):
    return web.json_response({"items": [
        {k: entry.get(k) for k in ("id", "at", "kind", "title", "body")}
        for entry in load_inbox()
    ]})


async def api_inbox_item(request):
    entry = next((e for e in load_inbox() if e.get("id") == request.match_info["id"]), None)
    if entry is None:
        raise web.HTTPNotFound(text="Not in the inbox any more.")
    return web.json_response({**entry, "stories": stories_by_id(entry.get("stories") or [])})


async def api_story(request):
    found = stories_by_id([request.match_info["id"]])
    if not found:
        raise web.HTTPNotFound(text="That story is too old.")
    return web.json_response(found[0])


async def api_rate(request):
    data = await body_of(request)
    rating = data.get("rating")
    if rating not in (1, -1, 0):
        raise web.HTTPBadRequest(text="rating must be 1, -1 or 0")
    title = curation.record_rating(str(data.get("id", "")), rating)
    if title is None:
        raise web.HTTPNotFound(text="That story is too old to rate now.")
    print(f"News rated {['back', 'up', 'down'][rating]} in the app: {title}")
    return web.json_response({"ok": True, "rating": rating})


async def api_seen(request):
    """You opened a story in the app: it won't come again in a digest or alert."""
    article = find_story(str((await body_of(request)).get("id", "")))
    if article and article.get("url") and curation.is_new(curation.load(), article):
        curation.mark_sent([article])
    return web.json_response({"ok": True})


def on_this_computer(request):
    """The app is open on the computer the bot runs on (not through Tailscale)."""
    forwarded = any(name in request.headers for name in
                    ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Tailscale-User-Login"))
    host = (request.host or "").rsplit(":", 1)[0].lower()
    return request.remote in ("127.0.0.1", "::1") and host in ("localhost", "127.0.0.1") and not forwarded


async def api_open(request):
    """Open a link in this computer's default browser (Opera, say). The app installed
    from Edge would otherwise open its links in Edge."""
    if not on_this_computer(request):
        raise web.HTTPForbidden(text="Only on the computer the bot runs on.")
    url = str((await body_of(request)).get("url", ""))
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or len(url) > 2000 \
            or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise web.HTTPBadRequest(text="Only web links.")
    await asyncio.to_thread(webbrowser.open, url)
    return web.json_response({"ok": True})


async def link_helper(request):
    """The link helper for your other computers (scripts/briefing-links.ps1), to download."""
    return web.FileResponse(FOLDER / "scripts" / "briefing-links.ps1", headers={
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": 'attachment; filename="briefing-links.ps1"',
    })


async def api_info(request):
    return web.json_response({
        "opens_here": on_this_computer(request),
        "push_key": webpush.public_key(),
        "devices": webpush.device_count(),
        "preview": _preview,
        "watchlist": [t for t, _ in WATCHLIST],
    })


async def api_subscribe(request):
    data = await body_of(request)
    if not webpush.add_subscription(data.get("subscription"), data.get("device", "")):
        raise web.HTTPBadRequest(text="Not a push subscription.")
    print(f"Dashboard: notifications turned on for {data.get('device') or 'a device'}")
    return web.json_response({"ok": True, "devices": webpush.device_count()})


async def api_unsubscribe(request):
    webpush.remove_subscription(str((await body_of(request)).get("endpoint", "")))
    return web.json_response({"ok": True, "devices": webpush.device_count()})


async def api_test(request):
    """A test notification to the device that asked."""
    endpoint = str((await body_of(request)).get("endpoint", ""))
    sent = await webpush.send({"title": "Notifications work 🎉", "body": "This is how news from the bot will look.",
                               "url": "/", "tag": "test"}, endpoint=endpoint)
    return web.json_response({"ok": sent > 0})


def make_app():
    app = web.Application(middlewares=[guard], client_max_size=64 * 1024)
    app.router.add_get("/", app_file("index.html", "text/html"))
    app.router.add_get("/app.js", app_file("app.js", "text/javascript"))
    app.router.add_get("/app.css", app_file("app.css", "text/css"))
    app.router.add_get("/sw.js", app_file("sw.js", "text/javascript"))
    app.router.add_get("/manifest.webmanifest", app_file("manifest.webmanifest", "application/manifest+json"))
    app.router.add_static("/icons/", APP_FOLDER / "icons")
    app.router.add_get("/api/news", api_news)
    app.router.add_get("/api/story/{id}", api_story)
    app.router.add_get("/api/stocks", api_stocks)
    app.router.add_get("/api/chart", api_chart)
    app.router.add_get("/api/weather", api_weather)
    app.router.add_get("/api/free", api_free)
    app.router.add_get("/api/inbox", api_inbox)
    app.router.add_get("/api/inbox/{id}", api_inbox_item)
    app.router.add_get("/api/info", api_info)
    app.router.add_post("/api/rate", api_rate)
    app.router.add_post("/api/seen", api_seen)
    app.router.add_post("/api/push/subscribe", api_subscribe)
    app.router.add_post("/api/push/unsubscribe", api_unsubscribe)
    app.router.add_post("/api/push/test", api_test)
    app.router.add_post("/api/open", api_open)
    app.router.add_get("/setup/briefing-links.ps1", link_helper)
    return app


async def start():
    """Start the dashboard (bot.py calls this once). A problem never stops the bot."""
    global _runner
    try:
        webpush.signing_key()  # made now, so the first notification isn't slowed down
        _runner = web.AppRunner(make_app(), access_log=None)
        await _runner.setup()
        await web.TCPSite(_runner, HOST, PORT).start()
        print(f"Dashboard app: http://localhost:{PORT}")
    except OSError as error:
        print(f"Dashboard could not start on port {PORT} ({error.strerror or error}). "
              "Is another copy of the bot running?")
        _runner = None


async def stop():
    if _runner is not None:
        await _runner.cleanup()


# =====================================
# TRY IT WITHOUT THE BOT
# =====================================

async def preview():
    """The app with real data but no Telegram. Your news ratings and inbox are
    copied first, so nothing here changes what the bot knows."""
    global INBOX_FILE, _preview
    _preview = True
    scratch = Path(tempfile.mkdtemp(prefix="dashboard-preview-"))
    for source, use in ((curation.MEMORY_FILE, "memory"), (INBOX_FILE, "inbox")):
        copy = scratch / source.name
        if source.exists():
            shutil.copy(source, copy)
        if use == "memory":
            curation.MEMORY_FILE = copy
        else:
            INBOX_FILE = copy
    await start()
    if _runner is None:
        return
    print("Preview: downloading the headlines (about a minute)...")
    news = await get_news(0)
    if not load_inbox() and news["ranked"]:  # an example message, so the inbox isn't empty
        stories = curation.ranked(curation.load(), news["ranked"])[:5]
        lines = "\n\n".join(f'{n}. <a href="{html.escape(a["url"], quote=True)}">{html.escape(a["title"])}</a>'
                            for n, a in enumerate(stories, 1))
        INBOX_FILE.write_text(json.dumps([{
            "id": "example", "at": datetime.now().isoformat(timespec="minutes"), "kind": "morning",
            "title": "🌅 Your morning news (example)", "body": "\n".join(a["title"] for a in stories[:3]),
            "html": "📰 <b>Your morning news</b>\n\n" + lines,
            "stories": [curation.article_id(a["url"]) for a in stories],
        }]), encoding="utf-8")
    print("Preview ready. Ctrl+C to stop.")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--port" in sys.argv:  # e.g. py dashboard.py --port 8766, while the bot uses 8765
        PORT = int(sys.argv[sys.argv.index("--port") + 1])
    try:
        asyncio.run(preview())
    except KeyboardInterrupt:
        pass
