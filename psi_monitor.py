"""
Singapore PSI monitor - raises an alarm when the 24-hr PSI reaches a threshold (default 200),
and can send the alarm to WhatsApp.

Data sources (data.gov.sg, no API key needed):
  - Live readings:  https://api-open.data.gov.sg/v2/real-time/api/psi
                    24-hr PSI per region (north/south/east/west/central), updated hourly.
  - Daily forecast: dataset d_c2c7fa70f297cdaee13afbe53a900eb9
                    24-hr PSI forecast band (lower/upper) for the day - used as an early warning.

WhatsApp alerts (optional) go through CallMeBot (https://www.callmebot.com, free). The phone that
receives alerts activates once to get an API key, which goes in psi_alerts.json next to this script:
  {"whatsapp": {"phone": "+6598299142", "apikey": "123456"}}
or in the PSI_WHATSAPP_PHONE / PSI_WHATSAPP_APIKEY environment variables.

Usage:
  python psi_monitor.py                  # check every 15 min, alarm at PSI >= 200
  python psi_monitor.py --threshold 150  # custom threshold
  python psi_monitor.py --once           # single check, then exit
  python psi_monitor.py --test-alarm     # fire the alarm once to check sound/popup
  python psi_monitor.py --test-whatsapp  # send a test WhatsApp message
"""

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REALTIME_URL = "https://api-open.data.gov.sg/v2/real-time/api/psi"
FORECAST_DATASET_ID = "d_c2c7fa70f297cdaee13afbe53a900eb9"
FORECAST_URL = "https://data.gov.sg/api/action/datastore_search?resource_id=" + FORECAST_DATASET_ID
LIVE_SITE = "https://gslmar.github.io/sg-psi-monitor/"
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
CONFIG_FILE = Path(__file__).with_name("psi_alerts.json")

# NEA's PSI bands
BANDS = [(50, "Good"), (100, "Moderate"), (200, "Unhealthy"), (300, "Very unhealthy")]


def band(psi):
    for upper, name in BANDS:
        if psi <= upper:
            return name
    return "Hazardous"


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def sg_time(iso):
    """'2026-10-20T13:00:00+08:00' -> '1pm, 20 Oct'"""
    t = datetime.fromisoformat(iso)
    return f"{t.hour % 12 or 12}{'am' if t.hour < 12 else 'pm'}, {t.day} {t:%b}"


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
    # data.gov.sg has used both *_band and *_bound field names
    lower = latest.get("forecast_lower_bound", latest.get("forecast_lower_band"))
    upper = latest.get("forecast_upper_bound", latest.get("forecast_upper_band"))
    return latest["forecast_date"], int(float(lower)), int(float(upper))


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


class WhatsApp:
    """Sends WhatsApp messages through CallMeBot."""

    def __init__(self, phone, apikey):
        self.phone = re.sub(r"[^\d+]", "", phone)
        self.apikey = apikey

    @classmethod
    def from_settings(cls, args):
        cfg = {}
        if CONFIG_FILE.exists():
            try:
                cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("whatsapp", {})
            except (ValueError, AttributeError) as e:
                log(f"Could not read {CONFIG_FILE.name}: {e}")
        phone = args.whatsapp_phone or os.environ.get("PSI_WHATSAPP_PHONE") or cfg.get("phone") or ""
        apikey = args.whatsapp_apikey or os.environ.get("PSI_WHATSAPP_APIKEY") or cfg.get("apikey") or ""
        if not apikey.strip() or "PASTE" in apikey.upper():
            apikey = ""
        if phone and apikey:
            return cls(phone, apikey)
        if phone:
            log(f"WhatsApp alerts off: no CallMeBot API key yet for {phone} (see {CONFIG_FILE.name}).")
        return None

    def send(self, text):
        """Send a message; returns True on success. Never raises, so a failed send can't stop the monitor."""
        url = CALLMEBOT_URL + "?" + urllib.parse.urlencode({"phone": self.phone, "text": text, "apikey": self.apikey})
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "psi-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", "replace")
        except Exception as e:
            log(f"WhatsApp send failed: {e}")
            return False
        reply = " ".join(re.sub(r"<[^>]+>", " ", body).split())  # CallMeBot replies with a small HTML page
        if "queued" in reply.lower() or "sent" in reply.lower():
            log(f"WhatsApp message sent to {self.phone}.")
            return True
        log(f"WhatsApp send failed: {reply[:200]}")
        return False


class Monitor:
    def __init__(self, threshold, whatsapp=None):
        self.threshold = threshold
        self.whatsapp = whatsapp
        self.last_alarmed_reading = None   # alarm once per hourly reading, not every poll
        self.last_warned_forecast = None   # warn once per forecast date
        self.was_above = False

    def notify(self, text):
        if self.whatsapp:
            self.whatsapp.send(text)

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
                self.notify(
                    f"⚠️ *PSI ALERT: {worst} ({band(worst)})*\n"
                    f"24-hr PSI has reached {self.threshold}+ in {regions} ({sg_time(timestamp)}).\n"
                    "Reduce prolonged outdoor exertion and consider an N95 mask outdoors.\n"
                    f"Live map: {LIVE_SITE}"
                )
        elif self.was_above:
            log(f"All regions back below {self.threshold}.")
            self.notify(
                f"✅ *PSI all clear*: every region is back below {self.threshold} "
                f"(highest {worst}, {worst_region.title()}, {sg_time(timestamp)}).\n{LIVE_SITE}"
            )
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
            self.notify(
                f"🌫️ *PSI forecast warning for {date}*\n"
                f"NEA forecasts 24-hr PSI of {lower}-{upper}, which may reach {self.threshold}+.\n{LIVE_SITE}"
            )


def main():
    p = argparse.ArgumentParser(description="Monitor Singapore PSI and alarm at a threshold.")
    p.add_argument("--threshold", type=int, default=200, help="alarm when 24-hr PSI >= this (default 200)")
    p.add_argument("--interval", type=int, default=15, help="minutes between checks (default 15)")
    p.add_argument("--once", action="store_true", help="check once and exit")
    p.add_argument("--test-alarm", action="store_true", help="fire a test alarm and exit")
    p.add_argument("--test-whatsapp", action="store_true", help="send a test WhatsApp message and exit")
    p.add_argument("--whatsapp-phone", help="WhatsApp number for alerts, e.g. +6598299142")
    p.add_argument("--whatsapp-apikey", help="CallMeBot API key for that number")
    args = p.parse_args()

    if args.test_alarm:
        alarm("PSI ALERT (test)", "This is a test of the PSI monitor alarm.")
        time.sleep(4)  # let the beeps finish
        input("Press Enter to exit...")
        return

    whatsapp = WhatsApp.from_settings(args)
    if args.test_whatsapp:
        if not whatsapp:
            log(f"Set the phone and CallMeBot API key in {CONFIG_FILE.name} first.")
            sys.exit(1)
        ok = whatsapp.send(f"✅ PSI monitor test: WhatsApp alerts are working.\n{LIVE_SITE}")
        sys.exit(0 if ok else 1)

    monitor = Monitor(args.threshold, whatsapp)
    log(f"Monitoring Singapore PSI; alarm at >= {args.threshold}. Ctrl+C to stop.")
    log(f"WhatsApp alerts: {'on, to ' + whatsapp.phone if whatsapp else 'off'}")
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
