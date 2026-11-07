# Telegram bot with a desk weather display

A personal Telegram bot for AI chat, news, stocks and weather. It also drives a small
ESP32-S3 screen that shows the morning and afternoon weather brief, and there's a
3D-printable case for that screen.

![What the display shows](esp32-display/screenshot-example.png)
![The case](esp32-display/case/renders/1_front.png)

## What's in here

| Path | What it is |
|---|---|
| `bot.py` | The bot. Start it with `py bot.py`. |
| `weather.py` | Forecasts from NWS and the ECMWF, GFS, ICON and GEM models (via Open-Meteo), blended into one. Every hour the earlier forecasts are checked against a weather station, so each source's weight follows its real accuracy. |
| `news.py`, `stocks.py` | NYT headlines and stock prices |
| `freebies.py` | Paid games and apps that are free to keep for a while (Epic, Steam, GOG, Android), for the morning digest and `/free` |
| `curation.py` | Picks the news for you: the AI rates every new story (major, important, tech/science) and learns from your 👍/👎 |
| `memory.py` | Chat history and saved facts, kept in `memory_data/` |
| `display.py` | Sends the weather, headlines and stocks to the display every 2 minutes, and passes its "send to my phone" presses to Telegram |
| `scripts/nightly-github-push.ps1` | Uploads the day's changes to GitHub every night (a Windows scheduled task); refuses if a key would be uploaded |
| `list_models.py` | Lists the Groq models your key can use |
| [`esp32-display/`](esp32-display) | The display's firmware: XIAO ESP32-S3 + 2" ST7789 screen, built with PlatformIO |
| [`esp32-display/case/`](esp32-display/case) | The case and desk dock: parametric model, STL files, build guide, 3D viewer |
| [`weather_experiment/`](weather_experiment) | A standalone script that ranks the 5 weather sources against a real station |

## Run the bot

1. Install the packages: `py -m pip install -r requirements.txt`
2. Copy `config.example.py` to `config.py` and fill in your keys.
3. Set your location (`HOME` in `weather.py`) and your stocks (`WATCHLIST` in `stocks.py`).
4. Start it: `py bot.py`, then send it `/help` in Telegram.

## Commands

- `/weather city`: weather and forecast (no city = `HOME`)
- `/wake 7:30`: your wake-up time, when the morning weather update comes
- `/subscribe`, `/unsubscribe`: the scheduled messages (below)
- `/news`: new stories you haven't seen yet, and stocks
- `/stocks`: prices and NYT stories for your watchlist
- `/search question`: search the web for a live answer
- `/free`: every game and app that is free to keep right now
- `/remember fact`, `/forget number`, `/memory`: facts the bot always knows
- `/reset`: start a new conversation (saved facts are kept)

Any other message is answered by the AI (Groq).

## Scheduled messages

| When | What |
|---|---|
| Your wake-up time (`/wake`) | The weather, then a news digest: 5–7 stories you missed, stocks, and new games/apps that are free to keep |
| 1 PM | The afternoon weather |
| Weekdays, an hour before the New York market closes (1 PM in Arizona, 12 PM in summer) | A stock check, with big moves pointed out |
| 10 PM | Tomorrow's weather, the market close, and today's stories you haven't seen |
| Any time but night | Breaking news, only when the AI rates a story major and very urgent (at most 3 a day) |

Every 30 minutes the bot collects the NYT headlines and the AI rates each new story.
Digests list major news first, then important news, then tech and science. A story is
never sent twice unless NYT changed its headline. Rate stories with the 👍/👎 buttons
under them: the AI uses your ratings to judge what you'd like (kept in `news_memory.json`).

## Kept off GitHub

These stay on your computer only (see `.gitignore`):
- `config.py` and `.env`: your keys
- `memory_data/`: chat history and saved facts
- `state.json` and `weather_accuracy.json`: the bot's schedule and learned weather accuracy
- `news_memory.json`: your news ratings and the AI's scores
