#!/usr/bin/env python3
"""Weather source experiment: collect every source's forecast once an hour.

Runs anywhere with Python 3, including an old Android phone with Termux.
No extra packages needed. Every hour (at :05) it appends to two CSV files:

  data/forecasts.csv     one row per source per forecast hour (next 24 hours)
  data/observations.csv  what the Phoenix Sky Harbor station actually measured

After a week, run analyze.py to see which source was most accurate.

Usage:
  python collect.py            run forever, once an hour
  python collect.py --once     collect once and stop (for testing)
  python collect.py --data DIR save the CSV files somewhere else
"""

import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

LAT, LON = 33.4148, -111.9093  # Tempe, Arizona
STATION = "KPHX"  # Phoenix Sky Harbor: the NWS station nearest to Tempe
MODELS = {  # Open-Meteo name -> short name
    "ecmwf_ifs025": "ECMWF",  # European model
    "gfs_seamless": "GFS",  # American model
    "icon_seamless": "ICON",  # German model
    "gem_seamless": "GEM",  # Canadian model
}
HOURS_AHEAD = 24  # save forecasts for the next 24 hours
RUN_AT_MINUTE = 5  # collect at :05, after the station's top-of-hour readings
USER_AGENT = "WeatherSourceExperiment/1.0 (personal project)"  # the NWS asks for one

FORECAST_COLUMNS = ["collected_at", "source", "target_time", "lead_hours",
                    "temperature_f", "rain_chance", "wind_mph"]
OBSERVATION_COLUMNS = ["time", "temperature_f", "wind_mph"]


def log(text):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}", flush=True)


def utc_text(moment):
    """All times in the CSV files are UTC, e.g. 2026-11-06T15:00Z."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def get_json(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def append_rows(path, columns, rows):
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


# =====================================
# SOURCES
# =====================================

def model_forecasts(this_hour):
    """The four weather models from Open-Meteo (one request)."""
    data = get_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": LAT, "longitude": LON,
        "models": ",".join(MODELS),
        "hourly": "temperature_2m,precipitation_probability,wind_speed_10m",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        "timezone": "GMT", "forecast_hours": HOURS_AHEAD + 1,
    })
    hourly = data["hourly"]
    rows = []
    for i, text in enumerate(hourly["time"]):
        target = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        for model, name in MODELS.items():
            temperature = (hourly.get(f"temperature_2m_{model}") or [None] * len(hourly["time"]))[i]
            if temperature is None:
                continue
            rows.append(make_row(this_hour, name, target, temperature,
                                 hourly.get(f"precipitation_probability_{model}", [None] * (i + 1))[i],
                                 hourly.get(f"wind_speed_10m_{model}", [None] * (i + 1))[i]))
    return rows


_nws_hourly_url = None


def nws_forecasts(this_hour):
    """The National Weather Service's official hourly forecast."""
    global _nws_hourly_url
    if _nws_hourly_url is None:
        _nws_hourly_url = get_json(f"https://api.weather.gov/points/{LAT},{LON}")["properties"]["forecastHourly"]
    rows = []
    for period in get_json(_nws_hourly_url)["properties"]["periods"]:
        target = datetime.fromisoformat(period["startTime"]).astimezone(timezone.utc)
        if not this_hour <= target <= this_hour + timedelta(hours=HOURS_AHEAD):
            continue
        temperature = period.get("temperature")
        if temperature is None:
            continue
        if period.get("temperatureUnit") == "C":
            temperature = temperature * 9 / 5 + 32
        winds = [int(n) for n in re.findall(r"\d+", period.get("windSpeed") or "")]
        rows.append(make_row(this_hour, "NWS", target, temperature,
                             (period.get("probabilityOfPrecipitation") or {}).get("value"),
                             max(winds) if winds else None))
    return rows


def make_row(this_hour, source, target, temperature, rain, wind):
    return {
        "collected_at": utc_text(this_hour),
        "source": source,
        "target_time": utc_text(target),
        "lead_hours": round((target - this_hour).total_seconds() / 3600),
        "temperature_f": round(temperature, 1),
        "rain_chance": "" if rain is None else round(rain),
        "wind_mph": "" if wind is None else round(wind, 1),
    }


def station_observations():
    """What the station measured over the last 3 hours (a reading every ~5 minutes)."""
    start = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
    data = get_json(f"https://api.weather.gov/stations/{STATION}/observations", {"start": start})
    rows = []
    for feature in data.get("features") or []:
        p = feature["properties"]
        celsius = (p.get("temperature") or {}).get("value")
        if celsius is None:
            continue
        kmh = (p.get("windSpeed") or {}).get("value")
        rows.append({
            "time": utc_text(datetime.fromisoformat(p["timestamp"])),
            "temperature_f": round(celsius * 9 / 5 + 32, 1),
            "wind_mph": "" if kmh is None else round(kmh / 1.609344, 1),
        })
    return rows


# =====================================
# COLLECTING
# =====================================

def collect(data_dir):
    os.makedirs(data_dir, exist_ok=True)
    this_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    forecast_rows = []
    for name, fetch in (("models", model_forecasts), ("NWS", nws_forecasts)):
        try:  # one failing source never stops the others
            rows = fetch(this_hour)
            forecast_rows += rows
            log(f"{name}: {len(rows)} forecast rows")
        except Exception as error:
            log(f"{name} failed: {type(error).__name__}: {error}")
    append_rows(os.path.join(data_dir, "forecasts.csv"), FORECAST_COLUMNS, forecast_rows)

    path = os.path.join(data_dir, "observations.csv")
    try:
        seen = set()
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                seen = {row["time"] for row in csv.DictReader(f)}
        new = [row for row in station_observations() if row["time"] not in seen]
        new.sort(key=lambda row: row["time"])
        append_rows(path, OBSERVATION_COLUMNS, new)
        log(f"station {STATION}: {len(new)} new readings")
    except Exception as error:
        log(f"station failed: {type(error).__name__}: {error}")


def seconds_until_next_run():
    now = datetime.now()
    run = now.replace(minute=RUN_AT_MINUTE, second=0, microsecond=0)
    if run <= now:
        run += timedelta(hours=1)
    return (run - now).total_seconds()


def main():
    args = sys.argv[1:]
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if "--data" in args:
        data_dir = args[args.index("--data") + 1]

    if "--once" in args:
        collect(data_dir)
        return

    log(f"Collecting every hour at :{RUN_AT_MINUTE:02d} into {data_dir}. Ctrl+C to stop.")
    collect(data_dir)  # start right away
    while True:
        time.sleep(seconds_until_next_run())
        try:
            collect(data_dir)
        except Exception as error:  # keep going no matter what
            log(f"collection failed: {type(error).__name__}: {error}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped.")
