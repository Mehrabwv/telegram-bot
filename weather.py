"""Weather from several sources, blended into one forecast.

Sources (all free, no API keys):
- The US National Weather Service (api.weather.gov): the official forecast from
  the local office, plus real measurements from the nearest weather station.
- Four weather models through Open-Meteo (open-meteo.com): the European (ECMWF),
  American (GFS), German (ICON) and Canadian (GEM) models.

How the forecast is made, hour by hour:
1. Every source gives its own forecast for the same hours.
2. Temperature, rain chance and wind are compared across the sources.
3. A source far from the others (an outlier) is left out.
4. The rest are averaged. Each source's weight comes from its track record:
   every hour the bot saves the forecasts and later checks them against what
   the weather station actually measured. Until a day has been checked, the
   NWS simply counts double.
5. A confidence note says how closely the sources agree.
"Now" uses the nearest station's measurement when there is a recent one.
"""

import asyncio
import html
import json
import math
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

# Used whenever no other place is named. Change it if you move.
HOME = {"name": "Tempe, Arizona", "lat": 33.4148, "lon": -111.9093}

# Jacket / cap / umbrella advice (change these to taste)
JACKET_FEELS_LIKE_F = 60  # jacket if it feels this cold or colder
WARM_JACKET_FEELS_LIKE_F = 45  # ...a warm one at this point
CAP_UV_INDEX = 6  # cap if the UV index reaches this (6 and up is "high")
UMBRELLA_RAIN_CHANCE = 40  # umbrella if the chance of rain reaches this (%)
DAY_ENDS_HOUR = 21  # advice covers the rest of the day until 9 PM

# The weather models, by their Open-Meteo name
MODELS = {
    "ecmwf_ifs025": "ECMWF",
    "gfs_seamless": "GFS",
    "icon_seamless": "ICON",
    "gem_seamless": "GEM",
}
SOURCE_LEGEND = (
    "NWS = US National Weather Service. ECMWF, GFS, ICON and GEM = the European, "
    "American, German and Canadian weather models."
)
# How much each source counts in the average until their accuracy is known. The
# NWS counts double: it is the official forecast from the local office.
SOURCE_WEIGHTS = {"NWS": 2.0, "ECMWF": 1.0, "GFS": 1.0, "ICON": 1.0, "GEM": 1.0}

# Learning each source's accuracy from the home weather station
ACCURACY_FILE = Path(__file__).resolve().parent / "weather_accuracy.json"
CHECK_HOURS_AHEAD = 12  # forecasts for the next 12 hours are saved and checked later
LEARN_AFTER_HOURS = 24  # learned weights are used once a day of hours is checked
ACCURACY_WINDOW_HOURS = 14 * 24  # accuracy follows about the last two weeks
LEARNED_NEAR_KM = 30  # learned weights are only used this close to home
MATCH_MINUTES = 15  # measurements within 15 minutes of an hour count for that hour
# A source this far from the middle value of all sources is left out as an outlier
OUTLIER_LIMITS = {"temperature_2m": 6, "precipitation_probability": 25, "wind_speed_10m": 8}
# Confidence: how far a source may be from the blend for "high" and for
# "medium" confidence (°F, % rain chance, mph). Any further is "low".
AGREEMENT_LIMITS = {"high": (2, 4), "low": (2, 4), "rain": (5, 15), "wind": (3, 6)}

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
NWS_URL = "https://api.weather.gov"
# The NWS asks every app to name itself in the User-Agent
NWS_HEADERS = {
    "User-Agent": "TelegramNewsBot/1.0 (personal weather bot)",
    "Accept": "application/geo+json",
}
NWS_TIMEOUT_SECONDS = 12  # the forecast goes ahead without the NWS if it's slower
STATION_MAX_KM = 25  # use a station's measurement only if it's this close
MEASUREMENT_MAX_AGE_MINUTES = 90

HOURLY_FIELDS = (
    "temperature_2m", "apparent_temperature", "relative_humidity_2m",
    "precipitation_probability", "precipitation", "weather_code", "uv_index",
    "wind_speed_10m", "wind_gusts_10m",
)
DAILY_FIELDS = (
    "weather_code", "temperature_2m_max", "temperature_2m_min",
    "precipitation_probability_max", "sunrise", "sunset", "uv_index_max",
)

# Weather codes used by Open-Meteo (WMO standard)
WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}

# So "Portland, ME" finds Maine and not Oregon
US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
    "ca": "california", "co": "colorado", "ct": "connecticut", "de": "delaware",
    "dc": "district of columbia", "fl": "florida", "ga": "georgia", "hi": "hawaii",
    "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
    "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada",
    "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york",
    "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah",
    "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming",
}


# =====================================
# SMALL HELPERS
# =====================================

def describe(code):
    return WEATHER_CODES.get(code, "unknown conditions")


def temp(value):
    return "n/a" if value is None else f"{round(value)}°F"


def percent(value):
    return "n/a" if value is None else f"{round(value)}%"


def mph(value):
    return "n/a" if value is None else f"{round(value)} mph"


def clock(iso_time, minutes=True):
    """'2026-11-05T15:15' -> '3:15 PM' (or '3 PM' with minutes=False)."""
    moment = datetime.fromisoformat(iso_time)
    return moment.strftime("%I:%M %p" if minutes else "%I %p").lstrip("0")


def first_known(*values):
    """The first value that isn't None."""
    return next((value for value in values if value is not None), None)


def distance_km(lat1, lon1, lat2, lon2):
    """Distance between two points on the Earth's surface."""
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def short_station_name(name):
    """'Phoenix, Phoenix Sky Harbor International Airport' -> 'Phoenix Sky Harbor'"""
    name = name.split(",")[-1].strip()
    for ending in (" International Airport", " Municipal Airport", " Regional Airport", " Airport"):
        if name.endswith(ending):
            return name[: -len(ending)]
    return name


# =====================================
# FINDING A PLACE
# =====================================

# Words after "in"/"for" that are not places ("rain in the afternoon")
NOT_PLACES = {
    "the", "a", "an", "my", "our", "your", "this", "that", "next", "last", "general",
    "home", "work", "school", "campus", "class", "celsius", "fahrenheit", "total",
    "today", "tonight", "tomorrow", "morning", "afternoon", "evening", "night",
    "week", "weekend", "hour", "hours", "minutes", "now", "advance",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}
# Words that end a place name ("in chicago tomorrow")
PLACE_ENDS = {
    "in", "for", "on", "at", "and", "or", "with", "by", "around", "after", "before",
    "until", "during", "over", "today", "tonight", "tomorrow", "now", "right",
    "this", "next", "later", "currently", "please", "like", "is", "be", "going", "will",
}


def place_in_message(text):
    """The place named in a message like 'weather in Chicago tomorrow', or None."""
    words = text.replace("?", " ").replace("!", " ").split()
    for i, word in enumerate(words):
        if word.lower() not in ("in", "for"):
            continue
        place = []
        for next_word in words[i + 1:]:
            bare = next_word.strip(".,'").lower()
            if bare in PLACE_ENDS or not bare.replace("-", "").replace("'", "").isalpha():
                break
            place.append(next_word)
        name = " ".join(place).strip(" ,.'-")
        if name and name.split()[0].strip(",.").lower() not in NOT_PLACES:
            return name
    return None


async def search_places(name):
    if not name:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                GEOCODING_URL,
                params={"name": name, "count": 10, "language": "en", "format": "json"},
            )
            response.raise_for_status()
            return response.json().get("results") or []
    except (httpx.HTTPError, ValueError) as error:
        print(f"Place lookup failed: {type(error).__name__}")
        return []


def place_matches(result, wanted):
    return (
        wanted == (result.get("country_code") or "").lower()
        or (result.get("admin1") or "").lower().startswith(wanted)
        or (result.get("country") or "").lower().startswith(wanted)
    )


async def find_place(query):
    """Look up 'Chicago', 'Portland, ME', 'tempe az' or 'Paris, France'.

    Returns {"name", "lat", "lon"}, or None if it can't be found.
    """
    name, _, qualifier = query.partition(",")
    name, qualifier = name.strip(), qualifier.strip().lower()

    results = await search_places(name)
    if not results and " " in name and not qualifier:
        name, qualifier = name.rsplit(" ", 1)  # "tempe az" -> "tempe" in "az"
        qualifier = qualifier.lower()
        results = await search_places(name)
    if not results:
        return None

    if qualifier:
        wanted = US_STATES.get(qualifier, qualifier)
        results = [r for r in results if place_matches(r, wanted)] or results

    best = results[0]
    region = best.get("admin1") if best.get("country_code") == "US" else best.get("country")
    label = ", ".join(part for part in (best.get("name"), region) if part)
    return {"name": label, "lat": best["latitude"], "lon": best["longitude"]}


# =====================================
# BLENDING SOURCES
# =====================================

def blend(readings, limit=None, weights=None):
    """Weighted average of [(source, value)], leaving out outliers.

    With 3+ sources, a value more than `limit` away from the middle (median)
    value is an outlier and is left out, as long as 2+ sources remain.
    weights: {source: weight}, SOURCE_WEIGHTS if not given.
    Returns (value, outliers); value is None when there are no readings.
    """
    weights = weights or SOURCE_WEIGHTS
    readings = [(source, value) for source, value in readings if value is not None]
    if not readings:
        return None, []
    kept, outliers = readings, []
    if limit is not None and len(readings) >= 3:
        middle = statistics.median(value for _, value in readings)
        close = [(source, value) for source, value in readings if abs(value - middle) <= limit]
        if len(close) >= 2:
            outliers = [source for source, value in readings if abs(value - middle) > limit]
            kept = close
    total = sum(weights.get(source, 1.0) for source, _ in kept)
    return sum(weights.get(source, 1.0) * value for source, value in kept) / total, outliers


def middle_code(readings, weights=None):
    """The middle weather code of [(source, code)], weighted like the averages.

    Weather codes run from clear (0) to stormy (99), so the middle one is the
    sources' middle opinion: clear, mainly clear, partly cloudy, overcast ->
    mainly clear, instead of whichever extreme one source picked.
    """
    weights = weights or SOURCE_WEIGHTS
    codes = sorted((code, weights.get(source, 1.0)) for source, code in readings if code is not None)
    half = sum(code_weight for _, code_weight in codes) / 2
    total = 0
    for code, code_weight in codes:
        total += code_weight
        if total >= half:
            return code
    return None


def feels_like_adjustment(sources, moment, weights):
    """How much warmer or colder it feels than the thermometer says (from the models)."""
    readings = []
    for name, series in sources.items():
        hour = series.get(moment) or {}
        if hour.get("apparent_temperature") is not None and hour.get("temperature_2m") is not None:
            readings.append((name, hour["apparent_temperature"] - hour["temperature_2m"]))
    return blend(readings, weights=weights)[0]


def whole_days(series):
    """Daily high, low and rain chance from an hourly forecast, for whole days only."""
    by_day = {}
    for moment, hour in (series or {}).items():
        by_day.setdefault(moment.date().isoformat(), []).append(hour)
    days = {}
    for day, hours in by_day.items():
        temps = [h["temperature_2m"] for h in hours if h.get("temperature_2m") is not None]
        rains = [h["precipitation_probability"] for h in hours
                 if h.get("precipitation_probability") is not None]
        if len(hours) >= 24 and temps:
            days[day] = {"temperature_2m_max": max(temps), "temperature_2m_min": min(temps)}
            if rains:
                days[day]["precipitation_probability_max"] = max(rains)
    return days


def local_time(moment, offset):
    """An exact (timezone-aware) time -> the place's local time, like Open-Meteo uses."""
    return (moment.astimezone(timezone.utc) + offset).replace(tzinfo=None)


def combine(place, data, nws):
    """Blend every source's forecast into the one the rest of the bot uses."""
    offset = timedelta(seconds=data.get("utc_offset_seconds") or 0)
    hourly, daily = data["hourly"], data["daily"]
    times = [datetime.fromisoformat(t) for t in hourly["time"]]

    # Each source's own forecast: {source: {time: {field: value}}}
    sources = {}
    if nws and nws["hourly"]:
        sources["NWS"] = {local_time(hour["start"], offset): hour for hour in nws["hourly"]}
    for model, name in MODELS.items():
        series = {}
        for i, moment in enumerate(times):
            series[moment] = {
                field: (hourly.get(f"{field}_{model}") or [None] * len(times))[i]
                for field in HOURLY_FIELDS
            }
        if any(hour["temperature_2m"] is not None for hour in series.values()):
            sources[name] = series

    # How much each source counts: its track record near home, else the defaults
    weights, weights_kind = weights_for(place, sources)

    # Hour by hour: the weighted average of the sources, without outliers
    blended = {key: [] for key in ("time", *HOURLY_FIELDS)}
    for moment in times:
        blended["time"].append(moment.isoformat(timespec="minutes"))
        for field in HOURLY_FIELDS:
            readings = [(name, series.get(moment, {}).get(field)) for name, series in sources.items()]
            if field == "weather_code":
                blended[field].append(middle_code(readings, weights))
            elif field != "apparent_temperature":
                blended[field].append(blend(readings, OUTLIER_LIMITS.get(field), weights)[0])
        # Feels-like: the models' adjustment, added to the blended temperature
        temperature = blended["temperature_2m"][-1]
        adjustment = feels_like_adjustment(sources, moment, weights)
        if temperature is not None and adjustment is not None:
            blended["apparent_temperature"].append(temperature + adjustment)
        else:
            blended["apparent_temperature"].append(temperature)

    # Right now: a real measurement nearby if there is one, else the blend
    now_time = datetime.fromisoformat(data["current"]["time"])

    def now_value(field):
        """The blended value right now, between this hour and the next."""
        values = blended[field]
        first = values[0] if values else None
        second = values[1] if len(values) > 1 else first
        if first is None or second is None:
            return first
        return first + (second - first) * now_time.minute / 60

    measured = nws["measurement"] if nws else None
    if measured:
        measured["time"] = local_time(measured["time"], offset)
    temperature = first_known(measured and measured["temperature"], now_value("temperature_2m"))
    model_temperature, model_feels = now_value("temperature_2m"), now_value("apparent_temperature")
    adjustment = (model_feels - model_temperature
                  if model_temperature is not None and model_feels is not None else 0)
    current = {
        "time": data["current"]["time"],
        "temperature_2m": temperature,
        "apparent_temperature": temperature + adjustment if temperature is not None else None,
        "relative_humidity_2m": first_known(measured and measured["humidity"],
                                            now_value("relative_humidity_2m")),
        "wind_speed_10m": first_known(measured and measured["wind"], now_value("wind_speed_10m")),
        "wind_gusts_10m": first_known(measured and measured["gust"], now_value("wind_gusts_10m")),
        "precipitation": blended["precipitation"][0] if blended["precipitation"] else None,
        "weather_code": blended["weather_code"][0] if blended["weather_code"] else None,
        "description": measured["description"] if measured else None,
    }

    # Day by day: the models' daily numbers, plus the NWS for the days it fully covers
    nws_days = whole_days(sources.get("NWS"))
    blended_daily = {"time": daily["time"], **{field: [] for field in DAILY_FIELDS}}
    for i, day in enumerate(daily["time"]):
        for field in DAILY_FIELDS:
            readings = [
                (name, (daily.get(f"{field}_{model}") or [None] * len(daily["time"]))[i])
                for model, name in MODELS.items()
            ]
            if field in nws_days.get(day, {}):
                readings.append(("NWS", nws_days[day][field]))
            if field == "weather_code":
                value = middle_code(readings, weights)
            elif field in ("sunrise", "sunset"):
                value = first_known(*(reading for _, reading in readings))
            else:
                limit_field = {"temperature_2m_max": "temperature_2m",
                               "temperature_2m_min": "temperature_2m",
                               "precipitation_probability_max": "precipitation_probability"}
                value = blend(readings, OUTLIER_LIMITS.get(limit_field.get(field, field)), weights)[0]
            blended_daily[field].append(value)

    return {
        "place": place["name"],
        "current": current,
        "hourly": blended,
        "daily": blended_daily,
        "sources": sources,  # each source's own hourly forecast
        "measured": measured,  # the nearest station's latest measurement, or None
        "station": nws["station"] if nws else None,  # {"id", "name"} of that station
        "weights": weights,  # how much each source counted
        "weights_kind": weights_kind,  # "learned" or "default"
        "near_home": is_near_home(place),
        "utc_offset_seconds": data.get("utc_offset_seconds") or 0,
    }


# =====================================
# DOWNLOADING
# =====================================

async def fetch_models(client, place):
    """All four weather models in one Open-Meteo request, or None if it fails."""
    try:
        response = await client.get(FORECAST_URL, params={
            "latitude": place["lat"],
            "longitude": place["lon"],
            "models": ",".join(MODELS),
            "current": "temperature_2m",  # for the place's local time
            "hourly": ",".join(HOURLY_FIELDS),
            "daily": ",".join(DAILY_FIELDS),
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "auto",
            "forecast_days": 7,
            "forecast_hours": 24,
        })
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as error:
        print(f"Weather models failed: {type(error).__name__}")
        return None


_nws_points = {}  # place -> NWS forecast link and nearest station (these don't change)


async def nws_point(client, place):
    """The NWS hourly forecast link and nearest station, or None outside the US."""
    key = (round(place["lat"], 4), round(place["lon"], 4))
    if key in _nws_points:
        return _nws_points[key]

    response = await client.get(f"{NWS_URL}/points/{key[0]},{key[1]}")
    if response.status_code in (400, 404):
        _nws_points[key] = None  # the NWS only covers the US
        return None
    response.raise_for_status()
    properties = response.json()["properties"]

    station = None
    response = await client.get(properties["observationStations"], params={"limit": 1})
    features = []
    if response.status_code == 200:
        features = response.json().get("features") or []
    if features:
        lon, lat = features[0]["geometry"]["coordinates"][:2]
        if distance_km(place["lat"], place["lon"], lat, lon) <= STATION_MAX_KM:
            station = {
                "id": features[0]["properties"]["stationIdentifier"],
                "name": short_station_name(features[0]["properties"]["name"]),
            }
    _nws_points[key] = {"hourly_url": properties["forecastHourly"], "station": station}
    return _nws_points[key]


async def nws_hourly(client, url):
    """The NWS hourly forecast as a list of hours (empty if it fails)."""
    try:
        response = await client.get(url)
        response.raise_for_status()
        hours = []
        for period in response.json()["properties"]["periods"]:
            temperature = period.get("temperature")
            if temperature is not None and period.get("temperatureUnit") == "C":
                temperature = temperature * 9 / 5 + 32
            winds = [int(number) for number in re.findall(r"\d+", period.get("windSpeed") or "")]
            hours.append({
                "start": datetime.fromisoformat(period["startTime"]),
                "temperature_2m": temperature,
                "precipitation_probability": (period.get("probabilityOfPrecipitation") or {}).get("value"),
                "wind_speed_10m": max(winds) if winds else None,
            })
        return hours
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        print(f"NWS forecast failed: {type(error).__name__}")
        return []


async def nws_measurement(client, station):
    """The latest measurement at a weather station, or None if there's no recent one."""
    if station is None:
        return None
    try:
        response = await client.get(f"{NWS_URL}/stations/{station['id']}/observations/latest")
        response.raise_for_status()
        properties = response.json()["properties"]

        def reading(key):
            return (properties.get(key) or {}).get("value")

        stamp = datetime.fromisoformat(properties["timestamp"])
        age = datetime.now(timezone.utc) - stamp
        if reading("temperature") is None or age > timedelta(minutes=MEASUREMENT_MAX_AGE_MINUTES):
            return None
        wind, gust = reading("windSpeed"), reading("windGust")
        return {
            "station": station["name"],
            "time": stamp,
            "temperature": reading("temperature") * 9 / 5 + 32,  # °C -> °F
            "humidity": reading("relativeHumidity"),
            "wind": wind / 1.609344 if wind is not None else None,  # km/h -> mph
            "gust": gust / 1.609344 if gust is not None else None,
            "description": (properties.get("textDescription") or "").strip().lower() or None,
        }
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        print(f"Weather station measurement failed: {type(error).__name__}")
        return None


async def fetch_nws(client, place):
    """The NWS forecast and nearest measurement, or None (outside the US, or failed)."""
    try:
        point = await nws_point(client, place)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        print(f"NWS lookup failed: {type(error).__name__}")
        return None
    if point is None:
        return None
    hourly, measurement = await asyncio.gather(
        nws_hourly(client, point["hourly_url"]), nws_measurement(client, point["station"])
    )
    return {"hourly": hourly, "measurement": measurement, "station": point["station"]}


async def fetch_nws_in_time(client, place):
    try:
        return await asyncio.wait_for(fetch_nws(client, place), NWS_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        print("NWS was too slow; using the weather models only.")
        return None


async def get_weather(place=None):
    """The blended forecast for a place (default HOME): now, the next 24 hours
    and 7 days, plus each source's own forecast. None if the models can't be
    reached. Times are the place's own local time."""
    place = place or HOME
    async with httpx.AsyncClient(timeout=15) as models_client, httpx.AsyncClient(
        timeout=10, headers=NWS_HEADERS, follow_redirects=True
    ) as nws_client:
        data, nws = await asyncio.gather(
            fetch_models(models_client, place), fetch_nws_in_time(nws_client, place)
        )
    if data is None:
        return None
    try:
        return combine(place, data, nws)
    except (KeyError, TypeError, ValueError, IndexError) as error:
        print(f"Blending the weather sources failed: {type(error).__name__}")
        return None


# =====================================
# LEARNING EACH SOURCE'S ACCURACY
# =====================================

_accuracy = None  # loaded from ACCURACY_FILE the first time it's needed


def load_accuracy():
    """{"pending": {hour: {source: [forecasts]}},
        "scores": {source: {"error", "bias", "hours"}}}  (°F, average over ~2 weeks)"""
    global _accuracy
    if _accuracy is None:
        try:
            with open(ACCURACY_FILE, "r", encoding="utf-8") as f:
                _accuracy = json.load(f)
        except (FileNotFoundError, ValueError):
            _accuracy = {}
        _accuracy.setdefault("pending", {})
        _accuracy.setdefault("scores", {})
    return _accuracy


def save_accuracy():
    with open(ACCURACY_FILE, "w", encoding="utf-8") as f:
        json.dump(load_accuracy(), f, indent=1)


def is_near_home(place):
    return distance_km(place["lat"], place["lon"], HOME["lat"], HOME["lon"]) <= LEARNED_NEAR_KM


def learned_weights():
    """{source: weight} from each source's average error, or None until enough
    hours are checked. A source twice as far off counts a quarter as much."""
    scores = load_accuracy()["scores"]
    ready = {name: score for name, score in scores.items()
             if name != "Blend" and score["hours"] >= LEARN_AFTER_HOURS}
    if len(ready) < 2:
        return None
    # The station reads whole °C (about ±1°F), so errors under 0.5°F aren't real differences
    return {name: 1 / max(score["error"], 0.5) ** 2 for name, score in ready.items()}


def weights_for(place, sources):
    """(weights, 'learned' or 'default') for blending these sources at a place."""
    learned = learned_weights()
    if learned and is_near_home(place) and all(name in learned for name in sources):
        return learned, "learned"
    return SOURCE_WEIGHTS, "default"


def update_score(scores, name, error, bias):
    """A running average of a source's error that slowly forgets old hours."""
    score = scores.setdefault(name, {"error": error, "bias": bias, "hours": 0})
    score["hours"] += 1
    count = min(score["hours"], ACCURACY_WINDOW_HOURS)
    score["error"] += (error - score["error"]) / count
    score["bias"] += (bias - score["bias"]) / count


def record_forecasts(weather):
    """Save every source's temperature forecast for the next hours, to check later."""
    pending = load_accuracy()["pending"]
    this_hour = datetime.fromisoformat(weather["current"]["time"]).replace(minute=0)
    blended = dict(zip(weather["hourly"]["time"], weather["hourly"]["temperature_2m"]))
    for hours_ahead in range(1, CHECK_HOURS_AHEAD + 1):
        target = this_hour + timedelta(hours=hours_ahead)
        key = target.isoformat(timespec="minutes")
        forecasts = pending.setdefault(key, {})
        for name, series in weather["sources"].items():
            value = (series.get(target) or {}).get("temperature_2m")
            if value is not None:
                forecasts.setdefault(name, []).append(round(value, 1))
        if blended.get(key) is not None:
            forecasts.setdefault("Blend", []).append(round(blended[key], 1))


def check_forecasts(measurements, now):
    """Score the saved forecasts for every past hour that has measurements.

    measurements: [(local time, °F)]. Returns how many hours were checked.
    """
    data = load_accuracy()
    checked = 0
    for key in sorted(data["pending"]):
        target = datetime.fromisoformat(key)
        if target > now:
            continue  # still to come
        near = [value for moment, value in measurements
                if abs((moment - target).total_seconds()) <= MATCH_MINUTES * 60]
        if near:
            measured = sum(near) / len(near)
            for name, forecasts in data["pending"][key].items():
                errors = [forecast - measured for forecast in forecasts]
                update_score(data["scores"], name,
                             sum(abs(e) for e in errors) / len(errors), sum(errors) / len(errors))
            checked += 1
            del data["pending"][key]
        elif now - target > timedelta(hours=30):
            del data["pending"][key]  # no measurement ever came for that hour
    return checked


async def station_history(client, station_id, hours=30):
    """[(exact time, °F)] measured at a station over the last `hours`."""
    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    response = await client.get(
        f"{NWS_URL}/stations/{station_id}/observations", params={"start": start}
    )
    response.raise_for_status()
    measurements = []
    for feature in response.json().get("features") or []:
        properties = feature["properties"]
        value = (properties.get("temperature") or {}).get("value")
        if value is not None:
            moment = datetime.fromisoformat(properties["timestamp"])
            measurements.append((moment, value * 9 / 5 + 32))  # °C -> °F
    return measurements


async def update_accuracy():
    """Run every hour: check earlier forecasts against the home station's
    measurements, then save the current forecasts to check later."""
    weather = await get_weather(HOME)
    if weather is None:
        print("Weather accuracy: skipped (no forecast right now).")
        return
    checked = 0
    station = weather.get("station")
    if station:
        try:
            async with httpx.AsyncClient(
                timeout=15, headers=NWS_HEADERS, follow_redirects=True
            ) as client:
                history = await station_history(client, station["id"])
            offset = timedelta(seconds=weather["utc_offset_seconds"])
            measurements = [(local_time(moment, offset), value) for moment, value in history]
            now = datetime.fromisoformat(weather["current"]["time"])
            checked = check_forecasts(measurements, now)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            print(f"Weather accuracy check failed: {type(error).__name__}")
    record_forecasts(weather)
    save_accuracy()
    print(f"Weather accuracy: checked {checked} hour(s). {accuracy_summary()}")


def accuracy_summary():
    """'Average error: NWS 1.2°F, GFS 1.6°F, ... (30 hours checked).'"""
    scores = load_accuracy()["scores"]
    if not scores:
        return "No forecasts checked yet."
    hours = max(score["hours"] for score in scores.values())
    ranked = sorted(scores.items(), key=lambda item: item[1]["error"])
    errors = ", ".join(f"{name} {score['error']:.1f}°F" for name, score in ranked)
    return f"Average error: {errors} ({hours} hours checked)."


def accuracy_lines(weather):
    """Each source's track record at the home station, for the /weather report."""
    if not weather.get("near_home"):
        return []
    station = (weather.get("station") or {}).get("name") or "the home weather station"
    scores = load_accuracy()["scores"]
    if not scores:
        return [f"<b>Accuracy:</b> learning has started. Every hour the forecasts are "
                f"checked against {html.escape(station)}."]

    parts = []
    for name, score in sorted(scores.items(), key=lambda item: item[1]["error"]):
        text = f"{name} {score['error']:.1f}°F"
        if score["hours"] >= LEARN_AFTER_HOURS:  # a day of checks before calling it a habit
            if score["bias"] <= -1.5:
                text += " (runs cold)"
            elif score["bias"] >= 1.5:
                text += " (runs warm)"
        parts.append(text)
    hours = max(score["hours"] for score in scores.values())
    if weather.get("weights_kind") == "learned":
        status = f"The weights above are learned from {hours} checked hours."
    else:
        status = (f"The NWS counts double until {LEARN_AFTER_HOURS} hours are checked "
                  f"({hours} so far); then the weights follow these errors.")
    return [
        f"<b>Accuracy</b> (average error vs {html.escape(station)}): {html.escape(', '.join(parts))}",
        html.escape(status),
    ]


# =====================================
# HOW WELL DO THE SOURCES AGREE?
# =====================================

def compare_sources(weather):
    """Compare the sources over the rest of the day (the hours the advice uses).

    Returns None if fewer than two sources cover those hours. Otherwise:
    {"day", "sources": {name: {"high", "low", "rain", "wind"}},
     "blend", "range", "outliers", "levels": per measurement, "level"},
    where a level is "high", "medium" or "low".
    """
    rows, day = rest_of_day(weather)
    times = [row["time"] for row in rows]
    per_source = {}
    for name, series in (weather.get("sources") or {}).items():
        hours = [series[moment] for moment in times if moment in series]
        temps = [h["temperature_2m"] for h in hours if h.get("temperature_2m") is not None]
        if not times or len(temps) < len(times):
            continue  # this source doesn't cover all of those hours
        rains = [h["precipitation_probability"] for h in hours
                 if h.get("precipitation_probability") is not None]
        winds = [h["wind_speed_10m"] for h in hours if h.get("wind_speed_10m") is not None]
        per_source[name] = {
            "high": max(temps),
            "low": min(temps),
            "rain": max(rains) if rains else None,
            "wind": max(winds) if winds else None,
        }
    if len(per_source) < 2:
        return None

    fields = {"high": "temperature_2m", "low": "temperature_2m",
              "rain": "precipitation_probability", "wind": "wind_speed_10m"}
    levels = ["high", "medium", "low"]
    result = {"day": day, "sources": per_source, "blend": {}, "range": {}, "outliers": {}, "levels": {}}
    worst = 0
    for metric, field in fields.items():
        readings = [(name, numbers[metric]) for name, numbers in per_source.items()
                    if numbers[metric] is not None]
        if len(readings) < 2:
            continue
        value, outliers = blend(readings, OUTLIER_LIMITS[field], weather.get("weights"))
        kept = [number for name, number in readings if name not in outliers]
        furthest = max(abs(number - value) for number in kept)
        high_limit, medium_limit = AGREEMENT_LIMITS[metric]
        level = 0 if furthest <= high_limit else 1 if furthest <= medium_limit else 2
        if outliers:
            level = max(level, 1)  # most agree, but not all
        result["blend"][metric] = value
        result["range"][metric] = (min(kept), max(kept))
        result["outliers"][metric] = outliers
        result["levels"][metric] = levels[level]
        worst = max(worst, level)
    result["level"] = levels[worst]
    return result


UNITS = {"high": "°F", "low": "°F", "rain": "%", "wind": " mph"}
METRIC_NAMES = {"high": "high", "low": "low", "rain": "rain chance", "wind": "wind"}


def span(low, high, unit):
    low, high = round(low), round(high)
    return f"{low}{unit}" if low == high else f"{low}–{high}{unit}"


def confidence_note(comparison):
    """e.g. 'high. All 5 sources agree: high 79–81°F, low 55–57°F, rain 0–5%'"""
    ranges = comparison["range"]
    parts = [f"{metric} {span(*ranges[metric], UNITS[metric])}"
             for metric in ("high", "low", "rain") if metric in ranges]
    if "wind" in ranges and comparison["levels"].get("wind") != "high":
        parts.append(f"wind {span(*ranges['wind'], UNITS['wind'])}")

    count = len(comparison["sources"])
    if comparison["level"] == "high":
        text = f"All {count} sources agree: " + ", ".join(parts)
    else:
        text = f"{count} sources: " + ", ".join(parts)
    left_out = [
        f"{name}'s {METRIC_NAMES[metric]} ({round(comparison['sources'][name][metric])}{UNITS[metric]})"
        for metric, names in comparison["outliers"].items() for name in names
    ]
    if left_out:
        text += ". Left out as an outlier: " + ", ".join(left_out)
    return f"{comparison['level']}. {text}"


def source_lines(comparison, weights=None):
    """One line per source: 'NWS: 80/64°F, 2% rain, 5 mph · counts 33%' (Telegram
    HTML), where 'counts' is the source's share of the blend."""
    weights = weights or SOURCE_WEIGHTS
    total = sum(weights.get(name, 1.0) for name in comparison["sources"])
    lines = []
    for name, numbers in comparison["sources"].items():
        outlier = any(name in names for names in comparison["outliers"].values())
        share = round(100 * weights.get(name, 1.0) / total)
        lines.append(
            f"{name}: {round(numbers['high'])}/{round(numbers['low'])}°F, "
            f"{percent(numbers['rain'])} rain, {mph(numbers['wind'])} · counts {share}%"
            + (" <i>(outlier)</i>" if outlier else "")
        )
    return lines


# =====================================
# JACKET / CAP / UMBRELLA
# =====================================

def hour(moment):
    """datetime -> '3 PM'"""
    return moment.strftime("%I %p").lstrip("0")


def hours_between(weather, start, end):
    """The hourly forecast from start to end (datetimes), one dict per hour."""
    hourly = weather["hourly"]
    rows = []
    for i, time in enumerate(hourly["time"]):
        moment = datetime.fromisoformat(time)
        if start <= moment <= end:
            row = {key: values[i] for key, values in hourly.items()}
            row["time"] = moment
            rows.append(row)
    return rows


def rest_of_day(weather):
    """(hours, 'today') from now until 9 PM. After 9 PM: tomorrow 7 AM-9 PM."""
    now = datetime.fromisoformat(weather["current"]["time"])
    if now.hour < DAY_ENDS_HOUR:
        end = now.replace(hour=DAY_ENDS_HOUR, minute=0)
        return hours_between(weather, now.replace(minute=0), end), "today"
    tomorrow = now + timedelta(days=1)
    start = tomorrow.replace(hour=7, minute=0)
    return hours_between(weather, start, tomorrow.replace(hour=DAY_ENDS_HOUR, minute=0)), "tomorrow"


def uv_level(uv):
    if uv < 3:
        return "low"
    if uv < 6:
        return "moderate"
    if uv < 8:
        return "high"
    return "very high" if uv < 11 else "extreme"


def values(rows, key):
    """[(value, time)] for one hourly measurement, skipping missing values."""
    return [(row[key], row["time"]) for row in rows if row.get(key) is not None]


def advice_facts(weather):
    """The numbers behind the jacket/cap/umbrella advice, for the rest of the day.

    Returns (facts, 'today' or 'tomorrow'); facts is None if there is no data.
    Each (value, time) pair is the earliest hour when there is a tie.
    """
    rows, day = rest_of_day(weather)
    feels = values(rows, "apparent_temperature")
    uvs = values(rows, "uv_index")
    rains = values(rows, "precipitation_probability")
    if not (feels and uvs and rains):
        return None, day

    coldest = min(feels, key=lambda pair: pair[0])
    peak = max(uvs, key=lambda pair: pair[0])
    wettest = max(rains, key=lambda pair: pair[0])
    rainfall = sum(row.get("precipitation") or 0 for row in rows)
    return {
        "coldest": coldest,  # (feels-like °F, time)
        "warmest": max(feels, key=lambda pair: pair[0]),
        "jacket": coldest[0] <= JACKET_FEELS_LIKE_F,
        "warm_jacket": coldest[0] <= WARM_JACKET_FEELS_LIKE_F,
        "uv_peak": peak,  # (UV index, time)
        "cap": peak[0] >= CAP_UV_INDEX,
        "wettest": wettest,  # (chance of rain %, time)
        "umbrella": wettest[0] >= UMBRELLA_RAIN_CHANCE or rainfall >= 0.05,
    }, day


def advice(weather):
    """Do you need a jacket, cap or umbrella for the rest of the day?

    Returns ([(emoji, item, answer)], 'today' or 'tomorrow'); empty if no data.
    """
    facts, day = advice_facts(weather)
    if facts is None:
        return [], day

    coldest, warmest = facts["coldest"], facts["warmest"]
    if not facts["jacket"]:
        jacket = f"No. Feels like {temp(coldest[0])} or warmer"
    else:
        yes = "Yes, a warm one" if facts["warm_jacket"] else "Yes"
        if warmest[0] <= JACKET_FEELS_LIKE_F:
            jacket = f"{yes}. Feels like {temp(coldest[0])} at {hour(coldest[1])}, no warmer than {temp(warmest[0])}"
        elif coldest[1] > warmest[1]:
            jacket = f"{yes}, for later. It will feel like {temp(coldest[0])} by {hour(coldest[1])}"
        else:
            jacket = (
                f"{yes}. Feels like {temp(coldest[0])} at {hour(coldest[1])}, "
                f"warming to {temp(warmest[0])} by {hour(warmest[1])}"
            )

    peak = facts["uv_peak"]
    if facts["cap"]:
        cap = f"Yes. UV index reaches {round(peak[0])} ({uv_level(peak[0])}) around {hour(peak[1])}"
    else:
        cap = f"No. UV index peaks at {round(peak[0])} ({uv_level(peak[0])})"

    wettest = facts["wettest"]
    if facts["umbrella"]:
        umbrella = f"Yes. {percent(wettest[0])} chance of rain around {hour(wettest[1])}"
    else:
        umbrella = f"No. At most {percent(wettest[0])} chance of rain"

    return [("🧥", "Jacket", jacket), ("🧢", "Cap", cap), ("☂️", "Umbrella", umbrella)], day


def advice_lines(items):
    return [f"{emoji} <b>{item}:</b> {html.escape(answer)}" for emoji, item, answer in items]


# =====================================
# MESSAGES
# =====================================

def now_text(weather, with_time=False):
    """'70°F, clear, feels like 68°F (measured at Phoenix Sky Harbor)'"""
    now = weather["current"]
    text = (f"{temp(now['temperature_2m'])}, {now.get('description') or describe(now['weather_code'])}, "
            f"feels like {temp(now['apparent_temperature'])}")
    measured = weather.get("measured")
    if measured:
        when = f", {measured['time']:%I:%M %p}".replace(" 0", " ") if with_time else ""
        text += f" (measured at {measured['station']}{when})"
    return text


def confidence_line(weather):
    comparison = compare_sources(weather)
    if comparison is None:
        return None
    return f"📊 <b>Confidence:</b> {html.escape(confidence_note(comparison))}"


def format_weather_update(weather, kind):
    """The 'morning' (wake-up) or 'afternoon' weather update, with advice."""
    rows, day = rest_of_day(weather)
    place = html.escape(weather["place"])

    if kind == "morning":
        lines = [f"<b>Good morning!</b> Here's the weather in {place}"]
    else:
        lines = [f"<b>Afternoon weather</b> ({place})"]
    lines.append(f"<b>Now:</b> {html.escape(now_text(weather))}")

    temps = values(rows, "temperature_2m")
    rains = [chance for chance, _ in values(rows, "precipitation_probability")]
    if temps:
        high = max(temps, key=lambda pair: pair[0])
        text = f"high {temp(high[0])} around {hour(high[1])}"
        if kind == "afternoon":
            text += f", {temp(temps[-1][0])} by {hour(temps[-1][1])}"
        if rains:
            text += f", {percent(max(rains))} chance of rain"
        if day == "tomorrow":
            label = "Tomorrow"
        else:
            label = "Today" if kind == "morning" else "Rest of today"
        lines.append(f"<b>{label}:</b> {text}")

    low = tonight_low(weather)
    if low:
        lines.append(f"<b>Tonight:</b> down to {temp(low[0])} by {clock(low[1], minutes=False)}")

    items, _ = advice(weather)
    if items:
        lines += [""] + advice_lines(items)
    confidence = confidence_line(weather)
    if confidence:
        lines += ["", confidence]
    return "\n".join(lines)


def format_weather_brief(weather):
    """Short weather section for the hourly post (Telegram HTML)."""
    daily = weather["daily"]
    # In the evening, tomorrow's forecast is more useful than today's
    evening = datetime.fromisoformat(weather["current"]["time"]).hour >= 18
    day = 1 if evening and len(daily["time"]) > 1 else 0
    return "\n".join([
        f"<b>Weather</b> ({html.escape(weather['place'])})",
        f"<b>Now:</b> {html.escape(now_text(weather))}",
        f"<b>{'Tomorrow' if day else 'Today'}:</b> high {temp(daily['temperature_2m_max'][day])}, "
        f"low {temp(daily['temperature_2m_min'][day])}, "
        f"{percent(daily['precipitation_probability_max'][day])} chance of rain",
    ])


def format_weather_report(weather):
    """Full weather report for /weather (Telegram HTML)."""
    now, hourly, daily = weather["current"], weather["hourly"], weather["daily"]
    lines = [
        f"<b>Weather in {html.escape(weather['place'])}</b> ({clock(now['time'])})",
        f"<b>Now:</b> {html.escape(now_text(weather, with_time=True))}",
        f"Wind {mph(now['wind_speed_10m'])}, humidity {percent(now['relative_humidity_2m'])}",
        f"Sunrise {clock(daily['sunrise'][0])}, sunset {clock(daily['sunset'][0])}",
    ]

    items, day = advice(weather)
    if items:
        lines += ["", f"<b>What to bring {day}</b>"] + advice_lines(items)
    confidence = confidence_line(weather)
    if confidence:
        lines.append(confidence)

    lines += ["", "<b>Next hours</b>"]
    hours = []
    # The first hour is the current one, which "Now" already covers
    for time, value, rain in list(zip(
        hourly["time"], hourly["temperature_2m"], hourly["precipitation_probability"]
    ))[1:7]:
        text = f"{clock(time, minutes=False)} {temp(value)}"
        if rain and rain >= 20:
            text += f" ({percent(rain)} rain)"
        hours.append(text)
    lines.append(" · ".join(hours) or "n/a")

    lines += ["", "<b>Next days</b>"]
    for i, day_name in enumerate(daily["time"][:5]):
        name = "Today" if i == 0 else datetime.fromisoformat(day_name).strftime("%a")
        lines.append(
            f"<b>{name}:</b> {temp(daily['temperature_2m_max'][i])} / "
            f"{temp(daily['temperature_2m_min'][i])}, {describe(daily['weather_code'][i])}, "
            f"{percent(daily['precipitation_probability_max'][i])} rain"
        )

    comparison = compare_sources(weather)
    if comparison:
        window = "rest of today" if comparison["day"] == "today" else "tomorrow"
        lines += ["", f"<b>Sources</b> ({window}: high/low, rain, wind, share of the blend)"]
        lines += source_lines(comparison, weather.get("weights"))
        lines += accuracy_lines(weather)
        lines.append(f"<i>{html.escape(SOURCE_LEGEND)}</i>")
    return "\n".join(lines)


def tonight_low(weather):
    """(temperature, time) of the coldest hour tonight, from the hourly forecast.

    A day's low usually comes around sunrise, so 'tonight' runs until 9 AM.
    Before 5 AM, 'tonight' is the night that is still going on.
    """
    now = datetime.fromisoformat(weather["current"]["time"])
    if now.hour < 5:
        start, end = now, now.replace(hour=9, minute=0)
    else:
        start = max(now, now.replace(hour=18, minute=0))
        end = (now + timedelta(days=1)).replace(hour=9, minute=0)

    hourly = weather["hourly"]
    tonight = [
        (value, time)
        for time, value in zip(hourly["time"], hourly["temperature_2m"])
        if value is not None and start <= datetime.fromisoformat(time) <= end
    ]
    return min(tonight) if tonight else None


def weather_facts(weather):
    """The weather data as plain text, for the AI to answer questions from."""
    now, daily = weather["current"], weather["daily"]
    lines = [
        f"Place: {weather['place']}. Local time: {now['time'].replace('T', ' ')}.",
        f"Now: {now_text(weather, with_time=True)}, humidity "
        f"{percent(now['relative_humidity_2m'])}, wind {mph(now['wind_speed_10m'])} "
        f"(gusts {mph(now.get('wind_gusts_10m'))}), precipitation {now.get('precipitation')} in.",
    ]
    low = tonight_low(weather)
    if low:
        lines.append(f"Tonight's low: {temp(low[0])} around {clock(low[1], minutes=False)}.")
    lines += [
        "Note: a day's high and low cover the whole calendar day, and the low "
        "usually happens around sunrise.",
        "Next hours:",
    ]
    for row in hours_between(weather, datetime.min, datetime.max):
        lines.append(
            f"- {row['time']:%a} {hour(row['time'])}: {temp(row['temperature_2m'])}, "
            f"feels like {temp(row.get('apparent_temperature'))}, {describe(row['weather_code'])}, "
            f"{percent(row['precipitation_probability'])} chance of rain, UV index {row.get('uv_index')}"
        )

    lines.append("Daily forecast:")
    for i, day in enumerate(daily["time"]):
        lines.append(
            f"- {datetime.fromisoformat(day).strftime('%A %B %d')}: "
            f"high {temp(daily['temperature_2m_max'][i])}, low {temp(daily['temperature_2m_min'][i])}, "
            f"{describe(daily['weather_code'][i])}, "
            f"{percent(daily['precipitation_probability_max'][i])} chance of rain, "
            f"sunrise {clock(daily['sunrise'][i])}, sunset {clock(daily['sunset'][i])}, "
            f"UV index {daily['uv_index_max'][i]}"
        )

    items, day = advice(weather)
    if items:
        lines.append(f"Jacket/cap/umbrella advice for {day}, from the bot's own rules (use it if asked):")
        lines += [f"- {item}: {answer}" for _, item, answer in items]

    comparison = compare_sources(weather)
    if comparison:
        names = ", ".join(comparison["sources"])
        if weather.get("weights_kind") == "learned":
            how = "each source counts according to its measured track record"
        elif "NWS" in comparison["sources"]:
            how = "the NWS counts double"
        else:
            how = "the sources count equally"
        if weather.get("near_home") and weather.get("weights_kind") != "learned":
            how += " (each source's accuracy is still being learned)"
        lines.append(
            f"How this forecast was made: a weighted average of {len(comparison['sources'])} "
            f"sources ({names}); {how}, and outliers are left out. {SOURCE_LEGEND}"
        )
        lines.append(f"Each source for {comparison['day']} until 9 PM ('counts' = its share of the average):")
        lines += [f"- {line.replace('<i>', '').replace('</i>', '')}"
                  for line in source_lines(comparison, weather.get("weights"))]
        lines.append(f"Confidence: {confidence_note(comparison)}")
    if weather.get("near_home") and load_accuracy()["scores"]:
        station = (weather.get("station") or {}).get("name") or "the home weather station"
        lines.append(f"Track record against {station}: {accuracy_summary()}")
    return "\n".join(lines)
