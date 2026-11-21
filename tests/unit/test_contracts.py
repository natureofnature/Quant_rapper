"""Invariants of the research contracts. Skipped when pydantic is not installed."""

from datetime import date, datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages/quant_contracts/src"))
try:
    from pydantic import ValidationError
    from quant_contracts import research as c
except ModuleNotFoundError:
    c = None


def utc(month: int, day: int, hour: int = 0) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=timezone.utc)


@unittest.skipIf(c is None, "pydantic is not installed")
class ContractTests(unittest.TestCase):
    def lead(self, frozen_at: datetime, lead_id: str = "lead:a"):
        version = c.GraphVersion(sector_id="semiconductors", sector_revision="r1", sector_sha256="a" * 64,
                                 profile_id="company:tsmc", profile_revision="r1", profile_sha256="b" * 64,
                                 frozen_at=frozen_at)
        return c.ResearchLead(lead_id=lead_id, subject_id="company:tsmc", source=version,
                           relation_kind="mechanism_hypothesis", scope="concept", hop="direct",
                           from_id="concept:x", to_id="company:tsmc", mechanism="Synthetic fixture.")

    def citation(self, published_on: date, **extra):
        return c.Citation(url="https://example.invalid/report", locator="page 1", published_on=published_on,
                          retrieved_on=date(2026, 12, 1), availability="filing", **extra)

    def evidence(self, **fields):
        base = {"evidence_id": "ev:1", "subject_id": "company:tsmc", "lead_id": "lead:a",
                "evidence_class": "direct", "assessment": "supports", "summary": "Synthetic fixture.",
                "citation": self.citation(date(2026, 10, 1))}
        return c.EvidenceRecord(**{**base, **fields})

    def recipe(self, decision_time: datetime, **fields):
        base = {"recipe_id": "recipe:1", "subject_id": "company:tsmc", "decision_time": decision_time,
                "leads": (self.lead(utc(10, 1)),), "trigger": "Monthly revenue release",
                "trigger_availability": "filing", "expected_direction": "unspecified", "benchmark": "SOXX",
                "data_requirements": (c.DataRequirement(series="Monthly revenue", status="observable"),)}
        return c.CandidateFactorRecipe(**{**base, **fields})

    def test_recipe_from_a_prior_frozen_after_the_decision_is_exploratory(self):
        recipe = self.recipe(utc(11, 1), leads=(self.lead(utc(11, 20)), self.lead(utc(11, 20), "lead:b")))
        self.assertEqual(recipe.prior_frozen_at, utc(11, 20))
        self.assertEqual(recipe.prior_status, "exploratory")
        self.assertEqual(self.recipe(utc(11, 1)).prior_status, "prior_available_at_decision")
        with self.assertRaises(ValidationError):  # the status cannot be supplied
            self.recipe(utc(11, 1), prior_status="prior_available_at_decision")

    def test_recipe_rejects_mixed_graph_versions(self):
        first = self.lead(utc(10, 1))
        second = self.lead(utc(10, 1), "lead:b").model_dump()
        second["source"].update(profile_revision="r2", profile_sha256="c" * 64)
        with self.assertRaisesRegex(ValidationError, "same frozen graph version"):
            self.recipe(utc(11, 1), leads=(first, c.ResearchLead(**second)))

    def test_publication_date_matches_the_source_local_time(self):
        with self.assertRaisesRegex(ValidationError, "published_at must match published_on"):
            self.citation(date(2026, 11, 20), published_at=utc(10, 1))
        citation = self.citation(date(2026, 11, 20), published_at="2026-11-20T01:00:00+08:00")
        self.assertEqual(citation.available_at, utc(11, 19, 17))

    def test_evidence_published_after_the_decision_is_rejected(self):
        late = self.evidence(citation=self.citation(date(2026, 11, 10)))
        with self.assertRaisesRegex(ValidationError, "not available at the decision time"):
            self.recipe(utc(11, 1), evidence=(late,))
        self.assertEqual(len(self.recipe(utc(11, 20), evidence=(late,)).evidence), 1)

    def test_date_only_source_cannot_inform_a_same_day_decision(self):
        same_day = self.evidence(citation=self.citation(date(2026, 11, 10)))
        with self.assertRaises(ValidationError):
            self.recipe(utc(11, 10, 23), evidence=(same_day,))
        timed = self.evidence(citation=self.citation(date(2026, 11, 10), published_at=utc(11, 10, 6)))
        self.assertEqual(len(self.recipe(utc(11, 10, 23), evidence=(timed,)).evidence), 1)

    def test_empty_horizons_and_blank_targets_are_rejected(self):
        for horizons in ((), (0,), (5, -1)):
            with self.assertRaises(ValidationError):
                self.recipe(utc(11, 1), horizons_sessions=horizons)
        for target in ({"lead_id": ""}, {"lead_id": "   "}, {"lead_id": None, "observable": " "},
                       {"lead_id": None}, {"observable": "ADR premium"}):
            with self.assertRaises(ValidationError):
                self.evidence(**target)

    def test_recipe_rejects_evidence_for_a_lead_it_does_not_include(self):
        with self.assertRaises(ValidationError):
            self.recipe(utc(11, 1), evidence=(self.evidence(lead_id="lead:other"),))

    def test_data_gap_has_no_citation_and_passes_the_availability_check(self):
        gap = c.EvidenceRecord(evidence_id="ev:2", subject_id="company:tsmc", observable="ADR premium",
                               evidence_class="data_gap", summary="Nothing dated found.", missing="A dated series")
        self.assertIsNone(gap.available_at)
        recipe = self.recipe(utc(11, 1), evidence=(gap,), data_requirements=(
            c.DataRequirement(series="ADR premium", status="data_gap"),))
        self.assertEqual((recipe.prior_status, recipe.observability), ("prior_available_at_decision", "data_gap"))
        with self.assertRaises(ValidationError):
            self.evidence(evidence_class="data_gap")


if __name__ == "__main__":
    unittest.main()
