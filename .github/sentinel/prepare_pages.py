"""Package the public Pages artifact, excluding engine, state, workflows and git metadata."""
import argparse
import hashlib
import json
import math
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from export_feed import LIMA

FILES = (".nojekyll", "index.html", "app.js", "styles.css", "sentinel.svg",
         "estadio.webp", "cancha.webp", "feed.json")
SCENARIOS = {"Victoria local", "Victoria visitante", "Local no pierde (1X)", "Visitante no pierde (X2)"}


def probability(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def validate(data):
    feed = json.loads(data)
    if set(feed) != {"version", "source", "generatedAt", "date", "forecasts"}:
        raise ValueError("Invalid feed fields")
    if feed["version"] != 1 or feed["source"] != "A12 Champion":
        raise ValueError("Wrong model")
    generated = datetime.fromisoformat(feed["generatedAt"])
    day = datetime.fromisoformat(feed["date"]).date()
    if generated.tzinfo is None or (day - generated.astimezone(LIMA).date()).days not in (0, 1):
        raise ValueError("Invalid generation time")
    forecasts = feed["forecasts"]
    if not isinstance(forecasts, list) or len(forecasts) > 500:
        raise ValueError("Invalid fixture list")
    ids = set()
    for row in forecasts:
        if set(row) != {"id", "home", "away", "league", "scenario", "probability", "lower", "upper", "kickoff"}:
            raise ValueError("Invalid forecast fields")
        if not all(isinstance(row[k], str) and 0 < len(row[k].strip()) <= 128
                   for k in ("id", "home", "away", "league", "scenario")):
            raise ValueError("Invalid labels")
        chance, lower, upper = row["probability"], row["lower"], row["upper"]
        if (row["id"] in ids or row["scenario"] not in SCENARIOS or not probability(chance)
                or chance == 0 or not probability(lower) or not probability(upper)
                or not lower <= chance <= upper):
            raise ValueError("Invalid calibrated distribution for scenario")
        kickoff = datetime.fromisoformat(row["kickoff"])
        if kickoff.tzinfo is None or kickoff.astimezone(LIMA).date() != day or kickoff <= generated + timedelta(minutes=5):
            raise ValueError("Invalid verified kickoff")
        ids.add(row["id"])
    return generated


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--artifact", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    feed = root / "feed.json"
    old = validate(feed.read_bytes())
    if args.candidate.is_file():
        raw = args.candidate.read_bytes()
        created = validate(raw)
        if created > datetime.now(LIMA) + timedelta(minutes=2):
            raise ValueError("Generation time is in the future")
        if created > old:
            # Source file and candidate are ordinary files from this checkout and the runner temp dir.
            # Preserve the previous version when no approved candidate was produced.
            temporary = feed.with_suffix(".json.next")
            temporary.write_bytes(raw)
            temporary.replace(feed)
    args.artifact.mkdir(parents=True, exist_ok=False)
    for name in FILES:
        asset = root / name
        if not asset.is_file() or asset.is_symlink():
            raise ValueError("Expected a regular public asset")
        shutil.copyfile(asset, args.artifact / name)
    digest = hashlib.sha256(feed.read_bytes()).hexdigest()
    with args.output.open("a", encoding="utf-8") as github_output:
        github_output.write(f"sha={digest}\n")
    print("Artefacto público preparado. Sólo pronósticos y archivos de la web.")


if __name__ == "__main__":
    main()
