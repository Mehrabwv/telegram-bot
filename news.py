"""Fetch New York Times headlines and rank them.

Ported from the ESP32-S3 sketch: collect up to 60 articles from several NYT
feeds, score them, and return the best ones.

The bot prepares (preprocesses) the headlines every hour, a few minutes before
they are posted, so the hourly post and /news never have to wait for NYT.
"""

import asyncio
import html
from datetime import datetime

import httpx

from config import NYT_API_KEY

NYT_BASE_URL = "https://api.nytimes.com"

# (endpoint, label, quota = max new articles taken from this feed, base score)
FEEDS = [
    ("/svc/mostpopular/v2/viewed/1.json", "Viewed 1d", 16, 130),
    ("/svc/topstories/v2/home.json", "Homepage", 14, 120),
    ("/svc/topstories/v2/technology.json", "Technology", 8, 100),
    ("/svc/topstories/v2/science.json", "Science", 6, 100),
    ("/svc/topstories/v2/world.json", "World", 4, 95),
    # Business is read mainly for the stock section (see stocks.py)
    ("/svc/topstories/v2/business.json", "Business", 4, 95),
]

MAX_CANDIDATES = 60
SECONDS_BETWEEN_REQUESTS = 13  # NYT allows about 5 requests per minute

# The most recently prepared headlines
_latest = {"time": None, "ranked": [], "all": []}
_fetch_lock = asyncio.Lock()


def source_points(base, position):
    return max(20, base - position * 3)


def freshness_bonus(published):
    try:
        day = datetime.strptime(published[:10], "%Y-%m-%d")
    except ValueError:
        return 0
    age_days = (datetime.now() - day.replace(hour=12)).total_seconds() / 86400
    if -1 <= age_days <= 1.5:
        return 16
    if -1 <= age_days <= 3.5:
        return 8
    if age_days > 7:
        return -15
    return 0


def final_score(article):
    score = article["score"] + freshness_bonus(article["published"])
    section = article["section"].lower()
    if "technolog" in section or "science" in section:
        score += 12
    return score


def parse_item(item):
    """Turn one NYT result into an article, or None if it can't be used."""
    url = item.get("url") or ""
    title = (item.get("title") or "").strip()
    if not url.startswith("https://www.nytimes.com/") or not title:
        return None

    orgs = item.get("org_facet")
    if not isinstance(orgs, list):
        orgs = []  # Most Popular sends "" when a story has no tags

    return {
        "title": title,
        "summary": (item.get("abstract") or "").strip(),
        "url": url,
        "section": (item.get("section") or "News").strip(),
        "published": item.get("published_date") or "",
        "orgs": [org for org in orgs if isinstance(org, str)],  # companies NYT tagged
        "score": 0,
    }


async def collect_candidates():
    """Download every feed. Returns (candidates, everything, feeds_ok).

    candidates: limited per feed (quota), so the top list stays varied.
    everything: every story seen in any feed, used for the stock section.
    """
    candidates = []
    by_url = {}  # candidates, by URL
    everything = {}  # every story, by URL
    feeds_ok = 0

    async with httpx.AsyncClient(timeout=30) as client:
        for number, (endpoint, label, quota, base) in enumerate(FEEDS):
            if number > 0:
                await asyncio.sleep(SECONDS_BETWEEN_REQUESTS)

            print(f"NYT feed {number + 1}/{len(FEEDS)}: {label}")
            try:
                response = await client.get(
                    NYT_BASE_URL + endpoint,
                    params={"api-key": NYT_API_KEY},
                )
                response.raise_for_status()
                results = response.json().get("results", [])
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                print(f"NYT feed '{label}' failed: HTTP {code}")
                if code in (401, 403):
                    print(
                        "Check the NYT key, and that the Top Stories and "
                        "Most Popular APIs are enabled for it."
                    )
                continue
            except httpx.RequestError as error:
                print(f"NYT feed '{label}' failed: {type(error).__name__}")
                continue
            except ValueError:
                print(f"NYT feed '{label}' returned invalid data")
                continue

            feeds_ok += 1
            added = 0
            for position, item in enumerate(results):
                article = parse_item(item)
                if article is None:
                    continue
                url = article["url"]
                article = everything.setdefault(url, article)

                points = source_points(base, position)
                if url in by_url:
                    # The same story in several lists is a strong signal
                    article["score"] = max(article["score"], points) + 22
                    continue

                if len(candidates) >= MAX_CANDIDATES or added >= quota:
                    continue

                article["score"] = points
                candidates.append(article)
                by_url[url] = article
                added += 1

    return candidates, list(everything.values()), feeds_ok


def needs_download(max_age_seconds):
    """True if there are no prepared headlines, or they are older than max_age_seconds."""
    last = _latest["time"]
    return last is None or (datetime.now() - last).total_seconds() > max_age_seconds


async def get_news(max_age_seconds):
    """Return the prepared headlines. Downloads them again (about a minute)
    only if they are older than max_age_seconds; 0 forces a new download.

    Returns {"time", "ranked": best first, "all": every story seen}.
    """
    if not needs_download(max_age_seconds):
        return dict(_latest)  # new enough: answer right away

    async with _fetch_lock:
        if not needs_download(max_age_seconds):
            return dict(_latest)  # prepared while we were waiting

        print("NYT: preparing headlines (takes about a minute)...")
        candidates, everything, feeds_ok = await collect_candidates()
        if not candidates:
            print("NYT: no articles downloaded; keeping the previous list.")
            return dict(_latest)

        _latest["ranked"] = sorted(candidates, key=final_score, reverse=True)
        _latest["all"] = everything
        _latest["time"] = datetime.now()
        print(f"NYT: ranked {len(candidates)} articles from {feeds_ok}/{len(FEEDS)} feeds.")
        return dict(_latest)


def latest_news():
    """The prepared headlines as they are now, without downloading (may be empty)."""
    return dict(_latest)


def pick_articles(ranked, sent_urls, count, skip_urls=()):
    """Return (articles, start_over).

    Picks the best articles that were not sent yet, never using skip_urls
    (stories that belong in the stock section). start_over is True when too
    few unsent articles are left, so the 'already sent' list should reset.
    """
    pool = [a for a in ranked if a["url"] not in skip_urls]
    unseen = [a for a in pool if a["url"] not in sent_urls]
    if len(unseen) < count:
        return pool[:count], True
    return unseen[:count], False


# NYT section ids that don't read well when just capitalized
SECTION_NAMES = {"us": "U.S.", "nyregion": "New York", "realestate": "Real Estate"}


def section_label(section):
    """'technology' -> 'Technology', 'us' -> 'U.S.'"""
    return SECTION_NAMES.get(section.lower()) or section[:1].upper() + section[1:]


def format_news_message(articles):
    """Build a Telegram message (HTML) with clickable titles."""
    now = datetime.now().strftime("%I:%M %p").lstrip("0")
    lines = [f"<b>NYT top {len(articles)} headlines</b> ({now})", ""]
    for number, article in enumerate(articles, 1):
        title = html.escape(article["title"])
        url = html.escape(article["url"], quote=True)
        lines.append(f'{number}. <a href="{url}">{title}</a>')
        lines.append(f"<b>{html.escape(section_label(article['section']))}</b>")
        if article["summary"]:
            lines.append(html.escape(article["summary"]))
        lines.append("")
    return "\n".join(lines).strip()
