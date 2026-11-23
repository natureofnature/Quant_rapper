"""The prediction-market briefing: a failed day stays retryable, a replay keeps its own date, no market is lost."""

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("market_briefing", ROOT / "apps/cli/market_briefing.py")
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)


def market(number, day=0.01, liquidity=500000.0):
    return {"id": str(number), "question": f"Question {number}?", "outcomes": '["Yes", "No"]', "outcomePrices": '["0.4", "0.6"]',
            "oneDayPriceChange": day, "oneWeekPriceChange": 0.0, "volume24hr": 1000.0, "liquidityNum": liquidity,
            "endDate": "2026-12-31T00:00:00Z", "active": True, "closed": False}


def events(*markets):
    return json.dumps([{"title": "Event", "markets": list(markets)}]).encode()


CONFIG = {"source": "https://example.invalid", "min_liquidity_usd": 20000, "thin_below_usd": 100000, "events_per_tag": 5,
          "topics": [{"topic": "First", "anchor": "concept:a", "tags": ["one"]}, {"topic": "Second", "anchor": "concept:b", "tags": ["two"]}]}


class MarketBriefingTests(unittest.TestCase):
    def run_main(self, root, fetcher, arguments=()):
        saved, mb.CONFIG = mb.CONFIG, root / "topics.json"
        mb.CONFIG.write_text(json.dumps(CONFIG))
        try:
            return mb.main(list(arguments), root=root, fetcher=fetcher, pause=0.0)
        finally:
            mb.CONFIG = saved

    def test_a_failed_download_publishes_nothing_and_the_day_can_be_retried(self):
        def broken(source, tag, limit):
            raise OSError("network down")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(self.run_main(root, broken), 1)
            self.assertEqual(self.run_main(root, lambda *a: b'{"error": "not a list"}'), 1)  # an unusable response is a failure too
            snapshots = root / "artifacts/snapshots/polymarket"
            self.assertEqual(list(snapshots.iterdir()), [])  # no dated folder and no temporary one left behind
            self.assertFalse((root / "monitors/Daily/briefings").exists())
            self.assertEqual(self.run_main(root, lambda source, tag, limit: events(market(1))), 0)  # the retry works
            self.assertEqual(len(list((root / "monitors/Daily/briefings").glob("*.md"))), 1)
            self.assertEqual(self.run_main(root, lambda source, tag, limit: events(market(1))), 1)  # and the day is then closed

    def test_one_usable_tag_is_enough_and_the_missing_one_is_named(self):
        def partial(source, tag, limit):
            if tag == "two":
                raise OSError("timeout")
            return events(market(1))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(self.run_main(root, partial), 0)
            text = next((root / "monitors/Daily/briefings").glob("*.md")).read_text()
            self.assertIn("No data for: two.", text)

    def test_a_replay_keeps_the_snapshot_date_and_refuses_an_undated_or_empty_one(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(self.run_main(root, None, ["--from", str(root / "missing")]), 1)
            old = root / "old"
            old.mkdir()
            (old / "one.json").write_bytes(events(market(1)))
            self.assertEqual(self.run_main(root, None, ["--from", str(old)]), 1)  # no recorded download time
            (old / "downloaded_at.txt").write_text("2025-03-04T21:30:00\n")
            self.assertEqual(self.run_main(root, None, ["--from", str(old)]), 1)  # a time without a timezone
            (old / "downloaded_at.txt").write_text("2025-03-04T21:30:00+00:00\n")
            self.assertEqual(self.run_main(root, None, ["--from", str(old)]), 0)
            briefings = sorted(p.name for p in (root / "monitors/Daily/briefings").glob("*.md"))
            self.assertEqual(briefings, ["2025-03-04.md"])  # dated by the data, not by today
            self.assertIn("Downloaded 2025-03-04 21:30 UTC", (root / "monitors/Daily/briefings/2025-03-04.md").read_text())
            (old / "one.json").write_text("not json")
            self.assertEqual(self.run_main(root, None, ["--from", str(old)]), 1)  # nothing valid to report

    def test_a_market_not_displayed_under_one_topic_is_still_available_to_the_next(self):
        many = [market(n, day=0.5 - n / 100.0) for n in range(1, mb.SHOWN + 2)]  # the last one ranks just below the cut
        hidden = many[-1]
        raw = {"one": json.loads(events(*many, many[0])), "two": json.loads(events(hidden, many[0]))}  # many[0] appears twice
        text = mb.briefing(CONFIG, raw, datetime(2026, 1, 2, tzinfo=timezone.utc), {})
        first, second = text.split("## Second")
        self.assertNotIn(hidden["question"], first)
        self.assertIn("1 more markets met the floor", first)
        self.assertIn(hidden["question"], second)  # shown where it is the only candidate
        self.assertEqual(text.count(many[0]["question"]), 1)  # shown once: not twice in a topic, not again in the next
        self.assertNotIn(many[0]["question"], second)


if __name__ == "__main__":
    unittest.main()
