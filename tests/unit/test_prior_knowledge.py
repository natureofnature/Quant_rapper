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

    def test_tsm_pilot_leads_in_company_projection(self):
        edges = {e["origin_edge_id"]: e for e in self.project()[1]}
        auto = edges["lead:demand:auto_and_industrial:orders:supply_chain:foundry"]
        packaging = edges["lead:supply_chain:packaging_and_test:bottleneck:supply_chain:designers"]
        self.assertEqual(auto["to"], "@company")
        self.assertEqual((packaging["from"], packaging["to"]), ("@company", "concept:supply_chain:designers"))
        self.assertTrue(any("CoWoS" in note for note in packaging["applicability_notes"]))
        for edge in (auto, packaging):
            self.assertTrue(edge["test_criteria"]["contradicts_if"])
        restriction = edges["lead:company:tsmc:concept:politics:us:china_customer_restriction"]
        self.assertEqual((restriction["from"], restriction["to"]), ("concept:politics:us", "@company"))
        self.assertIn("China", restriction["mechanism"])

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

    def test_incomplete_criteria_rejected_on_sector_edges_and_exposures(self):
        criteria = {"trigger": "Order cuts", "observable": "Utilisation",
                    "window": "One quarter", "supports_if": "Utilisation falls"}
        self.data["concept_edges"][0]["test_criteria"] = criteria
        with self.assertRaisesRegex(ValueError, "contradicts_if"):
            API["load_research_map"](self.path, self.data)
        self.profile["exposures"][0]["test_criteria"] = criteria
        with self.assertRaisesRegex(ValueError, "contradicts_if"):
            self.project()

    def test_observability_is_validated_and_needs_a_note_unless_direct(self):
        concept = self.data["concepts"][0]
        for fields, message in (({"observability": "partly"}, "invalid observability"),
                                ({"observability": "none"}, "observable_with"),
                                ({"observability": "proxy", "observable_with": "  "}, "observable_with"),
                                ({"observable_with": "A note without a level."}, "invalid observability")):
            with self.subTest(fields=fields):
                data = copy.deepcopy(self.data)
                data["concepts"][0].update(fields)
                with self.assertRaisesRegex(ValueError, message):
                    API["load_research_map"](self.path, data)
        self.data["concepts"][0].update(observability="proxy", observable_with="A public series with a stated assumption.")
        _, nodes, _ = API["load_research_map"](self.path, self.data)
        self.assertEqual(nodes[concept["id"]]["observability"], "proxy")

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

    def test_evidence_citing_a_retired_id_attaches_to_its_replacement(self):
        retired = self.profile["retired_ids"][0]
        record = {"id": "test-evidence", "lead_id": retired["id"], "assessment": "inconclusive",
                  "summary": "Synthetic fixture, not research evidence.",
                  "citation": {"url": "https://example.invalid/report", "locator": "page 1",
                               "published_on": "2026-11-18", "retrieved_on": "2026-11-19"}}
        self.profile["evidence"] = [record]
        edge = next(e for e in self.project()[1] if e["origin_edge_id"] == retired["replaced_by"])
        self.assertEqual(edge["evidence"], [record])  # the record keeps the id it was written with
        self.profile["retired_ids"][0]["replaced_by"] = "missing"
        with self.assertRaisesRegex(ValueError, "replaced by an unknown lead"):
            self.project()
        self.profile["retired_ids"] = [{"id": self.profile["exposures"][0]["id"], "replaced_by": retired["replaced_by"]}]
        with self.assertRaisesRegex(ValueError, "still in use"):
            self.project()

    def test_evidence_requires_known_lead_and_specific_citation(self):
        self.profile["evidence"] = [{"id": "test", "lead_id": "missing"}]
        with self.assertRaisesRegex(ValueError, "unknown lead"):
            self.project()
        self.profile["evidence"][0].update(
            lead_id=self.relations[0]["id"], assessment="supports", citation={"url": "https://example.invalid"})
        with self.assertRaisesRegex(ValueError, "dated, locatable citation"):
            self.project()

    def test_links_are_labelled_by_hop_and_carry_the_id_evidence_cites(self):
        _, edges, direct, second = self.project()
        self.assertEqual({e["hop"] for e in edges}, {"direct", "second_hop", "lateral"})
        for edge in edges:
            self.assertEqual(edge["lead_id"], edge["origin_edge_id"])
            ends = {edge["from"], edge["to"]}
            if edge["hop"] == "direct":
                self.assertIn("@company", ends)
            elif edge["hop"] == "second_hop":
                self.assertIn(edge["from"], second)
            else:
                self.assertLessEqual(ends, set(direct))

    def test_evidence_can_cite_a_profile_observable_instead_of_a_lead(self):
        record = {"id": "test-evidence", "observable": self.profile["observables_to_research"][0],
                  "assessment": "inconclusive", "summary": "Synthetic fixture, not research evidence.",
                  "citation": {"url": "https://example.invalid/report", "locator": "page 1",
                               "published_on": "2026-11-18", "retrieved_on": "2026-11-19"}}
        self.profile["evidence"] = [record]
        nodes, edges, _, _ = self.project()
        self.assertEqual(nodes["@company"]["evidence"], [record])
        self.assertFalse(any(e["evidence"] for e in edges))
        self.profile["evidence"] = [{**record, "observable": "not in the profile"}]
        with self.assertRaisesRegex(ValueError, "unknown observable"):
            self.project()
        self.profile["evidence"] = [{**record, "lead_id": self.relations[0]["id"]}]
        with self.assertRaisesRegex(ValueError, "not both"):
            self.project()

    def test_render_has_complete_provenance_and_valid_endpoints(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "semiconductor"
            shutil.copytree(self.path.parent, destination, ignore=shutil.ignore_patterns("*.html"))
            # The sector's related concepts point at the shared market-structure map.
            shutil.copytree(BASE / "market_structure", Path(temp) / "market_structure",
                            ignore=shutil.ignore_patterns("*.html"))
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
            pinned = {entry["id"]: entry["content_sha256"] for entry in data["inputs"]["related_sectors"]}
            self.assertEqual(set(pinned), {"semiconductors", "market_structure"})
            self.assertEqual(pinned["semiconductors"],
                             API["content_hash"](json.loads(target.with_suffix(".json").read_text())))
            self.assertEqual(pinned["market_structure"], API["content_hash"](
                json.loads((root / "market_structure/market_structure.json").read_text())))
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


class MarketStructureTests(unittest.TestCase):
    def setUp(self):
        self.path = BASE / "market_structure/market_structure.json"
        self.data = json.loads(self.path.read_text())
        self.view, self.nodes, self.relations = API["load"](self.path)

    def test_every_concept_states_how_observable_it_is(self):
        levels = {c["id"]: c.get("observability") for c in self.data["concepts"]}
        self.assertTrue(all(level in API["OBSERVABILITY"] for level in levels.values()), levels)
        self.assertEqual(levels["concept:derivatives_flows:vol_term_structure"], "direct")
        self.assertEqual(levels["concept:derivatives_flows:dealer_gamma"], "proxy")
        self.assertEqual(levels["concept:systematic_positioning:vol_control_flows"], "none")

    def test_term_structure_is_not_presented_as_a_measure_of_dealer_gamma(self):
        edges = {(e["from"], e["to"]): e for e in self.data["concept_edges"]}
        term, gamma = "concept:derivatives_flows:vol_term_structure", "concept:derivatives_flows:dealer_gamma"
        self.assertEqual(edges[(term, gamma)]["relation_kind"], "mechanism_hypothesis")
        observables = " ".join(self.nodes[gamma]["watch"])
        self.assertNotIn("VIX", observables)
        self.assertIn("VIX/VIX3M ratio", self.nodes[term]["watch"])

    def test_price_transforms_are_not_concepts(self):
        text = json.dumps(self.data["concepts"]).lower()
        for indicator in ("rsi", "macd", "kdj"):
            self.assertNotRegex(text, rf"\b{indicator}\b")

    def test_benchmark_funds_project_the_market_links(self):
        for symbol in ("SPY", "QQQ"):
            path = self.path.parent / symbol / "fund.json"
            _, edges, _, _ = API["company_graph"](json.loads(path.read_text()), self.nodes, self.relations, path, self.view)
            direct = [e for e in edges if e["hop"] == "direct"]
            self.assertEqual({e["scope"] for e in direct}, {"sector", "company"})

    def test_lead_about_an_unmeasurable_concept_cannot_be_scored(self):
        path = self.path.parent / "SPY/fund.json"
        profile = json.loads(path.read_text())
        citation = {"url": "https://example.invalid/report", "locator": "page 1",
                    "published_on": "2026-11-18", "retrieved_on": "2026-11-19"}

        def project(lead, assessment):
            profile["evidence"] = [{"id": "test-evidence", "lead_id": lead, "assessment": assessment,
                                    "summary": "Synthetic fixture, not research evidence.", "citation": citation}]
            return API["company_graph"](profile, self.nodes, self.relations, path, self.view)

        unmeasured = next(e["id"] for e in self.relations if e["from"] == "concept:systematic_positioning:vol_control_flows")
        proxy = next(e["id"] for e in self.relations if e["from"] == "concept:derivatives_flows:dealer_gamma")
        for assessment in ("supports", "contradicts"):
            with self.assertRaisesRegex(ValueError, "observability none"):
                project(unmeasured, assessment)
            project(proxy, assessment)  # a proxy concept stays assessable
        project(unmeasured, "inconclusive")

    def test_fund_flow_leads_stay_at_fund_level(self):
        # Any fund that has a flow lead; a benchmark used only for price comparison needs none.
        flows = [e for path in BASE.glob("*/*/fund.json")
                 for e in json.loads(path.read_text())["exposures"] if e["type"] == "fund_flows"]
        self.assertGreaterEqual(len(flows), 4)  # SPY, QQQ, SOXX and SMH today
        for lead in flows:
            self.assertIn("do not by themselves establish net flows", lead["mechanism_hypothesis"])

    def test_sector_maps_refer_to_the_shared_concepts(self):
        for folder in ("semiconductor", "technology_platforms"):
            data = json.loads(next((BASE / folder).glob("*.json")).read_text())
            targets = {link["concept_id"] for c in data["concepts"] for link in c.get("related_concepts", [])
                       if link["sector_id"] == "market_structure"}
            self.assertLessEqual(targets, set(self.nodes))
            self.assertIn("concept:derivatives_flows:vol_term_structure", targets)


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
