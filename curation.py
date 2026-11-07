"""Picks the news for you, and learns what you like.

Every time the bot collects NYT headlines (every 30 minutes), the AI rates each
new story:
- category: "major" (urgent or breaking, widely important), "important"
  (worth knowing today), "techsci" (a notable technology or science story) or
  "other"
- importance (0-10): how urgent and consequential it is
- interest (0-10): how much YOU would want it, judged from your ratings

Your 👍/👎 on stories (buttons under every news message) are saved here and
given to the AI each time, so its "interest" follows your taste. Sections you
keep liking or disliking also move up or down a little by themselves.

A story is never sent twice. It only comes back if NYT changed its headline
(an updated story). Everything is kept in news_memory.json.
"""

import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from config import GROQ_API_KEY, GROQ_MODEL
from news import final_score

MEMORY_FILE = Path(__file__).resolve().parent / "news_memory.json"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

CATEGORIES = ("major", "important", "techsci", "other")  # in the order they are shown
LABELS = {"major": "🔴 Major", "important": "🟠 Important", "techsci": "🔬 Tech & science", "other": "🔹 For you"}
SCREEN_TAGS = {"major": "MAJOR", "important": "IMPORTANT", "techsci": "TECH/SCI", "other": ""}

# Alerts: only major news this urgent, at most this often
ALERT_MIN_IMPORTANCE = 8
ALERTS_PER_DAY = 3
ALERT_GAP_MINUTES = 60
ALERT_MAX_AGE_HOURS = 6  # only stories first seen this recently

# Digests: at least DIGEST_MIN stories, up to DIGEST_MAX when more good ones are around
DIGEST_MIN, DIGEST_MAX = 5, 7
EXTRA_MIN_IMPORTANCE = 7  # a 6th/7th story must be major/important this high...
EXTRA_MIN_INTEREST = 8  # ...or this interesting to you

RATE_BATCH = 20  # stories per AI request
RATE_MAX_PER_RUN = 60  # the bot collects up to 60 stories at a time
PROFILE_EXAMPLES = 15  # liked / disliked headlines shown to the AI
KEEP_DAYS = 4  # ratings and stories are forgotten after this long...
KEEP_SENT_DAYS = 21  # ...and what was sent after this long
KEEP_RATINGS = 300

RATING_PROMPT = (
    "You pick news for one reader. Rate each numbered New York Times story.\n"
    'category: "major" = urgent or breaking news of wide importance that the reader '
    "should hear about now (for example a war escalating, a major disaster or attack, "
    "a historic ruling or election result, a market crash, the death of a world "
    'figure); "important" = significant news worth knowing today, but not urgent; '
    '"techsci" = a notable technology or science story; "other" = everything else '
    "(opinion, lifestyle, minor or local news).\n"
    "importance: 0-10, how urgent and consequential the story is for people in "
    "general. Keep 9-10 for rare, historic news.\n"
    "interest: 0-10, how much this particular reader would want to read it, judging "
    "by what they liked and disliked before (5 if you can't tell).\n"
    'Answer with JSON only: {"stories": [{"n": 1, "category": "major", '
    '"importance": 0, "interest": 0}, ...]}, one entry per story.'
)

_memory = None
_lock = asyncio.Lock()


# =====================================
# SAVED MEMORY
# =====================================

def article_id(url):
    """A short id for a story (used by the rating buttons and the display)."""
    return hashlib.sha1(url.encode()).hexdigest()[:8]


def load():
    global _memory
    if _memory is None:
        try:
            _memory = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            _memory = {}
        for key in ("scores", "sent", "articles"):
            _memory.setdefault(key, {})
        for key in ("ratings", "alerts"):
            _memory.setdefault(key, [])
    return _memory


def save():
    MEMORY_FILE.write_text(json.dumps(load(), indent=1, ensure_ascii=False), encoding="utf-8")


def stamp(moment=None):
    return (moment or datetime.now()).isoformat(timespec="minutes")


def prune(memory):
    now = datetime.now()

    def fresh(entry, days):
        try:
            return now - datetime.fromisoformat(entry["at"]) < timedelta(days=days)
        except (KeyError, ValueError):
            return False

    memory["scores"] = {k: v for k, v in memory["scores"].items() if fresh(v, KEEP_DAYS)}
    memory["articles"] = {k: v for k, v in memory["articles"].items() if fresh(v, KEEP_DAYS)}
    memory["sent"] = {k: v for k, v in memory["sent"].items() if fresh(v, KEEP_SENT_DAYS)}
    memory["alerts"] = [a for a in memory["alerts"] if fresh(a, 2)]
    memory["ratings"] = memory["ratings"][-KEEP_RATINGS:]


def remember_articles(memory, articles):
    """Keeps each story's details, so a button press days later still finds it."""
    for article in articles:
        entry = memory["articles"].setdefault(article_id(article["url"]), {"seen": stamp()})
        entry.update({
            "url": article["url"], "title": article["title"], "section": article["section"],
            "summary": article["summary"], "at": stamp(),
        })


# =====================================
# THE AI'S RATINGS
# =====================================

def clamp(value):
    try:
        return max(0, min(10, int(round(float(value)))))
    except (TypeError, ValueError):
        return 5


def needs_rating(memory, article):
    score = memory["scores"].get(article_id(article["url"]))
    return score is None or score["title"] != article["title"]


def profile_text(memory, reader_notes):
    lines = []
    if reader_notes:
        lines.append("About the reader:")
        lines += [f"- {note}" for note in reader_notes]
    liked = [r["title"] for r in memory["ratings"] if r["rating"] > 0][-PROFILE_EXAMPLES:]
    disliked = [r["title"] for r in memory["ratings"] if r["rating"] < 0][-PROFILE_EXAMPLES:]
    if liked:
        lines.append("Stories the reader liked:")
        lines += [f"- {title}" for title in liked]
    if disliked:
        lines.append("Stories the reader disliked:")
        lines += [f"- {title}" for title in disliked]
    if not (liked or disliked):
        lines.append("The reader hasn't rated any stories yet.")
    return "\n".join(lines)


async def ask_ai_json(messages):
    """One Groq request that should answer with a JSON object. Returns the dict or None."""
    body = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    if GROQ_MODEL.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(GROQ_URL, headers=headers, json=body)
        if response.status_code == 400:  # this model may not take JSON mode: ask plainly
            del body["response_format"]
            response = await client.post(GROQ_URL, headers=headers, json=body)
        response.raise_for_status()
    text = response.json()["choices"][0]["message"].get("content") or ""
    match = re.search(r"\{.*\}", text, re.S)
    return json.loads(match.group(0)) if match else None


async def rate_batch(batch, memory, reader_notes):
    stories = "\n".join(
        f"{n}. [{a['section']}] {a['title']}: {a['summary'][:220]}" for n, a in enumerate(batch, 1)
    )
    return await ask_ai_json([
        {"role": "system", "content": RATING_PROMPT},
        {"role": "user", "content": f"{profile_text(memory, reader_notes)}\n\nStories to rate:\n{stories}"},
    ])


async def rate_stories(articles, reader_notes=()):
    """Has the AI rate the stories it hasn't rated yet (or whose headline changed)."""
    async with _lock:
        memory = load()
        remember_articles(memory, articles)
        todo = [a for a in articles if needs_rating(memory, a)][:RATE_MAX_PER_RUN]
        rated = 0
        for start in range(0, len(todo), RATE_BATCH):
            batch = todo[start:start + RATE_BATCH]
            try:
                result = await rate_batch(batch, memory, reader_notes)
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
                print(f"News rating by the AI failed: {type(error).__name__}")
                break
            for item in (result or {}).get("stories") or []:
                if not isinstance(item, dict):
                    continue
                n = item.get("n")
                if not isinstance(n, int) or not 1 <= n <= len(batch):
                    continue
                article = batch[n - 1]
                category = item.get("category")
                memory["scores"][article_id(article["url"])] = {
                    "title": article["title"],
                    "category": category if category in CATEGORIES else "other",
                    "importance": clamp(item.get("importance")),
                    "interest": clamp(item.get("interest")),
                    "at": stamp(),
                }
                rated += 1
        prune(memory)
        save()
    if rated:
        print(f"News: the AI rated {rated} new stories.")


def score_of(memory, article):
    """The AI's rating, or a rough guess for a story it hasn't rated (yet)."""
    score = memory["scores"].get(article_id(article["url"]))
    if score and score["title"] == article["title"]:
        return score
    section = article["section"].lower()
    category = "techsci" if "technolog" in section or "science" in section else "other"
    importance = min(6, final_score(article) // 25) if "score" in article else 3
    return {"category": category, "importance": importance, "interest": 5, "guess": True}


def section_bias(memory, section):
    """-2..+2 points from your ratings of this section's stories."""
    votes = [r["rating"] for r in memory["ratings"] if r.get("section") == section]
    return 2 * sum(votes) / (len(votes) + 3) if votes else 0


def ranked(memory, articles):
    """Major first, then important, then tech/science, then the rest; best first in each."""
    def key(article):
        score = score_of(memory, article)
        value = score["importance"] * 0.6 + score["interest"] * 0.4 + section_bias(memory, article["section"])
        return CATEGORIES.index(score["category"]), -value

    return sorted(articles, key=key)


def is_new(memory, article):
    """Not sent yet, or sent with a different headline (the story was updated)."""
    sent = memory["sent"].get(article_id(article["url"]))
    return sent is None or sent["title"] != article["title"]


# =====================================
# WHAT GETS SENT
# =====================================

def pick_digest(articles, skip_urls=()):
    """5 to 7 stories you haven't seen: major, then important, then tech/science.
    Fewer than 5 only if there aren't 5 new stories at all."""
    memory = load()
    pool = [a for a in ranked(memory, articles) if a["url"] not in skip_urls and is_new(memory, a)]
    good = [a for a in pool if score_of(memory, a)["category"] != "other"]
    picks = good[:DIGEST_MIN]
    if len(picks) < DIGEST_MIN:  # not enough: the stories you'd like most
        rest = sorted((a for a in pool if a not in picks),
                      key=lambda a: -score_of(memory, a)["interest"])
        picks += rest[:DIGEST_MIN - len(picks)]
    for article in good[DIGEST_MIN:]:
        if len(picks) >= DIGEST_MAX:
            break
        score = score_of(memory, article)
        big = score["category"] in ("major", "important") and score["importance"] >= EXTRA_MIN_IMPORTANCE
        if big or score["interest"] >= EXTRA_MIN_INTEREST:
            picks.append(article)
    return picks


def pick_alerts(articles, skip_urls=(), now=None):
    """Breaking news worth a message right now (usually none)."""
    memory = load()
    now = now or datetime.now()
    today = [a for a in memory["alerts"] if a["at"][:10] == now.date().isoformat()]
    if len(today) >= ALERTS_PER_DAY:
        return []
    if memory["alerts"]:
        last = datetime.fromisoformat(memory["alerts"][-1]["at"])
        if now - last < timedelta(minutes=ALERT_GAP_MINUTES):
            return []
    picks = []
    for article in ranked(memory, articles):
        score = score_of(memory, article)
        if score.get("guess") or score["category"] != "major" or score["importance"] < ALERT_MIN_IMPORTANCE:
            continue
        if article["url"] in skip_urls or not is_new(memory, article):
            continue
        seen = memory["articles"].get(article_id(article["url"]), {}).get("seen")
        if seen and now - datetime.fromisoformat(seen) > timedelta(hours=ALERT_MAX_AGE_HOURS):
            continue
        picks.append(article)
        break  # one at a time
    return picks


def mark_sent(articles, alert=False):
    memory = load()
    for article in articles:
        memory["sent"][article_id(article["url"])] = {"title": article["title"], "at": stamp()}
        if alert:
            memory["alerts"].append({"url": article["url"], "at": stamp()})
    save()


def label(article):
    return LABELS[score_of(load(), article)["category"]]


def screen_tag(article):
    return SCREEN_TAGS[score_of(load(), article)["category"]]


def best(articles, count):
    """The top stories right now, seen or not (for the display)."""
    return ranked(load(), articles)[:count]


def article_by_id(story_id):
    return load()["articles"].get(story_id)


def record_rating(story_id, rating):
    """Saves your 👍 (1) or 👎 (-1). Returns the story's title, or None if unknown."""
    memory = load()
    story = memory["articles"].get(story_id)
    if story is None:
        return None
    memory["ratings"] = [r for r in memory["ratings"] if r["id"] != story_id]
    memory["ratings"].append({
        "id": story_id, "title": story["title"], "section": story["section"],
        "rating": rating, "at": stamp(),
    })
    save()
    return story["title"]


def rating_counts():
    ratings = load()["ratings"]
    return sum(r["rating"] > 0 for r in ratings), sum(r["rating"] < 0 for r in ratings)
