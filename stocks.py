"""Stock section: prices and NYT stories for a small watchlist.

Prices come from Yahoo Finance's free chart data (no API key needed).
Stories come from the NYT feeds that news.py already downloads. A story
belongs to a stock when NYT tagged the company, or when a business or
technology story names it.
"""

import asyncio
import html
import re
from datetime import date, datetime

import httpx

# Ticker, then the names that mark an NYT story as being about that company.
# The first name is the company's name. Names match as whole words, with the
# capital letters shown. An empty list means no NYT stories for that ticker.
WATCHLIST = [
    ("AAPL", ["Apple", "iPhone", "Tim Cook"]),
    ("NVDA", ["Nvidia", "Jensen Huang"]),
    ("GOOGL", ["Google", "Alphabet", "Waymo", "Sundar Pichai"]),
    ("SPCX", ["SpaceX", "Starlink", "Starship", "Space Exploration Technologies"]),
    # 2X short SpaceX ETF: SpaceX stories already show up under SPCX
    ("SSPC", []),
]

STORY_MAX_AGE_DAYS = 3  # older NYT stories are not shown as stock news

YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
# Yahoo answers "429 Too Many Requests" when there is no browser name
YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    )
}


def names_pattern(names, flags=0):
    return re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b", flags)


TEXT_PATTERNS = {ticker: names_pattern(names) for ticker, names in WATCHLIST if names}
TAG_PATTERNS = {
    ticker: names_pattern(names, re.IGNORECASE) for ticker, names in WATCHLIST if names
}


# =====================================
# NYT STORIES ABOUT THE WATCHLIST
# =====================================

def tickers_for(article):
    """The watchlist tickers an NYT story is about (empty list if none)."""
    tags = " | ".join(article.get("orgs", []))
    text = f"{article['title']} {article['summary']}"
    section = article["section"].lower()
    business_story = "business" in section or "technolog" in section

    return [
        ticker
        for ticker in TEXT_PATTERNS
        if TAG_PATTERNS[ticker].search(tags)
        or (business_story and TEXT_PATTERNS[ticker].search(text))
    ]


def stock_stories(articles):
    """Every story about the watchlist, newest first, as [(tickers, article)]."""
    found = []
    for article in articles:
        tickers = tickers_for(article)
        if tickers:
            found.append((tickers, article))
    found.sort(key=lambda pair: pair[1]["published"], reverse=True)
    return found


def is_recent(article):
    try:
        day = datetime.strptime(article["published"][:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return (date.today() - day).days <= STORY_MAX_AGE_DAYS


# =====================================
# PRICES
# =====================================

async def fetch_quote(client, ticker):
    """Latest price for one ticker, or None if Yahoo didn't answer."""
    for host in YAHOO_HOSTS:
        try:
            response = await client.get(
                f"https://{host}/v8/finance/chart/{ticker}",
                params={"range": "1d", "interval": "1d"},
            )
            response.raise_for_status()
            meta = response.json()["chart"]["result"][0]["meta"]
            price = float(meta["regularMarketPrice"])
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            print(f"Stock price for {ticker} failed on {host}: {type(error).__name__}")
            continue

        change = meta.get("regularMarketChangePercent")
        previous = meta.get("chartPreviousClose")
        if not isinstance(change, (int, float)) and isinstance(previous, (int, float)) and previous:
            change = (price - previous) / previous * 100
        if not isinstance(change, (int, float)):
            change = None

        stamp = meta.get("regularMarketTime")
        regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
        now = datetime.now().timestamp()

        def number(key):
            value = meta.get(key)
            return float(value) if isinstance(value, (int, float)) else None

        return {
            "price": price,
            "change": change,  # percent since the previous close
            "time": datetime.fromtimestamp(stamp) if isinstance(stamp, (int, float)) else None,
            "open": regular.get("start", 0) <= now < regular.get("end", 0),
            "previous": number("chartPreviousClose") or number("previousClose"),
            "high": number("regularMarketDayHigh"),  # today's range
            "low": number("regularMarketDayLow"),
            "name": meta.get("shortName") or meta.get("longName") or "",
        }
    return None


async def get_quotes():
    """Prices for the whole watchlist as {ticker: quote or None}. Takes a second."""
    async with httpx.AsyncClient(timeout=15, headers=YAHOO_HEADERS) as client:
        quotes = await asyncio.gather(*(fetch_quote(client, ticker) for ticker, _ in WATCHLIST))
    return {ticker: quote for (ticker, _), quote in zip(WATCHLIST, quotes)}


# =====================================
# MESSAGE
# =====================================

def clock(moment):
    """'2:00 PM' for today, 'Wed 2:00 PM' for another day."""
    text = moment.strftime("%I:%M %p").lstrip("0")
    if moment.date() != date.today():
        text = moment.strftime("%a ") + text
    return text


def price_text(quote):
    """'$344.17 ▼ 1.53%' (change since the previous close), or 'n/a'."""
    if quote is None:
        return "n/a"
    text = f"${quote['price']:,.2f}"
    if quote["change"] is not None:
        change = round(quote["change"], 2)
        arrow = "▲" if change > 0 else "▼" if change < 0 else "•"
        text += f" {arrow} {abs(change):.2f}%"
    return text


def format_stock_section(quotes, stories):
    """Telegram HTML: one line per stock, then NYT stories about them."""
    found = [quote for quote in quotes.values() if quote]

    if found:
        market = "market open" if any(q["open"] for q in found) else "market closed"
        times = [q["time"] for q in found if q["time"]]
        if times:
            market += f", as of {clock(max(times))}"
        lines = [f"<b>Stocks</b> ({market})"]
        for ticker, _ in WATCHLIST:
            lines.append(f"<b>{ticker}</b> {price_text(quotes.get(ticker))}")
    else:
        lines = ["<b>Stocks</b>", "Prices are unavailable right now."]

    if stories:
        lines += ["", "<b>Stock news</b>"]
        for tickers, article in stories:
            title = html.escape(article["title"])
            url = html.escape(article["url"], quote=True)
            lines.append(f'<b>{", ".join(tickers)}</b>: <a href="{url}">{title}</a>')
    return "\n".join(lines)
