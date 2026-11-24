"""Read a saved Yahoo Finance chart response (v8 chart API, daily interval).

Yahoo restates the whole price history after a split, so the values depend on when the
file was saved. A snapshot is therefore identified by its hash, not by its date range.
"""

from datetime import date, datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_contracts.pit import Observation, SeriesBinding

from quant_pit_data.asof import observe, observe_close, published, repairs


def _result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["chart"]["result"][0]


def load_daily(path: Path, open_binding: SeriesBinding, close_binding: SeriesBinding) -> list[Observation]:
    """One open and one close observation per session, on the snapshot's split basis, without dividends."""
    data = _result(path)
    zone, quote = ZoneInfo(data["meta"]["exchangeTimezoneName"]), data["indicators"]["quote"][0]
    rows, repaired = [], repairs(path)
    for stamp, opened, closed in zip(data["timestamp"], quote["open"], quote["close"]):
        session = datetime.fromtimestamp(stamp, zone).date()
        if opened is not None:
            rows.append(observe(open_binding, session, opened))
        if closed is not None:
            rows.append(observe_close(close_binding, session, closed, repaired))
    return published([open_binding, close_binding], rows)


def load_closes(path: Path, binding: SeriesBinding) -> list[Observation]:
    """One close observation per dated row that has a positive close, for a series read for its close only."""
    data = _result(path)
    zone, repaired = ZoneInfo(data["meta"]["exchangeTimezoneName"]), repairs(path)
    return published([binding], [observe_close(binding, datetime.fromtimestamp(stamp, zone).date(), closed, repaired)
                                 for stamp, closed in zip(data["timestamp"], data["indicators"]["quote"][0]["close"]) if closed is not None and closed > 0])


def close_dates(path: Path) -> list[date]:
    """Every date on which the file has a positive close, whether or not the series had been published by then.

    For a series read on its own dates this is its calendar. A binding's first-publication
    floor removes values, not dates: a date before the launch stays an expected session with
    no value, so a decision then is a recorded gap and not an error.
    """
    data = _result(path)
    zone = ZoneInfo(data["meta"]["exchangeTimezoneName"])
    return sorted(datetime.fromtimestamp(stamp, zone).date()
                  for stamp, closed in zip(data["timestamp"], data["indicators"]["quote"][0]["close"]) if closed is not None and closed > 0)


def monthly_mean_closes(path: Path) -> dict[str, float]:
    """Mean of the daily closes of each month, keyed "YYYY-MM", with a quote's month read in the exchange's timezone.

    A daily row is stamped at the local start of its day. In UTC that can be the evening
    before, so reading the month from the UTC date puts a month's first quote in the month before.
    """
    data = _result(path)
    zone, months = ZoneInfo(data["meta"]["exchangeTimezoneName"]), {}
    for stamp, closed in zip(data["timestamp"], data["indicators"]["quote"][0]["close"]):
        if closed is not None:
            months.setdefault(datetime.fromtimestamp(stamp, zone).strftime("%Y-%m"), []).append(closed)
    return {month: sum(values) / len(values) for month, values in months.items()}


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
