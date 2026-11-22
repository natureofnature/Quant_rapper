"""Render output/report.json as output/report.md. Formatting only: it computes nothing new."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
report = json.loads((HERE / "output/report.json").read_text())
reg = json.loads((HERE / "registration.json").read_text())
funds = report["funds"]
pct = lambda v: f"{100 * v:+.1f}%"


def row(label, pick):
    return f"| {label} | " + " | ".join(pick(funds[name]) for name in funds) + " |"


lines = ["# SOXX/QQQ rule baseline", "",
         "Produced by `run.py` from `registration.json` (sha256 `" + report["registration_sha256"][:12] + "…`), code commit `"
         + reg["code"]["commit"][:7] + "`. Every definition is in the registration. This file only formats `report.json`.", "",
         "| | " + " | ".join(funds) + " |", "| --- |" + " ---: |" * len(funds),
         row("First decision", lambda f: f["position_period"]["first_decision"]),
         row("Last decision, positions", lambda f: f["position_period"]["last_decision"]),
         row("Last decision, forward measure", lambda f: f["forward_period"]["last_decision"]),
         row("Decisions", lambda f: str(f["position_period"]["decisions"])),
         row("Decisions at 100% / 50% / 0%", lambda f: " / ".join(str(f["decisions_by_target"].get(k, 0)) for k in ("1.00", "0.50", "0.00"))),
         row("Sessions holding the previous target", lambda f: f"{f['held_sessions']} (longest run {f['longest_held_run']})"),
         row("Max drawdown, gate", lambda f: pct(f["max_drawdown"]["gate"])),
         row("Max drawdown, buy and hold", lambda f: pct(f["max_drawdown"]["buy_and_hold"])),
         row("Final equity, gate", lambda f: f"{f['final_equity']['gate']:.2f}"),
         row("Final equity, buy and hold", lambda f: f"{f['final_equity']['buy_and_hold']:.2f}"),
         row("Forward drawdown, stressed: mean / median / 5th pct", lambda f: " / ".join(pct(f["forward_drawdown"]["stressed"][k]) for k in ("mean", "median", "p05"))),
         row("Forward drawdown, ordinary: mean / median / 5th pct", lambda f: " / ".join(pct(f["forward_drawdown"]["ordinary"][k]) for k in ("mean", "median", "p05"))),
         row("Sessions (runs), stressed", lambda f: f"{f['forward_drawdown']['stressed']['n']} ({f['forward_drawdown']['stressed']['runs']})"),
         row("Sessions (runs), ordinary", lambda f: f"{f['forward_drawdown']['ordinary']['n']} ({f['forward_drawdown']['ordinary']['runs']})"),
         row("Stressed minus ordinary, mean", lambda f: pct(f["forward_drawdown"]["difference_of_means"])),
         row("90% interval", lambda f: " to ".join(pct(v) for v in f["forward_drawdown"]["interval_90"])),
         row("Decisions at 100% inside an episode (in neither group)", lambda f: str(f["full_exposure_decisions_inside_episodes"])),
         row("Episodes (unfinished)", lambda f: f"{f['episodes']['n']} ({f['episodes']['unfinished']})"),
         row("Episodes crossing the end of the forward period", lambda f: str(f["forward_period"]["episodes_crossing_its_end"])),
         row("Whipsaw rate", lambda f: f"{100 * f['whipsaw_rate']:.0f}%"),
         row("Re-entry cost, whipsaw episodes", lambda f: pct(f["reentry_lag_cost"]["whipsaw_episodes"])),
         row("Re-entry cost, all finished episodes", lambda f: pct(f["reentry_lag_cost"]["all_finished_episodes"])),
         "", "## Reading, by the registered rule", ""]
for name, f in funds.items():
    low, high = f["forward_drawdown"]["interval_90"]
    separated = "yes" if high < 0 else "no"
    reduced = "yes" if f["max_drawdown"]["gate"] > f["max_drawdown"]["buy_and_hold"] else "no"
    lines.append(f"- **{name}**: separated drawdown periods: {separated} (the interval {'lies' if high < 0 else 'does not lie'} wholly "
                 f"below zero). Reduced maximum drawdown: {reduced}.")
lines += ["", "The reading rule says nothing about return. Final equity is in the table: the gate ended well below buy and hold "
              "for both funds, on prices that exclude dividends.", "",
          "## Limits", ""] + [f"- {item}" for item in reg["known_limits"]] + [f"- {item}" for item in reg["data_gaps"]]
(HERE / "output/report.md").write_text("\n".join(lines) + "\n")
