"""The weather display: an ESP32-S3 with a 2-inch screen, on the same Wi-Fi.

Its program is in the esp32-display folder. The display runs a tiny web server
and the bot sends it the weather brief every few minutes. Only the bot makes
connections (outgoing), so the computer's firewall needs no changes.

What it shows:
- the morning brief (at your wake-up time) and the afternoon brief (1 PM),
  each for the BRIEF_HOURS hours it covers,
- otherwise "Right now", the same layout refreshed every LIVE_EVERY_MINUTES.
Each brief: now (temperature, feels like, conditions, high/low), jacket / cap /
umbrella (a colored card = bring it), and the next BRIEF_HOURS hours.
"""

import asyncio
import ipaddress
import socket
import time
from datetime import datetime, timedelta

import httpx

from weather import (
    advice_facts,
    clock,
    compare_sources,
    get_weather,
    hour,
    rest_of_day,
    tonight_low,
    uv_level,
    values,
)

# The display's name on your Wi-Fi. If the bot can't find it by name, put the
# IP address its screen shows here instead, e.g. "10.3.171.25". None = no display.
DISPLAY_HOST = "weather-display.local"
DISPLAY_PORT = 80
SEND_EVERY_MINUTES = 2  # how often the bot sends the brief (the display marks it "offline" after 10)
BRIEF_HOURS = 5  # the next hours shown, and how long a morning/afternoon brief stays up
LIVE_EVERY_MINUTES = 30  # between briefs, "Right now" is rebuilt this often
TIMEOUT_SECONDS = 5

TITLES = {"morning": "Morning brief", "afternoon": "Afternoon brief", "now": "Right now"}

# Short names that fit the screen (WMO weather codes, as in weather.WEATHER_CODES)
SHORT_NAMES = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Icy drizzle", 57: "Icy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Icy rain", 67: "Icy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Showers", 81: "Showers", 82: "Downpours", 85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Storm, hail", 99: "Storm, hail",
}
# The station's own words (e.g. "Partly Cloudy") -> icon. The first match wins.
TEXT_ICONS = (
    ("thunder", "storm"), ("hail", "storm"),
    ("snow", "snow"), ("sleet", "snow"), ("pellets", "snow"),
    ("drizzle", "drizzle"), ("rain", "rain"), ("shower", "rain"),
    ("fog", "fog"), ("mist", "fog"), ("haze", "fog"), ("smoke", "fog"), ("dust", "fog"),
    ("partly", "partly"), ("few clouds", "partly"), ("cloud", "cloudy"), ("overcast", "cloudy"),
    ("clear", "clear"), ("fair", "clear"), ("sunny", "clear"),
)
MAX_TEXT_LENGTH = 16  # longer station descriptions are replaced by the short name

_lock = asyncio.Lock()
_live = None  # the latest "Right now" brief
_live_built = None  # ...and when it was built
_online = None  # was the display reachable last time? (None = not tried yet)
_last_ip = None  # its address the last time it worked
_shown_id = None


# =====================================
# WHAT THE DISPLAY SHOWS
# =====================================

def icon_for_code(code):
    if code is None:
        return ""
    if code <= 1:
        return "clear"
    if code == 2:
        return "partly"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 57:
        return "drizzle"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "cloudy"


def icon_for_text(text):
    text = text.lower()
    return next((icon for words, icon in TEXT_ICONS if words in text), None)


def is_daytime(weather, moment):
    """Between sunrise and sunset (for a sun or a moon icon)?"""
    daily = weather["daily"]
    for i, day in enumerate(daily["time"]):
        if day == moment.date().isoformat() and daily["sunrise"][i] and daily["sunset"][i]:
            return (datetime.fromisoformat(daily["sunrise"][i]) <= moment
                    < datetime.fromisoformat(daily["sunset"][i]))
    return 6 <= moment.hour < 18


def whole(value):
    return None if value is None else round(value)


def gear(weather):
    """Jacket, cap, umbrella: [{name, need, answer, note: [two short lines]}], day."""
    facts, day = advice_facts(weather)
    if facts is None:
        return [], day

    coldest, warmest = facts["coldest"], facts["warmest"]
    if not facts["jacket"]:
        jacket = ["or warmer"]
    elif facts["warm_jacket"]:
        jacket = ["warm one!"]
    else:
        jacket = [f"{'by' if coldest[1] > warmest[1] else 'at'} {hour(coldest[1])}"]
    jacket.insert(0, f"Feels {round(coldest[0])}°")

    peak = facts["uv_peak"]
    if facts["cap"]:
        cap = [f"UV {round(peak[0])}", f"at {hour(peak[1])}"]
    else:
        cap = [f"UV max {round(peak[0])}", uv_level(peak[0])]

    wettest = facts["wettest"]
    umbrella = [f"Rain {round(wettest[0])}%", f"at {hour(wettest[1])}" if facts["umbrella"] else "at most"]

    items = []
    for name, need, note in (
        ("Jacket", facts["jacket"], jacket),
        ("Cap", facts["cap"], cap),
        ("Umbrella", facts["umbrella"], umbrella),
    ):
        items.append({"name": name, "need": need, "answer": "YES" if need else "NO", "note": note})
    return items, day


def build_brief(weather, kind, made=None):
    """What the display shows, as a JSON-ready dict. kind: morning, afternoon or now."""
    made = made or datetime.now()
    current = weather["current"]
    now_time = datetime.fromisoformat(current["time"])

    # Now: the station's words if it has short ones, else the forecast's
    code = current.get("weather_code")
    text = current.get("description") or ""
    icon = icon_for_text(text) if text else None
    if not text or len(text) > MAX_TEXT_LENGTH or icon is None:
        text, icon = SHORT_NAMES.get(code, ""), icon_for_code(code)
    text = text[:1].upper() + text[1:]

    items, day = gear(weather)
    rows, _ = rest_of_day(weather)
    temps = values(rows, "temperature_2m")
    low = tonight_low(weather)
    comparison = compare_sources(weather)

    hours = []
    start = now_time.replace(minute=0)
    hourly = weather["hourly"]
    for i, time_text in enumerate(hourly["time"]):
        moment = datetime.fromisoformat(time_text)
        if start < moment <= start + timedelta(hours=BRIEF_HOURS) and hourly["temperature_2m"][i] is not None:
            hours.append({
                "time": hour(moment),
                "temp": round(hourly["temperature_2m"][i]),
                "rain": round(hourly["precipitation_probability"][i] or 0),
                "icon": icon_for_code(hourly["weather_code"][i]),
                "day": is_daytime(weather, moment),
            })

    return {
        "v": 1,
        "id": f"{kind} {made.isoformat(timespec='minutes')}",
        "kind": kind,
        "title": TITLES[kind],
        # The header shows when a brief was made; "Right now" has the clock beside it already
        "made": "" if kind == "now" else clock(made.isoformat(timespec="minutes")),
        "made_at": made.isoformat(timespec="minutes"),
        "now": {
            "temp": whole(current["temperature_2m"]),
            "feels": whole(current["apparent_temperature"]),
            "text": text,
            "icon": icon,
            "day": is_daytime(weather, now_time),
        },
        "high": round(max(temps)[0]) if temps else None,
        "low": round(low[0]) if low else None,
        "for": day,
        "confidence": comparison["level"] if comparison else "",
        "items": items,
        "hours": hours,
        "utc_offset": weather.get("utc_offset_seconds") or 0,
    }


def brief_is_current(brief, now):
    """A morning/afternoon brief stays up for the hours it covers."""
    if not brief or brief.get("v") != 1 or not brief.get("made_at"):
        return False
    made = datetime.fromisoformat(brief["made_at"])
    return made <= now < made + timedelta(hours=BRIEF_HOURS)


async def brief_to_show(stored):
    """The stored morning/afternoon brief while it's current, else "Right now"."""
    global _live, _live_built
    now = datetime.now()
    if brief_is_current(stored, now):
        return stored
    if _live is None or now - _live_built >= timedelta(minutes=LIVE_EVERY_MINUTES):
        weather = await get_weather()
        if weather is not None:
            _live, _live_built = build_brief(weather, "now", now), now
    return _live or stored


# =====================================
# SENDING IT
# =====================================

def is_ip_address(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def display_address():
    """The display's IP address: looked up by name, else the last one that worked."""
    if is_ip_address(DISPLAY_HOST):
        return DISPLAY_HOST
    loop = asyncio.get_running_loop()
    try:
        found = await asyncio.wait_for(
            loop.getaddrinfo(DISPLAY_HOST, 80, family=socket.AF_INET, type=socket.SOCK_STREAM),
            TIMEOUT_SECONDS,
        )
        return found[0][4][0]
    except (OSError, asyncio.TimeoutError):
        return _last_ip


def went_offline(reason):
    global _online
    if _online is not False:
        print(f"Display not reachable at {DISPLAY_HOST} ({reason}). "
              f"Trying again every {SEND_EVERY_MINUTES} minutes.")
    _online = False


async def update(stored_brief):
    """Send the display what it should show now. stored_brief: the latest
    morning/afternoon brief (or None). Skips if an update is already running."""
    global _online, _last_ip, _shown_id
    if DISPLAY_HOST is None or _lock.locked():
        return
    async with _lock:
        ip = await display_address()
        if ip is None:
            went_offline("name not found")
            return
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            if _online is not True:
                # Is it there at all? Asked before downloading any weather for it
                try:
                    (await client.get(f"http://{ip}:{DISPLAY_PORT}/")).raise_for_status()
                except httpx.HTTPError as error:
                    went_offline(type(error).__name__)
                    return
            brief = await brief_to_show(stored_brief)
            if brief is None:
                return
            try:
                response = await client.post(f"http://{ip}:{DISPLAY_PORT}/brief", json={**brief, "sent": int(time.time())})
                response.raise_for_status()
            except httpx.HTTPError as error:
                went_offline(type(error).__name__)
                return
        if _online is not True:
            print(f"Display connected: {DISPLAY_HOST} ({ip})")
        _online, _last_ip = True, ip
        if brief["id"] != _shown_id and brief["kind"] != "now":
            print(f"Display: showing the {brief['title'].lower()} ({brief['made']})")
        _shown_id = brief["id"]
