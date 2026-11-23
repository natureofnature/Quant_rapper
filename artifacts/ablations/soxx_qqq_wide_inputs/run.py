"""Run the RFC-005 comparison. Every setting comes from registration.json beside this file.

Four arms per fund from one fixed tree model: the rule's 3 inputs against the wider set,
under the forward-average and the forward-drawdown target, each turned into a gate and
compared with the frozen rule over a development period and a later test period.

Usage: run.py                    the registered run, on real prices; writes output/
       run.py --coverage         which decisions have every input; reads no outcome; writes coverage.json
       run.py --synthetic SEED   a rehearsal on seeded random walks for both funds; writes output_synthetic/

The registered run does not trust the working tree: the registration must carry a code commit
and be the committed file, this script must equal its version at that commit, and the packages
are exported from git at that commit. The other two modes use local code. A rehearsal replaces
both funds' prices before any outcome is computed and means nothing about the funds.
"""
import argparse
import atexit
import csv
from datetime import date, time
import hashlib
import io
import json
from math import log, sqrt
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT, HERE = Path(__file__).resolve().parents[3], Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description="Run the RFC-005 wide-inputs comparison.")
mode = parser.add_mutually_exclusive_group()
mode.add_argument("--synthetic", type=int, metavar="SEED", help="rehearse on seeded random walks; writes output_synthetic/")
mode.add_argument("--coverage", action="store_true", help="report input coverage only; reads no outcome")
args = parser.parse_args()
SYNTHETIC, COVERAGE = args.synthetic, args.coverage
REGISTRATION = HERE / "registration.json"
reg = json.loads(REGISTRATION.read_text())
code_root = ROOT
if SYNTHETIC is None and not COVERAGE:  # the registered run: pinned code only
    commit = reg["code"]["commit"] or ""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        sys.exit("the registration has no code commit yet: only --coverage and --synthetic can run")
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], check=True, capture_output=True)
    here = str(HERE.relative_to(ROOT))
    if subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", f"{here}/registration.json"]).returncode \
            or not git("ls-files", "--", f"{here}/registration.json").stdout.strip():
        sys.exit("registration.json must be the committed file, unchanged")
    if git("show", f"{commit}:{here}/run.py").stdout != Path(__file__).read_bytes():
        sys.exit("this script differs from its version at the registered commit")
    code_root = Path(tempfile.mkdtemp(prefix="registered_run_"))
    atexit.register(shutil.rmtree, code_root, ignore_errors=True)
    with tarfile.open(fileobj=io.BytesIO(git("archive", commit, "packages").stdout)) as tar:
        tar.extractall(code_root, filter="data")
    sys.dont_write_bytecode = True
for package in ("quant_contracts", "quant_loop", "quant_pit_data", "quant_evaluator", "quant_causal_graph"):
    sys.path.insert(0, str(code_root / "packages" / package / "src"))
import numpy as np
from quant_causal_graph.causal_map import check_bindings
from quant_contracts.causal_map import SUBJECT_CLOSE, ResearchInputs
from quant_contracts.evaluation import Factor
from quant_contracts.pit import SeriesBinding, SessionCalendar
from quant_evaluator.beta.forward_target import class_thresholds, forward_average_return, trailing_volatility
from quant_evaluator.beta.model_gate import gate_states, paired_recall_difference, tail_recall, walk_forward_binary
from quant_evaluator.beta.risk_gate import episodes, equity_curve, forward_drawdown, max_drawdown, reentry_ratios
from quant_loop.feature_panel import feature_panel
from quant_pit_data.asof import load_daily_closes, on_calendar, ratio_series
from quant_pit_data.connectors.yahoo_chart import load_closes, load_daily

import quant_evaluator.beta.tree_model as loaded_model
if not loaded_model.__file__.startswith(str(code_root)):
    sys.exit("the model code was not loaded from the expected place")
P = reg["parameters"]
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
where = {}
for snapshot in reg["inputs"]["snapshots"]:
    for name, expected in snapshot["sha256"].items():
        where[name] = ROOT / snapshot["folder"] / name
        if digest(where[name]) != expected:
            sys.exit(f"{name} does not match the registered hash")
BASELINE = ROOT / reg["rule_baseline"]["folder"]
for name, expected in reg["rule_baseline"]["sha256"].items():
    if digest(BASELINE / name) != expected:
        sys.exit(f"the frozen rule baseline file {name} does not match the registered hash")
bindings_dir = ROOT / "knowledge_base/prior_knowledge/bindings"
if digest(bindings_dir / "factor_definitions.json") != reg["inputs"]["factor_definitions_sha256"]:
    sys.exit("factor_definitions.json does not match the registered hash")
research = ResearchInputs.model_validate_json((bindings_dir / "factor_definitions.json").read_text())
b = {s["series_id"]: SeriesBinding(**s) for s in json.loads((bindings_dir / "series_bindings.json").read_text())["series"]}
definitions = {d.factor.factor_id: d.factor for d in research.factors}
catalogue_used = {"cboe:vix", "cboe:vix3m", "treasury:real_yield_10y", "yahoo:spy:close",
                  *(f"yahoo:{t.lower()}:{side}" for t in P["subjects"] for side in ("open", "close"))}
binding_hashes = check_bindings(catalogue_used, b, research.bindings_sha256)
files = {}
for item in reg["bindings"]:  # the study's own bindings, pinned with the registration itself
    item = dict(item)
    files[item["series_id"]] = item.pop("file")
    if item["series_id"] in b:
        sys.exit(f"{item['series_id']} is already in the catalogue")
    b[item["series_id"]] = SeriesBinding(**item)
derived = SeriesBinding(**reg["derived_bindings"][0])
b[derived.series_id] = derived

equity = SessionCalendar(**json.loads(where["us_equity_sessions_2002_2026.json"].read_text()))
treasury = SessionCalendar(**json.loads(where["us_treasury_yield_sessions_2003_2026.json"].read_text()))
sessions = [s for s in equity.sessions if date.fromisoformat(P["first_session"]) <= s <= date.fromisoformat(P["last_session"])]
HORIZON, DECISION = P["horizon_sessions"], time.fromisoformat(P["decision_time"])
EXPOSURE = [P["exposure_by_state"][k] for k in ("GREEN", "YELLOW", "RED")]
state, _ = on_calendar(load_daily_closes(where["VIX.csv"], b["cboe:vix"]) + load_daily_closes(where["VIX3M.csv"], b["cboe:vix3m"]), equity)
observations = state + load_daily_closes(where["treasury_real_yield_10y.csv"], b["treasury:real_yield_10y"])
calendars = {"treasury:real_yield_10y": treasury}
by_series = {}
for series_id, name in files.items():
    rows = load_closes(where[name], b[series_id])
    if series_id in reg["series_on_their_own_dates"]:
        # Read on the dates the source has a value for: a lookback counts the series' own observations, and the age
        # limit still refuses a stale one. The calendar is the file's own dates, so it holds no claim about any other day.
        days = tuple(sorted(o.session for o in rows))
        calendars[series_id] = SessionCalendar(calendar_id=f"dates_of:{series_id}", timezone=b[series_id].timezone, first=days[0], last=days[-1],
                                               sessions=days, basis_note="The dates on which the pinned source file has a value.")
    else:
        rows, _ = on_calendar(rows, equity)
    by_series[series_id] = rows
    observations += rows
observations += ratio_series(derived, by_series["yahoo:hyg:close"], by_series["yahoo:lqd:close"])
spy = [o for o in load_daily(where["SPY.json"], b["yahoo:spy:open"], b["yahoo:spy:close"]) if o.series_id == "yahoo:spy:close"]
fund_prices = {}
for ticker in P["subjects"]:
    open_b, close_b = b[f"yahoo:{ticker.lower()}:open"], b[f"yahoo:{ticker.lower()}:close"]
    prices = load_daily(where[f"{ticker}.json"], open_b, close_b)
    if SYNTHETIC is not None:  # a random walk in place of the fund's prices: every outcome below is then meaningless
        rng, level, fake = np.random.default_rng(SYNTHETIC + len(ticker) + ord(ticker[0])), 100.0, []
        for x in sorted(prices, key=lambda x: (x.session, x.series_id.endswith(":close"))):
            level *= float(np.exp(rng.normal(0.0, 0.01)))
            fake.append(x.model_copy(update={"value": level}))
        prices = fake
    fund_prices[ticker] = (open_b, close_b, prices)
every_price = observations + spy + [o for _, _, prices in fund_prices.values() for o in prices]

INPUTS = [name for name in reg["input_definitions"] if name not in P["dropped_inputs"]]
NARROW = P["narrow_inputs"]
if INPUTS[:len(NARROW)] != NARROW:
    sys.exit("the narrow inputs must come first in the input list")
needed = {a for name in INPUTS for a in reg["input_definitions"][name]["of"]}
shared_factors = [Factor(**f) for f in reg["base_factors"]["shared"] if f["factor_id"] in needed]
fomc = sorted(date.fromisoformat(d) for d in json.loads(where["announcement_dates_2002_2026.json"].read_text())["fomc"]["scheduled"]
              + P["fomc_scheduled_after_file"])
at = {s: i for i, s in enumerate(equity.sessions)}


def sessions_to_fomc(session):
    """Sessions from this one to the next scheduled meeting, 0 on the meeting day. The schedule is published in advance."""
    ahead = next((d for d in fomc if d >= session), None)
    return None if ahead is None else float(sum(1 for s in equity.sessions[at[session]:] if s < ahead))


def compose(definition, row, session):
    op, values = definition["op"], [row.get(a) for a in definition["of"]]
    if op == "calendar":
        return sessions_to_fomc(session)
    if any(v is None for v in values):
        return None
    a, c = values[0], (values[1] if len(values) > 1 else None)
    try:
        return {"base": lambda: a, "log": lambda: log(a), "log1p": lambda: log(1.0 + a), "minus": lambda: a - c,
                "relative": lambda: (1.0 + a) / (1.0 + c) - 1.0, "over": lambda: a / c,
                "implied_minus_realised": lambda: a / 100.0 - c * sqrt(252.0)}[op]()
    except (ValueError, ZeroDivisionError):
        return None  # undefined for these inputs: missing, never a number


shared_panel = feature_panel("shared", sessions, DECISION, shared_factors, b, every_price, equity, calendars)
matrices = {}
for ticker, (open_b, close_b, prices) in fund_prices.items():
    own = [definitions[n].model_copy(update={"series_ids": tuple(close_b.series_id if i == SUBJECT_CLOSE else i for i in definitions[n].series_ids)})
           for n in reg["base_factors"]["from_factor_definitions"]]
    own += [Factor(**{**f, "series_ids": [close_b.series_id if i == SUBJECT_CLOSE else i for i in f["series_ids"]]})
            for f in reg["base_factors"]["per_subject"] if f["factor_id"] in needed]
    panel = feature_panel(ticker, sessions, DECISION, own, b, observations + prices, equity, calendars)
    rows = [{**s.features, **p.features} for s, p in zip(shared_panel, panel)]
    matrices[ticker] = np.array([[np.nan if (v := compose(reg["input_definitions"][name], row, session)) is None else v for name in INPUTS]
                                 for row, session in zip(rows, sessions)], dtype=float)

if COVERAGE:
    out = {"inputs": INPUTS, "dropped_inputs": P["dropped_inputs"], "funds": {}}
    for ticker, x in matrices.items():
        present = np.isfinite(x)
        first = int(np.argmax(present.all(axis=1)))
        spans = {"development_through_" + P["development_last"]: [i for i, s in enumerate(sessions) if i >= first and s <= date.fromisoformat(P["development_last"])],
                 "test_from_" + P["test_first"]: [i for i, s in enumerate(sessions) if s >= date.fromisoformat(P["test_first"])]}
        out["funds"][ticker] = {"first_decision_with_every_input": str(sessions[first]),
                                "first_decision_per_input": {n: str(sessions[int(np.argmax(present[:, k]))]) for k, n in enumerate(INPUTS)},
                                "decisions_missing_an_input": {label: {"decisions": len(idx), "missing_any": int((~present[idx].all(axis=1)).sum()),
                                                                       "by_input": {n: int((~present[idx, k]).sum()) for k, n in enumerate(INPUTS) if (~present[idx, k]).any()}}
                                                               for label, idx in spans.items()}}
    (HERE / "output_synthetic").mkdir(exist_ok=True)  # a local, uncommitted dump of the inputs for checking them; no outcome is in it
    np.savez(HERE / "output_synthetic/inputs.npz", sessions=np.array([str(d) for d in sessions]), names=np.array(INPUTS), **matrices)
    (HERE / "coverage.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({t: {"first": v["first_decision_with_every_input"], **v["decisions_missing_an_input"]} for t, v in out["funds"].items()}, indent=1))
    sys.exit(0)

OUT = HERE / ("output" if SYNTHETIC is None else "output_synthetic")
OUT.mkdir(exist_ok=True)
G, LIMITS = P["gate"], P["promotion_limits"]
dev_last = max(i for i, s in enumerate(sessions) if s <= date.fromisoformat(P["development_last"]))
test_first = min(i for i, s in enumerate(sessions) if s >= date.fromisoformat(P["test_first"]))


def arm_report(targets, held, o, c, drawdowns, start, end, cost_rate):
    """One arm over decisions ``start`` to ``end`` inclusive. Nothing after session ``end`` is read."""
    period = [t if start <= i <= end else None for i, t in enumerate(targets[:end + 1])]
    o, c, dd = o[:end + 1], c[:end + 1], [d if i + HORIZON <= end else None for i, d in enumerate(drawdowns[:end + 1])]
    first_traded, gated, hold = equity_curve(o, c, period, cost_rate)
    runs = episodes(period, P["episode_gap"])
    ratios, unfinished = reentry_ratios(o, runs)
    alerts = [None if t is None else t < 1.0 for t in period]
    executable = period[start:-1]
    traded = sum(abs(y - x) for x, y in zip([1.0] + executable[:-1], executable))
    return {"first_decision": str(sessions[start]), "last_session": str(sessions[end]), "decisions": end - start + 1,
            "held_decisions": sum(held[start:end + 1]), "max_drawdown": max_drawdown(gated), "final_equity": gated[-1],
            "buy_and_hold": {"max_drawdown": max_drawdown(hold), "final_equity": hold[-1]},
            "turnover_per_year": traded / ((sessions[end] - sessions[start]).days / 365.25),
            "episodes": len(runs), "unfinished_episodes": unfinished,
            "whipsaw_rate": sum(r > 1.0 for r in ratios) / len(ratios) if ratios else None,
            "tail_recall": tail_recall(dd, alerts, P["worst_share"])}, alerts, dd


def checks_against(rule, arm):
    return {"drawdown_smaller_by_points": (abs(rule["max_drawdown"]) - abs(arm["max_drawdown"])) * 100 >= LIMITS["drawdown_points"],
            "final_equity_share_of_rule": arm["final_equity"] >= LIMITS["equity_share"] * rule["final_equity"],
            "whipsaw_within_points": (arm["whipsaw_rate"] is not None and rule["whipsaw_rate"] is not None
                                      and (arm["whipsaw_rate"] - rule["whipsaw_rate"]) * 100 <= LIMITS["whipsaw_points"])}


report = {"registration_sha256": digest(REGISTRATION), "bindings_sha256": binding_hashes, "inputs": INPUTS, "synthetic_prices": SYNTHETIC is not None, "funds": {}}
for ticker, (open_b, close_b, prices) in fund_prices.items():
    opens = {x.session: x.value for x in prices if x.series_id == open_b.series_id}
    closes = {x.session: x.value for x in prices if x.series_id == close_b.series_id}
    o, c = [opens.get(s) for s in sessions], [closes.get(s) for s in sessions]
    wide = matrices[ticker]
    everything = np.isfinite(wide).all(axis=1)
    narrow = np.where(everything[:, None], wide[:, :len(NARROW)], np.nan)  # matched: the narrow arms are scored only where the wide ones can be
    average = [forward_average_return(o, c, i, HORIZON) for i in range(len(sessions))]
    drawdowns = [forward_drawdown(o, c, i, HORIZON) if average[i] is not None else None for i in range(len(sessions))]
    floor, cap = P["class_bounds"][ticker]
    left_tail = []
    for i, value in enumerate(average):
        volatility = trailing_volatility(c, i, P["volatility_sessions"])
        left_tail.append(None if value is None or volatility is None else float(value < class_thresholds(volatility, floor, cap, HORIZON)[0]))
    first = sessions[int(np.argmax(everything))]
    arms = {}
    for target, outcomes, share in (("avg", left_tail, None), ("dd", drawdowns, P["worst_share"])):
        for width, x in (("N", narrow), ("W", wide)):
            scores = walk_forward_binary(sessions, x, outcomes, first, P["tree"], share, HORIZON, 0, P["min_train"], P["min_positive"])
            states = gate_states(scores, G["red_quantile"], G["yellow_quantile"], G["window"], G["min_scores"], G["step_down_after"])
            arms[f"{width}-{target}"] = {"scores": scores, "states": [s for s, _ in states], "held": [h for _, h in states],
                                         "targets": [None if s is None else EXPOSURE[s] for s, _ in states]}
    gate_start = max(next(i for i, t in enumerate(a["targets"]) if t is not None) for a in arms.values())
    with (BASELINE / f"output/positions_{ticker}.csv").open(newline="") as handle:
        frozen = {date.fromisoformat(r["session"]): (float(r["target"]), r["held"] == "1") for r in csv.DictReader(handle)}
    rule_targets = [frozen[s][0] if s in frozen else None for s in sessions]
    rule_held = [frozen[s][1] if s in frozen else False for s in sessions]
    fund = {"first_gate_decision": str(sessions[gate_start]), "periods": {}}
    for label, start, end in (("development", gate_start, dev_last), ("test", test_first, len(sessions) - 1)):
        if start < gate_start:
            sys.exit("the gate does not have a state by the start of the test period")
        rule, _, dd = arm_report(rule_targets, rule_held, o, c, drawdowns, start, end, P["cost_rate"])
        block, alerts = {"rule": rule, "arms": {}}, {}
        for name, a in arms.items():
            block["arms"][name], alerts[name], _ = arm_report(a["targets"], a["held"], o, c, drawdowns, start, end, P["cost_rate"])
        pair = lambda x, y: paired_recall_difference(dd, alerts[x], alerts[y], P["draws"], P["seed"], P["worst_share"], P["episode_gap"])
        block["what_the_inputs_add"] = {"avg: W minus N": pair("N-avg", "W-avg"), "dd: W minus N": pair("N-dd", "W-dd")}
        block["what_the_target_adds"] = {"N: dd minus avg": pair("N-avg", "N-dd"), "W: dd minus avg": pair("W-avg", "W-dd")}
        block["checks"] = {name: checks_against(rule, block["arms"][name]) for name in ("W-avg", "W-dd")}
        passed = [name for name, checks in block["checks"].items() if all(checks.values())]
        block["candidates_meeting_every_limit"] = passed
        block["would_be_taken"] = min(passed, key=lambda n: abs(block["arms"][n]["max_drawdown"])) if passed else None
        block["sensitivity_decides_nothing"] = {
            f"cost_x{m}": {name: {k: v for k, v in arm_report(t, h, o, c, drawdowns, start, end, P["cost_rate"] * m)[0].items()
                                  if k in ("max_drawdown", "final_equity", "whipsaw_rate")}
                           for name, t, h in [("rule", rule_targets, rule_held)] + [(n, a["targets"], a["held"]) for n, a in arms.items()]}
            for m in P["sensitivity_cost_multiples"]}
        fund["periods"][label] = block
    test = fund["periods"]["test"]
    fund["decision"] = f"{test['would_be_taken']} replaces the rule" if test["would_be_taken"] else "the rule is kept"
    fund["positions_sha256"] = {}
    for name, a in arms.items():
        path = OUT / f"positions_{ticker}_{name}.csv"
        path.write_text("\n".join(["session,target,held,p_left,state"] + [
            f"{s},{a['targets'][i]:.2f},{int(a['held'][i])},{'' if a['scores'][i] is None else format(a['scores'][i], '.6f')},{a['states'][i]}"
            for i, s in enumerate(sessions) if a["targets"][i] is not None]) + "\n")
        fund["positions_sha256"][name] = digest(path)
    report["funds"][ticker] = fund
stop = all(f["decision"] == "the rule is kept" for f in report["funds"].values())
report["stopping_rule"] = ("met: no candidate is promoted for either fund; no variant of this list, these targets or this model is tried on the test period"
                           if stop else "not met: a candidate is promoted for at least one fund")
(OUT / "report.json").write_text(json.dumps(report, indent=1, allow_nan=False) + "\n")
print(json.dumps({t: {"decision": f["decision"], "test_checks": f["periods"]["test"]["checks"]} for t, f in report["funds"].items()}, indent=1))
