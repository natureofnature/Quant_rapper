"""Run the registered guidance-surprise table. EXPLORATORY: every definition is in registration.json beside this file.

Usage: run.py   Writes output/report.json and output/surprise.csv.
"""
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT, HERE = Path(__file__).resolve().parents[3], Path(__file__).resolve().parent
for package in ("quant_contracts", "quant_evaluator"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
from quant_evaluator.alpha.event_window import compare_groups

reg = json.loads((HERE / "registration.json").read_text()); P = reg["parameters"]
if not re.fullmatch(r"[0-9a-f]{40}", reg["code"]["commit"]):
    sys.exit("the registration has no code commit yet")
SNAP = ROOT / reg["inputs"]["snapshot"]
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for name, expected in reg["inputs"]["sha256"].items():
    if digest(SNAP / name) != expected:
        sys.exit(f"{name} does not match the registered hash")
if digest(ROOT / reg["inputs"]["returns"]["file"]) != reg["inputs"]["returns"]["sha256"]:
    sys.exit("the frozen event returns do not match the registered hash")
guidance = {r["guided_quarter"]: r for r in json.loads((SNAP / "tsmc_quarterly_guidance.json").read_text())["releases"] if r.get("low") is not None}
monthly = {r["month"]: r for r in json.loads((SNAP / "tsmc_monthly_revenue_first_release.json").read_text())["releases"]}
with (ROOT / reg["inputs"]["returns"]["file"]).open(newline="") as handle:
    returns = {r["event_id"][-7:]: r for r in csv.DictReader(handle)}
chart = json.loads((SNAP / "USDTWD.json").read_text())["chart"]["result"][0]
by_month: dict[str, list[float]] = {}
for stamp, close in zip(chart["timestamp"], chart["indicators"]["quote"][0]["close"]):
    if close is not None:
        by_month.setdefault(datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m"), []).append(close)
rate = {m: sum(v) / len(v) for m, v in by_month.items()}
quarters = [f"{y}Q{q}" for y in range(2005, 2027) for q in range(1, 5)]
quarters = [q for q in quarters if P["first_guided_quarter"] <= q <= P["last_guided_quarter"]]
rows, skipped = [], []
for quarter in quarters:
    year, n = int(quarter[:4]), int(quarter[5])
    months = [f"{year}-{m:02d}" for m in range(3 * n - 2, 3 * n + 1)]
    g, basis = guidance.get(quarter), "unconsolidated_or_unspecified_nt_million" if quarter <= P["unconsolidated_through"] else "consolidated_nt_million"
    values = [monthly.get(m, {}).get(basis) for m in months]
    event = returns.get(months[2])
    if g is None or any(v is None for v in values) or event is None:
        skipped.append({"quarter": quarter, "reason": "no numeric guidance" if g is None else "a monthly figure is missing" if event else "no return row for the release"})
        continue
    low, high = (g["guidance_consolidated_basis"]["low"], g["guidance_consolidated_basis"]["high"]) if quarter == "2006Q2" and isinstance(g.get("guidance_consolidated_basis"), dict) and "low" in g["guidance_consolidated_basis"] else (g["low"], g["high"])
    check = None
    if g["currency"] == "NTD":
        actual = sum(values) / 1000.0  # NT$ billion
    else:
        if any(m not in rate for m in months):
            skipped.append({"quarter": quarter, "reason": "no exchange rate for a month"}); continue
        actual = sum(v / rate[m] for v, m in zip(values, months)) / 1000.0  # US$ billion, month by month
        check = sum(values) / g["assumed_exchange_rate"] / 1000.0 if g.get("assumed_exchange_rate") else None
    side = lambda a: "above" if a > high else "below" if a < low else "inside"
    number = lambda key: float(event[key]) if event[key] != "" else None
    rows.append({"quarter": quarter, "release_month": months[2], "entry": event["entry"], "currency": g["currency"], "low": low, "high": high,
                 "actual": actual, "surprise": side(actual), "actual_over_midpoint": actual / ((low + high) / 2) - 1.0,
                 "surprise_at_assumed_rate": None if check is None else side(check), "revised_mid_quarter": "guidance_revision" in g,
                 "collisions": event["collisions"], "reaction": number("reaction_abnormal"), "entry_day": number("entry_day_abnormal"),
                 "window": number("window_abnormal")})
(HERE / "output").mkdir(exist_ok=True)
with (HERE / "output/surprise.csv").open("w", newline="") as handle:
    w = csv.DictWriter(handle, fieldnames=list(rows[0])); w.writeheader()
    for r in rows:
        w.writerow({k: (f"{v:.6f}" if isinstance(v, float) else "" if v is None else v) for k, v in r.items()})
def compare(subset, label):
    out = {"counts": {s: sum(r["surprise"] == s for r in subset) for s in ("above", "inside", "below")}}
    for window in ("reaction", "entry_day", "window"):
        pick = lambda side: [r[window] for r in subset if r["surprise"] == side and r[window] is not None]
        out[window] = compare_groups(f"{label}: {window}, above against below", pick("above"), pick("below")).model_dump()
        inside = pick("inside")
        out[window]["inside_mean"] = sum(inside) / len(inside) if inside else None
    return out
usd = [r for r in rows if r["surprise_at_assumed_rate"] is not None]
report = {"status": "EXPLORATORY", "registration_sha256": digest(HERE / "registration.json"), "quarters": len(rows), "skipped": skipped,
          "all_quarters": compare(rows, "all quarters"),
          "without_revised_quarters": compare([r for r in rows if not r["revised_mid_quarter"]], "without revised quarters"),
          "currency_check": {"quarters_in_usd": len(usd), "same_side_at_the_assumed_rate": sum(r["surprise"] == r["surprise_at_assumed_rate"] for r in usd)}}
(HERE / "output/report.json").write_text(json.dumps(report, indent=1, allow_nan=False) + "\n")
print("done:", len(rows), "quarters,", len(skipped), "skipped")
