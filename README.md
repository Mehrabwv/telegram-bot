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
| `dashboard.py`, [`dashboard/`](dashboard) | The Briefing app: news, stocks, weather, free games and an inbox, with Windows notifications (see below) |
| `webpush.py` | Sends the app's notifications (Web Push, encrypted for each device; no outside service) |
| `scripts/briefing-links.ps1` | The link helper: makes the app open links in your default browser (Opera) instead of Edge |
| `scripts/pi-install.sh` | Sets the bot up on a Raspberry Pi as a service that starts at boot (see below) |
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

Everything the bot sends on a schedule also goes to the dashboard app (below), as a
Windows notification and in its inbox.

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

## Dashboard app (Briefing)

The bot also runs a small app for your computers, with more detail than Telegram:

- **Today:** the weather, your stocks, new stories and the bot's latest messages
- **News:** every collected story in the AI's order, filters (new to you, major, important,
  tech & science, rated), each story's details and the AI's scores, 👍/👎
- **Stocks:** price charts (1 day to 1 year), previous close, today's range, NYT stories
- **Weather:** now, what to bring, the next 24 hours, 7 days, how much each source counts
- **Free:** every game and app that is free to keep right now, with a picture, what kind of
  game it is and its review score. Hover over one for its screenshots and description; click
  for all the pictures and links to its reviews and gameplay videos. (The details come from
  Steam, SteamSpy and the Play Store; Telegram's `/free` shows the type and score too.)
- **Inbox:** everything the bot sent (breaking news, digests, stock checks, weather), in full

Ratings in the app are the same as the 👍/👎 in Telegram. A story you open in the app
counts as seen: the bot won't send it to you again.

### On this computer

1. Start the bot (`py bot.py`). It prints `Dashboard app: http://localhost:8765`.
2. Open **http://localhost:8765** in Edge.
3. Install it: the **App available** icon at the right end of the address bar (or
   ⋯ → Apps → Install this site as an app). It gets its own window and Start menu entry.
4. In the app: **Settings → Turn on notifications**, then **Send a test**.

Notifications pop up in Windows even when the app is closed, as long as Edge is allowed
to run in the background (Edge → Settings → System and performance → "Continue running
background extensions and apps when Microsoft Edge is closed", on by default).

### Links open in your own browser (Opera)

Opera can't install web apps, so the app is installed from Edge, and an app installed from
Edge would open every link (NYT stories, claim pages) in Edge. Instead:

- **On the computer the bot runs on:** nothing to do. The bot opens the app's links in your
  default browser.
- **On your other computers:** install the link helper once. In the app: Settings → Open
  links → download the link helper, right-click it → Run with PowerShell, then tick "Use the
  link helper on this computer". The first link asks "Open Briefing link?": tick Always allow.
  It adds a `briefing-open:` link type for your Windows user only (no admin) and opens only
  web links, in your default browser. Remove it with
  `powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Briefing\briefing-links.ps1" -Uninstall`.
  Once the bot moves to the Raspberry Pi, use the helper on this computer too.
- **Or skip installing:** open the app in an Opera tab (pin it). Links then open in Opera, but
  notifications only come while Opera is running.

### On your other devices (and away from home): Tailscale

The app only listens on this computer, so other devices reach it through
[Tailscale](https://tailscale.com) (free), a private network between your own devices.
Nothing is opened to the internet, and it works from anywhere, not just your home Wi-Fi.
Tailscale also gives the app an `https://` address, which notifications need.

1. Install Tailscale on this computer and on the other device, and sign in to the same
   account on both.
2. On this computer, in a terminal: `tailscale serve --bg 8765`
   The first time, it asks you to turn on HTTPS for your Tailscale network (one click).
   It then prints the app's address, like `https://your-laptop.tail1234.ts.net`.
3. On the other device, open that address in Edge, install it and turn on notifications
   (steps 3–4 above).

Notes:
- The bot (and so the app) only works while this computer is on and awake. Moving the bot
  to the Raspberry Pi later fixes that; the app works the same there.
- A VPN (like Proton VPN) can get in Tailscale's way. If the address doesn't open, try
  with the VPN off.
- `tailscale serve --bg off` stops sharing the app.

### Try it without the bot

`py dashboard.py` runs the app on its own (no Telegram, no notifications from the bot). It
downloads the headlines first (about a minute) and uses copies of your ratings and inbox.
It uses port 8765 like the bot; while the bot runs, use another: `py dashboard.py --port 8766`.

## Run it on a Raspberry Pi

A Raspberry Pi Zero 2 W can run the bot day and night (about 1 W), so it no longer
depends on the laptop being on. Only one copy of the bot may run at a time: stop it on the
laptop when the Pi takes over.

**1. Prepare the SD card (on the laptop).** In [Raspberry Pi Imager](https://www.raspberrypi.com/software/):
- Device: Raspberry Pi Zero 2 W. OS: Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**.
- Edit settings: hostname `briefing-pi`, your username and password, your Wi-Fi (the
  Zero 2 W only does 2.4 GHz), time zone `America/Phoenix`; Services tab → enable SSH.
- Write it, put the card in the Pi and power it. The first boot takes a few minutes.

**2. Connect to it** from PowerShell on the laptop: `ssh yourname@briefing-pi.local`

**3. Get the code** (on the Pi): `git clone https://github.com/Mehrabwv/telegram-bot.git`

**4. Copy your private files** (on the laptop, in a second PowerShell; they're not on
GitHub). Stop the bot on the laptop first (Ctrl+C), so the files are final:
```
cd C:\Users\mehra\telegram-bot
scp config.py .env state.json news_memory.json weather_accuracy.json dashboard_keys.json dashboard_inbox.json freebies_details.json yourname@briefing-pi.local:telegram-bot/
scp -r memory_data yourname@briefing-pi.local:telegram-bot/
```

**5. Install and start it** (on the Pi): `cd telegram-bot && bash scripts/pi-install.sh`
It installs the packages and a service that starts the bot at every boot and restarts it if
it crashes. Check it with `journalctl -u telegram-bot -f` (Ctrl+C leaves the log) and send
the bot `/help` in Telegram.

**6. The app.** On the Pi, set up Tailscale (on the Pi:
`curl -fsSL https://tailscale.com/install.sh | sh`, then `sudo tailscale up` and open the
link it prints to sign in, then `sudo tailscale serve --bg 8765`). Then on both computers,
open the Pi's address (`https://briefing-pi.<your-tailnet>.ts.net`) in Edge, install the
app, turn notifications on, and use the link helper (Settings → Open links). Uninstall the
old `localhost` app.

**Updating later:** after changing the code on the laptop and pushing it to GitHub,
on the Pi: `cd telegram-bot && git fetch && git reset --hard origin/main && sudo systemctl restart telegram-bot`
(run `bash scripts/pi-install.sh` instead of the restart if `requirements.txt` changed). This
replaces the Pi's copy of the code with GitHub's; your private files aren't touched.

## Kept off GitHub

These stay on your computer only (see `.gitignore`):
- `config.py` and `.env`: your keys
- `memory_data/`: chat history and saved facts
- `state.json` and `weather_accuracy.json`: the bot's schedule and learned weather accuracy
- `news_memory.json`: your news ratings and the AI's scores
- `dashboard_keys.json`: the app's private key for signing notifications
- `dashboard_subscriptions.json`, `dashboard_inbox.json`: your devices' notification
  addresses, and the app's inbox
- `freebies_details.json`: the free games' details, looked up once and kept for 2 weeks
