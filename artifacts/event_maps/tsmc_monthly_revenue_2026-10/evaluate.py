"""Hand one generated event map and recipe to the existing evaluator entry point, ``build_table``.

Usage: evaluate.py OUTPUT_DIR   (a folder holding event_map.json and recipe.json)

The factor is read from the map's content, so it sees only what the map saw. Realised
returns enter here and nowhere earlier. Needs the raw TSM and SOXX price files of
snapshot 2026-11-22, which are not in the repository; it stops if their hashes differ.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT, HERE = Path(__file__).resolve().parents[3], Path(__file__).resolve().parent
for package in ("quant_contracts", "quant_loop", "quant_pit_data", "quant_evaluator"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
from quant_contracts.evaluation import EventTime, Factor
from quant_contracts.event_map import EventMap
from quant_contracts.pit import Cutoff, SeriesBinding, SessionCalendar
from quant_contracts.research import CandidateFactorRecipe
from quant_loop.event_table import build_table
from quant_pit_data.connectors.yahoo_chart import ex_dividend_dates, load_daily, split_dates

out = Path(sys.argv[1])
event_map = EventMap.model_validate_json((out / "event_map.json").read_text())
recipe = CandidateFactorRecipe.model_validate_json((out / "recipe.json").read_text())
SNAPSHOT = ROOT / "artifacts/snapshots/2026-11-22"
manifest = json.loads((SNAPSHOT / "manifest.json").read_text())["files"]
hashes = {}
for name in ("TSM.json", "SOXX.json", "us_equity_sessions_2002_2026.json"):
    hashes[name] = hashlib.sha256((SNAPSHOT / name).read_bytes()).hexdigest()
    if hashes[name] != manifest[name]["sha256"]:
        sys.exit(f"{name} does not match the snapshot manifest")
catalog = json.loads((ROOT / "knowledge_base/prior_knowledge/bindings/series_bindings.json").read_text())
b = {s["series_id"]: SeriesBinding(**s) for s in catalog["series"]}
equity = SessionCalendar(**json.loads((SNAPSHOT / "us_equity_sessions_2002_2026.json").read_text()))
periods = SessionCalendar(**json.loads((HERE / "monthly_periods_2026.json").read_text()))
subject, benchmark = (b["yahoo:tsm:open"], b["yahoo:tsm:close"]), (b["yahoo:soxx:open"], b["yahoo:soxx:close"])
series = event_map.content_series
factor = Factor(factor_id="reported_revenue_growth_yoy", measure="level", series_ids=(series,), max_age_days=45)
# The decision time is the study's own, so it is exact. Entry follows it; the factor is read before it.
# The factor is this event's own reported figure, so it must come from the event's period and no other.
event = EventTime(event_id=event_map.event_id, cutoff=Cutoff(time=event_map.decision_time, basis="exact",
                                                             source="The decision time the event map was built for"),
                  required_sessions={factor.factor_id: event_map.content_session})
prices = load_daily(SNAPSHOT / "TSM.json", *subject) + load_daily(SNAPSHOT / "SOXX.json", *benchmark)
actions = {f"{kind} {name}": dates(SNAPSHOT / f"{name}.json") for name in ("TSM", "SOXX")
           for kind, dates in (("ex-dividend", ex_dividend_dates), ("split", split_dates))}
table = build_table(recipe, [event], [factor], {series: b[series]}, subject, benchmark, list(event_map.content) + prices,
                    equity, actions, hashes, series_calendars={series: periods})
(out / "table.json").write_text(table.model_dump_json(indent=1) + "\n")
row, state = table.rows[0], table.rows[0].factors[0]
print(f"decision {event_map.decision_time} | recipe {recipe.observability} | factor {state.state.status} for {state.state.session}: "
      f"{state.value} ({state.assumed_inputs} assumed) | entry {row.entry_session} exit {row.exit_session} | relative {row.window.relative:+.4f} | "
      f"{row.prior_status}, timing verified {row.timing_verified}")
