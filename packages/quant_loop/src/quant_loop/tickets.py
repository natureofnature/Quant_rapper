"""The post-market loop: write one immutable beta ticket per subject and session, and score it only after it matures.

    python -m quant_loop.tickets emit SOXX --session 2026-11-20 --snapshot artifacts/snapshots/2026-11-22
    python -m quant_loop.tickets mature SOXX --session 2026-10-23 --snapshot artifacts/snapshots/2026-11-22

Tickets follow the frozen rule baseline: neither fitted model met the registered promotion
limits, so the rule produces the gate. A ticket is written once to
artifacts/tickets/<subject>/<session>.json and is never rewritten. Its outcome goes to a
separate file under outcomes/, and only when the full horizon is in the data. The daily job
passes another folder, in the records repository, so the forward record is kept in one place.
"""

import argparse
from datetime import date, datetime, time, timezone
import hashlib
import json
import operator
import os
from pathlib import Path
import sys
import tempfile

from quant_causal_graph.causal_map import check_bindings
from quant_contracts.causal_map import ResearchInputs
from quant_contracts.evaluation import Factor
from quant_contracts.forecast import AlphaTicket, BetaGap, BetaTicket, GatedAlpha, MaturedOutcome
from quant_contracts.pit import SeriesBinding, SessionCalendar
from quant_evaluator.beta.forward_target import forward_average_return
from quant_evaluator.beta.risk_gate import forward_drawdown
from quant_loop.daily_states import daily_states
from quant_pit_data.asof import load_daily_closes, on_calendar
from quant_pit_data.connectors.yahoo_chart import load_daily
from quant_pit_data.snapshots import problems

STATES = ("GREEN", "YELLOW", "RED")  # by number of stressed rules: none, one, two or more
OPS = {">": operator.gt, "<": operator.lt}


def apply_beta_veto(alpha: AlphaTicket, beta: BetaTicket) -> GatedAlpha:
    """The beta gate's say over one alpha ticket for the same session.

    RED vetoes every bullish ticket, whatever the ticket says about itself. YELLOW caps a
    bullish ticket: no added risk. Anything else passes. A beta ticket for another session
    is refused, since yesterday's gate must not clear today's trade.
    """
    if alpha.session != beta.session:
        raise ValueError("the beta ticket is for a different session than the alpha ticket")
    if alpha.direction == "bullish" and beta.state == "RED":
        action, reason = "vetoed", f"{beta.subject_id} gate is RED: no bullish single-stock entry"
    elif alpha.direction == "bullish" and beta.state == "YELLOW":
        action, reason = "capped", f"{beta.subject_id} gate is YELLOW: no added risk"
    else:
        action, reason = "allowed", f"{beta.subject_id} gate is {beta.state}; the ticket is {alpha.direction}"
    return GatedAlpha(alpha_ticket_id=alpha.ticket_id, beta_ticket_id=beta.ticket_id, action=action, reason=reason)


class NothingToHold(SystemExit):
    """A rule had no value and no earlier live ticket exists to hold. ``gap`` is the record to write in place of a ticket."""

    def __init__(self, gap: BetaGap):
        super().__init__(gap.reason)
        self.gap = gap


def write_once(path: Path, text: str) -> None:
    """Publish a file whole and only once: it fails if the file already exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp", delete=False) as handle:
        handle.write(text)
    try:
        os.link(handle.name, path)
    except FileExistsError:
        raise SystemExit(f"{path} exists: a ticket or outcome is written once and never replaced")
    finally:
        os.unlink(handle.name)


def _load(root: Path, snapshot: Path, symbol: str):
    registration_path = root / "artifacts/ablations/sector_risk_baseline/registration.json"
    registration = json.loads(registration_path.read_text())
    subject = next(s for s in registration["subjects"] if s["subject"] == symbol)
    bindings_dir = root / "knowledge_base/prior_knowledge/bindings"
    b = {s["series_id"]: SeriesBinding(**s) for s in json.loads((bindings_dir / "series_bindings.json").read_text())["series"]}
    manifest = json.loads((snapshot / "manifest.json").read_text())
    hashes, where = {}, {}
    names = ["VIX.csv", "VIX3M.csv", "treasury_real_yield_10y.csv", f"{symbol}.json"] + sorted({s["calendar"] + ".json" for s in registration["signals"]})
    # Only what this ticket reads is checked, by the rule the loaders apply: every record read is declared, present and
    # unchanged. The ticket can then be replayed from the files whose hashes it records, without the rest of the snapshot.
    wrong = problems(snapshot, names)
    if wrong:
        raise SystemExit(f"the snapshot is not sound for this ticket: {'; '.join(wrong)}")
    for name in names:
        # A file is in the snapshot folder, or it is a curated file shared between snapshots that this manifest pins by path and hash.
        entry = manifest["files"].get(name) or manifest.get("shared", {}).get(name)
        if entry is None:
            raise SystemExit(f"{name} is neither in the snapshot nor pinned by its manifest")
        where[name] = snapshot / name if name in manifest["files"] else root / entry["path"]
        hashes[name] = hashlib.sha256(where[name].read_bytes()).hexdigest()
        if hashes[name] != entry["sha256"]:
            raise SystemExit(f"{name} does not match the snapshot manifest")
        if "record_file" in entry:  # the loader dates and counts this file's values by its record, so the record is pinned too
            record = snapshot / entry["record_file"]["name"]
            if not record.is_file():
                raise SystemExit(f"the record of {name} is missing: its values cannot be dated")
            hashes[record.name] = hashlib.sha256(record.read_bytes()).hexdigest()
            if hashes[record.name] != entry["record_file"]["sha256"]:
                raise SystemExit(f"the record of {name} does not match the snapshot manifest")
    calendars = {s["calendar"]: SessionCalendar(**json.loads(where[s["calendar"] + ".json"].read_text())) for s in registration["signals"]}
    equity = calendars["us_equity_sessions_2002_2026"] if "us_equity_sessions_2002_2026" in calendars else next(iter(calendars.values()))
    prices = load_daily(snapshot / f"{symbol}.json", b[subject["open"]], b[subject["close"]])  # already floored at first publication
    return registration, hashlib.sha256(registration_path.read_bytes()).hexdigest(), subject, b, calendars, equity, prices, hashes, bindings_dir


def emit(root: Path, snapshot: Path, symbol: str, session: date, now: datetime | None = None, folder: Path | None = None) -> BetaTicket:
    """The ticket for one session, as of ``now`` (the clock, unless a test supplies it).

    It is refused before the session's decision time, and unless the snapshot already holds
    the session's own close: values carried from earlier days must never stand in for a
    session that has not happened. A ticket written before the next session opens is live;
    one written later is a replay and says so.
    """
    clock = (lambda: now) if now is not None else (lambda: datetime.now(timezone.utc))
    now = clock()
    registration, recipe_hash, subject, b, calendars, equity, prices, hashes, bindings_dir = _load(root, snapshot, symbol)
    factors, rules, series_calendars = [], [], {}
    for signal in registration["signals"]:
        spec = dict(signal["factor"])
        if spec["series_ids"][0].startswith("<"):
            spec["series_ids"] = [subject["close"]]
        factors.append(Factor(**spec))
        rules.append((OPS[signal["stressed_when"]["op"]], signal["stressed_when"]["threshold"]))
        for series_id in spec["series_ids"]:
            series_calendars[series_id] = calendars[signal["calendar"]]
    pinned = ResearchInputs.model_validate_json((bindings_dir / "factor_definitions.json").read_text()).bindings_sha256
    check_bindings({i for f in factors for i in f.series_ids} | {subject["open"], subject["close"]}, b, pinned)
    state, _ = on_calendar(load_daily_closes(snapshot / "VIX.csv", b["cboe:vix"])
                           + load_daily_closes(snapshot / "VIX3M.csv", b["cboe:vix3m"]), equity)
    yields = load_daily_closes(snapshot / "treasury_real_yield_10y.csv", b["treasury:real_yield_10y"])
    if session not in equity.sessions:
        raise SystemExit(f"{session} is not a session of {equity.calendar_id}")
    if not any(x.session == session and x.series_id == subject["close"] for x in prices):
        raise SystemExit(f"the snapshot has no {symbol} close for {session}: that session's ticket cannot be written yet")
    cutoff, states = daily_states([session], time.fromisoformat(registration["decision"]["time"]), factors, b,
                                  state + yields + prices, equity, series_calendars)[0]
    if now < cutoff.time:
        raise SystemExit(f"it is {now.isoformat()}, before the decision time {cutoff.time.isoformat()}: no ticket is written early")
    later = [s for s in equity.sessions if s > session]
    next_open = datetime.combine(later[0], b[subject["open"]].value_time, cutoff.time.tzinfo) if later else None
    values = {s.factor_id: s.value for s in states}
    folder = folder or root / "artifacts/tickets" / symbol  # where this issuer's earlier tickets are: a held state is read from there
    subject_id = next(a for a in b[subject["close"]].anchors)
    if any(v is None for v in values.values()):  # the registered rule: hold the previous target, and say so
        # Only a ticket known to have been written in time was ever in force. One written late shows what the rule would have
        # said, and one from before issuance was recorded has an unknown time: neither is held. Both stay readable as history.
        previous = None
        for path in sorted((p for p in folder.glob("*.json") if p.stem < str(session)), reverse=True) if folder.exists() else []:
            try:
                found = BetaTicket.model_validate_json(path.read_text())
            except ValueError:
                continue  # not a complete ticket: it holds nothing
            # It must be this subject's ticket for the session its file is named after: another subject's, or a copy under
            # another date, was never this subject's state.
            # And it must be that decision's official record: where a gap stands for the same decision, the gap is what was
            # decided, and a ticket beside it was never in force.
            gap_beside = path.parents[3] / "gaps" / path.parents[1].name / path.parents[0].name / path.name if path.parents[2].name == "tickets" else None
            if found.issuance == "live" and found.subject_id == subject_id and str(found.session) == path.stem and not (gap_beside and gap_beside.is_file()):
                previous = found
                break
        if previous is None:
            raise NothingToHold(BetaGap(
                gap_id=f"gap:rule:{subject_id}:{session}", subject_id=subject_id, session=session, decision_time=cutoff.time, issued_at=clock(),
                reason=f"a rule has no value on {session} and there is no earlier live ticket to hold",
                inputs=values, source=f"frozen rule {registration['registration_id']}", recipe_sha256=recipe_hash, snapshot_sha256=hashes))
        gate, stressed, held = previous.state, (), True
    else:
        stressed = tuple(f.factor_id for f, (test, cut) in zip(factors, rules) if test(values[f.factor_id], cut))
        gate, held = STATES[min(len(stressed), 2)], False
    exposure = registration["exposure"]["by_stressed_count"][STATES.index(gate)]
    now = clock()  # read again with the work done: this is when the ticket exists, and it decides live or replay
    issuance = "live" if next_open is not None and now < next_open else "replay"
    return BetaTicket(ticket_id=f"beta:{subject_id}:{session}", subject_id=subject_id, session=session, decision_time=cutoff.time,
                      issued_at=now, issuance=issuance, state=gate, target_exposure=exposure, held=held, stressed_rules=stressed, inputs=values, horizon_sessions=20,
                      source=f"frozen rule {registration['registration_id']}", recipe_sha256=recipe_hash, snapshot_sha256=hashes)


def mature(root: Path, snapshot: Path, symbol: str, session: date) -> MaturedOutcome:
    _, _, subject, b, _, equity, prices, hashes, _ = _load(root, snapshot, symbol)
    ticket = BetaTicket.model_validate_json((root / "artifacts/tickets" / symbol / f"{session}.json").read_text())
    sessions = list(equity.sessions)
    opens = {x.session: x.value for x in prices if x.series_id == subject["open"]}
    closes = {x.session: x.value for x in prices if x.series_id == subject["close"]}
    index = sessions.index(session)
    if index + ticket.horizon_sessions >= len(sessions):
        raise SystemExit("the calendar ends before the ticket's horizon")
    span = sessions[index + 1:index + ticket.horizon_sessions + 1]
    o, c = [opens.get(s) for s in sessions], [closes.get(s) for s in sessions]
    average = forward_average_return(o, c, index, ticket.horizon_sessions)
    if average is None:
        raise SystemExit(f"the ticket has not matured: prices to {span[-1]} are needed")
    return MaturedOutcome(ticket_id=ticket.ticket_id, entry_session=span[0], last_session=span[-1],
                          forward_drawdown=forward_drawdown(o, c, index, ticket.horizon_sessions),
                          forward_average_return=average, snapshot_sha256=hashes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a beta ticket, or score one that has matured.")
    parser.add_argument("action", choices=("emit", "mature"))
    parser.add_argument("symbol")
    parser.add_argument("--session", required=True, type=date.fromisoformat)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args()
    folder = args.root / "artifacts/tickets" / args.symbol
    if args.action == "emit":
        ticket = emit(args.root, args.snapshot, args.symbol, args.session)
        if ticket.issued_at is None or ticket.issuance is None:
            raise SystemExit("a newly issued ticket must record when it was issued")
        write_once(folder / f"{args.session}.json", ticket.model_dump_json(indent=1) + "\n")
        print(f"{ticket.ticket_id}: {ticket.state}, exposure {ticket.target_exposure}, stressed {list(ticket.stressed_rules)}, "
              f"held {ticket.held}, {ticket.issuance}")
    else:
        outcome = mature(args.root, args.snapshot, args.symbol, args.session)
        write_once(folder / "outcomes" / f"{args.session}.json", outcome.model_dump_json(indent=1) + "\n")
        print(f"{outcome.ticket_id}: matured {outcome.last_session}, forward drawdown {outcome.forward_drawdown:+.4f}")


if __name__ == "__main__":
    main()
