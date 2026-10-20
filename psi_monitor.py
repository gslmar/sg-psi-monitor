"""
Singapore PSI monitor - raises an alarm when the 24-hr PSI reaches a threshold (default 200).

Data sources (data.gov.sg, no API key needed):
  - Live readings:  https://api-open.data.gov.sg/v2/real-time/api/psi
                    24-hr PSI per region (north/south/east/west/central), updated hourly.
  - Daily forecast: dataset d_c2c7fa70f297cdaee13afbe53a900eb9
                    24-hr PSI forecast band (lower/upper) for the day - used as an early warning.

Usage:
  python psi_monitor.py                  # check every 15 min, alarm at PSI >= 200
  python psi_monitor.py --threshold 150  # custom threshold
  python psi_monitor.py --once           # single check, then exit
  python psi_monitor.py --test-alarm     # fire the alarm once to check sound/popup
"""

import argparse
import json
import sys
import threading
import time
import urllib.request
from datetime import datetime

REALTIME_URL = "https://api-open.data.gov.sg/v2/real-time/api/psi"
FORECAST_DATASET_ID = "d_c2c7fa70f297cdaee13afbe53a900eb9"
FORECAST_URL = "https://data.gov.sg/api/action/datastore_search?resource_id=" + FORECAST_DATASET_ID

# NEA's PSI bands
BANDS = [(50, "Good"), (100, "Moderate"), (200, "Unhealthy"), (300, "Very unhealthy")]


def band(psi):
    for upper, name in BANDS:
        if psi <= upper:
            return name
    return "Hazardous"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "psi-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_live_psi():
    """Return (reading timestamp, {region: 24-hr PSI})."""
    data = get_json(REALTIME_URL)
    if data.get("code") != 0:
        raise RuntimeError(data.get("errorMsg") or "unexpected response")
    item = data["data"]["items"][0]
    return item["timestamp"], item["readings"]["psi_twenty_four_hourly"]


def fetch_forecast():
    """Return (forecast date, lower band, upper band), or None if no forecast is published."""
    data = get_json(FORECAST_URL)
    records = data.get("result", {}).get("records", [])
    if not records:
        return None
    latest = max(records, key=lambda r: r["forecast_date"])
    return latest["forecast_date"], int(latest["forecast_lower_band"]), int(latest["forecast_upper_band"])


def alarm(title, message):
    """Console banner + beeps + popup (popup and sound on Windows only)."""
    print("\n" + "!" * 60)
    print(f"  {title}")
    print(f"  {message}")
    print("!" * 60 + "\n", flush=True)

    if sys.platform == "win32":
        import ctypes
        import winsound

        def beep():
            for _ in range(5):
                winsound.Beep(1500, 400)
                time.sleep(0.15)

        # MB_ICONWARNING | MB_SYSTEMMODAL (stays on top). Threads so the monitor keeps running.
        threading.Thread(target=beep, daemon=True).start()
        threading.Thread(
            target=ctypes.windll.user32.MessageBoxW, args=(None, message, title, 0x30 | 0x1000), daemon=True
        ).start()
    else:
        print("\a", end="", flush=True)


class Monitor:
    def __init__(self, threshold):
        self.threshold = threshold
        self.last_alarmed_reading = None   # alarm once per hourly reading, not every poll
        self.last_warned_forecast = None   # warn once per forecast date
        self.was_above = False

    def check(self):
        try:
            self.check_live()
        except Exception as e:
            log(f"Live PSI fetch failed: {e}")
        try:
            self.check_forecast()
        except Exception as e:
            log(f"Forecast fetch failed: {e}")

    def check_live(self):
        timestamp, readings = fetch_live_psi()
        worst_region = max(readings, key=readings.get)
        worst = readings[worst_region]
        summary = ", ".join(f"{r} {v}" for r, v in sorted(readings.items()))
        log(f"24-hr PSI ({timestamp}): {summary} | max {worst} ({band(worst)})")

        above = {r: v for r, v in readings.items() if v >= self.threshold}
        if above:
            if timestamp != self.last_alarmed_reading:
                self.last_alarmed_reading = timestamp
                regions = ", ".join(f"{r.title()} {v}" for r, v in sorted(above.items(), key=lambda x: -x[1]))
                alarm(
                    f"PSI ALERT: {worst} ({band(worst)})",
                    f"24-hr PSI has reached {self.threshold}+ in: {regions}.\n"
                    "Reduce prolonged outdoor exertion; consider an N95 mask outdoors.",
                )
        elif self.was_above:
            log(f"All regions back below {self.threshold}.")
        self.was_above = bool(above)

    def check_forecast(self):
        fc = fetch_forecast()
        if fc is None:
            return
        date, lower, upper = fc
        log(f"Forecast for {date}: 24-hr PSI {lower}-{upper}")
        if upper >= self.threshold and date != self.last_warned_forecast:
            self.last_warned_forecast = date
            alarm(
                f"PSI FORECAST WARNING for {date}",
                f"NEA forecasts 24-hr PSI of {lower}-{upper}, which may reach {self.threshold}+ today.",
            )


def main():
    p = argparse.ArgumentParser(description="Monitor Singapore PSI and alarm at a threshold.")
    p.add_argument("--threshold", type=int, default=200, help="alarm when 24-hr PSI >= this (default 200)")
    p.add_argument("--interval", type=int, default=15, help="minutes between checks (default 15)")
    p.add_argument("--once", action="store_true", help="check once and exit")
    p.add_argument("--test-alarm", action="store_true", help="fire a test alarm and exit")
    args = p.parse_args()

    if args.test_alarm:
        alarm("PSI ALERT (test)", "This is a test of the PSI monitor alarm.")
        time.sleep(4)  # let the beeps finish
        input("Press Enter to exit...")
        return

    monitor = Monitor(args.threshold)
    log(f"Monitoring Singapore PSI; alarm at >= {args.threshold}. Ctrl+C to stop.")
    while True:
        monitor.check()
        if args.once:
            time.sleep(4)
            return
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped.")
