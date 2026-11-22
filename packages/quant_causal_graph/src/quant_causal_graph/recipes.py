"""Turn leads from the frozen research map into a candidate factor recipe.

Reads the knowledge base through its own loader, so validation and projection stay in
one place. Nothing here calls an LLM or looks at prices.
"""

from datetime import datetime
import importlib.util
import json
from pathlib import Path

from quant_contracts.pit import SeriesBinding
from quant_contracts.research import (CandidateFactorRecipe, DataRequirement, GraphVersion,
                                      LeadTestCriteria, ResearchLead)


def _research_map(relationships: Path):
    spec = importlib.util.spec_from_file_location("research_map", relationships / "utils/research_map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_leads(relationships: Path, sector_folder: str, symbol: str,
                  frozen_at: datetime) -> dict[str, ResearchLead]:
    """Every lead the map projects onto one company, keyed by the id evidence cites.

    ``frozen_at`` is when this revision of the map and profile was fixed, e.g. its commit time.
    """
    api = _research_map(relationships)
    sector_path = relationships / sector_folder / f"{sector_folder}.json"
    profile_path = sector_path.parent / symbol / "company.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    view, nodes, relations = api.load(sector_path)
    _, edges, _, _ = api.company_graph(profile, nodes, relations, profile_path, view)
    subject = api.profile_id(profile)
    version = GraphVersion(sector_id=view["sector_id"], sector_revision=view["content_revision"],
                           sector_sha256=view["content_sha256"], profile_id=subject,
                           profile_revision=profile["content_revision"],
                           profile_sha256=api.content_hash(profile), frozen_at=frozen_at)

    def end(node_id: str) -> str:
        return subject if node_id == api.CENTRE else node_id

    return {edge["lead_id"]: ResearchLead(
        lead_id=edge["lead_id"], subject_id=subject, source=version, relation_kind=edge["relation_kind"],
        scope=edge["scope"], hop=edge["hop"], from_id=end(edge["from"]), to_id=end(edge["to"]),
        mechanism=edge["mechanism"],
        test_criteria=LeadTestCriteria(**edge["test_criteria"]) if edge.get("test_criteria") else None)
        for edge in edges}


def event_state_recipe(recipe_id: str, leads: list[ResearchLead], decision_time: datetime, trigger: str,
                       trigger_availability: str, benchmark: str, horizon_sessions: int,
                       inputs: list[SeriesBinding]) -> CandidateFactorRecipe:
    """A recipe that pairs a dated event with a market state and a relative-return window.

    Each bound input becomes an observable data requirement. The direction is left
    unspecified: this recipe describes what to measure, not what to expect.
    """
    requirements = tuple(DataRequirement(series=b.series_id, availability=b.availability, status="observable")
                         for b in inputs)
    return CandidateFactorRecipe(
        recipe_id=recipe_id, subject_id=leads[0].subject_id, decision_time=decision_time, leads=tuple(leads),
        trigger=trigger, trigger_availability=trigger_availability, expected_direction="unspecified",
        benchmark=benchmark, horizons_sessions=(horizon_sessions,), data_requirements=requirements)
