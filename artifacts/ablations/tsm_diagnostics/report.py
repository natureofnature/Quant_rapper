"""Render output/report.json as output/report.md. Formatting only: it computes nothing new."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
r = json.loads((HERE / "output/report.json").read_text()); reg = json.loads((HERE / "registration.json").read_text())
pct = lambda v: "n/a" if v is None else f"{100 * v:+.2f}%"
share = lambda v: "n/a" if v is None else f"{100 * v:.1f}%"
v, cat, q = r["part_1"]["variance"], r["part_1"]["catalyst_concentration_in_soxx_adjusted_residual"], r["part_2"]
lines = ["# TSM diagnostics (exploratory)", "",
         f"Produced by `run.py` from `registration.json` (sha256 `{r['registration_sha256'][:12]}…`), code commit `{reg['code']['commit'][:7]}`. "
         "Every definition is in the registration. This file only formats `report.json`.", "", reg["standing"], "",
         "## Part 1: where TSM's daily variance sits", "",
         f"{v['sessions']} sessions from {v['first']}. Betas are fitted on the 250 sessions before each day.", "",
         "| Adjusted for | Share of TSM's return variance left in the residual |", "| --- | ---: |"]
lines += [f"| {name} | {share(v['residual_share_of_variance'][key])} |" for key, name in (("spy", "SPY"), ("soxx", "SOXX"), ("both", "SPY and SOXX"))]
lines += ["", "Residual after SOXX, by year: " + ", ".join(f"{y} {share(x['soxx'])}" for y, x in v["by_year"].items()) + ".", "",
          "### Announcement sessions in the SOXX-adjusted residual", "",
          "| Type | Sessions | Share of sessions | Share of squared residual | Ratio |", "| --- | ---: | ---: | ---: | ---: |"]
lines += [f"| {k.replace('_', ' ')} | {c['sessions']} | {share(c['share_of_sessions'])} | {share(c['share_of_squared_residual'])} | "
          f"{c['share_of_squared_residual'] / c['share_of_sessions']:.2f} |" for k, c in cat.items()]
audit = r["part_1"]["shock_audit"]
lines += ["", f"Shock audit: {audit['sessions_listed']} of {audit['eligible_sessions']} non-announcement sessions have a residual beyond "
          f"{reg['parameters']['shock_threshold']} trailing standard deviations. They are listed in `shocks.csv` for review against the research map; none is explained here.", "",
          "## Part 2: the monthly revenue event study", "",
          f"{q['events']} releases, {q['events_with_a_pre_event_beta']} with a pre-event beta, {q['events_with_a_collision']} whose entry session is also a CPI, PPI, FOMC or TSMC earnings session.", "",
          "### Announcement-window returns by state (not drift)", "",
          "| State split | Window | Measure | n high / low | Mean high | Mean low | Difference | In standard errors | Episodes high / low |", "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
for key, x in q["question_1_window_mean"].items():
    factor, window, measure = key.split("|")
    se = x["standard_error"]
    lines.append(f"| {factor} | {window.replace('_', ' ')} | {measure} | {x['n_a']} / {x['n_b']} | {pct(x['mean_a'])} | {pct(x['mean_b'])} | {pct(x['difference'])} | "
                 f"{'n/a' if not se else format(x['difference'] / se, '+.2f')} | {x['episodes_a']} / {x['episodes_b']} |")
lines += ["", "### Announcement-day volatility against other sessions in the same state", "",
          "Mean absolute abnormal return, open to close.", "",
          "| State split | Sessions | High: announcement / other | Low: announcement / other | Difference in differences | Standard error |", "| --- | --- | ---: | ---: | ---: | ---: |"]
for key, x in q["question_2_volatility"].items():
    factor, label = key.split("|"); m = x["mean_abs_abnormal"]
    lines.append(f"| {factor} | {label.replace('_', ' ')} | {pct(m.get('high_announcement'))} / {pct(m.get('high_other'))} | {pct(m.get('low_announcement'))} / {pct(m.get('low_other'))} | "
                 f"{pct(x['difference_in_differences'])} | {pct(x['standard_error'])} |")
lines += ["", "The standard errors treat sessions as independent, which they are not, so they understate the uncertainty.", ""]
for name, group in q["small_groups_listed_individually"].items():
    lines += [f"### Listed event by event: {name} ({len(group)} events)", "", "| Event | Entry | State | Beta | Reaction, abnormal | Entry day, abnormal | 5 sessions, abnormal | Collisions |", "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    lines += [f"| {e['event_id'][-7:]} | {e['entry']} | {e['state']:.3f} | {'n/a' if e['pre_beta'] is None else format(e['pre_beta'], '.2f')} | {pct(e['reaction_abnormal'])} | "
              f"{pct(e['entry_day_abnormal'])} | {pct(e['window_abnormal'])} | {' '.join(e['collisions'])} |" for e in group] + [""]
lines += ["## Not done", ""] + [f"- {g}" for g in r["data_gaps"]] + ["", "## Limits", ""] + [f"- {x}" for x in reg["known_limits"]]
lines += ["", "## Amendments", ""] + [f"- {a['amended_on']}: {a['change']} {a['reason']}" for a in reg.get("amendments", [])]
(HERE / "output/report.md").write_text("\n".join(lines) + "\n")
