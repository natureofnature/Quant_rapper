"""Render output/report.json and output/surprise.csv as output/report.md. Formatting only: it computes nothing new."""
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
r = json.loads((HERE / "output/report.json").read_text()); reg = json.loads((HERE / "registration.json").read_text())
with (HERE / "output/surprise.csv").open(newline="") as handle:
    rows = list(csv.DictReader(handle))
pct = lambda v: "n/a" if v in (None, "") else f"{100 * float(v):+.2f}%"
lines = ["# TSM guidance surprise (exploratory)", "",
         f"Produced by `run.py` from `registration.json` (sha256 `{r['registration_sha256'][:12]}…`), code commit `{reg['code']['commit'][:7]}`. "
         "Every definition is in the registration. This file only formats the outputs.", "", reg["standing"], ""]
check = r["currency_check"]
for key, title in (("all_quarters", "All quarters"), ("without_revised_quarters", "Without the quarters whose guidance was revised mid-quarter")):
    block = r[key]; c = block["counts"]
    lines += [f"## {title}", "", f"Above the range: {c['above']}. Inside: {c['inside']}. Below: {c['below']}.", "",
              "| Window | Mean, above | Mean, below | Mean, inside | Above minus below | In standard errors |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for window, name in (("reaction", "Reaction"), ("entry_day", "Entry session"), ("window", "5 sessions")):
        x = block[window]; se = x["standard_error"]
        lines.append(f"| {name} | {pct(x['mean_a'])} | {pct(x['mean_b'])} | {pct(x['inside_mean'])} | {pct(x['difference'])} | "
                     f"{'n/a' if not se else format(x['difference'] / se, '+.2f')} |")
    lines.append("")
lines += ["Returns are TSM's beta-adjusted abnormal returns around the release of the quarter's third month.", "",
          f"Currency check: of the {check['quarters_in_usd']} quarters guided in US dollars, {check['same_side_at_the_assumed_rate']} fall on the same side of the range "
          "when converted at the single rate the guidance assumed instead of month by month.", "",
          "## Every quarter", "", "| Quarter | Currency | Range | Actual | Against midpoint | Surprise | At assumed rate | Revised | Reaction | Entry session | 5 sessions |",
          "| --- | --- | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: |"]
lines += [f"| {x['quarter']} | {x['currency']} | {float(x['low']):g} to {float(x['high']):g} | {float(x['actual']):.2f} | {pct(x['actual_over_midpoint'])} | {x['surprise']} | "
          f"{x['surprise_at_assumed_rate'] or ''} | {'yes' if x['revised_mid_quarter'] == 'True' else ''} | {pct(x['reaction'])} | {pct(x['entry_day'])} | {pct(x['window'])} |" for x in rows]
lines += ["", "## Limits", ""] + [f"- {x}" for x in reg["known_limits"]]
(HERE / "output/report.md").write_text("\n".join(lines) + "\n")
