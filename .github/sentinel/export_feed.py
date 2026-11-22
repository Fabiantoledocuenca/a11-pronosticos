"""Project a local A12 cache to the website's public forecast format."""

import argparse
import hashlib
import json
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path


LIMA = timezone(timedelta(hours=-5))
STATUSES = {"ACTUAR", "ESPERAR", "INVESTIGAR_MAS", "ABSTENERSE"}


def clean_text(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def fixture_key(league, home, away):
    return tuple(unicodedata.normalize("NFC", item).casefold() for item in (league, home, away))


def probability(value, *, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not 0 <= value <= 1:
        return None
    if value == 0 and not allow_zero:
        return None
    return value


def load_kickoffs(verified_fixtures, target_date):
    if verified_fixtures is None:
        return {}
    if not isinstance(verified_fixtures, dict) or verified_fixtures.get("source") != "ESPN":
        raise ValueError("El archivo de horarios debe identificar a ESPN como fuente.")
    if verified_fixtures.get("date") != target_date.isoformat():
        raise ValueError("La fecha de los horarios verificados debe coincidir con la fecha del caché.")
    fixtures = verified_fixtures.get("fixtures")
    if not isinstance(fixtures, list):
        raise ValueError("El archivo de horarios debe incluir una lista fixtures.")

    kickoffs = {}
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise ValueError("Se encontró un horario sin estructura de partido.")
        league, home, away, event_id = (
            clean_text(fixture.get(field)) for field in ("league", "home", "away", "id")
        )
        if not all((league, home, away, event_id)):
            raise ValueError("Cada horario verificado requiere liga, equipos e ID de ESPN.")
        kickoff = datetime.fromisoformat(clean_text(fixture.get("kickoff")))
        if kickoff.tzinfo is None or kickoff.astimezone(LIMA).date() != target_date:
            raise ValueError("El horario de ESPN debe llevar zona y corresponder al día del partido en Lima.")
        key = fixture_key(league, home, away)
        kickoff_utc = kickoff.astimezone(timezone.utc).isoformat()
        if key in kickoffs and kickoffs[key] != (event_id, kickoff_utc):
            raise ValueError(f"Horarios de ESPN ambiguos para {home} vs {away}.")
        kickoffs[key] = (event_id, kickoff_utc)
    return kickoffs


def export_feed(cache, verified_fixtures=None):
    if not isinstance(cache, dict) or not isinstance(cache.get("predictions"), list):
        raise ValueError("El caché debe incluir una lista predictions.")
    target_date = datetime.strptime(clean_text(cache.get("date")), "%d/%m/%Y").date()
    generated_raw = clean_text(cache.get("generated_at"))
    if "T" not in generated_raw:
        raise ValueError("El caché debe incluir fecha y hora de generación.")
    generated_at = datetime.fromisoformat(generated_raw)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=LIMA)
    kickoffs = load_kickoffs(verified_fixtures, target_date)

    forecasts = {}
    for row in cache["predictions"]:
        if not isinstance(row, dict) or row.get("a12_activo") is not True:
            continue
        teams = clean_text(row.get("partido")).split(" vs ")
        league = clean_text(row.get("liga"))
        scenario = clean_text(row.get("escenario_principal"))
        status = clean_text(row.get("accion_recomendada"))
        chance = probability(row.get("prob_calibrada"))
        if len(teams) != 2 or not all(teams) or not league or not scenario or status not in STATUSES or chance is None:
            continue
        home, away = teams
        key = fixture_key(league, home, away)
        identity = json.dumps([target_date.isoformat(), *key], ensure_ascii=False, separators=(",", ":"))
        row_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        lower = probability(row.get("ci95_low"), allow_zero=True)
        upper = probability(row.get("ci95_high"), allow_zero=True)
        if lower is None or upper is None or not lower <= chance <= upper:
            lower = upper = None
        forecast = {
            "id": row_id,
            "home": home,
            "away": away,
            "league": league,
            "scenario": scenario,
            "probability": chance,
            "lower": lower,
            "upper": upper,
            "kickoff": kickoffs.get(key, (None, None))[1],
        }
        if row_id in forecasts and forecasts[row_id] != forecast:
            raise ValueError(f"Pronósticos en conflicto para {home} vs {away}.")
        forecasts[row_id] = forecast

    return {
        "version": 1,
        "source": "A12 Champion",
        "generatedAt": generated_at.isoformat(),
        "date": target_date.isoformat(),
        "forecasts": list(forecasts.values()),
    }


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_feed(path, feed):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            json.dump(feed, output, ensure_ascii=False, allow_nan=False, indent=2)
            output.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True, help="Caché privado de entrada")
    parser.add_argument("--output", type=Path, required=True, help="Archivo JSON público de salida")
    parser.add_argument("--fixtures", type=Path, help="Horarios cotejados con ESPN, usando los nombres del caché")
    args = parser.parse_args()

    inputs = {args.cache.resolve()}
    if args.fixtures:
        inputs.add(args.fixtures.resolve())
    if args.output.resolve() in inputs:
        parser.error("La salida y los archivos de entrada deben ser distintos.")

    try:
        verified_fixtures = read_json(args.fixtures) if args.fixtures else None
        feed = export_feed(read_json(args.cache), verified_fixtures)
        write_feed(args.output, feed)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"No se pudo exportar el feed: {exc}\n")

    print(f"Exportados {len(feed['forecasts'])} pronósticos de {feed['date']}.")


if __name__ == "__main__":
    main()
