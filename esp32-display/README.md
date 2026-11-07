# Weather display (ESP32-S3)

A small screen for your desk. It shows the bot's weather briefs. The bot runs on
your computer and sends the brief to the display over Wi-Fi every 2 minutes.

```
┌──────────────────────────────────────────┐
│ Morning brief  8:00 AM           9:41 AM │
│  ☀   72°   Feels 70°               H 84° │
│            Clear                   L 56° │
│ What to bring today     ● Confidence: high│
│ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│ │JACKET    │ │CAP       │ │UMBRELLA  │   │  colored card = bring it
│ │YES       │ │YES       │ │NO        │   │  dark card    = leave it
│ │Feels 52° │ │UV 7      │ │Rain 10%  │   │
│ │at 7 AM   │ │at 12 PM  │ │at most   │   │
│ └──────────┘ └──────────┘ └──────────┘   │
│  9 AM   10 AM   11 AM   12 PM   1 PM     │  the next 5 hours
│   ☀      ☀       ⛅      ⛅      ☀       │
│  74°    77°     80°     82°     84°      │
│   0%     0%     10%     10%      5%      │  chance of rain
└──────────────────────────────────────────┘
```

- **Morning brief** at your wake-up time (`/wake`), shown for the 5 hours it covers.
- **Afternoon brief** at 1 PM, shown for the 5 hours it covers.
- **Right now** at all other times, refreshed every 30 minutes. After 9 PM the
  cards are for tomorrow.
- The header turns **red ("bot offline")** if the bot hasn't sent anything for 10 minutes.
Other screens (button A steps through them):
- **Headlines:** the top 9 NYT stories in the order the AI ranked them for you, each
  marked MAJOR, IMPORTANT or TECH/SCI, 2 or 3 per page. Every 5 minutes they come up
  by themselves for 10 seconds a page, then the weather returns (`NEWS_EVERY_MINUTES`
  in `include/settings.h`, 0 = only with the buttons).
- **Stocks:** your watchlist's prices and today's change (green up, red down).
- **Later:** the next 12 hours, or tomorrow 7 AM–6 PM in the evening.
- Open a headline or a stock for details: the story's summary, or the stock's previous
  close, today's range and recent NYT stories about it.

## Wiring (already done)

| Screen pin | XIAO ESP32-S3 |
|---|---|
| VCC | 3V3 |
| GND | GND |
| SCL | D8 (GPIO 7) |
| SDA | D10 (GPIO 9) |
| CS | D0 (GPIO 1) |
| DC | D1 (GPIO 2) |
| RES | D3 (GPIO 4) |

## 1. Put the program on the board (once)

1. **Quit VS Code completely** (File → Exit), then open it again from the Start
   menu. It has to pick up a new setting that lets PlatformIO run on an ARM laptop.
2. Plug the XIAO into the laptop with a USB-C cable. It must be a **data** cable;
   some cables only charge.
3. In VS Code: **File → Open Folder… → `telegram-bot\esp32-display`**. Wait until
   PlatformIO finishes loading. A blue bar appears at the bottom.
4. In that blue bar, click **→ (Upload)**. This takes about a minute the first time.
   - **✓ (Build)** only compiles. **🔌 (Serial Monitor)** shows the board's log.
   - If the upload says it can't find the board: hold the **B** button, press and
     release **R**, then release **B**. Click Upload again. Afterwards, press **R** once.

## 2. Connect the display to Wi-Fi (once)

The screen shows the steps:
1. On your phone, join the Wi-Fi network **Weather-Display-Setup**.
2. A setup page opens. If it doesn't, go to **192.168.4.1** in the browser.
3. Tap **Configure WiFi**, pick your Wi-Fi and type its password.
   The ESP32 only works with **2.4 GHz** Wi-Fi. If your network isn't in the
   list, it's probably 5 GHz only.

Your Wi-Fi password is stored on the board only; it isn't in any file here.
To change Wi-Fi later, hold the **B** button for 3 seconds (or both buttons on top
of the case).

## 3. Restart the bot

In the bot's window: Ctrl+C, then `py bot.py`. Within 2 minutes the display shows
the weather, and the bot prints `Display connected`.

## If something's wrong

| What you see | Fix (in `include/settings.h`, then Upload again) |
|---|---|
| Black and white swapped on the color bars | change `SCREEN_INVERT` to `false` |
| Red and blue swapped | change `SCREEN_BGR` to `true` |
| Garbled picture | lower `SCREEN_SPI_MHZ` to `10` |
| Black screen (light on) | set `SCREEN_SPI_MODE` to `3`; check the wiring |
| Upside down | change `SCREEN_ROTATION` to `3` |

- **The bot says "Display not reachable … name not found"**: the computer can't
  find `weather-display.local`. Set `DISPLAY_HOST` in `display.py` (in the bot
  folder) to the IP address the display shows, e.g. `"10.3.171.25"`. Then restart the bot.
- **The display shows "Waiting for the bot…"**: the bot isn't running, or it
  hasn't been restarted since this was added.
- From any browser on the same Wi-Fi, open `http://weather-display.local/` for the
  display's status, or `http://weather-display.local/screenshot.bmp` for a picture
  of the screen.

## Buttons

Two buttons can be wired to **D2** (A) and **D4** (B), each switch between its pin and GND:

Presses move around, holds do something:

| | Press | Hold (0.6 s) |
|---|---|---|
| **A** | Next page / screen: weather → headlines (pages) → stocks → later → weather. With details open: back to the list | Open the highlighted headline or stock (details); hold again to close |
| **B** | Highlight the next headline or stock (with details open: the next one's details). On the weather: the later forecast | Send the highlighted headline or stock to your phone. On the weather: the status screen |
| **The XIAO's B button** | Same as A press | Same as A hold (3 s: Wi-Fi setup) |

- **Hold A and B together for 3 seconds:** Wi-Fi setup.
- **Send to your phone:** the display can't reach the bot, so the bot asks it every
  10 seconds for anything waiting. Within about 10 seconds the story (with 👍/👎) or the
  stock arrives on Telegram, and the screen says "Sent to your phone".
- Any screen but the weather goes back to the weather after 60 seconds without a press.
- From a browser on the same Wi-Fi: `http://weather-display.local/press?button=a`
  (or `b`, add `&hold=1` for a hold) acts like a press.

If the laptop can't reach the display (the bot says "Display not reachable"), a VPN
may be blocking your local network: allow LAN connections in it, or disconnect it.

## 3D-printed case

A 62.4 × 41.9 × 14.4 mm case with a magnetic charging dock is in the `case` folder:
the files to print, pictures, and the full build guide (measurements, shopping
list, wiring, assembly). Start with [case/README.md](case/README.md).

## Files

- `include/settings.h`: pins, screen orientation and colors, names
- `src/main.cpp`: Wi-Fi, the web server the bot talks to, the clock
- `src/draw.cpp`: the screen layouts and the weather icons
- `src/brief.cpp`: reads the brief the bot sends (JSON)
- `src/feeds.cpp`: reads the headlines and stock prices the bot sends (JSON)
- `case/`: the 3D-printed case and dock (`build_case.py` makes the STL files)
- The bot's side: `display.py` in the bot folder
