"""Read a saved Yahoo Finance chart response (v8 chart API, daily interval).

Yahoo restates the whole price history after a split, so the values depend on when the
file was saved. A snapshot is therefore identified by its hash, not by its date range.
"""

from datetime import date, datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_contracts.pit import Observation, SeriesBinding

from quant_pit_data.asof import observe


def _result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["chart"]["result"][0]


def load_daily(path: Path, open_binding: SeriesBinding, close_binding: SeriesBinding) -> list[Observation]:
    """One open and one close observation per session, on the snapshot's split basis, without dividends."""
    data = _result(path)
    zone, quote = ZoneInfo(data["meta"]["exchangeTimezoneName"]), data["indicators"]["quote"][0]
    rows = []
    for stamp, opened, closed in zip(data["timestamp"], quote["open"], quote["close"]):
        session = datetime.fromtimestamp(stamp, zone).date()
        if opened is not None:
            rows.append(observe(open_binding, session, opened))
        if closed is not None:
            rows.append(observe(close_binding, session, closed))
    return rows


def ex_dividend_dates(path: Path) -> list[date]:
    data = _result(path)
    zone = ZoneInfo(data["meta"]["exchangeTimezoneName"])
    return sorted(datetime.fromtimestamp(item["date"], zone).date()
                  for item in data.get("events", {}).get("dividends", {}).values())


def split_dates(path: Path) -> list[date]:
    """Dates of splits and stock distributions, on which the restated history changes basis."""
    data = _result(path)
    zone = ZoneInfo(data["meta"]["exchangeTimezoneName"])
    return sorted(datetime.fromtimestamp(item["date"], zone).date()
                  for item in data.get("events", {}).get("splits", {}).values())
