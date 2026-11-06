import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from telegram import Update, LinkPreviewOptions
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import display
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
from news import format_news_message, get_news, latest_news, needs_download, pick_articles
from stocks import WATCHLIST, format_stock_section, get_quotes, is_recent, stock_stories
from weather import (
    HOME,
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

NEWS_COUNT = 3  # top headlines per post
NEWS_EVERY_MINUTES = 60  # news posts happen on the clock, e.g. 1:00, 2:00, 3:00
COLLECT_EVERY_MINUTES = 30  # NYT headlines are collected (preprocessed) this often,
COLLECT_MINUTES_EARLY = 5  # ...this long before each half hour: at :25 and :55
NEWS_MAX_AGE_MINUTES = COLLECT_EVERY_MINUTES + 10  # /news downloads only if older
STOCK_NEWS_COUNT = 3  # NYT stories in the stock section
WEATHER_IN_HOURLY = False  # weather has its own updates now (below)

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

async def send_news(bot, chat_id, message):
    await bot.send_message(
        chat_id=chat_id,
        text=message,
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def build_briefing(hourly=False):
    """Top headlines + stock section as one message, or None if there is no news.

    hourly=True (the hourly post): only stories that were not sent before are
    used, and they are remembered. Otherwise (/news) the current best are shown.
    """
    # The hourly post uses the collection from :55; /news is fine with the last one
    max_age_minutes = COLLECT_MINUTES_EARLY * 2 if hourly else NEWS_MAX_AGE_MINUTES
    news = await get_news(max_age_minutes * 60)  # already collected, so instant
    if not news["ranked"]:
        return None

    # Stories about the watchlist go in the stock section, not the top headlines
    all_stock = stock_stories(news["all"])
    stock_urls = {article["url"] for _, article in all_stock}
    recent_stock = [(tickers, a) for tickers, a in all_stock if is_recent(a)]

    if not hourly:
        picks, _ = pick_articles(news["ranked"], set(), NEWS_COUNT, stock_urls)
        stories = recent_stock[:STOCK_NEWS_COUNT]
    else:
        # Loaded right before it changes, so nothing else can change it in between
        state = load_state()
        picks, start_over = pick_articles(
            news["ranked"], set(state["sent_urls"]), NEWS_COUNT, stock_urls
        )
        sent_stock = set(state["sent_stock_urls"])
        stories = [(t, a) for t, a in recent_stock if a["url"] not in sent_stock]
        stories = stories[:STOCK_NEWS_COUNT]

        if start_over:
            state["sent_urls"] = []
        state["sent_urls"].extend(article["url"] for article in picks)
        state["sent_urls"] = state["sent_urls"][-200:]
        state["sent_stock_urls"].extend(article["url"] for _, article in stories)
        state["sent_stock_urls"] = state["sent_stock_urls"][-200:]
        save_state(state)

    quotes = await get_quotes()  # prices and weather are always fetched fresh
    message = format_news_message(picks) + "\n\n" + format_stock_section(quotes, stories)
    if WEATHER_IN_HOURLY:
        weather = await get_weather()
        if weather:
            message += "\n\n" + format_weather_brief(weather)
    return message


async def build_stock_message():
    """Prices + recent NYT stories for the watchlist (never waits for NYT)."""
    stories = [
        (tickers, a) for tickers, a in stock_stories(latest_news()["all"]) if is_recent(a)
    ]
    quotes = await get_quotes()
    return format_stock_section(quotes, stories[:STOCK_NEWS_COUNT])


async def send_news_now(bot, chat_id):
    """Send the current headlines and stocks to one chat (used by /news)."""
    message = await build_briefing()
    if message is None:
        await bot.send_message(
            chat_id=chat_id,
            text="Sorry, I couldn't get the news right now. Please try again later.",
        )
        return
    await send_news(bot, chat_id, message)
    print("News sent.")


async def send_stocks_now(bot, chat_id):
    """Send the stock section to one chat (used by /stocks)."""
    await send_news(bot, chat_id, await build_stock_message())
    print("Stocks sent.")


async def send_to_subscribers(app: Application, subscribers, message, what):
    sent = 0
    for chat_id in subscribers:
        try:
            await send_news(app.bot, chat_id, message)
            sent += 1
        except TelegramError as error:
            print(f"Could not send {what} to a subscriber: {type(error).__name__}")
    print(f"Scheduled {what} sent to {sent} chat(s).")


async def collect_news(max_age_minutes=0):
    """Preprocess: download and rank the NYT headlines (about a minute).

    Skipped if they were already collected in the last max_age_minutes.
    """
    try:
        await get_news(max_age_minutes * 60)
    except Exception as error:
        print(f"Collecting headlines failed: {type(error).__name__}")


async def post_hourly_news(app: Application):
    """Send the collected headlines + stocks to every subscriber."""
    if not load_state()["subscribers"]:
        return

    message = await build_briefing(hourly=True)
    if message is None:
        print("Scheduled news skipped: no articles available.")
        return
    await send_to_subscribers(app, load_state()["subscribers"], message, "news")


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
    await send_to_subscribers(
        app, state["subscribers"], format_weather_update(weather, kind), f"{kind} weather"
    )
    await update_display()


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


async def run_posts(app: Application, weather_kinds, news_due):
    """Weather first (the morning one is your wake-up message), then the news."""
    for kind in weather_kinds:
        await post_weather_update(app, kind)
    if news_due:
        await post_hourly_news(app)


async def check_weather_accuracy():
    try:
        await update_accuracy()
    except Exception as error:
        print(f"Weather accuracy check failed: {type(error).__name__}")


def start_due_jobs(app: Application, moment):
    """Start everything that is due at this minute."""
    minute_of_day = moment.hour * 60 + moment.minute
    if (minute_of_day + COLLECT_MINUTES_EARLY) % COLLECT_EVERY_MINUTES == 0:
        start_background(collect_news(max_age_minutes=COLLECT_MINUTES_EARLY * 2))
    if moment.minute == ACCURACY_CHECK_MINUTE:
        start_background(check_weather_accuracy())

    weather_kinds = weather_updates_due(load_state(), moment)
    news_due = minute_of_day % NEWS_EVERY_MINUTES == 0
    if weather_kinds or news_due:
        start_background(run_posts(app, weather_kinds, news_due))
    # The weather display (a new morning/afternoon update sends it the brief itself)
    if minute_of_day % display.SEND_EVERY_MINUTES == 0 and not weather_kinds:
        start_background(update_display())


async def clock_loop(app: Application):
    """The schedule. Once a minute, on the minute, it starts whatever is due:
    - collect the NYT headlines every 30 minutes (at :25 and :55)
    - post headlines + stocks every hour (at :00)
    - weather updates at your wake-up time (8:00 AM for now) and at 1:00 PM
    - check the weather sources' accuracy every hour (at :05)
    - send the weather display its brief every 2 minutes
    """
    start_background(collect_news())  # right away, so /news works from the start
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


async def post_init(app: Application):
    app.bot_data["clock_task"] = asyncio.create_task(clock_loop(app))


async def post_shutdown(app: Application):
    tasks = [task for task in (app.bot_data.get("clock_task"), *_background_tasks) if task]
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
    f"/subscribe - Get {NEWS_COUNT} NYT headlines + your stocks every hour, and weather "
    "updates when you wake up and at 1 PM\n"
    "/unsubscribe - Stop the scheduled messages\n"
    "/news - Headlines and stocks right now (or just say \"check the news\")\n"
    f"/stocks - Prices and NYT stories for {WATCHLIST_TEXT} "
    "(or say \"check my stocks\")\n"
    f"/weather city - Weather and forecast (no city = {HOME['name']})\n"
    "/wake 7:30 - Set your wake-up time for the morning weather update\n\n"
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
        f"Subscribed! You'll get {NEWS_COUNT} New York Times headlines and a stock "
        f"section ({WATCHLIST_TEXT}) every {NEWS_EVERY_MINUTES} minutes on the hour, "
        f"plus weather updates at {nice_time(morning_time(state))} and "
        f"{nice_time(AFTERNOON_WEATHER_TIME)}. Send /unsubscribe to stop."
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
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CommandHandler("wake", wake_command))
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
    print(
        f"News: {NEWS_COUNT} NYT articles every {NEWS_EVERY_MINUTES} minutes, "
        f"collected every {COLLECT_EVERY_MINUTES} minutes"
    )
    print(f"Stocks: {WATCHLIST_TEXT}")
    print(
        f"Weather: NWS + 4 models for {HOME['name']}, updates at "
        f"{nice_time(morning_time(load_state()))} and {nice_time(AFTERNOON_WEATHER_TIME)}, "
        "accuracy checked hourly"
    )
    if display.DISPLAY_HOST:
        print(f"Weather display: {display.DISPLAY_HOST}, every {display.SEND_EVERY_MINUTES} minutes")
    print("Memory: saved in the memory_data folder")
    print("Bot running...")
    print("Press Ctrl+C to stop.")
    print("=" * 40)

    app.run_polling()


if __name__ == "__main__":
    main()