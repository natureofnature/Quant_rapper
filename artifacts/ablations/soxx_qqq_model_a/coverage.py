"""Coverage only: when each comparison could first train and first trade. It reads no outcome's value.

Usage: coverage.py [REVENUE_OBSERVATIONS.json]

It builds the real feature panel, asks only whether each decision's outcome window and
volatility window are complete, and walks the monthly folds. For each fund and comparison
it reports the first trainable month, the first decision the gate could act on, and the
number of forecast days. Returns, classes and model fits are never computed here.

The revenue file is optional and may cover any span. Reporting periods exist whether or not
a release is held for them, so the period calendar is built here over the whole sample, and
a month without a verified release is a missing value, never an error. Comparison 2's
status is always reported, with the months that have no release.

The first eligible decision follows the live gate: the first decision that has a forecast
and at least 252 earlier forecasts. Days without a forecast count as held only after that.
"""
import calendar as month_lengths
from datetime import date, time
import hashlib
import json
from pathlib import Path
import sys

ROOT, HERE = Path(__file__).resolve().parents[3], Path(__file__).resolve().parent
for package in ("quant_contracts", "quant_loop", "quant_pit_data", "quant_evaluator", "quant_causal_graph"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
from quant_contracts.causal_map import SUBJECT_CLOSE, ResearchInputs
from quant_contracts.pit import Observation, SeriesBinding, SessionCalendar
from quant_evaluator.beta.forward_target import HORIZON, target_end, usable
from quant_evaluator.beta.walkforward import monthly_folds
from quant_loop.feature_panel import feature_panel
from quant_pit_data.asof import load_daily_closes, on_calendar
from quant_pit_data.connectors.yahoo_chart import load_daily

SNAPSHOT = ROOT / "artifacts/snapshots/2026-11-22"
MIN_TRAIN, MIN_SCORES, LAST = 750, 252, date(2026, 11, 20)
GENERIC = ("vix_term_structure", "real_yield_10y_change_20_rank_250", "trend_200")
REVENUE = "reported_revenue_growth_yoy"
manifest = json.loads((SNAPSHOT / "manifest.json").read_text())["files"]
for name in ("VIX.csv", "VIX3M.csv", "SOXX.json", "QQQ.json", "treasury_real_yield_10y.csv"):
    if hashlib.sha256((SNAPSHOT / name).read_bytes()).hexdigest() != manifest[name]["sha256"]:
        sys.exit(f"{name} does not match the snapshot manifest")
bindings_dir = ROOT / "knowledge_base/prior_knowledge/bindings"
b = {s["series_id"]: SeriesBinding(**s) for s in json.loads((bindings_dir / "series_bindings.json").read_text())["series"]}
definitions = {d.factor.factor_id: d.factor for d in
               ResearchInputs.model_validate_json((bindings_dir / "factor_definitions.json").read_text()).factors}
equity = SessionCalendar(**json.loads((SNAPSHOT / "us_equity_sessions_2002_2026.json").read_text()))
treasury = SessionCalendar(**json.loads((SNAPSHOT / "us_treasury_yield_sessions_2003_2026.json").read_text()))
state, _ = on_calendar(load_daily_closes(SNAPSHOT / "VIX.csv", b["cboe:vix"])
                       + load_daily_closes(SNAPSHOT / "VIX3M.csv", b["cboe:vix3m"]), equity)
yields = load_daily_closes(SNAPSHOT / "treasury_real_yield_10y.csv", b["treasury:real_yield_10y"])
sessions = [s for s in equity.sessions if date(2009, 1, 2) <= s <= LAST]
revenue = [Observation(**o) for o in json.loads(Path(sys.argv[1]).read_text())["observations"]] if len(sys.argv) > 1 else []
month_ends = [date(y, m, month_lengths.monthrange(y, m)[1]) for y in range(2008, 2027) for m in range(1, 13)]
periods = SessionCalendar(calendar_id="monthly_periods_2008_2026", timezone="Asia/Taipei", first=date(2008, 1, 1),
                          last=date(2026, 12, 31), sessions=tuple(month_ends),
                          basis_note="One session per calendar month, dated on its last day. A reporting period, not a trading calendar.")
released = {o.session for o in revenue}
missing_months = [f"{d.year}-{d.month:02d}" for d in month_ends if date(2009, 8, 31) <= d <= date(2026, 10, 31) and d not in released]
report = {}
for ticker in ("SOXX", "QQQ"):
    open_b, close_b = b[f"yahoo:{ticker.lower()}:open"], b[f"yahoo:{ticker.lower()}:close"]
    prices = load_daily(SNAPSHOT / f"{ticker}.json", open_b, close_b)
    opens = {o.session: o.value for o in prices if o.series_id == open_b.series_id}
    closes = {o.session: o.value for o in prices if o.series_id == close_b.series_id}
    o, c = [opens.get(s) for s in sessions], [closes.get(s) for s in sessions]
    # Whether the outcome and the class could be computed: completeness only, never the value.
    outcome_complete = [target_end(i) < len(sessions) and usable(o[i + 1])
                        and all(usable(v) for v in c[i + 1:target_end(i) + 1]) for i in range(len(sessions))]
    labelled = [complete and i >= 20 and all(usable(v) for v in c[i - 20:i + 1])
                for i, complete in enumerate(outcome_complete)]
    names = list(GENERIC) + ([REVENUE] if ticker == "SOXX" else [])
    factors = [definitions[n].model_copy(update={"series_ids": tuple(close_b.series_id if i == SUBJECT_CLOSE else i
                                                                    for i in definitions[n].series_ids)}) for n in names]
    calendars = {"treasury:real_yield_10y": treasury, definitions[REVENUE].series_ids[0]: periods}
    panel = feature_panel(ticker, sessions, time(17, 0), factors, b, state + yields + prices + revenue, equity, calendars)
    report[ticker] = {}
    for label, used in (("comparison_1_generic", GENERIC), ("comparison_2_with_revenue", names if REVENUE in names else None)):
        if used is None:
            continue
        have = [all(row.features[n] is not None for n in used) for row in panel]
        trainable = [h and l for h, l in zip(have, labelled)]
        if not any(have):
            report[ticker][label] = {"features": list(used), "status": "not run: no decision has every feature"
                                     + ("; no revenue file was supplied" if not revenue else ""),
                                     "forecast_days_with_complete_outcome": 0, "gate_days_with_complete_outcome": 0,
                                     "release_months_missing_2009-08_to_2026-10": len(missing_months)}
            continue
        first_feature = next(i for i, h in enumerate(have) if h)
        first_month, forecasts, eligible, held = None, [], None, 0
        for fold in monthly_folds(sessions, sessions[first_feature], min_train=1):
            if sum(trainable[i] for i in fold.train) < MIN_TRAIN:
                continue
            first_month = first_month or fold.month
            for i in fold.test:
                if have[i]:
                    if eligible is None and len(forecasts) >= MIN_SCORES:
                        eligible = i  # as the live gate: its first state needs a score now and 252 before it
                    forecasts.append(i)
                elif eligible is not None:
                    held += 1
        active_forecasts = [i for i in forecasts if eligible is not None and i >= eligible]
        report[ticker][label] = {
            "status": "coverage computed" if eligible is not None else "not run: the gate never has 252 earlier forecasts",
            "features": list(used), "first_decision_with_all_features": str(sessions[first_feature]),
            "decisions_with_all_features": sum(have), "decisions_missing_a_feature_after_that": sum(not h for h in have[first_feature:]),
            "first_trainable_month": f"{first_month[0]}-{first_month[1]:02d}" if first_month else None,
            "first_forecast": str(sessions[forecasts[0]]) if forecasts else None,
            "first_eligible_trading_decision": str(sessions[eligible]) if eligible is not None else None,
            "forecast_days_from_then": len(active_forecasts), "held_days_from_then": held,
            "forecast_days_with_complete_outcome": sum(outcome_complete[i] for i in active_forecasts),
            "gate_days_with_complete_outcome": sum(outcome_complete[eligible:]) if eligible is not None else 0,
            "last_decision_with_a_complete_outcome_window": str(sessions[max(i for i, complete in enumerate(outcome_complete) if complete)]),
            "last_session_for_equity_and_drawdown": str(sessions[-1])}
        if REVENUE in used:
            gaps = [str(row.session) for row in panel[first_feature:] if row.features[REVENUE] is None]
            report[ticker][label]["decisions_without_a_fresh_release"] = len(gaps)
            report[ticker][label]["first_such_decisions"] = gaps[:10]
            report[ticker][label]["release_months_missing_2009-08_to_2026-10"] = missing_months
(HERE / "coverage.json").write_text(json.dumps({"min_train": MIN_TRAIN, "min_scores": MIN_SCORES, "funds": report}, indent=2) + "\n")
print(json.dumps(report, indent=1))
