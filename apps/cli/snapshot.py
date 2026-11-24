"""Download every series the bindings catalogue reads from the network into one snapshot folder, with a manifest.

    python apps/cli/snapshot.py                       the latest US session that has closed
    python apps/cli/snapshot.py --session 2026-11-24  a named session

The folder is artifacts/snapshots/<session>/ and is written once: an existing snapshot is never
overwritten, and a run in which any source fails publishes nothing and exits non-zero, so the
day can be retried. A copy goes to run/backup/snapshots/<session>/ with a SHA256SUMS file. Both
folders are git-ignored except for manifest.json: the data is not redistributed.

A file is saved as the source served it. Two repairs are made, and only for the snapshot's own
session. Each keeps the served bytes under raw/, is recorded in the manifest, and writes
<name>.repair.json beside the file with the time the repairing quote was stamped: the loaders
treat the value as knowable no earlier than that, so a quote stamped after a decision's cutoff
cannot reach that decision.
  * Yahoo can serve a finished session's bar with no close for some hours. The close is then
    taken from the same response's last regular-session price, if that price is stamped at or
    after the session's regular close.
  * Cboe's VIX history files can lag a session. The missing row is then taken from Yahoo's
    quote of the same index, if the two sources agree on the session before.
Neither value is the source's final figure, and a later snapshot may hold a slightly different one.
A market that trades around the clock (futures, the dollar index) is served with a last bar that is
the live quote at download; the manifest marks it, and nothing repairs it.

A snapshot holds what was downloadable, not what was knowable at a decision: selection by
availability time is done later, by the bindings.

The session calendars and announcement dates are curated files shared between snapshots. They
are not copied; the manifest pins each by path and hash under "shared", and a reader resolves
them from there. A run that finds the snapshot already written verifies it and completes a
missing backup copy; it never rewrites the snapshot.
"""
import argparse
import csv
from datetime import date, datetime, time, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time as clock
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = "knowledge_base/prior_knowledge/bindings/series_bindings.json"
CALENDAR = "artifacts/snapshots/2026-11-22/us_equity_sessions_2002_2026.json"
SHARED = (CALENDAR, "artifacts/snapshots/2026-11-22/us_treasury_yield_sessions_2003_2026.json",
          "artifacts/snapshots/2026-11-22/announcement_dates_2002_2026.json")  # curated, not downloaded: pinned by each manifest
NEW_YORK = ZoneInfo("America/New_York")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
CBOE = "https://cdn.cboe.com/api/global/us_indices/daily_prices/"
TREASURY_PAGE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_real_yield_curve"
TREASURY_YEAR = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
                 "{year}/all?type=daily_treasury_real_yield_curve")
TREASURY_FIRST_YEAR = 2003
CBOE_ON_YAHOO = {"VIX.csv": "^VIX", "VIX3M.csv": "^VIX3M"}  # where a lagging Cboe file's last row may be taken from
AGREE = 0.011  # two sources agree on an index level when they differ by no more than a rounding step


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research-script)"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan(catalogue: dict) -> tuple[dict[str, dict], dict[str, str]]:
    """(files to download keyed by file name, series left out with the reason), from the catalogue's source addresses."""
    files, left_out = {}, {}
    for series in catalogue["series"]:
        url, series_id = series["source_url"], series["series_id"]
        if url.startswith(YAHOO):
            symbol = url[len(YAHOO):]
            name, entry = symbol.replace("^", "").replace("=", "") + ".json", {"kind": "yahoo", "symbol": symbol, "source": url}
        elif url.startswith(CBOE) and url.endswith("_History.csv"):
            name, entry = url[len(CBOE):-len("_History.csv")] + ".csv", {"kind": "cboe", "source": url}
        elif url == TREASURY_PAGE:
            name, entry = "treasury_real_yield_10y.csv", {"kind": "treasury", "source": TREASURY_YEAR.format(year="<year>") + ", 10 YR column"}
        elif url.startswith("derived from "):
            left_out[series_id] = "computed from other series, not downloaded"
            continue
        else:
            left_out[series_id] = "not a daily download: its values come from dated sources collected by hand"
            continue
        files.setdefault(name, {**entry, "series_ids": []})["series_ids"].append(series_id)
    return files, left_out


def yahoo_url(symbol: str, now: datetime, recent: bool = False) -> str:
    query = "interval=1d&range=1mo" if recent else f"period1=0&period2={int(now.timestamp())}&interval=1d&events=div%2Csplit"
    return f"{YAHOO}{urllib.parse.quote(symbol, safe='')}?{query}"


def yahoo_bars(data: bytes) -> tuple[dict, dict, list[date]]:
    """(the whole response, its one result, the local date of each bar). Raises if the response is not a chart with bars."""
    whole = json.loads(data)
    result = whole["chart"]["result"][0]
    zone = ZoneInfo(result["meta"]["exchangeTimezoneName"])
    days = [datetime.fromtimestamp(stamp, zone).date() for stamp in result["timestamp"]]
    quote = result["indicators"]["quote"][0]
    if not days or len(quote["close"]) != len(days) or len(quote["open"]) != len(days):
        raise ValueError("a chart response without matching bars")
    return whole, result, days


def last_regular_price(result: dict, session: date) -> tuple[float, datetime] | None:
    """The last regular-session price and its time, if it is stamped on ``session`` at or after that session's regular close."""
    meta = result["meta"]
    price, stamp = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
    if price is None or stamp is None:
        return None
    at = datetime.fromtimestamp(stamp, NEW_YORK)
    regular_end = (meta.get("currentTradingPeriod") or {}).get("regular", {}).get("end")
    closed = (datetime.fromtimestamp(regular_end, NEW_YORK) if regular_end and datetime.fromtimestamp(regular_end, NEW_YORK).date() == session
              else datetime.combine(session, time(16, 0), NEW_YORK))
    return (float(price), at) if at.date() == session and at >= closed else None


def yahoo_file(data: bytes, session: date, now: datetime) -> tuple[bytes, dict]:
    """The file to save and its manifest fields. Fills the session's missing close when the response itself holds it."""
    whole, result, days = yahoo_bars(data)
    quote = result["indicators"]["quote"][0]
    closes = [d for d, c in zip(days, quote["close"]) if c is not None]
    note = {"first_session": str(days[0]), "last_session": str(max(closes)) if closes else None, "rows": len(days),
            "rows_without_a_close": len(days) - len(closes)}
    # A market that trades around the clock is served with a last bar that is the quote at download: either stamped with the
    # quote's own time, or belonging to a trading period that has not ended. A later snapshot will hold another value for that date.
    period = (result["meta"].get("currentTradingPeriod") or {}).get("regular") or {}
    unfinished = period.get("start") is not None and period.get("end") is not None and period["start"] <= result["timestamp"][-1] and now.timestamp() < period["end"]
    if quote["close"][-1] is not None and (unfinished or result["timestamp"][-1] == result["meta"].get("regularMarketTime")):
        note["last_bar_is_a_live_quote"] = str(days[-1])
    if days[-1] == session and quote["close"][-1] is None:
        found = last_regular_price(result, session)
        if found is None:
            note["session_close_missing"] = "the session's bar has no close and the response holds no price stamped after the regular close"
            return data, note
        quote["close"][-1] = found[0]
        note.update(last_session=str(session), rows_without_a_close=note["rows_without_a_close"] - 1, raw_sha256=digest(data),
                    filled_session_close={"value": found[0], "from": "meta.regularMarketPrice of the same response", "stamped": found[1].isoformat()},
                    repair={"session": str(session), "field": "close", "stamped": found[1].isoformat()})
        return json.dumps(whole, separators=(",", ":")).encode("utf-8"), note
    return data, note


def cboe_rows(data: bytes) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    if not rows or not {"DATE", "CLOSE"} <= set(rows[0]):
        raise ValueError("not a Cboe index history file")
    for row in rows:
        row["_day"] = datetime.strptime(row["DATE"], "%m/%d/%Y").date()
        float(row["CLOSE"])
    return rows


def cboe_file(name: str, data: bytes, session: date, now: datetime, fetcher) -> tuple[bytes, dict, bytes | None]:
    """The file to save, its manifest fields, and the Yahoo response used for a repair (None when none was needed)."""
    rows = cboe_rows(data)
    note = {"first_session": str(rows[0]["_day"]), "last_session": str(rows[-1]["_day"]), "rows": len(rows)}
    if rows[-1]["_day"] >= session or name not in CBOE_ON_YAHOO:
        return data, note, None
    other = fetcher(yahoo_url(CBOE_ON_YAHOO[name], now, recent=True))
    _, result, days = yahoo_bars(other)
    quote = result["indicators"]["quote"][0]
    bars = {d: i for i, d in enumerate(days)}
    previous, here = bars.get(rows[-1]["_day"]), bars.get(session)
    close = None if here is None else quote["close"][here]
    found = last_regular_price(result, session)
    if close is None and here is not None and found:
        close = found[0]
    # The row is knowable no earlier than the quote it is taken from. A finished bar carries no time of its own, so
    # without a quote stamped on the session the download time stands in: late, never early.
    stamped = found[1] if found else now.astimezone(NEW_YORK)
    problem = ("the other source has no close for the session" if close is None else
               "the other source has no value for the file's last session" if previous is None or quote["close"][previous] is None else
               "the two sources disagree on the file's last session" if abs(quote["close"][previous] - float(rows[-1]["CLOSE"])) > AGREE else None)
    if problem:
        note["session_row_missing"] = f"the file ends at {rows[-1]['_day']}; not repaired: {problem}"
        return data, note, other
    cells = {"DATE": session.strftime("%m/%d/%Y"), "CLOSE": f"{close:.6f}",
             **{k: f"{quote[k.lower()][here]:.6f}" for k in ("OPEN", "HIGH", "LOW") if k in rows[0] and quote[k.lower()][here] is not None}}
    header = [k for k in rows[0] if k != "_day"]
    text = data.decode("utf-8").rstrip("\r\n") + "\n" + ",".join(cells.get(k, "") for k in header) + "\n"
    note.update(last_session=str(session), rows=len(rows) + 1, raw_sha256=digest(data),
                appended_session_row={"close": float(cells["CLOSE"]), "from": f"Yahoo {CBOE_ON_YAHOO[name]}",
                                      "agreement_checked_on": str(rows[-1]["_day"]), "stamped": stamped.isoformat()},
                repair={"session": str(session), "field": "close", "stamped": stamped.isoformat()})
    return text.encode("utf-8"), note, other


def treasury_file(years: dict[int, bytes]) -> tuple[bytes, dict]:
    """One DATE,CLOSE file of the 10-year real yield, oldest first, from the per-year files. A day with no 10-year value is left out."""
    values = {}
    for year, data in years.items():
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        if not rows or "Date" not in rows[0] or "10 YR" not in rows[0]:
            raise ValueError(f"the Treasury file for {year} has no Date or 10 YR column")
        for row in rows:
            if row["10 YR"] not in ("", "N/A"):
                values[datetime.strptime(row["Date"], "%m/%d/%Y").date()] = row["10 YR"]
    days = sorted(values)
    text = "DATE,CLOSE\n" + "".join(f"{d:%m/%d/%Y},{values[d]}\n" for d in days)
    return text.encode("utf-8"), {"first_session": str(days[0]), "last_session": str(days[-1]), "rows": len(days)}


def latest_closed_session(sessions: list[date], now: datetime) -> date:
    """The latest session whose regular close (taken as 16:00 New York) is at or before ``now``."""
    local = now.astimezone(NEW_YORK)
    done = [s for s in sessions if s < local.date() or (s == local.date() and local.time() >= time(16, 0))]
    if not done:
        raise SystemExit("no session in the calendar has closed yet")
    return done[-1]


def build(folder: Path, session: date, now: datetime, catalogue_bytes: bytes, previous: Path | None, fetcher, pause: float,
          shared: dict[str, dict]) -> dict:
    """Download everything into ``folder`` and return the manifest. Raises on the first source that fails."""
    files, left_out = plan(json.loads(catalogue_bytes))
    manifest = {"snapshot": str(session), "session": str(session), "downloaded_at": now.astimezone(timezone.utc).isoformat(),
                "catalogue_sha256": digest(catalogue_bytes),
                "describe": "Every series the bindings catalogue reads from the network, as downloadable after this session. Downloaded market "
                            "data is not in the repository; exact replay needs the saved bytes matching the hashes below.",
                "files": {}, "shared": shared, "not_downloaded": left_out}

    def save(name: str, data: bytes, raw: bytes | None = None) -> None:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if raw is not None:
            (folder / "raw" / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / "raw" / name).write_bytes(raw)

    for name, entry in sorted(files.items()):
        kind, note = entry.pop("kind"), {}
        if kind == "yahoo":
            raw = fetcher(yahoo_url(entry.pop("symbol"), now))
            data, note = yahoo_file(raw, session, now)
            save(name, data, raw if "raw_sha256" in note else None)
        elif kind == "cboe":
            raw = fetcher(entry["source"])
            data, note, other = cboe_file(name, raw, session, now, fetcher)
            save(name, data, raw if "raw_sha256" in note else None)
            if other is not None:
                save(f"raw/{Path(name).stem}_yahoo.json", other)
        else:  # the Treasury publishes one file per year. A finished year is reused from the previous snapshot, but only if
            # that snapshot was itself taken after the year ended: one taken in late December does not hold the year's last days.
            years, reused = {}, []
            for year in range(TREASURY_FIRST_YEAR, session.year + 1):
                kept = previous / "treasury_real_yield" / f"{year}.csv" if previous else None
                if kept is not None and kept.is_file() and int(previous.name[:4]) > year:
                    years[year] = kept.read_bytes(); reused.append(year)
                else:
                    years[year] = fetcher(TREASURY_YEAR.format(year=year)); clock.sleep(pause)
                save(f"treasury_real_yield/{year}.csv", years[year])
            data, note = treasury_file(years)
            note["years"] = {str(y): digest(b) for y, b in years.items()}
            if reused:
                note["years_reused_from"] = f"{previous.name}: {reused[0]} to {reused[-1]}"
            save(name, data)
        repair = note.pop("repair", None)
        if repair is not None:  # read by the loaders: the repaired value is knowable no earlier than the quote that supplied it
            record = json.dumps({"repairs": [repair]}, indent=1).encode("utf-8") + b"\n"
            save(f"{Path(name).stem}.repair.json", record)
            note["repair_file"] = {"name": f"{Path(name).stem}.repair.json", "sha256": digest(record)}
        manifest["files"][name] = {**entry, **note, "sha256": digest(data)}
        clock.sleep(pause)
    return manifest


def checksums(folder: Path) -> str:
    return "".join(f"{digest(p.read_bytes())}  ./{p.relative_to(folder).as_posix()}\n" for p in sorted(folder.rglob("*")) if p.is_file() and p.name != "SHA256SUMS")


def verify(folder: Path) -> list[str]:
    """Names of the files in a written snapshot that are missing or no longer match its manifest."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    return [name for name, entry in manifest["files"].items()
            if not (folder / name).is_file() or digest((folder / name).read_bytes()) != entry["sha256"]]


def back_up(folder: Path, copy: Path) -> None:
    """Copy a snapshot with its checksums. The copy appears under its name only when it is complete."""
    copy.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{copy.name}.partial.", dir=copy.parent))
    try:
        shutil.copytree(folder, work / "copy")
        (work / "copy" / "SHA256SUMS").write_text(checksums(work / "copy"), encoding="utf-8")
        os.rename(work / "copy", copy)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(arguments: list[str] | None = None, root: Path = ROOT, fetcher=fetch, pause: float = 0.6, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download every network-bound series into one snapshot folder.")
    parser.add_argument("--session", type=date.fromisoformat, help="the US session the snapshot is for; default: the latest one that has closed")
    parser.add_argument("--no-backup", action="store_true", help="do not copy the snapshot to run/backup/snapshots/")
    args = parser.parse_args(arguments)
    now = now or datetime.now(timezone.utc)
    sessions = [date.fromisoformat(s) for s in json.loads((root / CALENDAR).read_text(encoding="utf-8"))["sessions"]]
    session = args.session or latest_closed_session(sessions, now)
    if session not in sessions:
        print(f"{session} is not a session of the calendar", file=sys.stderr)
        return 2
    if session > latest_closed_session(sessions, now):
        print(f"{session} has not closed yet", file=sys.stderr)
        return 2
    home, backup = root / "artifacts/snapshots", root / "run/backup/snapshots"
    target, copy = home / str(session), backup / str(session)
    if target.exists():  # never rewritten; a run that follows a failed backup copy verifies it and finishes the copy
        if args.no_backup or copy.exists():
            print(f"{target.relative_to(root)} already exists: a snapshot is written once", file=sys.stderr)
            return 1
        wrong = verify(target)
        if wrong:
            print(f"{target.relative_to(root)} exists but does not match its manifest ({', '.join(wrong)}): not copied", file=sys.stderr)
            return 1
        back_up(target, copy)
        print(f"{target.relative_to(root)} was already written and matches its manifest; copied to {copy.relative_to(root)}")
        return 0
    earlier = sorted(p for p in home.glob("*/treasury_real_yield") if not p.parent.name.startswith(".") and p.parent.name < str(session))
    home.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{session}.partial.", dir=home))
    try:
        shared = {Path(p).name: {"path": p, "sha256": digest((root / p).read_bytes())} for p in SHARED}
        manifest = build(work, session, now, (root / CATALOGUE).read_bytes(), earlier[-1].parent if earlier else None, fetcher, pause, shared)
        (work / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.rename(work, target)  # refuses a non-empty target, so two runs cannot both publish
    except Exception as error:  # any source failing publishes nothing: the day can be retried
        shutil.rmtree(work, ignore_errors=True)
        print(f"no snapshot written: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    flagged = {n: f for n, f in manifest["files"].items() if {"session_close_missing", "session_row_missing"} & set(f)}
    behind = sorted(n for n, f in manifest["files"].items() if (f.get("last_session") or "") < str(session))
    print(f"wrote {target.relative_to(root)}: {len(manifest['files'])} files; repaired: "
          f"{sorted(n for n, f in manifest['files'].items() if 'raw_sha256' in f) or 'none'}; without a value for {session}: {behind or 'none'}")
    for name, entry in flagged.items():
        print(f"  {name}: {entry.get('session_close_missing') or entry.get('session_row_missing')}")
    if not args.no_backup:
        if copy.exists():
            print(f"backup {copy.relative_to(root)} already exists and was left alone", file=sys.stderr)
            return 1
        try:
            back_up(target, copy)
        except OSError as error:  # the snapshot stands; running the command again completes the copy
            print(f"the snapshot is written but its backup copy failed ({error}); run again to complete it", file=sys.stderr)
            return 1
        print(f"copied to {copy.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
