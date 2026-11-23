"""Render report.json as report.md. Formatting only: it computes nothing new.

Usage: report.py [--synthetic]   reads output/ or, for a rehearsal, output_synthetic/
"""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
OUT = HERE / ("output_synthetic" if "--synthetic" in sys.argv[1:] else "output")
r = json.loads((OUT / "report.json").read_text()); reg = json.loads((HERE / "registration.json").read_text())
pct = lambda v: "n/a" if v is None else f"{100 * v:.1f}%"
ARMS = (("N-avg", "3 inputs, forward average"), ("W-avg", f"{len(r['inputs'])} inputs, forward average"),
        ("N-dd", "3 inputs, drawdown"), ("W-dd", f"{len(r['inputs'])} inputs, drawdown"))
lines = ["# SOXX and QQQ: a wider input set for the risk gate (RFC-005)", ""]
if r["synthetic_prices"]:
    lines += ["**REHEARSAL ON SYNTHETIC PRICES. Both funds' prices are random walks. Nothing below says anything about the funds.**", ""]
lines += [f"Produced by `run.py` from `registration.json` (sha256 `{r['registration_sha256'][:12]}…`), code commit `{str(reg['code']['commit'])[:7]}`. "
          "Every definition is in the registration. This file only formats `report.json`.", "",
          "## Decision", ""]
lines += [f"- **{ticker}**: {fund['decision']}." for ticker, fund in r["funds"].items()]
lines += ["", f"Stopping rule: {r['stopping_rule']}.", "",
          "Only the two wide arms can replace the rule, and only on the test period. The 3-input arms are controls, scored on the same decisions."]
for ticker, fund in r["funds"].items():
    for label, title in (("test", "Test period"), ("development", "Development period (already seen when the comparison was fixed)")):
        b = fund["periods"][label]; rule = b["rule"]
        lines += ["", f"## {ticker}: {title}", "",
                  f"{rule['first_decision']} to {rule['last_session']}, {rule['decisions']} decisions. Buy and hold: maximum drawdown "
                  f"{pct(rule['buy_and_hold']['max_drawdown'])}, final equity {rule['buy_and_hold']['final_equity']:.2f}.", "",
                  "| Arm | Max drawdown | Final equity | Alert rate | Tail recall | Episodes | Whipsaw | Turnover a year | Held decisions |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for name, a in [("Frozen rule", rule)] + [(text, b["arms"][key]) for key, text in ARMS]:
            t = a["tail_recall"]
            lines.append(f"| {name} | {pct(a['max_drawdown'])} | {a['final_equity']:.2f} | {pct(t['alert_rate'])} | {pct(t['recall'])} | {a['episodes']} | "
                         f"{pct(a['whipsaw_rate'])} | {a['turnover_per_year']:.1f} | {a['held_decisions']} |")
        lines += ["", "Promotion limits against the rule:", "", "| Candidate | Drawdown smaller by 2 points | Equity at least 95% of the rule's | Whipsaw within 5 points | All |",
                  "| --- | --- | --- | --- | --- |"]
        yes = lambda v: "yes" if v else "no"
        for key in ("W-avg", "W-dd"):
            c = b["checks"][key]
            lines.append(f"| {dict(ARMS)[key]} | {yes(c['drawdown_smaller_by_points'])} | {yes(c['final_equity_share_of_rule'])} | {yes(c['whipsaw_within_points'])} | {yes(all(c.values()))} |")
        lines += ["", "Recall on the same realised drawdown episodes, second arm minus first (descriptive, promotes nothing):", "",
                  "| Contrast | Episodes | Recall, first | Recall, second | Difference | 90% interval |", "| --- | ---: | ---: | ---: | ---: | --- |"]
        for group in ("what_the_inputs_add", "what_the_target_adds"):
            for name, p in b[group].items():
                interval = "n/a" if p["interval"] is None else f"{100 * p['interval'][0]:+.1f} to {100 * p['interval'][1]:+.1f} points"
                diff = "n/a" if p["difference"] is None else f"{100 * p['difference']:+.1f} points"
                lines.append(f"| {name} | {p['episodes']} | {pct(p['recall_a'])} | {pct(p['recall_b'])} | {diff} | {interval} |")
        lines += ["", "At higher costs (deciding nothing): " + "; ".join(
            f"{m.replace('cost_x', '')}× cost: " + ", ".join(f"{k} {pct(v['max_drawdown'])} / {v['final_equity']:.2f}" for k, v in arms.items())
            for m, arms in b["sensitivity_decides_nothing"].items()) + "."]
lines += ["", "## Inputs", "", ", ".join(f"`{n}`" for n in r["inputs"]) + ".", "",
          "Dropped before registration: " + (", ".join(f"`{n}`" for n in reg["parameters"]["dropped_inputs"]) or "none") + ".", "",
          "## Limits", ""] + [f"- {x}" for x in reg.get("known_limits", [])]
(OUT / "report.md").write_text("\n".join(lines) + "\n")
