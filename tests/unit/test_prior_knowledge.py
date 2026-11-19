"""Semantic checks for research-map projection, provenance and content pins."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
# Found by name, so moving these folders inside knowledge_base/ does not break the tests.
KNOWLEDGE = ROOT / "knowledge_base"
BASE = next(KNOWLEDGE.rglob("semiconductor/semiconductor.json")).parent.parent  # holds the sector folders
INSTITUTIONS = next(KNOWLEDGE.rglob("institutions.json"))
sys.path.insert(0, str(next(KNOWLEDGE.rglob("relationship_visualizer.py")).parent))  # the visualizer and utils/
import relationship_visualizer  # noqa: E402
from utils import research_map  # noqa: E402

API = {**vars(research_map), **vars(relationship_visualizer)}


class ResearchMapTests(unittest.TestCase):
    def setUp(self):
        self.path = BASE / "semiconductor/semiconductor.json"
        self.data = json.loads(self.path.read_text())
        self.profile_path = self.path.parent / "TSM/company.json"
        self.profile = json.loads(self.profile_path.read_text())
        self.view, self.nodes, self.relations = API["load"](self.path)
        # The tests set the projection rule they need, so editing the curated profile cannot break them.
        role = self.profile["business_roles"][0]["concept_id"]
        self.conditioned = next(e["id"] for e in self.relations if e["to"] == role)
        self.profile["projection"] = {"excluded_edge_ids": [],
                                      "edge_conditions": {self.conditioned: "Synthetic condition for the test."}}

    def project(self):
        return API["company_graph"](self.profile, self.nodes, self.relations, self.profile_path, self.view)

    def test_original_ids_and_endpoints_survive_projection(self):
        _, edges, _, _ = self.project()
        originals = {e["id"]: e for e in self.data["concept_edges"]}
        self.assertEqual(len(edges), len({e["id"] for e in edges}))
        for edge in edges:
            self.assertTrue(edge["origin_content_sha256"])
            if edge["scope"] == "company":
                self.assertEqual(edge["status"], "research_lead")
                continue
            original = originals[edge["origin_edge_id"]]
            self.assertEqual((edge["origin_from"], edge["origin_to"]), (original["from"], original["to"]))
            if "@company" in (edge["from"], edge["to"]):
                self.assertEqual(edge["status"], "candidate")
                self.assertTrue(edge["projection_basis"])

    def test_company_condition_is_retained_on_candidate(self):
        edge_id, condition = next(iter(self.profile["projection"]["edge_conditions"].items()))
        edges = self.project()[1]
        projected = next(e for e in edges if e["origin_edge_id"] == edge_id)
        self.assertIn(condition, projected["applicability_notes"])
        self.assertEqual(projected["to"], "@company")

    def test_excluded_edge_does_not_reappear_as_upstream_link(self):
        edge_id = next(iter(self.profile["projection"]["edge_conditions"]))
        self.profile["projection"]["edge_conditions"] = {}
        self.profile["projection"]["excluded_edge_ids"] = [edge_id]
        self.assertNotIn(edge_id, {e["origin_edge_id"] for e in self.project()[1]})

    def test_sector_candidate_can_be_excluded(self):
        edge_id = next(e["id"] for e in self.relations if e["to"] == self.view["root_id"])
        self.profile["projection"]["excluded_edge_ids"] = [edge_id]
        self.assertNotIn(edge_id, {e["origin_edge_id"] for e in self.project()[1]})

    def test_typo_in_projection_rule_is_rejected(self):
        self.profile["projection"]["excluded_edge_ids"] = ["missing-edge"]
        with self.assertRaisesRegex(ValueError, "excluded_edge_ids"):
            self.project()

    def test_stale_content_pin_is_rejected_even_without_revision_bump(self):
        self.data["concept_edges"][0]["mechanism_hypothesis"] += " revised"
        self.view, self.nodes, self.relations = API["load_research_map"](self.path, self.data)
        with self.assertRaisesRegex(ValueError, "sector reference"):
            self.project()

    def test_hash_ignores_object_order(self):
        reordered = dict(reversed(list(self.data.items())))
        self.assertEqual(API["content_hash"](self.data), API["content_hash"](reordered))

    def test_duplicate_ids_and_dangling_edges_are_rejected(self):
        bad = copy.deepcopy(self.data)
        bad["concept_edges"].append(copy.deepcopy(bad["concept_edges"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate edge"):
            API["load_research_map"](self.path, bad)
        bad = copy.deepcopy(self.data)
        bad["concept_edges"][0]["to"] = "missing-concept"
        with self.assertRaisesRegex(ValueError, "unknown endpoint"):
            API["load_research_map"](self.path, bad)

    def test_non_business_role_cannot_trigger_role_projection(self):
        self.profile["business_roles"][0]["concept_id"] = "concept:politics:us"
        with self.assertRaisesRegex(ValueError, "invalid business role"):
            self.project()

    def test_observation_links_do_not_become_mechanism_claims(self):
        edges = self.project()[1]
        observations = [e for e in edges if e["relation_kind"] == "observation_lead"]
        self.assertTrue(observations)
        self.assertTrue(all(e["status"] in {"research_lead", "candidate"} for e in observations))

    def test_evidence_attaches_to_origin_without_promoting_candidate(self):
        lead = next(iter(self.profile["projection"]["edge_conditions"]))
        for assessment in ("supports", "contradicts", "inconclusive"):
            with self.subTest(assessment=assessment):
                self.profile["evidence"] = [{
                    "id": "test-evidence", "lead_id": lead, "assessment": assessment,
                    "summary": "Synthetic fixture, not research evidence.",
                    "citation": {"url": "https://example.invalid/report", "locator": "page 1",
                                 "published_on": "2026-11-18", "retrieved_on": "2026-11-19"},
                }]
                edge = next(e for e in self.project()[1] if e["origin_edge_id"] == lead)
                self.assertEqual(edge["evidence"][0]["assessment"], assessment)
                self.assertEqual(edge["status"], "candidate")

    def test_evidence_requires_known_lead_and_specific_citation(self):
        self.profile["evidence"] = [{"id": "test", "lead_id": "missing"}]
        with self.assertRaisesRegex(ValueError, "unknown lead"):
            self.project()
        self.profile["evidence"][0].update(
            lead_id=self.relations[0]["id"], assessment="supports", citation={"url": "https://example.invalid"})
        with self.assertRaisesRegex(ValueError, "dated, locatable citation"):
            self.project()

    def test_render_has_complete_provenance_and_valid_endpoints(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "semiconductor"
            shutil.copytree(self.path.parent, destination, ignore=shutil.ignore_patterns("*.html"))
            output = API["render"](destination / "semiconductor.json")
            page = output.read_text()
            payload = json.loads(page.split("const PAGE = ", 1)[1].split(";\nconst map", 1)[0])
        companies = [json.loads(p.read_text())["symbol"] for p in API["profile_paths"](self.path.parent)]
        self.assertEqual(payload["order"], ["semiconductor", *companies])
        self.assertEqual(payload["inputs"]["sector"]["content_sha256"], self.view["content_sha256"])
        self.assertFalse(payload["usage"]["historical_use_before_version_is_available"]["eligible_for_strict_point_in_time_selection"])
        for view in payload["views"].values():
            for edge in view["relations"]:
                self.assertIn(edge["from"], view["nodes"])
                self.assertIn(edge["to"], view["nodes"])
                self.assertTrue(edge["origin_edge_id"])


class MapBuildTests(unittest.TestCase):
    def test_first_single_map_build_and_input_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "maps"
            shutil.copytree(BASE, root, ignore=shutil.ignore_patterns("*.html", "__pycache__"))
            cli = root / "relationship_visualizer.py"
            subprocess.run([sys.executable, str(cli), "technology_platforms"], check=True, capture_output=True)
            page = root / "technology_platforms/technology_platforms.html"

            def payload():
                return json.loads(page.read_text().split("const PAGE = ", 1)[1].split(";\nconst map", 1)[0])

            data = payload()
            for entry in data["maps"]:
                self.assertTrue((page.parent / entry["href"]).is_file())
            node = data["views"]["MSFT"]["nodes"]["concept:technology_platforms:data_centres"]
            link = node["related_concepts"][0]
            target = page.parent / link["href"].split("#")[0]
            self.assertEqual(target.stem, "semiconductor")
            self.assertEqual(data["inputs"]["related_sectors"][0]["content_sha256"],
                             API["content_hash"](json.loads(target.with_suffix(".json").read_text())))
            registry = root / INSTITUTIONS.relative_to(BASE)
            edited = json.loads(registry.read_text())
            edited["content_revision"] += ".test"
            registry.write_text(json.dumps(edited))
            API["render"](page.with_suffix(".json"))
            updated = payload()["inputs"]
            self.assertNotEqual(data["inputs"]["registry"], updated["registry"])
            self.assertEqual(updated["registry"]["content_sha256"], API["content_hash"](edited))
            self.assertEqual(data["inputs"]["sector"], updated["sector"])


class InstitutionRegistryTests(unittest.TestCase):
    path = INSTITUTIONS

    def load(self, path=None):
        sector_nodes, companies = {}, set()
        for sector in API["find_maps"](BASE)[0]:
            view, nodes, _ = API["load"](sector)
            sector_nodes[view["sector_id"]] = nodes
            companies.update(API["profile_id"](json.loads(p.read_text())) for p in API["profile_paths"](sector.parent))
        return API["load_institutions"](path or self.path, sector_nodes, companies)

    def test_registry_loads_and_covers_every_kind(self):
        kinds = {i["institution_kind"] for i in self.load().values()}
        self.assertEqual(kinds, API["INSTITUTION_KINDS"])

    def test_link_to_unknown_concept_is_rejected(self):
        data = json.loads(self.path.read_text())
        data["institutions"][0]["concept_links"][0]["concept_id"] = "concept:missing"
        with tempfile.TemporaryDirectory() as temp:
            broken = Path(temp) / "institutions.json"
            broken.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "unknown concept"):
                self.load(broken)


class FundProfileTests(unittest.TestCase):
    def test_fund_takes_sector_links_and_its_own_exposures_only(self):
        sector = BASE / "semiconductor/semiconductor.json"
        view, nodes, relations = API["load"](sector)
        path = sector.parent / "SOXX/fund.json"
        profile = json.loads(path.read_text())
        _, edges, _, _ = API["company_graph"](profile, nodes, relations, path, view)
        direct = [e for e in edges if "@company" in (e["from"], e["to"])]
        self.assertTrue(direct)
        self.assertEqual({e["scope"] for e in direct}, {"sector", "company"})
        profile["business_roles"] = [{"concept_id": "concept:supply_chain:foundry", "scope_note": "x"}]
        with self.assertRaisesRegex(ValueError, "fund has no business_roles"):
            API["company_graph"](profile, nodes, relations, path, view)


if __name__ == "__main__":
    unittest.main()
