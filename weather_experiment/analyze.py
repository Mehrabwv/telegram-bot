#!/usr/bin/env python3
"""Which weather source was most accurate? Reads the CSV files collect.py wrote.

Every saved forecast is compared with what the station actually measured at
that hour (the average of its readings within 15 minutes of the hour).

Usage:
  python analyze.py            uses the data folder next to this script
  python analyze.py --data DIR

Prints a ranking and writes data/results.csv.
"""

import csv
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

MATCH_MINUTES = 15  # readings this close to the hour count as "measured at that hour"
MIN_HOURS = 72  # fewer checked hours than this (3 days) and the ranking is a guess
TIE_F = 0.3  # sources closer than this (°F of average error) are practically tied
LEAD_GROUPS = [(1, 3), (4, 6), (7, 12), (13, 24)]  # hours ahead
AVERAGE = "Average of all"


def parse(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%MZ")


def read_csv(path):
    if not os.path.exists(path):
        sys.exit(f"Missing {path}. Run collect.py first.")
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def summary(errors):
    """errors: list of forecast - measured. -> (count, average error, bias, rmse)"""
    if not errors:
        return 0, None, None, None
    n = len(errors)
    return (n, sum(abs(e) for e in errors) / n, sum(errors) / n,
            math.sqrt(sum(e * e for e in errors) / n))


def main():
    args = sys.argv[1:]
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if "--data" in args:
        data_dir = args[args.index("--data") + 1]

    readings = [(parse(r["time"]), float(r["temperature_f"]),
                 float(r["wind_mph"]) if r["wind_mph"] else None)
                for r in read_csv(os.path.join(data_dir, "observations.csv"))]
    forecasts = read_csv(os.path.join(data_dir, "forecasts.csv"))

    measured_cache = {}

    def measured(target):
        """(temperature, wind) measured at an hour, or None if there are no readings."""
        if target not in measured_cache:
            near = [(t, w) for moment, t, w in readings
                    if abs((moment - target).total_seconds()) <= MATCH_MINUTES * 60]
            if not near:
                measured_cache[target] = None
            else:
                winds = [w for _, w in near if w is not None]
                measured_cache[target] = (sum(t for t, _ in near) / len(near),
                                          sum(winds) / len(winds) if winds else None)
        return measured_cache[target]

    temp_errors = defaultdict(list)  # source -> [error]
    wind_errors = defaultdict(list)
    lead_errors = defaultdict(lambda: defaultdict(list))  # source -> lead group -> [error]
    checked_hours = set()
    same_forecast = defaultdict(dict)  # (collected_at, target) -> {source: temp}

    for row in forecasts:
        lead = int(row["lead_hours"])
        if lead < 1:
            continue  # the current hour is "now", not a forecast
        target = parse(row["target_time"])
        actual = measured(target)
        if actual is None:
            continue
        temperature = float(row["temperature_f"])
        same_forecast[(row["collected_at"], target)][row["source"]] = (temperature, lead)
        add_error(row["source"], temperature - actual[0], lead, temp_errors, lead_errors)
        if row["wind_mph"] and actual[1] is not None:
            wind_errors[row["source"]].append(float(row["wind_mph"]) - actual[1])
        checked_hours.add(target)

    # For comparison: what if you averaged every source?
    for (_, target), by_source in same_forecast.items():
        if len(by_source) >= 3:
            temps = [t for t, _ in by_source.values()]
            lead = next(iter(by_source.values()))[1]
            add_error(AVERAGE, sum(temps) / len(temps) - measured(target)[0], lead,
                      temp_errors, lead_errors)

    if not temp_errors:
        sys.exit("No forecasts can be checked yet: let collect.py run for a few hours.")

    first, last = min(checked_hours), max(checked_hours)
    print(f"\nChecked {len(checked_hours)} hours ({first:%b %d %H:%M} to {last:%b %d %H:%M} UTC) "
          f"against the station's measurements.\n")

    header = f"{'Source':16}{'Avg error':>10}{'Bias':>8}{'RMSE':>7}{'Wind err':>10}" + "".join(
        f"{f'{a}-{b}h':>8}" for a, b in LEAD_GROUPS)
    print(header)
    print("-" * len(header))
    results = []
    for source, errors in sorted(temp_errors.items(), key=lambda item: summary(item[1])[1]):
        n, mae, bias, rmse = summary(errors)
        wind = summary(wind_errors.get(source, []))[1]
        by_lead = [summary(lead_errors[source][group])[1] for group in LEAD_GROUPS]
        print(f"{source:16}{mae:>8.1f}°F{bias:>+7.1f}°{rmse:>7.1f}"
              f"{(f'{wind:.1f} mph' if wind is not None else 'n/a'):>10}"
              + "".join(f"{(f'{m:.1f}' if m is not None else '-'):>8}" for m in by_lead))
        results.append({"source": source, "forecasts_checked": n, "avg_error_f": round(mae, 2),
                        "bias_f": round(bias, 2), "rmse_f": round(rmse, 2),
                        "wind_avg_error_mph": "" if wind is None else round(wind, 2),
                        **{f"avg_error_{a}_{b}h": "" if m is None else round(m, 2)
                           for (a, b), m in zip(LEAD_GROUPS, by_lead)}})

    print("\nAvg error = how far off the temperature forecast was on average (lower is better).")
    print("Bias = + forecasts too warm, - too cold. The last columns split the error by how far ahead.")

    singles = [r for r in results if r["source"] != AVERAGE]
    best, runner_up = singles[0], singles[1] if len(singles) > 1 else None
    print(f"\nMost accurate single source: {best['source']} "
          f"(off by {best['avg_error_f']:.1f}°F on average).")
    if runner_up and runner_up["avg_error_f"] - best["avg_error_f"] < TIE_F:
        print(f"  {runner_up['source']} is practically tied ({runner_up['avg_error_f']:.1f}°F): "
              f"either is fine, so pick whichever is cheaper to call.")
    average = next((r for r in results if r["source"] == AVERAGE), None)
    if average:
        gap = best["avg_error_f"] - average["avg_error_f"]
        if gap > 0:
            print(f"  Averaging all sources would be {gap:.1f}°F more accurate, "
                  "at the cost of more API calls.")
        else:
            print("  It even beats averaging all sources, so one source loses nothing.")
    if len(checked_hours) < MIN_HOURS:
        print(f"\nNote: only {len(checked_hours)} hours checked. Wait for at least "
              f"{MIN_HOURS} (3 days) before trusting this ranking.")

    path = os.path.join(data_dir, "results.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(f"\nSaved {path}")


def add_error(source, error, lead, temp_errors, lead_errors):
    temp_errors[source].append(error)
    for low, high in LEAD_GROUPS:
        if low <= lead <= high:
            lead_errors[source][(low, high)].append(error)


if __name__ == "__main__":
    main()
