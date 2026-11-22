"""A prospective registration must stay consistent with the recipe it fixes.

Skipped when pydantic is not installed.
"""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
for package in ("quant_contracts", "quant_loop", "quant_pit_data", "quant_evaluator"):
    sys.path.insert(0, str(ROOT / "packages" / package / "src"))
try:
    from quant_contracts.evaluation import Factor
    from quant_contracts.research import CandidateFactorRecipe
except ModuleNotFoundError:
    Factor = None


@unittest.skipIf(Factor is None, "pydantic is not installed")
class ProspectiveRegistrationTests(unittest.TestCase):
    def test_each_registration_matches_its_recipe(self):
        folders = sorted(p.parent for p in (ROOT / "artifacts/prospective").glob("*/registration.json"))
        self.assertTrue(folders)
        for folder in folders:
            with self.subTest(folder=folder.name):
                registration = json.loads((folder / "registration.json").read_text(encoding="utf-8"))
                recipe = CandidateFactorRecipe.model_validate_json((folder / registration["recipe_file"]).read_text(encoding="utf-8"))
                self.assertEqual(hashlib.sha256(recipe.model_dump_json().encode("utf-8")).hexdigest(), registration["recipe_sha256"])
                self.assertEqual(recipe.prior_status, "prior_available_at_decision")
                first = datetime.fromisoformat(registration["first_event"]["scheduled_release"])
                self.assertLess(recipe.decision_time, first)  # the recipe was fixed before the first event
                self.assertEqual((recipe.benchmark, recipe.horizons_sessions),
                                 (registration["outcome"]["benchmark"], (registration["outcome"]["horizon_sessions"],)))
                factors = {Factor(**f).factor_id for f in registration["factors"]}
                self.assertEqual({c["factor_id"] for c in registration["comparisons"]}, factors)
                required = {r.series for r in recipe.data_requirements}
                self.assertLessEqual({i for f in registration["factors"] for i in f["series_ids"]}, required)
                prices = registration["price_series"]
                self.assertLessEqual({*prices["subject"], *prices["benchmark"]}, required)

    def test_each_baseline_registration_names_valid_factors_and_pinned_inputs(self):
        folders = sorted(p.parent for p in (ROOT / "artifacts/ablations").glob("*/registration.json"))
        self.assertTrue(folders)
        catalog = json.loads((ROOT / "knowledge_base/prior_knowledge/bindings/series_bindings.json").read_text(encoding="utf-8"))
        bound = {s["series_id"] for s in catalog["series"]}
        for folder in folders:
            with self.subTest(folder=folder.name):
                registration = json.loads((folder / "registration.json").read_text(encoding="utf-8"))
                manifest = json.loads((ROOT / registration["inputs"]["snapshot"] / "manifest.json").read_text(encoding="utf-8"))
                for name, digest in registration["inputs"]["sha256"].items():
                    self.assertEqual(manifest["files"][name]["sha256"], digest, name)
                self.assertRegex(registration["code"]["commit"], r"^[0-9a-f]{40}$")
                if registration["kind"] == "comparison_registration":  # it also pins the frozen baseline it compares with
                    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
                    for name, digest in registration["rule_baseline"]["sha256"].items():
                        self.assertEqual(sha(ROOT / registration["rule_baseline"]["folder"] / name), digest, name)
                    if "coverage" in registration:  # only a comparison that fixed its start dates from a coverage table
                        self.assertEqual(sha(folder / "coverage.json"), registration["coverage"]["coverage_json_sha256"])
                    for name, digest in registration.get("model_a", {}).get("sha256", {}).items():  # a frozen earlier arm it shows
                        self.assertEqual(sha(ROOT / registration["model_a"]["folder"] / name), digest, name)
                    self.assertEqual(sha(ROOT / "knowledge_base/prior_knowledge/bindings/factor_definitions.json"),
                                     registration["inputs"]["factor_definitions_sha256"])
                    self.assertLessEqual(set(registration["parameters"]["comparison_2_subjects"]), set(registration["parameters"]["subjects"]))
                    self.frozen_record_matches(folder, registration)
                    continue
                closes = {s["close"] for s in registration["subjects"]}
                self.assertLessEqual({i for s in registration["subjects"] for i in (s["open"], s["close"])}, bound)
                for signal in registration["signals"]:
                    self.assertIn(signal["calendar"] + ".json", registration["inputs"]["sha256"])
                    series = signal["factor"]["series_ids"]
                    for close in closes if series[0].startswith("<") else [None]:
                        factor = Factor(**{**signal["factor"], "series_ids": [close] if close else series})
                        self.assertLessEqual(set(factor.series_ids), bound)
                self.assertEqual(len(registration["exposure"]["by_stressed_count"]), 3)
                self.assertRegex(registration["code"]["commit"], r"^[0-9a-f]{40}$")
                self.frozen_record_matches(folder, registration)

    def frozen_record_matches(self, folder, registration):
        """A frozen record must still be the registration and the output files beside it."""
        if not (folder / "frozen.json").exists():
            return
        frozen = json.loads((folder / "frozen.json").read_text(encoding="utf-8"))
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(frozen["registration_sha256"], digest(folder / "registration.json"))
        self.assertEqual(frozen["code_commit"], registration["code"]["commit"])
        self.assertTrue(frozen["outputs_sha256"])
        for name, expected in frozen["outputs_sha256"].items():
            self.assertEqual(digest(folder / "output" / name), expected, name)


if __name__ == "__main__":
    unittest.main()
