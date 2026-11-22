"""Render output/report.json as output/report.md. Formatting only: it computes nothing new."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
report = json.loads((HERE / "output/report.json").read_text())
reg = json.loads((HERE / "registration.json").read_text())
pct = lambda v: "n/a" if v is None else f"{100 * v:+.1f}%"
share = lambda v: "n/a" if v is None else f"{100 * v:.1f}%"


def table(arms):
    """arms: list of (column title, arm report). One row per reported quantity."""
    rows = [("First decision", lambda a: a["first_decision"]), ("Last session", lambda a: a["last_session"]),
            ("Decisions (held)", lambda a: f"{a['decisions']} ({a['held_decisions']})"),
            ("Decisions at 100% / 50% / 0%", lambda a: " / ".join(str(a["decisions_by_target"].get(k, 0)) for k in ("1.00", "0.50", "0.00"))),
            ("Max drawdown", lambda a: pct(a["max_drawdown"])), ("Final equity", lambda a: f"{a['final_equity']:.2f}"),
            ("Buy and hold: max drawdown / final equity", lambda a: f"{pct(a['buy_and_hold']['max_drawdown'])} / {a['buy_and_hold']['final_equity']:.2f}"),
            ("Episodes (unfinished)", lambda a: f"{a['episodes']} ({a['unfinished_episodes']})"),
            ("Whipsaw rate", lambda a: share(a["whipsaw_rate"])), ("Re-entry cost, all finished episodes", lambda a: pct(a["reentry_cost_all_finished"])),
            ("Turnover per year", lambda a: f"{a['turnover_per_year']:.1f}"),
            ("Recall on the worst 15% of forward drawdowns", lambda a: share(a["tail_recall"]["recall"])),
            ("Share of decisions under an alert", lambda a: share(a["tail_recall"]["alert_rate"])),
            ("Forward drawdown, stressed minus ordinary", lambda a: pct(a["forward_drawdown"]["difference_of_means"])),
            ("Its 90% interval", lambda a: " to ".join(pct(v) for v in a["forward_drawdown"]["interval_90"] or [])),
            ("Worst windows labelled as the left-tail class", lambda a: share(a["worst_windows_labelled_class_0"]))]
    lines = ["| | " + " | ".join(title for title, _ in arms) + " |", "| --- |" + " ---: |" * len(arms)]
    return lines + [f"| {label} | " + " | ".join(pick(a) for _, a in arms) + " |" for label, pick in rows]


lines = ["# SOXX and QQQ: Model C against the frozen rule, with Model A as frozen", "",
         f"Produced by `run.py` from `registration.json` (sha256 `{report['registration_sha256'][:12]}…`), code commit "
         f"`{reg['code']['commit'][:7]}`. Every definition is in the registration. This file only formats `report.json`.", "",
         "## The frozen rule, Model A and Model C over one period", ""]
for fund, result in report["comparison_1"].items():
    lines += [f"### {fund}", ""] + table([("Frozen rule", result["rule"]), ("Model A, as frozen", result["model_a"]), ("Model C", result["model"])]) + ["",
              "Registered limits: " + "; ".join(f"{k.replace('_', ' ')}: {'met' if v else 'not met'}" for k, v in result["checks"].items())
              + f". **Decision: {result['decision']}.**", "",
              "Sensitivity runs for Model C, which decide nothing (max drawdown / final equity, then the rule's over the same period):", ""]
    lines += [f"- {name.replace('_', ' ')}: {pct(s['model']['max_drawdown'])} / {s['model']['final_equity']:.2f}; "
              f"rule {pct(s['rule']['max_drawdown'])} / {s['rule']['final_equity']:.2f}" for name, s in result["sensitivity_decides_nothing"].items()] + [""]
lines += ["## Limits", ""] + [f"- {item}" for item in reg["known_limits"]]
(HERE / "output/report.md").write_text("\n".join(lines) + "\n")
