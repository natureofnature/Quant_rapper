"""Hand the candidate that measures the event's own content to the evaluator.

Usage: evaluate.py OUTPUT_DIR   (a folder holding causal_map.json and recipes.json)

If the map marks that candidate unmeasured there is no recipe for it, so no table is built:
the gap is written to gap.json as the map states it. Otherwise table.json holds the row.
Both are valid outcomes and exit with status 0; a failed check exits nonzero and leaves any
earlier output in place.
Needs the raw TSM and SOXX price files of snapshot 2026-11-22, which are not in the
repository; it stops if their hashes differ.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT, HERE = Path(__file__).resolve().parents[3], Path(__file__).resolve().parent
for package in ("quant_contracts", "quant_loop", "quant_pit_data", "quant_evaluator", "quant_causal_graph"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
from quant_contracts.causal_map import CausalMap
from quant_contracts.pit import SeriesBinding, SessionCalendar
from quant_contracts.research import CandidateFactorRecipe
from quant_loop.handoff import evaluate_candidate
from quant_pit_data.connectors.yahoo_chart import ex_dividend_dates, load_daily, split_dates

out = Path(sys.argv[1])
causal_map = CausalMap.model_validate_json((out / "causal_map.json").read_text())
recipes = {r["recipe_id"]: CandidateFactorRecipe.model_validate(r) for r in json.loads((out / "recipes.json").read_text())}
series = causal_map.event.content_series
candidate = next(c for c in causal_map.candidates if c.factor is not None and series in c.factor.series_ids)
if candidate.recipe_id is None:
    gap = {"candidate_id": candidate.candidate_id, "status": candidate.status, "missing": candidate.missing,
           "decision_time": causal_map.decision_time.isoformat()}
    (out / "gap.json").write_text(json.dumps(gap, indent=1) + "\n")
    (out / "table.json").unlink(missing_ok=True)  # only after the new result is written
    print(f"decision {causal_map.decision_time} | {candidate.candidate_id} is unmeasured, so nothing is evaluated: {candidate.missing}")
    sys.exit(0)

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
prices = (load_daily(SNAPSHOT / "TSM.json", b["yahoo:tsm:open"], b["yahoo:tsm:close"])
          + load_daily(SNAPSHOT / "SOXX.json", b["yahoo:soxx:open"], b["yahoo:soxx:close"]))
actions = {f"{kind} {name}": dates(SNAPSHOT / f"{name}.json") for name in ("TSM", "SOXX")
           for kind, dates in (("ex-dividend", ex_dividend_dates), ("split", split_dates))}
if candidate.recipe_id not in recipes:
    sys.exit(f"recipes.json does not hold {candidate.recipe_id}, which this map generated: the two files are from different runs")
table = evaluate_candidate(causal_map, recipes[candidate.recipe_id], b, prices, equity, actions, hashes,
                           series_calendars={series: periods})
(out / "table.json").write_text(table.model_dump_json(indent=1) + "\n")
(out / "gap.json").unlink(missing_ok=True)  # only after the new result is written
row, state = table.rows[0], table.rows[0].factors[0]
outcome = f"relative {row.window.relative:+.4f}" if row.status == "complete" else f"incomplete: {row.reason}"
print(f"decision {causal_map.decision_time} | {table.recipe_id} | factor {state.state.status} for {state.state.session}: "
      f"{state.value} ({state.assumed_inputs} assumed) | entry {row.entry_session} exit {row.exit_session} | "
      f"{outcome} | {row.prior_status}, timing verified {row.timing_verified}")
