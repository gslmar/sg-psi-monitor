# Singapore PSI Monitor

Live haze monitor for Singapore that sounds an alarm when the 24-hr PSI reaches 200 (or a level you choose).

**Live site:** https://gslmar.github.io/sg-psi-monitor/

- Highest 24-hr PSI and readings for all five NEA regions, refreshed every 5 minutes
- Haze map with each zone's PSI reading; click a reading for PM2.5, PM10, O₃, NO₂, SO₂ and CO
- 24-hour trend chart and NEA's daily PSI forecast
- iPhone-style alarm, flashing banner and desktop notification when any region crosses the threshold

Click **Arm alarm** after opening the page (browsers only play sound after a click), and keep the tab open.

## Data

From [data.gov.sg](https://data.gov.sg) (no API key needed):

- Live readings: `https://api-open.data.gov.sg/v2/real-time/api/psi`
- Daily forecast: dataset `d_c2c7fa70f297cdaee13afbe53a900eb9`

Map tiles © OpenStreetMap contributors.

## Command-line version

`psi_monitor.py` does the same check from a terminal (Python 3, standard library only):

```
python psi_monitor.py                  # check every 15 min, alarm at PSI >= 200
python psi_monitor.py --threshold 150
python psi_monitor.py --test-alarm
python psi_monitor.py --test-whatsapp
```

### WhatsApp alerts

The script can also send alerts to WhatsApp through [CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/) (free): a PSI alert for each new hourly reading at or above the threshold, an all-clear when every region drops back below it, and a forecast warning.

1. From the phone that should receive alerts, follow CallMeBot's instructions to get an API key (you message their number once).
2. Create `psi_alerts.json` next to the script (it's in `.gitignore`, so it's never committed):
   ```json
   {"whatsapp": {"phone": "+65XXXXXXXX", "apikey": "your-key"}}
   ```
3. Run `python psi_monitor.py --test-whatsapp`, then `python psi_monitor.py`.

Alerts only go out while the script is running.
