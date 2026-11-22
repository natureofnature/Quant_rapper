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


if __name__ == "__main__":
    unittest.main()
