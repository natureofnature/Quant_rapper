"""A daily briefing of what prediction markets are pricing, by topic. Context for a reader; no rule or gate reads it.

    python apps/cli/market_briefing.py                 fetch, save the raw responses, write today's briefing
    python apps/cli/market_briefing.py --from DIR      build a briefing from raw responses already saved in DIR

Topics, tags and thresholds come from knowledge_base/tracing/polymarket/topics.json. Raw responses go to
artifacts/snapshots/polymarket/<UTC date>/ and the briefing to monitors/Daily/briefings/<UTC date>.md. Both
folders are git-ignored: the data is not redistributed. A briefing is written once and never overwritten.

A download that returns nothing usable publishes nothing and exits non-zero, so the day can be retried. A
briefing is dated by the snapshot's recorded download time, never by the time it happens to be built.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "knowledge_base/tracing/polymarket/topics.json"
SHOWN = 12  # rows displayed per topic


def fetch(source: str, tag: str, limit: int) -> bytes:
    query = urllib.parse.urlencode({"closed": "false", "active": "true", "tag_slug": tag, "limit": limit,
                                    "order": "volume24hr", "ascending": "false"})
    request = urllib.request.Request(f"{source}?{query}", headers={"User-Agent": "research-script/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def number(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def rows_of(event: dict) -> list[dict]:
    """One row per open yes-or-no market of an event. A market that is not a plain Yes/No pair is left out."""
    rows = []
    for market in event.get("markets", []):
        try:
            outcomes, prices = json.loads(market.get("outcomes") or "[]"), json.loads(market.get("outcomePrices") or "[]")
        except ValueError:
            continue
        if market.get("closed") or not market.get("active") or outcomes != ["Yes", "No"] or len(prices) != 2:
            continue
        rows.append({"id": str(market.get("id")), "event": event.get("title") or "", "question": market.get("question") or "",
                     "probability": number(prices[0]), "day": number(market.get("oneDayPriceChange")),
                     "week": number(market.get("oneWeekPriceChange")), "volume_24h": number(market.get("volume24hr")) or 0.0,
                     "liquidity": number(market.get("liquidityNum") if market.get("liquidityNum") is not None else market.get("liquidity")) or 0.0,
                     "ends": (market.get("endDate") or "")[:10]})
    return rows


def briefing(config: dict, raw: dict[str, list], stamp: datetime, hashes: dict[str, str]) -> str:
    pct = lambda v: "" if v is None else f"{100 * v:.1f}%"
    move = lambda v: "" if v is None else f"{100 * v:+.1f}"
    money = lambda v: f"{v / 1e6:.2f}m" if v >= 1e6 else f"{v / 1e3:.0f}k"
    lines = [f"# Prediction-market briefing, {stamp:%Y-%m-%d}", "",
             f"Downloaded {stamp:%Y-%m-%d %H:%M} UTC from Polymarket's public API. Context only: no rule or gate reads this. "
             "A probability is a market price, not a forecast we have checked, and it is noisy where little money stands behind it.", "",
             f"Shown: open yes-or-no markets with at least ${config['min_liquidity_usd']:,} of liquidity, by topic, largest one-day move first. "
             f"`thin` marks liquidity under ${config['thin_below_usd']:,}.", ""]
    shown: set[str] = set()  # markets already displayed under an earlier topic
    for topic in config["topics"]:
        words = [w.lower() for w in topic.get("keywords", [])]
        found, missing = {}, []
        for tag in topic["tags"]:
            if tag not in raw:
                missing.append(tag)
                continue
            for event in raw[tag]:
                for row in rows_of(event):
                    text = (row["event"] + " " + row["question"]).lower()
                    if row["id"] in shown or row["liquidity"] < config["min_liquidity_usd"] or row["probability"] is None:
                        continue
                    if words and tag != topic["tags"][0] and not any(w in text for w in words):
                        continue  # a broad second tag is narrowed by the topic's keywords
                    found.setdefault(row["id"], row)  # one row per market within the topic
        rows = list(found.values())
        rows.sort(key=lambda r: (-abs(r["day"] or 0.0), -r["volume_24h"]))
        lines += [f"## {topic['topic']}", "", f"Points to `{topic['anchor']}` in the research map."
                  + ("" if topic.get("tags_verified") else " The tag names for this topic have not been checked against the source.")
                  + (f" No data for: {', '.join(missing)}." if missing else ""), ""]
        if not rows:
            lines += ["No market met the liquidity floor.", ""]
            continue
        lines += ["| Question | Probability | 1 day | 1 week | Volume, 24h | Liquidity | Ends |", "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
        for r in rows[:SHOWN]:
            shown.add(r["id"])  # only a displayed market is withheld from later topics
            thin = " `thin`" if r["liquidity"] < config["thin_below_usd"] else ""
            lines.append(f"| {r['question'].replace('|', '/')}{thin} | {pct(r['probability'])} | {move(r['day'])} | {move(r['week'])} | "
                         f"${money(r['volume_24h'])} | ${money(r['liquidity'])} | {r['ends']} |")
        if len(rows) > SHOWN:
            lines.append(f"\n{len(rows) - SHOWN} more markets met the floor and are not shown here.")
        lines.append("")
    lines += ["## Inputs", "", "Moves are in percentage points of probability, as the source reports them.", ""]
    lines += [f"- `{name}` sha256 `{digest[:16]}…`" for name, digest in sorted(hashes.items())]
    return "\n".join(lines) + "\n"


def valid(data: bytes) -> list | None:
    """The parsed response if it is a JSON list of events, else None."""
    try:
        parsed = json.loads(data)
    except ValueError:
        return None
    return parsed if isinstance(parsed, list) else None


def download(config: dict, folder: Path, stamp: datetime, fetcher=fetch, pause: float = 1.0) -> None:
    """Fetch every tag into a temporary folder and publish it as ``folder`` only if something usable arrived.

    Raises FileExistsError if the snapshot already exists and RuntimeError if no tag returned a valid response;
    in both cases nothing is left behind, so a failed day can be retried.
    """
    if folder.exists():
        raise FileExistsError(f"{folder} already exists: a snapshot is never overwritten")
    folder.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{folder.name}.", dir=folder.parent))
    try:
        good = 0
        for tag in dict.fromkeys(tag for topic in config["topics"] for tag in topic["tags"]):
            try:
                data = fetcher(config["source"], tag, config["events_per_tag"])
            except OSError as problem:
                print(f"no data for tag {tag}: {problem}", file=sys.stderr)
            else:
                if valid(data) is None:
                    print(f"no data for tag {tag}: the response is not a list of events", file=sys.stderr)
                else:
                    (work / f"{tag}.json").write_bytes(data)
                    good += 1
            time.sleep(pause)  # a handful of requests a day, spaced out
        if not good:
            raise RuntimeError("no tag returned a usable response: nothing was saved, and the day can be retried")
        (work / "downloaded_at.txt").write_text(stamp.isoformat() + "\n")
        os.rename(work, folder)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def load(config: dict, folder: Path) -> tuple[dict[str, list], dict[str, str], datetime]:
    """A saved snapshot: its responses, their hashes and its recorded download time. Refuses anything less."""
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a snapshot folder")
    try:
        stamp = datetime.fromisoformat((folder / "downloaded_at.txt").read_text().strip())
    except (OSError, ValueError):
        raise ValueError(f"{folder} has no valid downloaded_at.txt: its data cannot be dated") from None
    if stamp.tzinfo is None:
        raise ValueError(f"{folder}: the recorded download time has no timezone")
    raw, hashes = {}, {}
    for tag in dict.fromkeys(tag for topic in config["topics"] for tag in topic["tags"]):
        path = folder / f"{tag}.json"
        if path.exists():
            data = path.read_bytes()
            parsed = valid(data)
            if parsed is not None:
                raw[tag], hashes[path.name] = parsed, hashlib.sha256(data).hexdigest()
    if not raw:
        raise ValueError(f"{folder} holds no valid response")
    return raw, hashes, stamp


def main(arguments: list[str] | None = None, root: Path = ROOT, fetcher=fetch, pause: float = 1.0) -> int:
    parser = argparse.ArgumentParser(description="Write the daily prediction-market briefing.")
    parser.add_argument("--from", dest="folder", type=Path, help="build from raw responses already saved in this folder")
    args = parser.parse_args(arguments)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    folder = args.folder
    try:
        if folder is None:
            now = datetime.now(timezone.utc)
            folder = root / "artifacts/snapshots/polymarket" / f"{now:%Y-%m-%d}"
            download(config, folder, now, fetcher, pause)
        raw, hashes, stamp = load(config, folder)
    except (FileExistsError, RuntimeError, ValueError) as problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    stamp = stamp.astimezone(timezone.utc)
    out = root / "monitors/Daily/briefings" / f"{stamp:%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with out.open("x", encoding="utf-8") as handle:  # written once
            handle.write(briefing(config, raw, stamp, hashes))
    except FileExistsError:
        print(f"error: {out} already exists: a briefing is never overwritten", file=sys.stderr)
        return 1
    print(f"wrote {out} from {len(raw)} tags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
