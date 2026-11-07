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
- **Headlines:** the top 9 NYT headlines the bot collected (the same ones as its
  hourly posts), 2 or 3 per page. Every 5 minutes they come up by themselves for
  10 seconds a page, then the weather returns. Change this in `include/settings.h`
  (`NEWS_EVERY_MINUTES`, 0 = only with the buttons).

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

| Button | Short press |
|---|---|
| **A** | Next screen: weather → headlines page 1 → page 2 … → weather |
| **B** | Back to the weather. On the weather: the status screen for 10 seconds (Wi-Fi signal, IP, last update from the bot) |
| **The XIAO's B button** | Same as A, so it works before the case buttons are wired |

- **Hold both A and B (or the XIAO's B button) for 3 seconds:** Wi-Fi setup.
- Headlines opened with a button stay until 60 seconds pass without a press.
- From a browser on the same Wi-Fi: `http://weather-display.local/press?button=a`
  (or `b`) acts like a press, and `/show-status` shows the status screen.

If the laptop can't reach the display (the bot says "Display not reachable"), a VPN
may be blocking your local network: allow LAN connections in it, or disconnect it.

## 3D-printed case

A 62.4 × 41.9 × 14.4 mm case with a magnetic charging dock is in the `case` folder:
the files to print, pictures, and the full build guide (measurements, shopping
list, wiring, assembly). Start with [case/README.md](case/README.md).

## Files

- `include/settings.h`: pins, screen orientation and colors, names
- `src/main.cpp`: Wi-Fi, the web server the bot talks to, the clock
- `src/draw.cpp`: the screen layouts (weather, headlines) and the weather icons
- `src/brief.cpp`: reads the brief the bot sends (JSON)
- `src/headlines.cpp`: reads the headlines the bot sends (JSON)
- `case/`: the 3D-printed case and dock (`build_case.py` makes the STL files)
- The bot's side: `display.py` in the bot folder
