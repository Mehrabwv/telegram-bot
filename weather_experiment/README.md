# Weather source experiment

Collects the forecast of 5 weather sources every hour for Tempe, AZ, plus what the
Phoenix Sky Harbor station actually measured, then ranks the sources by accuracy.

Sources: NWS (National Weather Service), and the ECMWF, GFS, ICON and GEM models
(via Open-Meteo). No API keys, no extra Python packages.

## Run it on an Android phone (Termux)

1. Install **Termux** from F-Droid (the Play Store version is outdated).
2. In Termux:
   ```
   pkg install python
   termux-setup-storage
   ```
3. Copy this folder to the phone (e.g. into `Download/weather_experiment`), then:
   ```
   cd ~/storage/downloads/weather_experiment
   termux-wake-lock
   nohup python collect.py > collect.log 2>&1 &
   ```
4. In Android settings, turn off battery optimization for Termux, and keep the
   phone plugged in and on Wi-Fi.

Check that it's alive: `tail collect.log` (a few lines every hour at :05).
Stop it: `pkill -f collect.py`.

## After a week

```
python analyze.py
```

Prints the ranking and saves `data/results.csv`. Give it at least 3 days of data
before trusting it.

## Files

- `data/forecasts.csv`: one row per source per forecast hour (times in UTC)
- `data/observations.csv`: the station's readings, about one every 5 minutes
