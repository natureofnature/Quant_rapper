"""Run the registered SOXX/QQQ rule baseline. Every choice comes from registration.json beside this file.

Usage: run.py   (from anywhere). Writes output/report.json and output/positions_<fund>.csv beside this file.
It stops unless every input file matches its registered hash. The raw market files are not in the
repository: see retrieval_note in the snapshot's manifest.json.
"""
from datetime import date, time
import hashlib
import json
import operator
from pathlib import Path
from statistics import fmean, median, quantiles
import sys

ROOT = Path(__file__).resolve().parents[3]
for package in ("quant_contracts", "quant_loop", "quant_pit_data", "quant_evaluator"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
from quant_contracts.evaluation import Factor
from quant_contracts.pit import SeriesBinding, SessionCalendar
from quant_evaluator.beta.risk_gate import (episode_interval, episodes, equity_curve, forward_drawdown, gate_targets,
                                            max_drawdown, reentry_ratios)
from quant_loop.daily_states import daily_states
from quant_pit_data.asof import load_daily_closes, on_calendar
from quant_pit_data.connectors.yahoo_chart import load_daily

HERE, OUTPUT = Path(__file__).resolve().parent, Path(__file__).resolve().parent / "output"
REGISTRATION = HERE / "registration.json"
reg = json.loads(REGISTRATION.read_text())
SNAPSHOT = ROOT / reg["inputs"]["snapshot"]
for name, digest in reg["inputs"]["sha256"].items():
    if hashlib.sha256((SNAPSHOT / name).read_bytes()).hexdigest() != digest:
        sys.exit(f"{name} does not match the registered hash")
catalog = json.loads((ROOT / "knowledge_base/prior_knowledge/bindings/series_bindings.json").read_text())
if catalog["content_revision"] != reg["bindings_revision"]:
    sys.exit("the bindings revision differs from the registered one")
b = {s["series_id"]: SeriesBinding(**s) for s in catalog["series"]}
calendars = {name: SessionCalendar(**json.loads((SNAPSHOT / f"{name}.json").read_text()))
             for name in {s["calendar"] for s in reg["signals"]}}
equity = calendars["us_equity_sessions_2002_2026"]
OPS = {">": operator.gt, "<": operator.lt}
HORIZON, MERGE, DRAWS, SEED = 20, 20, 2000, 20261122
decision_time = time.fromisoformat(reg["decision"]["time"])
LAST = date(2026, 11, 20)

state_rows, dropped = on_calendar(load_daily_closes(SNAPSHOT / "VIX.csv", b["cboe:vix"])
                                  + load_daily_closes(SNAPSHOT / "VIX3M.csv", b["cboe:vix3m"]), equity)
yields = load_daily_closes(SNAPSHOT / "treasury_real_yield_10y.csv", b["treasury:real_yield_10y"])
print(f"{len(dropped)} VIX/VIX3M rows dropped: not US equity sessions")


def summary(values):
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": fmean(values), "median": median(values),
            "p05": quantiles(values, n=20)[0] if len(values) > 1 else values[0]}


report = {"registration_sha256": hashlib.sha256(REGISTRATION.read_bytes()).hexdigest(),
          "funds": {}}
OUTPUT.mkdir(exist_ok=True)
for subject in reg["subjects"]:
    name, open_b, close_b = subject["subject"], b[subject["open"]], b[subject["close"]]
    prices = load_daily(SNAPSHOT / f"{name}.json", open_b, close_b)
    opens = {o.session: o.value for o in prices if o.series_id == open_b.series_id}
    closes = {o.session: o.value for o in prices if o.series_id == close_b.series_id}
    sessions = [s for s in equity.sessions if date(2009, 1, 2) <= s <= LAST]
    missing = [s for s in sessions if s not in opens or s not in closes]
    if missing:
        sys.exit(f"{name}: no price on {missing[:5]}")
    factors, rules, series_calendars = [], [], {}
    for signal in reg["signals"]:
        spec = dict(signal["factor"])
        if spec["series_ids"][0].startswith("<"):
            spec["series_ids"] = [close_b.series_id]
        factors.append(Factor(**spec))
        rules.append((OPS[signal["stressed_when"]["op"]], signal["stressed_when"]["threshold"]))
        for series_id in spec["series_ids"]:
            series_calendars[series_id] = calendars[signal["calendar"]]
    states = daily_states(sessions, decision_time, factors, b, state_rows + yields + prices, equity, series_calendars)
    signals = [tuple(None if s.value is None else bool(test(s.value, cut)) for s, (test, cut) in zip(row, rules))
               for _, row in states]
    gate = gate_targets(signals, reg["exposure"]["by_stressed_count"])
    targets, held = [t for t, _ in gate], [h for _, h in gate]
    o, c = [opens[s] for s in sessions], [closes[s] for s in sessions]
    start, gated, hold = equity_curve(o, c, targets, reg["accounting"]["cost_rate"])

    lines = ["session," + ",".join(f.factor_id for f in factors) + "," + ",".join(f"{f.factor_id}_session" for f in factors)
             + ",stressed_count,target,held"]
    for session, (_, row), signal, target, was_held in zip(sessions, states, signals, targets, held):
        if target is None:
            continue
        lines.append(",".join([str(session), *("" if s.value is None else f"{s.value:.6f}" for s in row),
                               *(str(s.state.session or "") for s in row),
                               "" if None in signal else str(sum(signal)), f"{target:.2f}", str(int(was_held))]))
    (OUTPUT / f"positions_{name}.csv").write_text("\n".join(lines) + "\n")

    forward = [None if t is None else forward_drawdown(o, c, i, HORIZON) for i, t in enumerate(targets)]
    runs = episodes(targets, MERGE)
    first = start - 1
    in_episode = [False] * len(sessions)
    for a, z in runs:
        for i in range(a, z + 1):
            in_episode[i] = True
    stressed = [[forward[i] for i in range(a, z + 1) if forward[i] is not None and targets[i] < 1.0] for a, z in runs]
    ordinary, current = [], []
    for i in range(first, len(sessions)):
        if in_episode[i]:
            if current:
                ordinary.append(current)
            current = []
        elif forward[i] is not None:
            current.append(forward[i])
    if current:
        ordinary.append(current)
    stressed, ordinary = [r for r in stressed if r], [r for r in ordinary if r]
    measured = [i for i, v in enumerate(forward) if v is not None]
    forward_last = measured[-1]
    outside = sum(1 for a, z in runs if a > forward_last)
    crossing = sum(1 for a, z in runs if a <= forward_last < z)
    ratios, unfinished = reentry_ratios(o, runs)
    whipsaws = [r - 1.0 for r in ratios if r > 1.0]
    held_runs, run_length = [], 0
    for flag in held:
        run_length = run_length + 1 if flag else 0
        held_runs.append(run_length)
    flat_s, flat_o = [v for r in stressed for v in r], [v for r in ordinary for v in r]
    report["funds"][name] = {
        "position_period": {"first_decision": str(sessions[first]), "last_decision": str(sessions[-1]),
                            "decisions": len(sessions) - first},
        "forward_period": {"first_decision": str(sessions[first]), "last_decision": str(sessions[forward_last]),
                           "decisions": len(measured), "episodes_wholly_after_it": outside, "episodes_crossing_its_end": crossing},
        "decisions_by_target": {f"{t:.2f}": sum(1 for x in targets[first:] if x == t) for t in sorted(set(targets[first:]))},
        "held_sessions": sum(held), "longest_held_run": max(held_runs),
        "final_equity": {"gate": gated[-1], "buy_and_hold": hold[-1]},
        "max_drawdown": {"gate": max_drawdown(gated), "buy_and_hold": max_drawdown(hold)},
        "forward_drawdown": {"stressed": {**summary(flat_s), "runs": len(stressed)},
                             "ordinary": {**summary(flat_o), "runs": len(ordinary)},
                             "difference_of_means": fmean(flat_s) - fmean(flat_o) if flat_s and flat_o else None,
                             "interval_90": list(episode_interval(stressed, ordinary, DRAWS, SEED)) if stressed and ordinary else None},
        # Decisions at 100% inside a merged episode are in neither group, as the registration states.
        "full_exposure_decisions_inside_episodes": sum(1 for i in range(first, len(sessions)) if in_episode[i] and targets[i] == 1.0),
        "episodes": {"n": len(runs), "unfinished": unfinished,
                     "list": [{"first": str(sessions[a]), "last": str(sessions[z])} for a, z in runs]},
        "whipsaw_rate": len(whipsaws) / len(ratios) if ratios else None,
        "reentry_lag_cost": {"whipsaw_episodes": fmean(whipsaws) if whipsaws else None,
                             "all_finished_episodes": fmean(r - 1.0 for r in ratios) if ratios else None},
        "positions_sha256": hashlib.sha256((OUTPUT / f"positions_{name}.csv").read_bytes()).hexdigest()}
(OUTPUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
