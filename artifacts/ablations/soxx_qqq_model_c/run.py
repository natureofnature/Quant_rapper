"""Run the registered Model C comparison. Every setting comes from registration.json beside this file.

Three arms per fund over one period: the frozen rule, Model A as frozen in soxx_qqq_model_a, and
Model C, the boosted-tree model, fitted here on the same features, folds and gate.

Usage: run.py                    the registered run, on real prices; writes output/
       run.py --synthetic SEED   a rehearsal on a seeded random walk; writes output_synthetic/

Both stop unless every input matches its registered hash and the frozen rule baseline is intact.

The registered run does not trust the working tree. The registration must carry a real code
commit and be the committed file; this script must equal its version at that commit; and the
packages are exported from git at that commit and imported from there. A rehearsal uses the
local code, replaces both funds' prices before any outcome is computed, and means nothing
about the funds.
"""
import argparse
import atexit
import csv
from datetime import date, time
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
from statistics import fmean, median, quantiles
import subprocess
import sys
import tarfile
import tempfile

ROOT, HERE = Path(__file__).resolve().parents[3], Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description="Run the registered Model C comparison.")
parser.add_argument("--synthetic", type=int, metavar="SEED",
                    help="rehearse on a seeded random walk instead of real prices; writes output_synthetic/")
args = parser.parse_args()
SYNTHETIC = args.synthetic
REGISTRATION = HERE / "registration.json"
reg = json.loads(REGISTRATION.read_text())
code_root = ROOT
if SYNTHETIC is None:  # the registered run: pinned code only
    commit = reg["code"]["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        sys.exit("the registration has no code commit yet: only --synthetic rehearsals can run")
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
from quant_contracts.pit import Observation, SeriesBinding, SessionCalendar
from quant_evaluator.beta.forward_target import class_thresholds, forward_average_return, ordinal_class, trailing_volatility
from quant_evaluator.beta.model_gate import gate_states, paired_recall_difference, tail_recall, walk_forward_left_tail
from quant_evaluator.beta.risk_gate import episode_interval, episodes, equity_curve, forward_drawdown, max_drawdown, reentry_ratios
from quant_loop.feature_panel import feature_panel
from quant_pit_data.asof import load_daily_closes, on_calendar
from quant_pit_data.connectors.yahoo_chart import load_daily

import quant_evaluator.beta.tree_model as loaded_model
if not loaded_model.__file__.startswith(str(code_root)):
    sys.exit("the model code was not loaded from the expected place")
P = reg["parameters"]
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
SNAPSHOT = ROOT / reg["inputs"]["snapshot"]
for name, expected in reg["inputs"]["sha256"].items():
    if digest(SNAPSHOT / name) != expected:
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
GENERIC, REVENUE = tuple(P["features_generic"]), None
subject_inputs = {}
used_bindings = set()
for ticker in P["subjects"]:
    open_b, close_b = b[f"yahoo:{ticker.lower()}:open"], b[f"yahoo:{ticker.lower()}:close"]
    names = list(GENERIC) + ([REVENUE] if ticker in P["comparison_2_subjects"] else [])
    factors = []
    used_bindings.update((open_b.series_id, close_b.series_id))
    for name in names:
        factor = definitions[name]
        series_ids = tuple(close_b.series_id if i == SUBJECT_CLOSE else i for i in factor.series_ids)
        factors.append(factor.model_copy(update={"series_ids": series_ids}))
        used_bindings.update(series_ids)
    subject_inputs[ticker] = (open_b, close_b, factors)
binding_hashes = check_bindings(used_bindings, b, research.bindings_sha256)

equity = SessionCalendar(**json.loads((SNAPSHOT / "us_equity_sessions_2002_2026.json").read_text()))
treasury = SessionCalendar(**json.loads((SNAPSHOT / "us_treasury_yield_sessions_2003_2026.json").read_text()))
state, _ = on_calendar(load_daily_closes(SNAPSHOT / "VIX.csv", b["cboe:vix"])
                       + load_daily_closes(SNAPSHOT / "VIX3M.csv", b["cboe:vix3m"]), equity)
yields = load_daily_closes(SNAPSHOT / "treasury_real_yield_10y.csv", b["treasury:real_yield_10y"])
revenue = []
MODEL_A = ROOT / reg["model_a"]["folder"]
for name, expected in reg["model_a"]["sha256"].items():
    if digest(MODEL_A / name) != expected:
        sys.exit(f"the frozen Model A file {name} does not match the registered hash")
sessions = [s for s in equity.sessions if date.fromisoformat(P["first_session"]) <= s <= date.fromisoformat(P["last_session"])]
HORIZON, DECISION = P["horizon_sessions"], time.fromisoformat(P["decision_time"])
EXPOSURE = [P["exposure_by_state"][k] for k in ("GREEN", "YELLOW", "RED")]
years = (sessions[-1] - sessions[0]).days / 365.25


def summary(values):
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": fmean(values), "median": median(values),
            "p05": quantiles(values, n=20)[0] if len(values) > 1 else values[0]}


def scores_for(features, classes, first, **override):
    setting = {"l2": P["l2_penalty"], "tail_weight": P["tail_weight"], "embargo": P["embargo_sessions"],
               "rolling_sessions": None, "min_train": P["min_train"], "tree": P["tree"], **override}
    return walk_forward_left_tail(sessions, features, classes, first, horizon=HORIZON, **setting)


def gate_targets_from(scores):
    states = gate_states(scores, P["gate"]["red_quantile"], P["gate"]["yellow_quantile"], P["gate"]["window"],
                         P["gate"]["min_scores"], P["gate"]["step_down_after"])
    return [None if s is None else EXPOSURE[s] for s, _ in states], [h for _, h in states], [s for s, _ in states]


def arm_report(name, targets, held, o, c, drawdowns, classes, start, cost_rate):
    """Everything reported for one arm over the comparison period, which begins at decision ``start``."""
    period = [t if i >= start else None for i, t in enumerate(targets)]
    first_traded, gated, hold = equity_curve(o, c, period, cost_rate)
    runs = episodes(period, P["episode_gap"])
    ratios, unfinished = reentry_ratios(o, runs)
    in_episode = [False] * len(sessions)
    for a, z in runs:
        for i in range(a, z + 1):
            in_episode[i] = True
    stressed = [[drawdowns[i] for i in range(a, z + 1) if drawdowns[i] is not None and period[i] < 1.0] for a, z in runs]
    ordinary, current = [], []
    for i in range(start, len(sessions)):
        if in_episode[i]:
            if current:
                ordinary.append(current)
            current = []
        elif drawdowns[i] is not None:
            current.append(drawdowns[i])
    if current:
        ordinary.append(current)
    stressed, ordinary = [r for r in stressed if r], [r for r in ordinary if r]
    flat_s, flat_o = [v for r in stressed for v in r], [v for r in ordinary for v in r]
    alerts = [None if t is None else t < 1.0 for t in period]
    recall = tail_recall(drawdowns, alerts, P["worst_share"])
    scored = sorted((drawdowns[i], classes[i]) for i in range(start, len(sessions)) if drawdowns[i] is not None)
    worst = scored[:max(1, int(len(scored) * P["worst_share"]))]
    executable = period[start:-1]  # the last decision's target has no next open, as in the equity curve
    traded = sum(abs(b_ - a_) for a_, b_ in zip([1.0] + executable[:-1], executable))
    whipsaws = [r - 1.0 for r in ratios if r > 1.0]
    return {"arm": name, "first_decision": str(sessions[start]), "first_session_traded": str(sessions[first_traded]),
            "last_session": str(sessions[-1]), "decisions": len(sessions) - start, "held_decisions": sum(held[start:]),
            "decisions_by_target": {f"{t:.2f}": sum(1 for x in period[start:] if x == t) for t in sorted({x for x in period[start:]})},
            "max_drawdown": max_drawdown(gated), "final_equity": gated[-1],
            "buy_and_hold": {"max_drawdown": max_drawdown(hold), "final_equity": hold[-1]},
            "turnover_per_year": traded / ((sessions[-1] - sessions[start]).days / 365.25),
            "episodes": len(runs), "unfinished_episodes": unfinished,
            "whipsaw_rate": len(whipsaws) / len(ratios) if ratios else None,
            "reentry_cost_all_finished": fmean(r - 1.0 for r in ratios) if ratios else None,
            "forward_drawdown": {"stressed": {**summary(flat_s), "runs": len(stressed)}, "ordinary": {**summary(flat_o), "runs": len(ordinary)},
                                 "difference_of_means": fmean(flat_s) - fmean(flat_o) if flat_s and flat_o else None,
                                 "interval_90": list(episode_interval(stressed, ordinary, P["draws"], P["seed"])) if stressed and ordinary else None},
            "tail_recall": recall,
            "worst_windows_labelled_class_0": sum(1 for _, k in worst if k == 0) / len(worst) if worst else None}, period, alerts


def write_positions(name, period, held, extra):
    lines = ["session,target,held" + "".join(f",{k}" for k in extra)]
    for i, session in enumerate(sessions):
        if period[i] is not None:
            lines.append(f"{session},{period[i]:.2f},{int(held[i])}" + "".join(
                "," + ("" if extra[k][i] is None else f"{extra[k][i]:.6f}" if isinstance(extra[k][i], float) else str(extra[k][i])) for k in extra))
    path = OUT / f"positions_{name}.csv"
    path.write_text("\n".join(lines) + "\n")
    return digest(path)


OUT = HERE / ("output" if SYNTHETIC is None else "output_synthetic")
OUT.mkdir(exist_ok=True)
report = {"registration_sha256": digest(REGISTRATION), "bindings_sha256": binding_hashes, "comparison_1": {}, "comparison_2": {}}
for ticker, (open_b, close_b, factors) in subject_inputs.items():
    prices = load_daily(SNAPSHOT / f"{ticker}.json", open_b, close_b)
    if SYNTHETIC is not None:  # a random walk in place of the fund's prices: every outcome below is then meaningless
        rng, level, fake = np.random.default_rng(SYNTHETIC + len(ticker)), 100.0, []
        for x in sorted(prices, key=lambda x: (x.session, x.series_id.endswith(":close"))):
            level *= float(np.exp(rng.normal(0.0, 0.01)))
            fake.append(x.model_copy(update={"value": level}))
        prices = fake
    opens = {x.session: x.value for x in prices if x.series_id == open_b.series_id}
    closes = {x.session: x.value for x in prices if x.series_id == close_b.series_id}
    o, c = [opens.get(s) for s in sessions], [closes.get(s) for s in sessions]
    calendars = {"treasury:real_yield_10y": treasury}
    panel = feature_panel(ticker, sessions, DECISION, factors, b, state + yields + prices + revenue, equity, calendars)
    matrix = {f.factor_id: np.asarray([row.features[f.factor_id] for row in panel], dtype=float) for f in factors}
    floor, cap = P["class_bounds"][ticker]
    outcomes = [forward_average_return(o, c, i, HORIZON) for i in range(len(sessions))]
    classes = []
    for i, value in enumerate(outcomes):
        volatility = trailing_volatility(c, i, P["volatility_sessions"])
        classes.append(None if value is None or volatility is None else ordinal_class(value, class_thresholds(volatility, floor, cap, HORIZON)))
    drawdowns = [forward_drawdown(o, c, i, HORIZON) if outcomes[i] is not None else None for i in range(len(sessions))]
    generic = np.column_stack([matrix[n] for n in GENERIC])
    first = sessions[next(i for i in range(len(sessions)) if np.isfinite(generic[i]).all())]

    # Comparison 1: the frozen rule against Model A on the generic features, over the model gate's period.
    with (BASELINE / f"output/positions_{ticker}.csv").open(newline="") as handle:
        frozen = {date.fromisoformat(r["session"]): (float(r["target"]), r["held"] == "1") for r in csv.DictReader(handle)}
    rule_targets = [frozen[s][0] if s in frozen else None for s in sessions]
    rule_held = [frozen[s][1] if s in frozen else False for s in sessions]
    scores = scores_for(generic, classes, first)
    model_targets, model_held, model_states = gate_targets_from(scores)
    start = next(i for i, t in enumerate(model_targets) if t is not None)
    rate = reg["shared_setup"]["accounting"]["cost_rate"]
    rule, rule_period, _ = arm_report("frozen rule", rule_targets, rule_held, o, c, drawdowns, classes, start, rate)
    model, model_period, _ = arm_report("Model C, generic features", model_targets, model_held, o, c, drawdowns, classes, start, rate)
    with (MODEL_A / f"output/positions_{ticker}_model_a.csv").open(newline="") as handle:
        earlier = {date.fromisoformat(r["session"]): (float(r["target"]), r["held"] == "1") for r in csv.DictReader(handle)}
    a_targets = [earlier[s][0] if s in earlier else None for s in sessions]
    a_held = [earlier[s][1] if s in earlier else False for s in sessions]
    model_a, _, _ = arm_report("Model A, as frozen", a_targets, a_held, o, c, drawdowns, classes, start, rate)
    rule["positions_sha256"] = write_positions(f"{ticker}_rule", rule_period, rule_held, {})
    model["positions_sha256"] = write_positions(f"{ticker}_model_c", model_period, model_held, {"p_left": scores, "state": model_states})
    limits = P["promotion_limits"]
    checks = {"drawdown_smaller_by_points": (abs(rule["max_drawdown"]) - abs(model["max_drawdown"])) * 100 >= limits["drawdown_points"],
              "final_equity_share_of_rule": model["final_equity"] >= limits["equity_share"] * rule["final_equity"],
              "whipsaw_within_points": (model["whipsaw_rate"] is not None and rule["whipsaw_rate"] is not None
                                        and (model["whipsaw_rate"] - rule["whipsaw_rate"]) * 100 <= limits["whipsaw_points"])}
    sensitivity = {}
    for label, override, multiple in (("cost_x2", {}, 2), ("cost_x3", {}, 3), ("rolling_5y", {"rolling_sessions": P["rolling_sessions"]}, 1),
                                      ("embargo_5", {"embargo": P["sensitivity_embargo"]}, 1)):
        alt_targets, alt_held, _ = gate_targets_from(scores_for(generic, classes, first, **override)) if override else (model_targets, model_held, None)
        alt_start = next(i for i, t in enumerate(alt_targets) if t is not None)
        alt, _, _ = arm_report(label, alt_targets, alt_held, o, c, drawdowns, classes, alt_start, rate * multiple)
        alt_rule, _, _ = arm_report(label, rule_targets, rule_held, o, c, drawdowns, classes, alt_start, rate * multiple)
        sensitivity[label] = {"first_decision": alt["first_decision"],
                              "model": {k: alt[k] for k in ("max_drawdown", "final_equity", "whipsaw_rate")},
                              "rule": {k: alt_rule[k] for k in ("max_drawdown", "final_equity", "whipsaw_rate")}}
    report["comparison_1"][ticker] = {"rule": rule, "model_a": model_a, "model": model, "checks": checks,
                                      "decision": "model replaces the rule" if all(checks.values()) else "the rule is kept",
                                      "sensitivity_decides_nothing": sensitivity}

    # Comparison 2: the same model with and without the graph-derived factor, on identical dates.
    if ticker not in P["comparison_2_subjects"]:
        continue
    present = np.isfinite(matrix[REVENUE])
    matched = np.where(present[:, None], generic, np.nan)  # the generic arm sees only the dates the factor covers
    augmented = np.column_stack([matched, matrix[REVENUE]])
    first2 = sessions[next(i for i in range(len(sessions)) if np.isfinite(augmented[i]).all())]
    arms = {}
    for label, features in (("without", matched), ("with", augmented)):
        s = scores_for(features, classes, first2)
        targets, held, states = gate_targets_from(s)
        arms[label] = (s, targets, held, states)
    start2 = max(next(i for i, t in enumerate(arms[k][1]) if t is not None) for k in arms)
    out, alerts = {}, {}
    for label, (s, targets, held, states) in arms.items():
        out[label], period, alerts[label] = arm_report(f"Model A {label} {REVENUE}", targets, held, o, c, drawdowns, classes, start2, rate)
        out[label]["positions_sha256"] = write_positions(f"{ticker}_{label}_revenue", period, held, {"p_left": s, "state": states})
    paired = paired_recall_difference(drawdowns, alerts["without"], alerts["with"], P["draws"], P["seed"], P["worst_share"], P["episode_gap"])
    eligible = [i for i in range(start2, len(sessions))  # the decisions recall is computed on: an outcome, and a state in both arms
                if drawdowns[i] is not None and alerts["with"][i] is not None and alerts["without"][i] is not None]
    gate_days = len(eligible)
    held_days = sum(1 for i in eligible if arms["with"][2][i])
    need = P["sufficient_coverage"]
    coverage = {"gate_days_with_outcome": gate_days, "held_days": held_days, "realised_drawdown_episodes": paired["episodes"],
                "sufficient": gate_days >= need["gate_days"] and held_days <= need["held_share"] * gate_days and paired["episodes"] >= need["episodes"]}
    rule2 = P["increment_rule"]
    rate_of = lambda a: sum(bool(a[i]) for i in eligible) / len(eligible)  # over the same decisions as recall
    evidence = {"recall_higher_by_points": paired["difference"] is not None and paired["difference"] * 100 >= rule2["recall_points"],
                "alert_share_rise_within_points": (rate_of(alerts["with"]) - rate_of(alerts["without"])) * 100 <= rule2["alert_share_points"],
                "interval_above_zero": paired["interval"] is not None and paired["interval"][0] > 0,
                "max_drawdown_not_larger": abs(out["with"]["max_drawdown"]) <= abs(out["without"]["max_drawdown"])}
    status = ("not completed: insufficient coverage" if not coverage["sufficient"] else
              "completed, evidence threshold met" if all(evidence.values()) else "completed, evidence threshold not met (a null result)")
    report["comparison_2"][ticker] = {"without": out["without"], "with": out["with"], "paired_recall": paired,
                                      "alert_share": {k: rate_of(alerts[k]) for k in alerts}, "coverage": coverage,
                                      "evidence": evidence, "status": status}
report["synthetic_prices"] = SYNTHETIC is not None
(OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"comparison_1": {t: {"decision": v["decision"], "checks": v["checks"]} for t, v in report["comparison_1"].items()},
                  "comparison_2": {t: {"status": v["status"], "evidence": v["evidence"], "coverage": v["coverage"]}
                                   for t, v in report["comparison_2"].items()}}, indent=1))
