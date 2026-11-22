"""Decide whether a backup trigger can reuse a public, fresh Champion edition."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import urllib.request

from prepare_pages import validate

BACKUP = "11 0,2,4,6,8,10,12,14,16,18,20,22 * * *"
PUBLIC_FEED = "https://fabiantoledocuenca.github.io/a11-pronosticos/feed.json"


def should_skip_backup(local, now, fetch_public):
    try:
        generated = validate(local)
        if not timedelta(0) <= now - generated <= timedelta(minutes=16):
            return False
        current = json.loads(local)
        if not any(datetime.fromisoformat(row["kickoff"]) > now + timedelta(minutes=5)
                   for row in current["forecasts"]):
            return False
        return fetch_public() == local
    except (ValueError, TypeError, KeyError, OSError):
        return False


def fetch_public():
    url = f"{PUBLIC_FEED}?backup={datetime.now(timezone.utc).timestamp()}"
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "Sentinel Backup"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(1_000_000)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True, choices=["push", "schedule", "workflow_dispatch"])
    parser.add_argument("--cron", default="")
    parser.add_argument("--feed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    backup = args.event == "schedule" and args.cron == BACKUP
    skip = backup and should_skip_backup(args.feed.read_bytes(), datetime.now(timezone.utc), fetch_public)
    if args.event == "schedule":
        print("Disparo por horario: respaldo." if backup else "Disparo por horario: principal.")
    if skip:
        print("La última edición pública coincide y tiene pronósticos por iniciar. Se omite el cálculo de respaldo.")
    with args.output.open("a", encoding="utf-8") as output:
        output.write(f"calculate={'yes' if args.event != 'push' and not skip else 'no'}\n")
        output.write(f"publish={'no' if skip else 'yes'}\n")


if __name__ == "__main__":
    main()
