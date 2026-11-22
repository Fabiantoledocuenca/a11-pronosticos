"""Run the fixed private A12 Champion revision and export only verified forecasts."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from contextlib import ExitStack, closing, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta
from functools import partial
import gzip
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import patch
from urllib.parse import urlparse

from export_feed import LIMA, export_feed, write_feed

ENGINE_REF = "2a3e8ebca139a88a7eee9c1869c84843fa5e4b1f"
CHAMPION_SHA = "8e278eb3c3520d8671944b987bdd374bf94bf8d9074251213d37b2090f756239"
SCENARIOS = {"Victoria local", "Victoria visitante", "Local no pierde (1X)", "Visitante no pierde (X2)"}
DATABASES = {"A11_Historical.sqlite", "A11_Backtest.sqlite"}


def restore_private_state(target):
    """Decode the encrypted repository secret only inside the ephemeral runner."""
    raw = base64.b64decode(os.environ.pop("SENTINEL_STATE"), validate=True)
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as package:
        state = json.loads(package.read(1_000_000))
    names = set(state["files"])
    if state.get("version") != 1 or not DATABASES <= names <= DATABASES | {"A11_Calibration.json"}:
        raise ValueError("Invalid private state")
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, encoded in state["files"].items():
        destination = target / name
        destination.write_bytes(base64.b64decode(encoded, validate=True))
        destination.chmod(0o600)
        if name in DATABASES:
            with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("pragma quick_check").fetchall() != [("ok",)]:
                    raise ValueError("Invalid private calibration database")
        else:
            if not isinstance(json.loads(destination.read_text(encoding="utf-8")), dict):
                raise ValueError("Invalid private calibration document")


def verify_checkout(engine):
    commit = subprocess.check_output(
        ["git", "-C", str(engine), "rev-parse", "HEAD"], text=True
    ).strip()
    model = engine / "data" / "a12_models" / "champion_v1.pkl"
    if commit != ENGINE_REF or hashlib.sha256(model.read_bytes()).hexdigest() != CHAMPION_SHA:
        raise ValueError("Private revision or Champion model hash mismatch")


def generate(engine, state, dates):
    verify_checkout(engine)
    os.chdir(engine)
    sys.path.insert(0, str(engine))
    import requests
    import a11_engine
    import a11_database
    import a11_backtester
    import a11_mle_calibrator as mle
    from a11_pit_logger import pit_logger
    from a12_lab.predictor import A12Predictor
    from api import main as api

    if A12Predictor.load_default() is None:
        raise RuntimeError("Champion model unavailable")

    serialize = api._serializar_resultado
    predict = A12Predictor.predict_fixture
    send = requests.Session.send
    base_engine = a11_engine.A11Engine
    stats = Counter()
    proven = {}
    kickoffs = {}
    captured = None
    target = None

    class PublicationEngine(base_engine):
        def __init__(self, *args, persist_picks=False, **kwargs):
            super().__init__(*args, persist_picks=False, **kwargs)

    def evidence(instance, **kwargs):
        result = predict(instance, **kwargs)
        if result is not None:
            key = (kwargs["liga"], kwargs["local_name"], kwargs["away_name"])
            proven[key] = (
                result.escenario_principal, round(float(result.prob_calibrada), 4),
                round(float(result.ci95_low), 4), round(float(result.ci95_high), 4),
            )
        return result

    def provider_status(session, request, **kwargs):
        scoreboard = (urlparse(request.url).hostname == "site.api.espn.com"
                      and "/scoreboard" in urlparse(request.url).path)
        try:
            response = send(session, request, **kwargs)
        except requests.RequestException:
            if scoreboard:
                stats["espn_errors"] += 1
            raise
        if scoreboard:
            stats["espn_ok" if response.status_code == 200 else "espn_errors"] += 1
        return response

    def project_evidence(result, match=None):
        row = serialize(result, match)
        if match is None or str(getattr(match, "_estado_espn", "")).casefold() != "pre":
            return row
        try:
            kickoff = datetime.fromisoformat(match._kickoff_iso.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            return row
        if kickoff.tzinfo is None or kickoff.astimezone(LIMA).date() != target:
            return row
        teams = row["partido"].split(" vs ")
        if len(teams) != 2:
            return row
        key = (row["liga"], *teams)
        published = (row["escenario_principal"], row["prob_calibrada"], row["ci95_low"], row["ci95_high"])
        if proven.get(key) != published or published[0] not in SCENARIOS:
            return row
        old = kickoffs.get(key, kickoff)
        kickoffs[key] = kickoff if old == kickoff else None
        return row

    def capture(rows, date):
        nonlocal captured
        captured = {"date": date, "generated_at": datetime.now(LIMA).isoformat(), "predictions": rows}

    with ExitStack() as stack:
        stack.enter_context(patch.object(api, "_leer_cache", return_value=None))
        stack.enter_context(patch.object(api, "_guardar_cache", capture))
        stack.enter_context(patch.object(api, "_serializar_resultado", project_evidence))
        stack.enter_context(patch.object(A12Predictor, "predict_fixture", evidence))
        stack.enter_context(patch.object(requests.Session, "send", provider_status))
        stack.enter_context(patch.object(a11_engine, "A11Engine", PublicationEngine))
        stack.enter_context(patch.object(a11_database, "log_prediction", return_value=None))
        stack.enter_context(patch.object(a11_database, "DB_PATH", state / "unused-shadow.sqlite"))
        stack.enter_context(patch.object(pit_logger, "snapshot", return_value=None))
        stack.enter_context(patch.object(a11_backtester, "WalkForwardBacktester", partial(
            a11_backtester.WalkForwardBacktester, db_path=state / "A11_Backtest.sqlite")))
        historical = state / "A11_Historical.sqlite"
        stack.enter_context(patch.object(mle, "HISTORICAL_DB_PATH", historical))
        stack.enter_context(patch.object(mle, "CALIBRATION_CACHE_PATH", state / "A11_Calibration.json"))
        stack.enter_context(patch.object(mle, "MLECalibrator", partial(mle.MLECalibrator, db_path=historical)))
        for target in dates:
            captured = None
            proven.clear()
            kickoffs.clear()
            stats.clear()
            api._ejecutar_scan(fecha=target.isoformat())
            if api.scanner_status.get("error") or captured is None or not stats["espn_ok"]:
                raise RuntimeError("Scanner failed or no ESPN request succeeded")
            cutoff = datetime.fromisoformat(captured["generated_at"]) + timedelta(minutes=5)
            fixtures = {
                "source": "ESPN", "date": target.isoformat(), "fixtures": [
                    {"id": str(i), "league": league, "home": home, "away": away, "kickoff": kickoff.isoformat()}
                    for i, ((league, home, away), kickoff) in enumerate(kickoffs.items())
                    if kickoff and kickoff > cutoff
                ]
            }
            output = export_feed(captured, fixtures)
            output["forecasts"] = [row for row in output["forecasts"] if row["kickoff"] is not None]
            yield output, dict(stats)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    engine, state, output = (path.resolve() for path in (args.engine, args.state, args.output))
    if output.exists():
        raise SystemExit("Expected an unused feed path.")
    try:
        if "SENTINEL_STATE" in os.environ:
            restore_private_state(state)
        if not all((state / name).is_file() for name in DATABASES):
            raise ValueError("Private calibration is missing")
        today = datetime.now(LIMA).date()
        reports = []
        with open(os.devnull, "w", encoding="utf-8") as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
            logging.disable(logging.CRITICAL)
            try:
                with closing(generate(engine, state, (today, today + timedelta(days=1)))) as scan:
                    for feed, stats in scan:
                        reports.append((feed["date"], len(feed["forecasts"]), stats))
                        if feed["forecasts"]:
                            write_feed(output, feed)
                            break
            finally:
                logging.disable(logging.NOTSET)
        for date, count, stats in reports:
            print(f"Agenda {date}: {count} pronósticos Champion; ESPN válido={stats.get('espn_ok', 0)}, fallos={stats.get('espn_errors', 0)}")
        if not output.exists():
            print("Sin nuevos pronósticos admitidos. Se conserva la última edición.")
    except Exception as error:
        # El escáner y las respuestas de los proveedores pueden contener datos privados.
        raise SystemExit(f"Cálculo interrumpido ({type(error).__name__}). La edición anterior se conserva.") from None


if __name__ == "__main__":
    main()
