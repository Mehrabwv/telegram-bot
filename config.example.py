"""Private keys and settings for bot.py.

Copy this file to config.py and fill in your own values. config.py is listed in
.gitignore, so your keys never get uploaded.
"""

# From @BotFather in Telegram
TELEGRAM_TOKEN = "paste-your-bot-token-here"

# From https://console.groq.com/keys
GROQ_API_KEY = "paste-your-groq-key-here"

# From https://developer.nytimes.com: an app with the Top Stories and Most Popular APIs
NYT_API_KEY = "paste-your-nyt-key-here"

# /search only works with openai/gpt-oss-20b or openai/gpt-oss-120b
GROQ_MODEL = "openai/gpt-oss-20b"

# Your Telegram chat ID (a number): you get the scheduled updates from the start.
# 0 = nobody gets them until they send /subscribe.
MY_CHAT_ID = 0
