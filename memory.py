"""Simple, model-independent memory for the bot.

Everything is stored as plain text files in the 'memory_data' folder:
  history_<chat_id>.jsonl   one JSON object per line: time, role, content
  facts_<chat_id>.md        one fact per line, written as "- fact"

Roles are always "user" or "assistant", so the files work with any AI model.
Only the final request step in bot.py is specific to one provider.
"""

import json
import re
from collections import deque
from datetime import datetime
from pathlib import Path

MEMORY_DIR = Path(__file__).resolve().parent / "memory_data"
ARCHIVE_DIR = MEMORY_DIR / "archive"
MEMORY_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR.mkdir(exist_ok=True)

MAX_FACTS = 50
MAX_FACT_LENGTH = 300


def history_path(chat_id):
    return MEMORY_DIR / f"history_{chat_id}.jsonl"


def facts_path(chat_id):
    return MEMORY_DIR / f"facts_{chat_id}.md"


# =====================================
# CONVERSATION HISTORY
# =====================================

def load_history(chat_id, limit):
    """Return the last `limit` saved messages as [{'role', 'content'}, ...]."""
    path = history_path(chat_id)
    if not path.exists():
        return []

    messages = []
    with open(path, "r", encoding="utf-8") as f:
        for line in deque(f, maxlen=limit):
            try:
                entry = json.loads(line)
                messages.append({"role": entry["role"], "content": entry["content"]})
            except (ValueError, KeyError, TypeError):
                continue  # skip damaged lines

    # A conversation should start with the user's message
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def append_message(chat_id, role, content):
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "role": role,
        "content": content,
    }
    with open(history_path(chat_id), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def count_messages(chat_id):
    path = history_path(chat_id)
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def archive_history(chat_id):
    """Move the current conversation into the archive folder (not deleted)."""
    path = history_path(chat_id)
    if not path.exists():
        return False
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path.replace(ARCHIVE_DIR / f"history_{chat_id}_{stamp}.jsonl")
    return True


# =====================================
# LONG-TERM FACTS
# =====================================

def load_facts(chat_id):
    path = facts_path(chat_id)
    if not path.exists():
        return []

    facts = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("- ") and len(line) > 2:
                facts.append(line[2:].strip())
    return facts


def save_facts(chat_id, facts):
    with open(facts_path(chat_id), "w", encoding="utf-8") as f:
        f.write("# Things to remember\n\n")
        for fact in facts:
            f.write(f"- {fact}\n")


def looks_like_secret(text):
    """True if the text contains a long key-like string (tokens, API keys)."""
    return re.search(r"[A-Za-z0-9_\-:]{30,}", text) is not None


def add_fact(chat_id, text):
    """Save a fact. Returns (success, message_for_the_user)."""
    fact = " ".join(text.split())  # collapse line breaks and extra spaces

    if not fact:
        return False, "There was nothing to save."
    if len(fact) > MAX_FACT_LENGTH:
        return False, f"That's too long (max {MAX_FACT_LENGTH} characters). Try a shorter fact."
    if looks_like_secret(fact):
        return False, "That looks like a password or API key, so I didn't save it."

    facts = load_facts(chat_id)
    if len(facts) >= MAX_FACTS:
        return False, f"Memory is full ({MAX_FACTS} facts). Use /forget to remove one first."
    if fact.lower() in (existing.lower() for existing in facts):
        return False, "I already have that saved."

    facts.append(fact)
    save_facts(chat_id, facts)
    return True, f"Saved as fact #{len(facts)}."


def remove_fact(chat_id, number):
    """Remove fact number `number` (starting at 1). Returns the text, or None."""
    facts = load_facts(chat_id)
    if not 1 <= number <= len(facts):
        return None
    removed = facts.pop(number - 1)
    save_facts(chat_id, facts)
    return removed