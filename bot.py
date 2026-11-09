import asyncio
import html
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import curation
import dashboard
import display
import freebies
from config import GROQ_API_KEY, GROQ_MODEL, MY_CHAT_ID, NYT_API_KEY, TELEGRAM_TOKEN
from memory import (
    add_fact,
    append_message,
    archive_history,
    count_messages,
    load_facts,
    load_history,
    remove_fact,
)
from news import get_news, latest_news, needs_download, section_label
from stocks import WATCHLIST, format_stock_section, get_quotes, is_recent, price_text, stock_stories
from weather import (
    HOME,
    advice,
    advice_lines,
    find_place,
    format_weather_brief,
    format_weather_report,
    format_weather_update,
    get_weather,
    place_in_message,
    update_accuracy,
    weather_facts,
)

# =====================================
# SETTINGS
# =====================================

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Groq's built-in browser search only works on these models
SEARCH_MODELS = {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}
SEARCH_AVAILABLE = GROQ_MODEL in SEARCH_MODELS

MAX_HISTORY = 20  # how many recent messages are sent to the AI

# News: there is no hourly post. The NYT headlines are collected every 30 minutes
# and the AI rates each new story (see curation.py). Only major breaking news is
# sent right away; everything else waits for the next digest (below).
COLLECT_EVERY_MINUTES = 30  # NYT headlines are collected (preprocessed) this often,
COLLECT_MINUTES_EARLY = 5  # ...this long before each half hour: at :25 and :55
NEWS_MAX_AGE_MINUTES = COLLECT_EVERY_MINUTES + 10  # /news downloads only if older
STOCK_NEWS_COUNT = 3  # NYT stories in the stock section
SUMMARY_MAX_LENGTH = 220  # story summaries in messages are cut to this

# Scheduled messages, besides the weather updates:
# - the morning digest, right after the morning weather: new stories + stocks
# - the stock check on weekdays, an hour before the New York market closes
#   (1 PM in Arizona in winter, 12 PM in summer)
# - the night summary: tomorrow's weather, the market close and the day's news.
#   From then until you wake up, breaking news waits for the morning digest.
STOCK_CHECK_MINUTES_BEFORE_CLOSE = 60
NIGHT_SUMMARY_TIME = "22:00"
DIGEST_LATE_MINUTES = 60  # a missed one (computer asleep) can still go out this late
BIG_MOVE_PERCENT = 3  # the stock check points out moves at least this big
DISPLAY_POLL_SECONDS = 10  # how often the bot asks the display for "send to my phone"

# Weather updates: the morning one comes at your wake-up time (change it with /wake)
MORNING_WEATHER_TIME = "08:00"  # used until you set a wake-up time with /wake
AFTERNOON_WEATHER_TIME = "13:00"
WEATHER_LATE_MINUTES = 60  # a missed update (computer asleep) can still go out this late
# Every hour at :05, earlier forecasts are checked against the weather station,
# so each weather source's weight follows its real accuracy
ACCURACY_CHECK_MINUTE = 5

WATCHLIST_TEXT = ", ".join(ticker for ticker, _ in WATCHLIST)

STATE_FILE = Path(__file__).resolve().parent / "state.json"

# Words used to recognize a plain "show me the news" request
NEWS_WORDS = {"news", "headlines", "headline", "headliners", "nyt"}
NEWS_INTENT_WORDS = {
    "check", "show", "give", "get", "read", "tell", "latest", "today",
    "todays", "today's", "top", "what", "whats", "what's", "any", "current",
}
NEWS_TOPIC_WORDS = {"about", "on", "for", "regarding", "from", "in", "of", "at"}

# Words used to recognize a plain "check my stocks" request
STOCK_WORDS = {"stock", "stocks", "watchlist", "portfolio", "market", "markets"}
STOCK_OTHER_WORDS = {
    "check", "show", "give", "get", "me", "my", "the", "how", "how's", "hows",
    "are", "is", "doing", "today", "now", "latest", "current", "update",
    "price", "prices", "quote", "quotes", "news", "what", "what's", "whats",
    "s", "a", "please",
}
# ...plus the tickers and company names, e.g. "nvidia stock"
STOCK_OTHER_WORDS |= {ticker.lower() for ticker, _ in WATCHLIST}
STOCK_OTHER_WORDS |= {names[0].lower() for _, names in WATCHLIST if names}

# Messages with these words are answered from the weather API, not web search
WEATHER_WORDS = {
    "weather", "forecast", "temperature", "temp", "rain", "raining", "rainy",
    "snow", "snowing", "humid", "humidity", "wind", "windy", "storm", "storms",
    "thunderstorm", "sunny", "cloudy", "umbrella", "sunrise", "sunset", "uv",
    "precipitation", "monsoon", "haboob", "fahrenheit", "celsius",
}
# "hot"/"cold" only count together with a time, e.g. "is it cold tonight"
FEEL_WORDS = {"hot", "cold", "warm", "chilly", "cool", "freezing"}
WHEN_WORDS = {
    "today", "tonight", "tomorrow", "outside", "weekend", "now", "morning",
    "afternoon", "evening", "week",
}

WEATHER_PROMPT = (
    "The user asked about the weather. Below is live weather data: a blend of "
    "several forecasts (the National Weather Service and four weather models) and, "
    "when available, a real measurement from a nearby weather station. Answer only "
    "from this data: use its numbers, mention the place, and do not search the web. "
    "For a jacket, cap or umbrella question, give the bot's advice listed in the data, "
    "with its numbers. If asked how sure or accurate the forecast is, use the "
    "confidence line and how the sources compare. If the question is about a place "
    "or time the data does not cover, say so and suggest /weather followed by a "
    "city name.\n\n"
)


def build_system_prompt(chat_id):
    today = datetime.now().strftime("%A, %B %d, %Y")
    prompt = (
        "You are a friendly and helpful personal AI assistant inside a Telegram bot. "
        "Answer questions clearly and naturally. "
        "Keep answers short unless more detail is requested. "
        "Use plain text only: no markdown, no asterisks, no headings, "
        "no footnote or citation markers, and no web links. "
        "When listing items, put each item on its own line, numbered, "
        "with a blank line between items, and keep each item to one or two "
        "short sentences. "
        "You can search the web. Search when the user asks about news, "
        "sports, prices, recent events, or anything that may have changed recently. "
        "Otherwise answer from your own knowledge. "
        "When you used web search, briefly name the websites your answer is based on. "
        "Never invent facts, sources or links. If the search does not give a clear "
        "answer, say so instead of guessing. "
        f"Today's date is {today}."
    )

    facts = load_facts(chat_id)
    if facts:
        prompt += (
            "\n\nThese are things you know about the user from long-term memory. "
            "Use them naturally when relevant, and do not list them unless asked:\n"
            + "\n".join(f"- {fact}" for fact in facts)
        )
    return prompt


# =====================================
# TEXT HELPERS
# =====================================

def clean_reply(text):
    """Remove markdown and citation markers that Telegram would show as raw text."""
    text = re.sub(r"【[^】]*】", "", text)  # markers like 【1†L17-L20】
    text = re.sub(r"(?m)^\s*\*\s+", "- ", text)  # "* item" -> "- item"
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # **bold**
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)  # headings
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_news_request(text):
    """True for short messages like 'check the news' (not 'news about Intel')."""
    words = re.findall(r"[a-z']+", text.lower())
    if not words or len(words) > 6:
        return False
    if not NEWS_WORDS.intersection(words):
        return False
    if NEWS_TOPIC_WORDS.intersection(words):
        return False  # a topic request goes to web search instead
    return len(words) == 1 or bool(NEWS_INTENT_WORDS.intersection(words))


def is_stock_request(text):
    """True for short messages like 'check my stocks' (not 'what stock should I buy')."""
    words = re.findall(r"[a-z']+", text.lower())
    if not words or len(words) > 6 or not STOCK_WORDS.intersection(words):
        return False
    return all(word in STOCK_WORDS or word in STOCK_OTHER_WORDS for word in words)


def is_weather_message(text):
    """True for weather questions like 'will it rain tomorrow?' or 'is it hot outside'."""
    words = set(re.findall(r"[a-z']+", text.lower()))
    if WEATHER_WORDS.intersection(words):
        return True
    return bool(FEEL_WORDS.intersection(words) and WHEN_WORDS.intersection(words))


def parse_clock_time(text):
    """'7:30', '7', '6:45am', '7 pm', '19:30' -> 'HH:MM' (24-hour), or None."""
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?(?:m\.?)?", text.strip().lower())
    if not match:
        return None
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "p" else 0)
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def nice_time(hhmm):
    """'07:30' -> '7:30 AM'"""
    return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p").lstrip("0")


# =====================================
# SAVED STATE (subscribers, sent articles, wake-up time)
# =====================================

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, ValueError):
        state = {}
    # Until someone subscribes or unsubscribes, the scheduled messages go to your own chat
    state.setdefault("subscribers", [MY_CHAT_ID] if MY_CHAT_ID else [])
    state.setdefault("sent_urls", [])
    state.setdefault("sent_stock_urls", [])
    state.setdefault("weather_sent", {})  # e.g. {"morning": "2026-11-06"}
    state.setdefault("digest_sent", {})  # e.g. {"night": "2026-11-06"}
    return state


def morning_time(state):
    """Your wake-up time as 'HH:MM': the one set with /wake, else the default."""
    return state.get("morning_time") or MORNING_WEATHER_TIME


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# =====================================
# ASK GROQ AI
# =====================================

async def ask_groq(chat_id, history, mode="off", extra_prompt=None):
    """mode: 'off' = no search, 'auto' = model decides, 'force' = always search.
    extra_prompt: more instructions or data for this one answer (e.g. weather)."""
    system_prompt = build_system_prompt(chat_id)
    if extra_prompt:
        system_prompt += "\n\n" + extra_prompt
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)

    body = {"model": GROQ_MODEL, "messages": messages}

    if mode != "off" and SEARCH_AVAILABLE:
        body["tools"] = [{"type": "browser_search"}]
        body["tool_choice"] = "required" if mode == "force" else "auto"
        body["reasoning_effort"] = "low"

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    response.raise_for_status()

    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        return "Sorry, I couldn't generate an answer."

    reply = choices[0].get("message", {}).get("content", "")
    if isinstance(reply, str):
        reply = clean_reply(reply)
        if reply:
            return reply
    return "Sorry, I couldn't generate an answer."


async def ask_groq_safe(chat_id, history, mode, extra_prompt=None):
    """Like ask_groq, but if automatic search is rejected, retry without it."""
    try:
        return await ask_groq(chat_id, history, mode, extra_prompt)
    except httpx.HTTPStatusError as error:
        if mode == "auto" and error.response.status_code == 400:
            print("Groq rejected the search request; retrying without search.")
            return await ask_groq(chat_id, history, "off", extra_prompt)
        raise


async def keep_typing(chat):
    """Keep the 'typing...' indicator on while a slow answer is being prepared."""
    try:
        while True:
            await chat.send_action(ChatAction.TYPING)
            await asyncio.sleep(4)
    except Exception:
        pass


async def reply_long(message, text):
    """Reply, splitting text that is longer than Telegram's message limit."""
    for i in range(0, len(text), 3900):
        await message.reply_text(text[i:i + 3900])


async def answer_with_groq(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text,
    mode,
    extra_prompt=None,
    fallback_html=None,
):
    """Answer with the AI. If the AI fails and fallback_html is given (for
    example a weather report), that is sent instead of an error message."""
    chat_id = update.effective_chat.id

    # Recent conversation comes from the saved history file
    history = load_history(chat_id, MAX_HISTORY)
    history.append({"role": "user", "content": text})

    typing_task = asyncio.create_task(keep_typing(update.effective_chat))
    failed = True

    try:
        reply = await ask_groq_safe(chat_id, history, mode, extra_prompt)
    except httpx.HTTPStatusError as error:
        code = error.response.status_code
        print(f"Groq HTTP error: {code} {error.response.text[:300]}")
        if code == 429:
            reply = "I've reached the AI request limit. Please try again later."
        elif code in (401, 403):
            reply = "AI authentication failed. Please check the Groq API key."
        elif code == 404:
            reply = "The selected AI model was not found. Please check the model name."
        else:
            reply = "Sorry, the AI service returned an error. Please try again."
    except (httpx.RequestError, ValueError, KeyError):
        print("AI request or response error.")
        reply = "I couldn't reach the AI service or read its response. Please try again."
    else:
        failed = False
        # Only successful exchanges are saved to memory
        append_message(chat_id, "user", text)
        append_message(chat_id, "assistant", reply)
    finally:
        typing_task.cancel()

    if failed and fallback_html:
        # The AI is unavailable, but the data itself can still be shown
        await send_news(context.bot, chat_id, fallback_html)
        print("AI failed; sent the data instead.")
        return

    await reply_long(update.message, reply)
    print(f"Bot replied: {reply}")


# =====================================
# NEWS + STOCKS
# =====================================

async def send_news(bot, chat_id, message, keyboard=None):
    await bot.send_message(
        chat_id=chat_id,
        text=message,
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        reply_markup=keyboard,
    )


def reader_notes():
    """What the bot knows about you (/remember), so the AI can judge what you'd like."""
    return load_facts(MY_CHAT_ID) if MY_CHAT_ID else []


def short(text, limit):
    """Cut text at a word, adding '…', if it is longer than limit."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def story_block(number, article, summary=True):
    """One story: linked title, section and how important it is, summary."""
    title = html.escape(article["title"])
    url = html.escape(article["url"], quote=True)
    lines = [f'{f"{number}. " if number else ""}<a href="{url}">{title}</a>',
             f"<b>{html.escape(section_label(article['section']))}</b> · {curation.label(article)}"]
    if summary and article.get("summary"):
        lines.append(html.escape(short(article["summary"], SUMMARY_MAX_LENGTH)))
    return "\n".join(lines)


def format_stories(heading, articles, summaries=True):
    return "\n\n".join([heading] + [story_block(n, a, summaries) for n, a in enumerate(articles, 1)])


def rating_keyboard(articles):
    """👍/👎 buttons for each story (two stories per row). They teach the bot your taste."""
    if len(articles) == 1:
        story_id = curation.article_id(articles[0]["url"])
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("👍 More like this", callback_data=f"r:{story_id}:1"),
            InlineKeyboardButton("👎 Less like this", callback_data=f"r:{story_id}:-1"),
        ]])
    buttons = []
    for number, article in enumerate(articles, 1):
        story_id = curation.article_id(article["url"])
        buttons += [
            InlineKeyboardButton(f"{number} 👍", callback_data=f"r:{story_id}:1"),
            InlineKeyboardButton(f"{number} 👎", callback_data=f"r:{story_id}:-1"),
        ]
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    return InlineKeyboardMarkup(rows) if rows else None


async def fresh_news():
    """The collected headlines, with every story rated by the AI."""
    news = await get_news(NEWS_MAX_AGE_MINUTES * 60)  # already collected, so instant
    if news["ranked"]:
        await curation.rate_stories(news["ranked"], reader_notes())
    return news


def stock_story_urls(news):
    """Stories about the watchlist go in the stock section, not with the other news."""
    return {article["url"] for _, article in stock_stories(news["all"])}


async def news_digest(heading):
    """(message, keyboard, stories) with 5-7 stories you haven't seen yet,
    or (None, None, []) if there is nothing new."""
    news = await fresh_news()
    stories = curation.pick_digest(news["ranked"], stock_story_urls(news))
    if not stories:
        return None, None, []
    return format_stories(heading, stories), rating_keyboard(stories), stories


async def stock_section(new_stories_only=True):
    """(Telegram HTML, quotes): prices plus NYT stories about the watchlist.
    new_stories_only: leave out stock stories that were sent before (and remember these)."""
    recent = [(t, a) for t, a in stock_stories(latest_news()["all"]) if is_recent(a)]
    if new_stories_only:
        state = load_state()
        sent = set(state["sent_stock_urls"])
        stories = [(t, a) for t, a in recent if a["url"] not in sent][:STOCK_NEWS_COUNT]
        if stories:
            state["sent_stock_urls"] = (state["sent_stock_urls"] + [a["url"] for _, a in stories])[-200:]
            save_state(state)
    else:
        stories = recent[:STOCK_NEWS_COUNT]
    quotes = await get_quotes()  # prices are always fetched fresh
    return format_stock_section(quotes, stories), quotes


async def freebies_section(only_new=True):
    """'🎁 Free to keep': games and apps that are free for a limited time (see freebies.py).
    only_new: leave out ones already reported (and remember these). None if nothing."""
    try:
        found = await freebies.add_details(await freebies.get_freebies())
    except Exception as error:
        print(f"Free games/apps failed: {type(error).__name__}")
        return None
    if not only_new:  # /free: all of them
        return freebies.section(found, {}, max_games=len(found["games"]))[0]
    text, keys = freebies.section(found, load_state().get("freebies_seen", {}))
    if keys:
        state = load_state()
        state["freebies_seen"] = freebies.remember(state.get("freebies_seen", {}), keys)
        save_state(state)
    return text


def big_moves(quotes):
    """'⚡ Big moves: NVDA ▲ 4.21%' for stocks that moved BIG_MOVE_PERCENT or more."""
    moved = [
        f"<b>{ticker}</b> {price_text(quote).split(' ', 1)[-1]}"
        for ticker, quote in quotes.items()
        if quote and quote["change"] is not None and abs(quote["change"]) >= BIG_MOVE_PERCENT
    ]
    return f"\n\n⚡ <b>Big moves:</b> {', '.join(moved)}" if moved else ""


async def build_stock_message():
    """Prices + recent NYT stories for the watchlist (never waits for NYT)."""
    stories = [
        (tickers, a) for tickers, a in stock_stories(latest_news()["all"]) if is_recent(a)
    ]
    quotes = await get_quotes()
    return format_stock_section(quotes, stories[:STOCK_NEWS_COUNT])


async def send_news_now(bot, chat_id):
    """/news: the best stories you haven't seen yet, plus stocks."""
    message, keyboard, stories = await news_digest("📰 <b>New for you</b>")
    if not latest_news()["ranked"]:
        await bot.send_message(
            chat_id=chat_id,
            text="Sorry, I couldn't get the news right now. Please try again later.",
        )
        return
    if message is None:
        message = "📰 Nothing new since your last update: you've seen all the top stories."
    stocks_text, _ = await stock_section(new_stories_only=False)
    await send_news(bot, chat_id, message + "\n\n" + stocks_text, keyboard)
    curation.mark_sent(stories)
    print(f"News sent ({len(stories)} new stories).")


async def send_stocks_now(bot, chat_id):
    """Send the stock section to one chat (used by /stocks)."""
    await send_news(bot, chat_id, await build_stock_message())
    print("Stocks sent.")


def plain(message):
    """Telegram HTML -> plain text (for Windows notifications)."""
    return html.unescape(re.sub(r"<[^>]+>", "", message)).strip()


def headline_list(stories, shown=3):
    """'1. First headline\\n2. Second…\\n+3 more' for a notification."""
    lines = [f"{n}. {story['title']}" for n, story in enumerate(stories[:shown], 1)]
    if len(stories) > shown:
        lines.append(f"+{len(stories) - shown} more")
    return "\n".join(lines)


def has_audience(state):
    """Someone gets the scheduled messages: a Telegram subscriber or the dashboard app."""
    return bool(state["subscribers"]) or dashboard.has_listeners()


async def send_to_subscribers(app: Application, subscribers, message, what, keyboard=None):
    sent = 0
    for chat_id in subscribers:
        try:
            await send_news(app.bot, chat_id, message, keyboard)
            sent += 1
        except TelegramError as error:
            print(f"Could not send {what} to a subscriber: {type(error).__name__}")
    print(f"Scheduled {what} sent to {sent} chat(s).")


async def collect_news(app=None, max_age_minutes=0):
    """Preprocess: download the NYT headlines (about a minute), have the AI rate
    the new stories, and send breaking news if there is any (app given).

    The download is skipped if the headlines were collected in the last max_age_minutes.
    """
    try:
        news = await get_news(max_age_minutes * 60)
        if news["ranked"]:
            await curation.rate_stories(news["ranked"], reader_notes())
            if app is not None:
                await send_alerts(app, news)
    except Exception as error:
        print(f"Collecting headlines failed: {type(error).__name__}")


def is_quiet(state, moment):
    """Between the night summary and your wake-up time, breaking news waits."""
    now, night, wake = moment.strftime("%H:%M"), NIGHT_SUMMARY_TIME, morning_time(state)
    if night > wake:
        return now >= night or now < wake
    return night <= now < wake


async def send_alerts(app: Application, news):
    """Breaking news: only major stories the AI rated very urgent (usually none)."""
    state = load_state()
    if not has_audience(state) or is_quiet(state, datetime.now()):
        return
    for article in curation.pick_alerts(news["ranked"], stock_story_urls(news)):
        message = "🚨 <b>Breaking news</b>\n\n" + story_block(None, article)
        curation.mark_sent([article], alert=True)  # first, so it can never go out twice
        await send_to_subscribers(app, state["subscribers"], message, "breaking news",
                                  rating_keyboard([article]))
        await dashboard.notify("alert", "🚨 Breaking news", article["title"], message, [article],
                               urgent=True, open_story=article)


# =====================================
# DIGESTS (morning, stock check, night)
# =====================================

def market_close(day):
    """When the New York market closes (4 PM there) on `day`, in this computer's time."""
    march = date(day.year, 3, 8)
    dst_start = march + timedelta(days=(6 - march.weekday()) % 7)  # 2nd Sunday of March
    november = date(day.year, 11, 1)
    dst_end = november + timedelta(days=(6 - november.weekday()) % 7)  # 1st Sunday of November
    new_york_utc = -4 if dst_start <= day < dst_end else -5
    local_utc = datetime.now().astimezone().utcoffset()
    return datetime.combine(day, datetime.min.time()) + timedelta(hours=16 - new_york_utc) + local_utc


def at_time(day, hhmm):
    return datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time())


def digests_due(state, moment):
    """Which digests should go out now: 'morning', 'stocks' and/or 'night'.
    Each goes out once a day, up to DIGEST_LATE_MINUTES late."""
    today = moment.date()
    times = {"night": at_time(today, NIGHT_SUMMARY_TIME)}
    if state["weather_sent"].get("morning") == today.isoformat():
        times["morning"] = at_time(today, morning_time(state))  # right after the morning weather
    if today.weekday() < 5:
        times["stocks"] = market_close(today) - timedelta(minutes=STOCK_CHECK_MINUTES_BEFORE_CLOSE)
    late = timedelta(minutes=DIGEST_LATE_MINUTES)
    return [
        kind for kind, at in times.items()
        if state["digest_sent"].get(kind) != today.isoformat() and at <= moment < at + late
    ]


def tomorrow_weather(weather):
    """Tomorrow's high, low and rain, plus the jacket/cap/umbrella advice for it."""
    lines = [format_weather_brief(weather)]  # in the evening it covers tomorrow
    items, day = advice(weather)
    if items and day == "tomorrow":
        lines += [""] + advice_lines(items)
    return "\n".join(lines)


async def post_digest(app: Application, kind):
    state = load_state()
    today = datetime.now().date().isoformat()
    if state["digest_sent"].get(kind) == today or not has_audience(state):
        return
    state["digest_sent"][kind] = today  # saved first, so it can never go out twice
    save_state(state)

    keyboard, stories = None, []
    if kind == "stocks":
        stocks_text, quotes = await stock_section()
        close = nice_time(market_close(date.today()).strftime("%H:%M"))
        message = f"📈 <b>Stock check:</b> the market closes at {close}\n\n{stocks_text}{big_moves(quotes)}"
    else:
        if kind == "morning":  # news first, then stocks
            heading, nothing_new = "📰 <b>Your morning news</b>", "📰 No new stories since last night."
            stocks_text, _ = await stock_section()
            free_text = await freebies_section()
            before, after = [], [stocks_text] + ([free_text] if free_text else [])
        else:  # night: tomorrow's weather, the market close, then the news
            heading = "📰 <b>Today's news you haven't seen</b>"
            nothing_new = "📰 You've seen all of today's top stories."
            before = ["🌙 <b>Good night!</b> Here's your summary."]
            weather = await get_weather()
            if weather:
                before.append(tomorrow_weather(weather))
            stocks_text, _ = await stock_section()
            before, after = before + [stocks_text], []
        news = await fresh_news()
        stories = curation.pick_digest(news["ranked"], stock_story_urls(news))
        keyboard = rating_keyboard(stories) if stories else None

        def compose(summaries):
            news_part = format_stories(heading, stories, summaries) if stories else nothing_new
            return "\n\n".join(before + [news_part] + after)

        message = compose(True)
        if len(message) > 4000:  # Telegram's limit is 4096 characters
            message = compose(False)
        if len(message) > 4000:  # still too long: the free games without their "what is it" lines
            after = [re.sub(r"\n    <i>.*?</i>", "", part) for part in after]
            message = compose(False)

    await send_to_subscribers(app, state["subscribers"], message, f"{kind} digest", keyboard)
    curation.mark_sent(stories)

    # The dashboard app: the whole message in its inbox, a short notification
    if kind == "stocks":
        moves = big_moves(quotes)
        body = plain(moves).replace("⚡ Big moves: ", "Big moves: ") if moves else ", ".join(
            f"{ticker} {price_text(quote)}" for ticker, quote in quotes.items() if quote)
        title = "📈 Stock check"
    else:
        title = "🌅 Your morning news" if kind == "morning" else "🌙 Night summary"
        body = headline_list(stories) if stories else plain(nothing_new)
    await dashboard.notify(kind, title, body, message, stories)


# =====================================
# WEATHER UPDATES (morning + afternoon)
# =====================================

def weather_updates_due(state, moment):
    """Which weather updates should go out now: 'morning' and/or 'afternoon'.

    A missed one (computer asleep, bot restarted) still goes out up to
    WEATHER_LATE_MINUTES late, but each is sent at most once a day.
    """
    today = moment.date().isoformat()
    due = []
    for kind, at in (("morning", morning_time(state)), ("afternoon", AFTERNOON_WEATHER_TIME)):
        hour, minute = map(int, at.split(":"))
        scheduled = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
        late = timedelta(minutes=WEATHER_LATE_MINUTES)
        if state["weather_sent"].get(kind) != today and scheduled <= moment < scheduled + late:
            due.append(kind)
    return due


async def post_weather_update(app: Application, kind):
    """Send the morning (wake-up) or afternoon weather update with jacket/cap/umbrella advice."""
    weather = await get_weather()
    if weather is None:
        print(f"{kind.capitalize()} weather not sent yet: the weather API failed. Retrying next minute.")
        return

    # Loaded right before it changes, so nothing else can change it in between
    state = load_state()
    today = datetime.now().date().isoformat()
    if state["weather_sent"].get(kind) == today:
        return  # already sent today
    state["weather_sent"][kind] = today
    state["display_brief"] = display.build_brief(weather, kind)  # for the ESP32 screen
    save_state(state)
    message = format_weather_update(weather, kind)
    await send_to_subscribers(app, state["subscribers"], message, f"{kind} weather")
    await update_display()

    brief = state["display_brief"]
    bring = [item["name"].lower() for item in brief["items"] if item["need"]]
    high_low = ", ".join(f"{name} {brief[key]}°" for name, key in (("high", "high"), ("low", "low"))
                         if brief[key] is not None)
    body = (f"{brief['now']['temp']}° {brief['now']['text']}" + (f", {high_low}" if high_low else "") + ". "
            + (f"Bring: {', '.join(bring)}." if bring else "Nothing extra to bring."))
    await dashboard.notify("weather", f"{'☀️ Morning' if kind == 'morning' else '🌤️ Afternoon'} weather",
                           body, message)


async def update_display():
    """Send the ESP32 weather display what it should show now (see display.py)."""
    try:
        await display.update(load_state().get("display_brief"))
    except Exception as error:
        print(f"Display update failed: {type(error).__name__}")


# =====================================
# THE CLOCK (runs the schedule)
# =====================================

_background_tasks = set()


def start_background(coroutine):
    """Run a job on its own, so a slow one never holds up the clock."""
    task = asyncio.create_task(coroutine)
    _background_tasks.add(task)  # keep it alive until it finishes
    task.add_done_callback(_background_tasks.discard)
    return task


async def sleep_until(moment):
    """Wait until the clock reaches `moment` (returns at once if it already has)."""
    while True:
        seconds = (moment - datetime.now()).total_seconds()
        if seconds <= 0:
            return
        await asyncio.sleep(seconds)


async def run_posts(app: Application, weather_kinds, digest_kinds):
    """Weather first (the morning one is your wake-up message), then the digests."""
    for kind in weather_kinds:
        await post_weather_update(app, kind)
    for kind in digest_kinds:
        await post_digest(app, kind)


async def check_weather_accuracy():
    try:
        await update_accuracy()
    except Exception as error:
        print(f"Weather accuracy check failed: {type(error).__name__}")


def start_due_jobs(app: Application, moment):
    """Start everything that is due at this minute."""
    minute_of_day = moment.hour * 60 + moment.minute
    if (minute_of_day + COLLECT_MINUTES_EARLY) % COLLECT_EVERY_MINUTES == 0:
        start_background(collect_news(app, max_age_minutes=COLLECT_MINUTES_EARLY * 2))
    if moment.minute == ACCURACY_CHECK_MINUTE:
        start_background(check_weather_accuracy())

    state = load_state()
    weather_kinds = weather_updates_due(state, moment)
    digest_kinds = digests_due(state, moment)
    if weather_kinds or digest_kinds:
        start_background(run_posts(app, weather_kinds, digest_kinds))
    # The weather display (a new morning/afternoon update sends it the brief itself)
    if minute_of_day % display.SEND_EVERY_MINUTES == 0 and not weather_kinds:
        start_background(update_display())


async def clock_loop(app: Application):
    """The schedule. Once a minute, on the minute, it starts whatever is due:
    - collect the NYT headlines every 30 minutes (at :25 and :55), have the AI
      rate the new stories, and send breaking news if there is any
    - weather updates at your wake-up time and at 1:00 PM
    - the morning digest after the morning weather, the stock check an hour
      before the market closes (weekdays), and the night summary at 10 PM
    - check the weather sources' accuracy every hour (at :05)
    - send the weather display its brief every 2 minutes
    """
    start_background(collect_news(app))  # right away, so /news works from the start
    start_background(update_display())
    tick = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=1)
    while True:
        await sleep_until(tick)
        try:
            start_due_jobs(app, tick)
        except Exception as error:
            print(f"Schedule error: {type(error).__name__}")
        # Next minute. Minutes missed while the computer slept are skipped, not replayed.
        tick = max(tick, datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)


# =====================================
# WEATHER QUESTIONS (chat + /weather)
# =====================================

async def get_weather_for(update: Update, place_name):
    """Weather for a named place (or HOME if None). Replies and returns None on failure."""
    place = HOME
    if place_name:
        place = await find_place(place_name)
        if place is None:
            await update.message.reply_text(
                f'I couldn\'t find a place called "{place_name}". '
                "Try a city name, like /weather Chicago or /weather Paris, France."
            )
            return None

    weather = await get_weather(place)
    if weather is None:
        await update.message.reply_text(
            "Sorry, I couldn't get the weather right now. Please try again in a minute."
        )
    return weather


async def answer_weather(update: Update, context: ContextTypes.DEFAULT_TYPE, text):
    """Answer a weather question from live weather API data instead of web search."""
    weather = await get_weather_for(update, place_in_message(text))
    if weather is None:
        return
    print(f"Weather data for {weather['place']} given to the AI.")
    await answer_with_groq(
        update,
        context,
        text,
        mode="off",
        extra_prompt=WEATHER_PROMPT + weather_facts(weather),
        fallback_html=format_weather_report(weather),
    )


async def display_request_loop(app: Application):
    """'Send to my phone' (hold B on the display): the display can't reach the bot,
    so the bot asks it for new requests every few seconds."""
    while True:
        await asyncio.sleep(DISPLAY_POLL_SECONDS)
        for request in await display.pending_requests():
            try:
                await send_display_request(app, request)
            except Exception as error:
                print(f"Sending a display request failed: {type(error).__name__}")


async def send_display_request(app: Application, request):
    state = load_state()
    chat_id = MY_CHAT_ID or (state["subscribers"] or [None])[0]
    if not chat_id or not isinstance(request, dict):
        return
    kind, item = request.get("type"), str(request.get("id", ""))
    if kind == "headline":
        story = curation.article_by_id(item)
        if story is None:
            return
        message = "📌 <b>From your display</b>\n\n" + story_block(None, story)
        await send_news(app.bot, chat_id, message, rating_keyboard([story]))
        await dashboard.notify("display", "📌 From your display", story["title"], message, [story],
                               open_story=story)
        print(f"Display: sent a story to the phone: {story['title']}")
    elif kind == "stock":
        quotes = await get_quotes()
        stories = [(t, a) for t, a in stock_stories(latest_news()["all"]) if item in t and is_recent(a)]
        lines = [f"📌 <b>{html.escape(item)}</b> from your display: {price_text(quotes.get(item))}"]
        quote = quotes.get(item)
        if quote and quote.get("previous") and quote.get("low") and quote.get("high"):
            lines.append(f"Previous close ${quote['previous']:,.2f}, "
                         f"today ${quote['low']:,.2f} - ${quote['high']:,.2f}")
        for _, article in stories[:STOCK_NEWS_COUNT]:
            url = html.escape(article["url"], quote=True)
            lines.append(f'• <a href="{url}">{html.escape(article["title"])}</a>')
        await send_news(app.bot, chat_id, "\n".join(lines))
        await dashboard.notify("display", f"📌 {item} from your display", plain(lines[0].split(": ", 1)[-1]),
                               "\n".join(lines))
        print(f"Display: sent {item} to the phone")


async def rate_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A 👍/👎 under a story: saved, and shown with a ✓ on the button."""
    query = update.callback_query
    try:
        _, story_id, value = query.data.split(":")
        rating = 1 if value == "1" else -1
    except (AttributeError, ValueError):
        await query.answer()
        return
    title = curation.record_rating(story_id, rating)
    if title is None:
        await query.answer("That story is too old to rate now.")
        return
    await query.answer(("👍 More like this: " if rating > 0 else "👎 Less like this: ") + short(title, 150))
    rows = []
    for row in query.message.reply_markup.inline_keyboard if query.message.reply_markup else []:
        new_row = []
        for button in row:
            text = button.text
            parts = (button.callback_data or "").split(":")
            if len(parts) == 3 and parts[1] == story_id:  # this story's two buttons
                text = text.replace(" ✓", "") + (" ✓" if parts[2] == value else "")
            new_row.append(InlineKeyboardButton(text, callback_data=button.callback_data))
        rows.append(new_row)
    if rows:
        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(rows))
        except TelegramError:
            pass  # e.g. the same button pressed twice: nothing to change
    print(f"News rated {'up' if rating > 0 else 'down'}: {title}")


async def post_init(app: Application):
    app.bot_data["clock_task"] = asyncio.create_task(clock_loop(app))
    app.bot_data["display_task"] = asyncio.create_task(display_request_loop(app))
    await dashboard.start()


async def post_shutdown(app: Application):
    await dashboard.stop()
    tasks = [task for task in (app.bot_data.get("clock_task"), app.bot_data.get("display_task"),
                               *_background_tasks) if task]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# =====================================
# COMMANDS
# =====================================

HELP_TEXT = (
    "Commands:\n"
    "/start - Welcome message\n"
    "/help - Show this menu\n"
    "/reset - Start a new conversation (saved facts are kept)\n"
    "/remember fact - Save a fact I should always know\n"
    "/forget number - Delete a saved fact\n"
    "/memory - Show what I remember\n"
    "/search question - Search the web for a live answer\n"
    "/subscribe - Breaking news when it happens; weather and a news digest when you "
    "wake up; weather and a stock check in the afternoon; a summary at 10 PM\n"
    "/unsubscribe - Stop the scheduled messages\n"
    "/news - New stories you haven't seen, and stocks (or just say \"check the news\")\n"
    f"/stocks - Prices and NYT stories for {WATCHLIST_TEXT} "
    "(or say \"check my stocks\")\n"
    "/free - Games and apps that are free to keep right now (Steam, Epic, GOG, Android)\n"
    f"/weather city - Weather and forecast (no city = {HOME['name']})\n"
    "/wake 7:30 - Set your wake-up time for the morning weather update\n\n"
    "Rate stories with the 👍/👎 buttons under them: I learn what you like.\n\n"
    "You can also send me any text question. Weather questions like \"will it "
    "rain tomorrow?\" are answered from live weather data; for other recent "
    "things I may search the web on my own."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = (
        "Hello! I'm your personal AI assistant, powered by Groq AI.\n\n"
        "Ask me questions, request explanations or just chat.\n\n" + HELP_TEXT
    )
    await update.message.reply_text(reply)
    print("User started the bot.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # The old conversation is moved to the archive folder, not deleted
    archive_history(update.effective_chat.id)
    await update.message.reply_text(
        "Started a new conversation. I still remember your saved facts "
        "(see /memory)."
    )
    print("Conversation archived; starting fresh.")


async def remember_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fact = " ".join(context.args).strip()
    if not fact:
        await update.message.reply_text(
            "Usage: /remember something to remember\n"
            "Example: /remember I'm learning Spanish"
        )
        return

    saved, message = add_fact(update.effective_chat.id, fact)
    await update.message.reply_text(message)
    print(f"/remember: {'saved' if saved else 'not saved'}")


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /forget number\nSend /memory to see the numbered list."
        )
        return

    removed = remove_fact(update.effective_chat.id, int(context.args[0]))
    if removed is None:
        await update.message.reply_text(
            "There's no fact with that number. Send /memory to see the list."
        )
    else:
        await update.message.reply_text(f"Forgot: {removed}")


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    facts = load_facts(chat_id)

    if facts:
        listing = "\n".join(f"{n}. {fact}" for n, fact in enumerate(facts, 1))
        text = f"Things I remember:\n{listing}"
    else:
        text = "I have no saved facts yet. Use /remember to add one."

    text += f"\n\nSaved messages in this conversation: {count_messages(chat_id)}"
    await reply_long(update.message, text)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args).strip()

    if not question:
        await update.message.reply_text(
            "Usage: /search your question\n"
            "Example: /search latest news about semiconductors"
        )
        return

    if not SEARCH_AVAILABLE:
        await update.message.reply_text(
            "Web search needs the model openai/gpt-oss-20b or openai/gpt-oss-120b. "
            f"The current model is {GROQ_MODEL}."
        )
        return

    print(f"User searched: {question}")
    await answer_with_groq(update, context, question, mode="force")


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = load_state()
    if chat_id not in state["subscribers"]:
        state["subscribers"].append(chat_id)
        save_state(state)
    await update.message.reply_text(
        f"Subscribed! At {nice_time(morning_time(state))} (your wake-up time): the weather, "
        f"the news you missed and any new free games or apps. At {nice_time(AFTERNOON_WEATHER_TIME)}: "
        "the weather. "
        f"On weekdays, an hour before the market closes: your stocks ({WATCHLIST_TEXT}). "
        f"At {nice_time(NIGHT_SUMMARY_TIME)}: tomorrow's weather, the market close and "
        "today's news. Breaking news comes when it happens (not at night). "
        "Send /unsubscribe to stop."
    )
    print("A chat subscribed to news.")


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = load_state()
    if chat_id in state["subscribers"]:
        state["subscribers"].remove(chat_id)
        save_state(state)
    await update.message.reply_text(
        "Unsubscribed. No more scheduled headlines or weather updates."
    )
    print("A chat unsubscribed from news.")


async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Headlines are prepared every hour, so this is usually instant
    if needs_download(NEWS_MAX_AGE_MINUTES * 60):
        await update.message.reply_text(
            "Getting the latest New York Times headlines ready. "
            "This takes about a minute..."
        )
    # Run in the background so the bot keeps answering other messages
    context.application.create_task(send_news_now(context.bot, update.effective_chat.id))


async def free_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/free: every game and app that is free to keep right now."""
    text = await freebies_section(only_new=False)
    await send_news(context.bot, update.effective_chat.id,
                    text or "🎁 Nothing is free to keep right now. I'll tell you in the morning when something is.")


async def stocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.application.create_task(send_stocks_now(context.bot, update.effective_chat.id))


async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    weather = await get_weather_for(update, " ".join(context.args).strip() or None)
    if weather is None:
        return
    await send_news(context.bot, update.effective_chat.id, format_weather_report(weather))
    print(f"Weather sent for {weather['place']}.")


async def wake_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/wake 7:30 sets the time of the morning weather update."""
    if not context.args:
        current = nice_time(morning_time(load_state()))
        await update.message.reply_text(
            f"Your morning weather update comes at {current}. To change it, send "
            "your wake-up time, like /wake 7:30 or /wake 6:45am"
        )
        return

    wake = parse_clock_time(" ".join(context.args))
    if wake is None:
        await update.message.reply_text(
            "I didn't understand that time. Try /wake 7:30 or /wake 6:45am"
        )
        return

    state = load_state()
    state["morning_time"] = wake
    save_state(state)
    await update.message.reply_text(
        f"Got it! Your morning weather update will come at {nice_time(wake)} every day."
    )
    print(f"Wake-up time set to {wake}.")


# =====================================
# HANDLE USER MESSAGES
# =====================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

    print(f"User sent: {text}")

    # Preserve original custom greeting
    if text.lower().strip(" .!?,") == "hello luke":
        reply = "hi luke i made this bot"
        await update.message.reply_text(reply)
        print(f"Bot replied: {reply}")
        return

    # "check the news" -> real NYT headlines with links
    if is_news_request(text):
        await news(update, context)
        return

    # "check my stocks" -> watchlist prices and NYT stock stories
    if is_stock_request(text):
        await stocks(update, context)
        return

    # "will it rain tomorrow?" -> the AI answers from live weather API data
    if is_weather_message(text):
        await answer_weather(update, context, text)
        return

    mode = "auto" if SEARCH_AVAILABLE else "off"
    await answer_with_groq(update, context, text, mode)


# =====================================
# MAIN PROGRAM
# =====================================

def main():
    for key_name, value in (
        ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
        ("GROQ_API_KEY", GROQ_API_KEY),
        ("NYT_API_KEY", NYT_API_KEY),
    ):
        if not value:
            raise SystemExit(f"Missing {key_name} in config.py")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("remember", remember_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CommandHandler("stocks", stocks))
    app.add_handler(CommandHandler("free", free_command))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CommandHandler("wake", wake_command))
    app.add_handler(CallbackQueryHandler(rate_button, pattern=r"^r:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("=" * 40)
    print("TELEGRAM AI + NEWS BOT")
    print("=" * 40)
    print("Provider: Groq")
    print("Model:", GROQ_MODEL)
    if SEARCH_AVAILABLE:
        print("Web search: ON (Groq browser_search)")
    else:
        print("Web search: OFF (needs openai/gpt-oss-20b or openai/gpt-oss-120b)")
    liked, disliked = curation.rating_counts()
    print(
        f"News: collected every {COLLECT_EVERY_MINUTES} minutes and rated by the AI; "
        f"breaking news right away, digests at wake-up and {nice_time(NIGHT_SUMMARY_TIME)} "
        f"(your ratings so far: {liked} liked, {disliked} disliked)"
    )
    print(f"Stocks: {WATCHLIST_TEXT}")
    print(
        f"Weather: NWS + 4 models for {HOME['name']}, updates at "
        f"{nice_time(morning_time(load_state()))} and {nice_time(AFTERNOON_WEATHER_TIME)}, "
        "accuracy checked hourly"
    )
    if display.DISPLAY_HOST:
        print(f"Weather display: {display.DISPLAY_HOST}, every {display.SEND_EVERY_MINUTES} minutes")
    print(f"Dashboard app: http://localhost:{dashboard.PORT} "
          f"({dashboard.webpush.device_count()} device(s) with notifications on)")
    print("Memory: saved in the memory_data folder")
    print("Bot running...")
    print("Press Ctrl+C to stop.")
    print("=" * 40)

    app.run_polling()


if __name__ == "__main__":
    main()