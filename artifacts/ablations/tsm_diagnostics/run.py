"""Run the registered TSM diagnostics. EXPLORATORY: every definition comes from registration.json beside this file.

Usage: run.py   Writes output/report.json, output/shocks.csv and output/events.csv.
"""
import calendar as month_lengths
import csv
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import re
from statistics import fmean, median
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT, HERE = Path(__file__).resolve().parents[3], Path(__file__).resolve().parent
for package in ("quant_contracts", "quant_loop", "quant_pit_data", "quant_evaluator", "quant_causal_graph"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
import numpy as np
from quant_causal_graph.recipes import event_state_recipe, project_leads
from quant_contracts.evaluation import EventTime, Factor
from quant_contracts.pit import Cutoff, SeriesBinding, SessionCalendar
from quant_evaluator.alpha.event_window import beta_before, compare_groups
from quant_loop.event_table import build_table
from quant_pit_data.asof import load_daily_closes, on_calendar
from quant_pit_data.connectors.yahoo_chart import load_daily

reg = json.loads((HERE / "registration.json").read_text()); P = reg["parameters"]
if not re.fullmatch(r"[0-9a-f]{40}", reg["code"]["commit"]):
    sys.exit("the registration has no code commit yet")
SNAP = ROOT / reg["inputs"]["snapshot"]; NY = ZoneInfo("America/New_York")
for name, expected in reg["inputs"]["sha256"].items():
    if hashlib.sha256((SNAP / name).read_bytes()).hexdigest() != expected:
        sys.exit(f"{name} does not match the registered hash")
relationships = ROOT / "knowledge_base/prior_knowledge/Relationships"
b = {s["series_id"]: SeriesBinding(**s) for s in json.loads((relationships.parent / "bindings/series_bindings.json").read_text())["series"]}
equity = SessionCalendar(**json.loads((SNAP / "us_equity_sessions_2002_2026.json").read_text()))
sessions = [s for s in equity.sessions if date.fromisoformat(P["first_session"]) <= s <= date.fromisoformat(P["last_session"])]
index = {s: i for i, s in enumerate(sessions)}
N, K = len(sessions), P["beta_sessions"]


def series(ticker):
    ob, cb = b[f"yahoo:{ticker.lower()}:open"], b[f"yahoo:{ticker.lower()}:close"]
    rows = load_daily(SNAP / f"{ticker}.json", ob, cb)
    o = {x.session: x.value for x in rows if x.series_id == ob.series_id}; c = {x.session: x.value for x in rows if x.series_id == cb.series_id}
    return rows, np.array([o.get(s, np.nan) for s in sessions]), np.array([c.get(s, np.nan) for s in sessions])


tsm_rows, tsm_o, tsm_c = series("TSM"); soxx_rows, soxx_o, soxx_c = series("SOXX"); _, _, spy_c = series("SPY")
ret = lambda c: np.concatenate([[np.nan], c[1:] / c[:-1] - 1.0])
r_tsm, r_soxx, r_spy = ret(tsm_c), ret(soxx_c), ret(spy_c)

# Part 1. Residual of session t from betas fitted on the K returns ending at t-1.
resid = {k: np.full(N, np.nan) for k in ("spy", "soxx", "both")}
beta_soxx = np.full(N, np.nan)
for t in range(K + 1, N):
    y, x1, x2 = r_tsm[t - K:t], r_spy[t - K:t], r_soxx[t - K:t]
    if not (np.isfinite(y).all() and np.isfinite(x1).all() and np.isfinite(x2).all()):
        continue
    for key, design in (("spy", [x1]), ("soxx", [x2]), ("both", [x1, x2])):
        A = np.column_stack([np.ones(K), *design]); coef = np.linalg.lstsq(A, y, rcond=None)[0]
        today = [r_spy[t]] if key == "spy" else [r_soxx[t]] if key == "soxx" else [r_spy[t], r_soxx[t]]
        resid[key][t] = r_tsm[t] - float(np.dot(coef[1:], today))
        if key == "soxx":
            beta_soxx[t] = coef[1]
ok = np.isfinite(resid["both"])
share = lambda mask, key: float(np.var(resid[key][mask]) / np.var(r_tsm[mask]))
variance = {"sessions": int(ok.sum()), "first": str(sessions[int(np.argmax(ok))]),
            "residual_share_of_variance": {k: share(ok, k) for k in resid},
            "by_year": {str(y): {k: share(ok & np.array([s.year == y for s in sessions]), k) for k in resid}
                        for y in sorted({s.year for s, g in zip(sessions, ok) if g})}}

# Catalyst sessions.
def on_or_after(day):
    return next((i for i, s in enumerate(sessions) if s >= day), None)

dates = json.loads((SNAP / "announcement_dates_2002_2026.json").read_text())
events = json.loads((SNAP / "tsmc_monthly_revenue_events.json").read_text())["events"]
catalysts = {"cpi": {on_or_after(date.fromisoformat(d)) for d in dates["cpi"]["dates"]},
             "ppi": {on_or_after(date.fromisoformat(d)) for d in dates["ppi"]["dates"]},
             "fomc": {on_or_after(date.fromisoformat(d)) for d in dates["fomc"]["scheduled"] + dates["fomc"]["unscheduled"]},
             "tsmc_earnings": {on_or_after(date.fromisoformat(r["date"])) for r in dates["tsmc_earnings"]["releases"]},
             "tsmc_monthly_revenue": set(), "opex": set()}
entry_of = {}
for e in events:
    accepted = datetime.fromisoformat(e["accepted"]).replace(tzinfo=NY)
    i = next((i for i, s in enumerate(sessions) if datetime.combine(s, time(9, 30), NY) > accepted), None)
    entry_of[e["month"]] = i
    catalysts["tsmc_monthly_revenue"].add(i)
for year in range(sessions[0].year, sessions[-1].year + 1):
    for month in range(1, 13):
        fridays = [date(year, month, d) for d in range(1, month_lengths.monthrange(year, month)[1] + 1) if date(year, month, d).weekday() == 4]
        day = fridays[2] if fridays[2] in index else fridays[2] - timedelta(days=1)
        if day in index:
            catalysts["opex"].add(index[day])
catalysts = {k: {i for i in v if i is not None} for k, v in catalysts.items()}
any_catalyst = set().union(*catalysts.values())
sq = resid["soxx"] ** 2; valid = np.isfinite(sq); total = float(sq[valid].sum())
def concentration(ids):
    ids = [i for i in ids if valid[i]]
    return {"sessions": len(ids), "share_of_sessions": len(ids) / int(valid.sum()), "share_of_squared_residual": float(sq[ids].sum()) / total}
catalyst_report = {k: concentration(v) for k, v in catalysts.items()} | {"any_catalyst": concentration(any_catalyst)}

# Shock audit: non-catalyst sessions whose residual exceeds the threshold times its own trailing standard deviation.
shocks = []
for t in range(N):
    window = resid["soxx"][t - K:t] if t >= K else np.array([np.nan])
    if t in any_catalyst or not np.isfinite(resid["soxx"][t]) or not np.isfinite(window).all():
        continue
    scale = float(np.std(window, ddof=1))
    if abs(resid["soxx"][t]) > P["shock_threshold"] * scale:
        shocks.append((sessions[t], float(resid["soxx"][t]), float(resid["soxx"][t] / scale), float(r_tsm[t]), float(r_soxx[t])))
(HERE / "output").mkdir(exist_ok=True)
with (HERE / "output/shocks.csv").open("w", newline="") as handle:
    csv.writer(handle).writerows([("session", "residual", "in_trailing_standard_deviations", "tsm_return", "soxx_return")]
                                 + [(s, f"{r:.6f}", f"{z:.2f}", f"{a:.6f}", f"{c:.6f}") for s, r, z, a, c in shocks])

# Part 2. Event rows with a pre-event beta, through the event table.
frozen = datetime.fromisoformat(subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%cI", "68e1218c7a01de80f53621048fb41ef2a06340a9"],
                                               check=True, capture_output=True, text=True).stdout.strip())
lead = project_leads(relationships, "semiconductor", "TSM", frozen)["lead:economy:risk_appetite:sector_beta:sector"]
subject, benchmark = (b["yahoo:tsm:open"], b["yahoo:tsm:close"]), (b["yahoo:soxx:open"], b["yahoo:soxx:close"])
recipe = event_state_recipe("recipe:tsmc:monthly_revenue:diagnostics", [lead], frozen, "TSMC monthly revenue report", "filing", "SOXX",
                            P["horizon_sessions"], [b["cboe:vix"], b["cboe:vix3m"], *subject, *benchmark])
state, _ = on_calendar(load_daily_closes(SNAP / "VIX.csv", b["cboe:vix"]) + load_daily_closes(SNAP / "VIX3M.csv", b["cboe:vix3m"]), equity)
factors = [Factor(factor_id="vix_term_structure", measure="ratio", series_ids=("cboe:vix", "cboe:vix3m"), max_age_days=P["state_max_age_days"]),
           Factor(factor_id="vix_level", measure="level", series_ids=("cboe:vix",), max_age_days=P["state_max_age_days"])]
def event_time(e):
    accepted = datetime.fromisoformat(e["accepted"]).replace(tzinfo=NY)
    return EventTime(event_id=f"tsmc:monthly_revenue:{e['month']}", cutoff=Cutoff(time=accepted, basis="upper_bound", source="SEC EDGAR acceptance"),
                     state_cutoff=Cutoff(time=accepted.replace(hour=0, minute=0, second=0, microsecond=0), basis="assumed",
                                         source="Start of the US Eastern day of the EDGAR acceptance"))
table = build_table(recipe, [event_time(e) for e in events], factors, {i: b[i] for f in factors for i in f.series_ids}, subject, benchmark,
                    state + tsm_rows + soxx_rows, equity, {}, reg["inputs"]["sha256"], beta_sessions=K)
vix = {x.session: x.value for x in state if x.series_id == "cboe:vix"}; vix3 = {x.session: x.value for x in state if x.series_id == "cboe:vix3m"}
collision = {i: sorted(f"COLLISION_{k.upper().replace('TSMC_', '')}" for k in ("cpi", "ppi", "fomc", "tsmc_earnings") if i in catalysts[k]) for i in range(N)}
rows = []
for row in table.rows:
    if row.status != "complete":
        continue
    i = index[row.entry_session]; beta = row.window.pre_beta
    day_s, day_b = tsm_c[i] / tsm_o[i] - 1.0, soxx_c[i] / soxx_o[i] - 1.0
    rows.append({"event_id": row.event_id, "entry": row.entry_session, "i": i, "pre_beta": beta,
                 "state": {s.factor_id: s.value for s in row.factors}, "collisions": collision[i],
                 "reaction": {"raw": row.reaction.subject, "relative": row.reaction.relative, "abnormal": row.reaction.beta_adjusted},
                 "entry_day": {"raw": day_s, "relative": day_s - day_b, "abnormal": None if beta is None else day_s - beta * day_b},
                 "window": {"raw": row.window.subject, "relative": row.window.relative, "abnormal": row.window.beta_adjusted}})
def episodes_of(group):
    months = sorted(int(r["event_id"][-7:-3]) * 12 + int(r["event_id"][-2:]) for r in group)
    return sum(1 for k, m in enumerate(months) if k == 0 or m - months[k - 1] >= P["episode_gap_months"])
question_1, small_groups = {}, {}
for factor_id, cut in P["thresholds"].items():
    have = [r for r in rows if r["state"][factor_id] is not None]
    high, low = [r for r in have if r["state"][factor_id] >= cut], [r for r in have if r["state"][factor_id] < cut]
    for outcome in ("reaction", "entry_day", "window"):
        for measure in ("raw", "relative", "abnormal"):
            a = [r[outcome][measure] for r in high if r[outcome][measure] is not None]; c = [r[outcome][measure] for r in low if r[outcome][measure] is not None]
            result = compare_groups(f"{outcome} {measure}: {factor_id} >= {cut} against < {cut}", a, c).model_dump()
            result["episodes_a"], result["episodes_b"] = episodes_of(high), episodes_of(low)
            question_1[f"{factor_id}|{outcome}|{measure}"] = result
    for name, group in (("at_or_above", high), ("below", low)):
        if len(group) < P["list_individually_below"]:
            small_groups[f"{factor_id} {name} {cut}"] = [{"event_id": r["event_id"], "entry": str(r["entry"]), "state": r["state"][factor_id], "pre_beta": r["pre_beta"],
                                                        "collisions": r["collisions"], **{f"{o}_{m}": r[o][m] for o in ("reaction", "entry_day", "window") for m in ("raw", "relative", "abnormal")}} for r in group]

# Question 2: |abnormal| on announcement sessions against other sessions in the same state, compared across states.
daily = np.full(N, np.nan)
for t in range(K + 1, N):
    beta = beta_before(list(tsm_c), list(soxx_c), t - 1, K)
    if beta is not None and all(np.isfinite(v) for v in (tsm_o[t], tsm_c[t], soxx_o[t], soxx_c[t])):
        daily[t] = abs((tsm_c[t] / tsm_o[t] - 1.0) - beta * (soxx_c[t] / soxx_o[t] - 1.0))
def daily_state(t, factor_id):  # read as an event's is: from the last session before this one, no older than the age limit
    for back in range(1, 6):
        s = sessions[t - back] if t - back >= 0 else None
        if s is None or (sessions[t] - s).days > P["state_max_age_days"]:
            return None
        if s in vix and (factor_id == "vix_level" or s in vix3):
            return vix[s] if factor_id == "vix_level" else (vix[s] / vix3[s] if vix3[s] else None)
    return None
announcement = {r["i"] for r in rows}
question_2 = {}
for factor_id, cut in P["thresholds"].items():
    for label, drop in (("all_sessions", False), ("without_collision_sessions", True)):
        cells = {}
        for t in range(N):
            value = daily_state(t, factor_id)
            if value is None or not np.isfinite(daily[t]) or (drop and collision[t]):
                continue
            cells.setdefault(("high" if value >= cut else "low", "announcement" if t in announcement else "other"), []).append(float(daily[t]))
        mean = {k: fmean(v) for k, v in cells.items()}; var_n = {k: float(np.var(v, ddof=1)) / len(v) if len(v) > 1 else None for k, v in cells.items()}
        complete = len(cells) == 4 and all(v is not None for v in var_n.values())
        did = (mean[("high", "announcement")] - mean[("high", "other")]) - (mean[("low", "announcement")] - mean[("low", "other")]) if len(cells) == 4 else None
        question_2[f"{factor_id}|{label}"] = {"n": {f"{a}_{c}": len(v) for (a, c), v in cells.items()}, "mean_abs_abnormal": {f"{a}_{c}": mean[(a, c)] for a, c in cells},
                                             "difference_in_differences": did, "standard_error": sum(var_n.values()) ** 0.5 if complete else None}
with (HERE / "output/events.csv").open("w", newline="") as handle:
    w = csv.writer(handle); w.writerow(["event_id", "entry", "pre_beta", "vix_term_structure", "vix_level", "collisions"] + [f"{o}_{m}" for o in ("reaction", "entry_day", "window") for m in ("raw", "relative", "abnormal")])
    for r in rows:
        w.writerow([r["event_id"], r["entry"], "" if r["pre_beta"] is None else f"{r['pre_beta']:.4f}", *("" if r["state"][f] is None else f"{r['state'][f]:.4f}" for f in ("vix_term_structure", "vix_level")),
                    " ".join(r["collisions"])] + ["" if r[o][m] is None else f"{r[o][m]:.6f}" for o in ("reaction", "entry_day", "window") for m in ("raw", "relative", "abnormal")])
report = {"status": "EXPLORATORY", "registration_sha256": hashlib.sha256((HERE / "registration.json").read_bytes()).hexdigest(),
          "part_1": {"variance": variance, "catalyst_concentration_in_soxx_adjusted_residual": catalyst_report,
                     "shock_audit": {"sessions_listed": len(shocks), "eligible_sessions": int(sum(1 for t in range(N) if t not in any_catalyst and np.isfinite(resid['soxx'][t]))), "file": "shocks.csv"}},
          "part_2": {"events": len(rows), "events_with_a_pre_event_beta": sum(r["pre_beta"] is not None for r in rows),
                     "events_with_a_collision": sum(bool(r["collisions"]) for r in rows), "question_1_window_mean": question_1,
                     "question_2_volatility": question_2, "small_groups_listed_individually": small_groups},
          "data_gaps": reg["data_gaps"]}
(HERE / "output/report.json").write_text(json.dumps(report, indent=1) + "\n")
print("done:", len(rows), "events,", len(shocks), "shocks listed")
