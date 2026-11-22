"""Record one registered TSMC monthly revenue release as a row, exactly as registration.json fixes it.

When to run: after the close of the fifth US session of the window, so the row is complete.
For the November 2026 report, scheduled for 2026-12-10 13:30 Taipei, that is 2026-12-16.

Steps:
  1. Save a snapshot folder holding VIX.csv, VIX3M.csv, TSM.json, SOXX.json and the session
     calendar, with a manifest.json giving each file's sha256 (as artifacts/snapshots/2026-11-22).
     Request both dividends and splits for the entire price range. For each ticker record
     their returned counts in corporate_action_counts, covering first_session through
     last_session. Enter zero only when that action was requested and none was returned.
  2. Write EVENT.json by hand, from sources you have opened:
       {"month": "2026-11",
        "reporting_period_text": "<the release's title or the calendar entry, quoted, e.g. 'TSMC November 2026 Revenue Report'>",
        "scheduled_release": "2026-12-10T13:30:00+08:00",
        "scheduled_release_source": "<the page that gives this month's scheduled time>",
        "edgar_accepted": "<acceptance time of the Form 6-K, with UTC offset>",
        "edgar_accession": "<accession number>",
        "timed_release": null,           (or the time of a dated and timed record of the actual release)
        "timed_release_source": null}    (and where that record is)
  3. Run:  python record_event.py EVENT.json --snapshot SNAPSHOT_DIR --calendar CALENDAR_FILE_NAME

The script applies the registered cutoff rule, builds the row, and writes rows/<month>.json
beside this file. It draws no conclusion: the registration says the first reading is after
36 events.

What it will not take on trust:
  - The code. The table builder and the bindings are exported from git at the registration's
    table_builder_commit into a temporary folder and run from there. No checkout is supplied.
  - The registration. registration.json and recipe.json must be the committed files.
  - The inputs. Each file is read once; those bytes are checked against the manifest and are
    the bytes that are parsed. A matching hash is not enough: the calendar must be the US
    equity calendar and each price file must name the ticker it is loaded as. The event file's
    own hash is recorded with the others.
  - The exit price. Each price file's last quote must be dated after the exit date, or be at or
    after that session's regular close: the trading period's end the file itself reports for
    that date, or 13:00 Eastern on the usual early-close days, whichever is earlier, else 16:00.
    A daily bar exists as soon as a session opens, so its presence alone proves nothing.
  - The event. The month is a strict YYYY-MM, and reporting_period_text, quoted from the
    release's title or the financial calendar, must name the same month and year. The accession
    is well-formed and not already recorded. The first event must carry the registered scheduled
    time; a later one needs the previous month's row to exist. Every timestamp is after the
    reported month has ended. A timed release is not later than the EDGAR acceptance, which
    bounds it from above. An acceptance more than a day before the schedule or more than seven
    days after the release is recorded as a warning, not refused: the registration allows a
    documented release whose filing comes later. It cannot read the filing's own reporting
    period; these are the checks available without it.
A row that exists is never rebuilt, and two runs cannot both write it. --dry-run prints a row
for any month and writes nothing; it skips only the checks that tie the event to the registration.
"""
import argparse
import atexit
import calendar as month_names
from datetime import date, datetime, time, timedelta
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
NEW_YORK, TAIPEI = ZoneInfo("America/New_York"), ZoneInfo("Asia/Taipei")
sys.dont_write_bytecode = True

parser = argparse.ArgumentParser(description="Record one registered release as a row.")
parser.add_argument("event", type=Path)
parser.add_argument("--snapshot", required=True, type=Path)
parser.add_argument("--calendar", required=True, help="name of the session calendar file inside the snapshot")
parser.add_argument("--out", type=Path, default=HERE / "rows")
parser.add_argument("--dry-run", action="store_true", help="print the row and write nothing; for rehearsal on any month")
args = parser.parse_args()

git = lambda *a, **k: subprocess.run(["git", "-C", str(ROOT), *a], check=True, capture_output=True, **k)
pinned = [str(HERE.relative_to(ROOT) / name) for name in ("registration.json", "recipe.json")]
if subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", *pinned]).returncode \
        or len(git("ls-files", "--", *pinned, text=True).stdout.split()) != 2:
    sys.exit("registration.json and recipe.json must be the committed files, unchanged")
registration = json.loads((HERE / "registration.json").read_text())
commit = registration["table_builder_commit"]

# The event: read once, its bytes hashed, checked for form and chronology before anything is computed.
event_bytes = args.event.read_bytes()
event = json.loads(event_bytes)
if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(event.get("month"))):
    sys.exit("month must be YYYY-MM")
if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(event.get("edgar_accession"))):
    sys.exit("edgar_accession must be an accession number such as 0001046179-26-000796")
if not str(event.get("scheduled_release_source") or "").strip():
    sys.exit("scheduled_release_source must say where this month's scheduled time was read")
year, month = map(int, event["month"].split("-"))
period_text = str(event.get("reporting_period_text") or "")
month_pattern = "|".join(month_names.month_name[1:])
periods = re.findall(rf"\b({month_pattern})\s+(\d{{4}})\b|\b(\d{{4}})\s+({month_pattern})\b", period_text, re.IGNORECASE)
named_periods = {((m or reverse_m).lower(), y or reverse_y) for m, y, reverse_y, reverse_m in periods}
if named_periods != {(month_names.month_name[month].lower(), str(year))}:
    sys.exit(f"reporting_period_text must quote a title or calendar entry naming the single period {month_names.month_name[month]} {year}; "
             f"got {period_text!r}")
event_id = f"tsmc:monthly_revenue:{event['month']}"
scheduled = datetime.fromisoformat(event["scheduled_release"])
accepted = datetime.fromisoformat(event["edgar_accepted"])
timed = datetime.fromisoformat(event["timed_release"]) if event.get("timed_release") else None
if timed is not None and not str(event.get("timed_release_source") or "").strip():
    sys.exit("a timed release needs timed_release_source")
if any(stamp.tzinfo is None for stamp in (scheduled, accepted, timed) if stamp is not None):
    sys.exit("every timestamp needs a UTC offset")
period_end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=TAIPEI)  # the first instant after the reported month
for name, stamp in (("scheduled_release", scheduled), ("edgar_accepted", accepted), ("timed_release", timed)):
    if stamp is not None and stamp < period_end:
        sys.exit(f"{name} {stamp.isoformat()} is before the reported month {event['month']} has ended")
if timed is not None and timed > accepted:
    sys.exit("the timed release is later than the EDGAR acceptance, which bounds the release from above")
released, warnings = timed or scheduled, []
if accepted < scheduled - timedelta(days=1):
    warnings.append("the EDGAR acceptance is more than a day before the scheduled release")
if accepted > released + timedelta(days=7):
    warnings.append("the EDGAR acceptance is more than seven days after the release")
target = args.out / f"{event['month']}.json"
if not args.dry_run:
    first = registration["first_event"]
    if event_id < first["event_id"]:
        sys.exit(f"{event_id} is before the first registered event {first['event_id']}; use --dry-run")
    if event_id == first["event_id"]:
        if scheduled != datetime.fromisoformat(first["scheduled_release"]):
            sys.exit(f"the first event is registered for {first['scheduled_release']}, not {event['scheduled_release']}")
    else:
        previous = f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"
        if not (args.out / f"{previous}.json").exists():
            sys.exit(f"the row for {previous} is not recorded: releases are recorded in order, with none left out")
    for earlier in sorted(args.out.glob("*.json")):
        if json.loads(earlier.read_text())["edgar_accession"] == event["edgar_accession"]:
            sys.exit(f"filing {event['edgar_accession']} is already recorded in {earlier.name}")
    if target.exists():
        sys.exit(f"{target} exists: a recorded row is not rebuilt")

# The inputs: read once, verified, and parsed from the verified bytes.
manifest_bytes = (args.snapshot / "manifest.json").read_bytes()
manifest = json.loads(manifest_bytes)["files"]
work = Path(tempfile.mkdtemp(prefix="record_event_"))
atexit.register(shutil.rmtree, work, ignore_errors=True)
hashes, inputs = {}, work / "inputs"
inputs.mkdir()
for name in ("VIX.csv", "VIX3M.csv", "TSM.json", "SOXX.json", Path(args.calendar).name):
    data = (args.snapshot / name).read_bytes()
    hashes[name] = hashlib.sha256(data).hexdigest()
    if hashes[name] != manifest[name]["sha256"]:
        sys.exit(f"{name} does not match the snapshot manifest")
    (inputs / name).write_bytes(data)
# What each verified file is. The subject and benchmark tickers come from the registration's outcome.
quoted = {}
for ticker in (registration["outcome"]["subject"], registration["outcome"]["benchmark"]):
    chart = json.loads((inputs / f"{ticker}.json").read_text())["chart"]["result"][0]
    meta = chart["meta"]
    if meta.get("symbol") != ticker:
        sys.exit(f"{ticker}.json holds prices for {meta.get('symbol')!r}, not {ticker}")
    counts = manifest[f"{ticker}.json"].get("corporate_action_counts", {})
    for kind in ("dividends", "splits"):
        expected = counts.get(kind) if isinstance(counts, dict) else None
        if type(expected) is not int or expected < 0:
            sys.exit(f"{ticker}: snapshot manifest lacks a confirmed {kind} count")
        returned = chart.get("events", {}).get(kind, {})
        if not isinstance(returned, dict) or len(returned) != expected:
            sys.exit(f"{ticker}: {kind} in the price file do not match the manifest count of {expected}")
    trading_end = datetime.fromtimestamp(meta["currentTradingPeriod"]["regular"]["end"], NEW_YORK)
    quoted[ticker] = (datetime.fromtimestamp(meta["regularMarketTime"], NEW_YORK), trading_end)
calendar_data = json.loads((inputs / Path(args.calendar).name).read_text())
if not calendar_data["calendar_id"].startswith("us_equity_sessions") or calendar_data["timezone"] != "America/New_York":
    sys.exit(f"{calendar_data['calendar_id']} is not the US equity session calendar")

# The code: exported from git at the pinned commit, so local edits to any checkout cannot reach it.
archive = git("archive", commit, "packages", "knowledge_base/prior_knowledge/bindings").stdout
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    tar.extractall(work / "code", filter="data")
for package in ("quant_contracts", "quant_loop", "quant_pit_data", "quant_evaluator", "quant_causal_graph"):
    sys.path.insert(0, str(work / "code/packages" / package / "src"))
from quant_contracts.evaluation import EventOutcomeRow, EventTime, Factor
from quant_contracts.pit import Cutoff, SeriesBinding, SessionCalendar
from quant_contracts.research import CandidateFactorRecipe
import quant_loop.event_table as event_table
from quant_pit_data.asof import load_daily_closes, on_calendar
from quant_pit_data.connectors.yahoo_chart import ex_dividend_dates, load_daily, split_dates

if not event_table.__file__.startswith(str(work)):
    sys.exit("the table builder was not loaded from the pinned export")
recipe = CandidateFactorRecipe.model_validate_json((HERE / registration["recipe_file"]).read_text())
if hashlib.sha256(recipe.model_dump_json().encode()).hexdigest() != registration["recipe_sha256"]:
    sys.exit("recipe.json does not match the registered hash")
catalog = json.loads((work / "code/knowledge_base/prior_knowledge/bindings/series_bindings.json").read_text())
if catalog["content_revision"] != registration["bindings_revision"]:
    sys.exit("the pinned commit holds another bindings revision than the registered one")
b = {s["series_id"]: SeriesBinding(**s) for s in catalog["series"]}
calendar = SessionCalendar(**calendar_data)
subject = tuple(b[i] for i in registration["price_series"]["subject"])
benchmark = tuple(b[i] for i in registration["price_series"]["benchmark"])
factors = [Factor(**f) for f in registration["factors"]]
state, _ = on_calendar(load_daily_closes(inputs / "VIX.csv", b["cboe:vix"])
                       + load_daily_closes(inputs / "VIX3M.csv", b["cboe:vix3m"]), calendar)
observations = state + load_daily(inputs / "TSM.json", *subject) + load_daily(inputs / "SOXX.json", *benchmark)
actions = {f"{kind} {name}": dates(inputs / f"{name}.json") for name in ("TSM", "SOXX")
           for kind, dates in (("ex-dividend", ex_dividend_dates), ("split", split_dates))}

filing = f"SEC EDGAR acceptance of Form 6-K {event['edgar_accession']}"
same_day = accepted >= scheduled and accepted.astimezone(NEW_YORK).date() == scheduled.astimezone(NEW_YORK).date()
midnight = accepted.astimezone(NEW_YORK).replace(hour=0, minute=0, second=0, microsecond=0)
rule_two = EventTime(event_id=event_id, cutoff=Cutoff(time=scheduled, basis="assumed",
                                                      source="Scheduled release time from the company's financial calendar"))
rule_three = EventTime(event_id=event_id, cutoff=Cutoff(time=accepted, basis="upper_bound", source=filing),
                       state_cutoff=Cutoff(time=min(scheduled, midnight), basis="assumed",
                                           source="The earlier of the scheduled release time and the start of the US Eastern "
                                                  "day of the EDGAR acceptance"))
if timed is not None:
    rule, chosen = 1, EventTime(event_id=event_id, cutoff=Cutoff(time=timed, basis="exact", source=event["timed_release_source"]))
elif same_day:
    rule, chosen = 2, rule_two
else:
    rule, chosen = 3, rule_three


def build(event_time):
    return event_table.build_table(recipe, [event_time], factors, {i: b[i] for f in factors for i in f.series_ids}, subject,
                                   benchmark, observations, calendar, actions, hashes)


table = build(chosen)
row = table.rows[0]
if row.status == "complete":
    previous_session = calendar.sessions[calendar.sessions.index(row.entry_session) - 1]
    for ticker in quoted:
        spec = manifest[f"{ticker}.json"]
        if not (date.fromisoformat(spec["first_session"]) <= previous_session
                and date.fromisoformat(spec["last_session"]) >= row.exit_session):
            sys.exit(f"{ticker}: corporate action coverage does not span the event window")
picked = lambda r: ([s.state.session for s in r.factors], r.entry_session)
# Registered for every row, whichever rule set its cutoff: would rules 2 and 3 have disagreed?
if picked(build(rule_two).rows[0]) != picked(build(rule_three).rows[0]):
    # The flag goes on the row itself, the one place a sensitivity repeat reads it from.
    sensitive = "timing-sensitive: rules 2 and 3 select a different factor session or entry session"
    row = EventOutcomeRow.model_validate({**row.model_dump(), "flags": (*row.flags, sensitive)})
flags = list(row.flags)
record = {"event_id": event_id, "cutoff_rule_applied": rule, "scheduled_release": event["scheduled_release"],
          "scheduled_release_source": event["scheduled_release_source"], "reporting_period_text": period_text,
          "event_sha256": hashlib.sha256(event_bytes).hexdigest(), "warnings": warnings, "edgar_accepted": event["edgar_accepted"], "edgar_accession": event["edgar_accession"],
          "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
          "timed_release": event.get("timed_release"), "timed_release_source": event.get("timed_release_source"),
          "registration_sha256": hashlib.sha256((HERE / "registration.json").read_bytes()).hexdigest(),
          "table_builder_commit": commit, "recipe_sha256": table.recipe_sha256, "inputs_sha256": hashes,
          "row": json.loads(row.model_dump_json())}
values = {s.factor_id: s.value for s in row.factors}
print(f"{event_id} | rule {rule} | cutoff {row.cutoff.time} ({row.cutoff.basis}) | state before {row.state_cutoff.time} | "
      f"factors {values} | entry {row.entry_session} exit {row.exit_session} | {row.status}"
      + (f" | relative {row.window.relative:+.4f}" if row.status == "complete" else f": {row.reason}") + f" | flags {flags}"
      + (f" | warnings {warnings}" if warnings else ""))
if row.status != "complete":
    sys.exit("the window is not complete yet: nothing is written. Run again once the snapshot covers the exit session.")


def regular_close(session: date, reported_end: datetime) -> datetime:
    """When the session's regular trading ended: the file's own period end for that date, or the usual early close, if earlier than 16:00."""
    thanksgiving = [d for d in (date(session.year, 11, n) for n in range(1, 31)) if d.weekday() == 3][3]
    early_days = {thanksgiving + timedelta(days=1), date(session.year, 12, 24)}
    if date(session.year, 7, 3).weekday() < 4:  # 3 July, Monday to Thursday: the day before a weekday holiday
        early_days.add(date(session.year, 7, 3))
    candidates = [datetime.combine(session, time(13, 0) if session in early_days else time(16, 0), NEW_YORK)]
    if reported_end.date() == session:
        candidates.append(reported_end)
    return min(candidates)


early = []
for ticker, (stamp, reported_end) in sorted(quoted.items()):
    close = regular_close(row.exit_session, reported_end)
    if stamp.date() <= row.exit_session and stamp < close:  # a quote dated after the exit date is past its close
        early.append(f"{ticker} quoted {stamp.isoformat()}, before that session's close at {close.isoformat()}")
if early:
    sys.exit(f"the exit price for {row.exit_session} is not final: {'; '.join(early)}. "
             "Take a new snapshot after that session has closed.")
if args.dry_run:
    sys.exit(0)
args.out.mkdir(parents=True, exist_ok=True)
# Publish whole and only once: write beside the target, then link it into place, which fails if the row exists.
with tempfile.NamedTemporaryFile("w", dir=args.out, prefix=f".{event['month']}.", suffix=".tmp", delete=False) as handle:
    handle.write(json.dumps(record, indent=1) + "\n")
try:
    os.link(handle.name, target)
except FileExistsError:
    sys.exit(f"{target} exists: another run recorded this row first, and it is not replaced")
finally:
    os.unlink(handle.name)
print("wrote", target)
