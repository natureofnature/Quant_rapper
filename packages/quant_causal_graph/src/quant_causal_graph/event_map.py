"""Build the event map and candidate recipe for one event at one decision time.

Entry point:
    python -m quant_causal_graph.event_map PACKET.json --decision-time 2026-11-11T08:00:00-05:00 --out DIR

It reads the curated source packet, the research prior pinned by the packet's commit and
the shared series bindings, and writes ``event_map.json`` and ``recipe.json``. The same
packet, prior and decision time always give the same two files. Nothing here looks at
prices or returns, and nothing calls an LLM.
"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

from quant_contracts.event_map import EventMap, MapEdge, SourcePacket
from quant_contracts.pit import Observation, SeriesBinding
from quant_contracts.research import CandidateFactorRecipe, DataRequirement
from quant_causal_graph.recipes import project_leads


def build_event_map(relationships: Path, packet: SourcePacket, bindings: dict[str, SeriesBinding], frozen_at: datetime,
                    decision_time: datetime, inputs_sha256: dict[str, str]) -> tuple[EventMap, CandidateFactorRecipe]:
    """Select the packet's paths from the pinned prior and attach what was available before ``decision_time``.

    A dated source is used only if its availability time is strictly before the decision
    time; a source that states only a date counts from noon UTC the next day. Recorded gaps
    are always kept. The recipe's content input is a data gap until the event's own value is
    available. The source's own URL and availability travel with each content value.
    """
    leads = project_leads(relationships, packet.sector_folder, packet.symbol, frozen_at)
    profile = json.loads((relationships / packet.sector_folder / packet.symbol / "company.json").read_text(encoding="utf-8"))
    unknown = [p.lead_id for p in packet.paths if p.lead_id not in leads]
    if unknown:
        raise ValueError(f"not a lead of the pinned prior for {packet.symbol}: {unknown}")
    observables = set(profile["observables_to_research"])
    named = {packet.observable, *(e.observable for e in packet.evidence if e.observable is not None)}
    if named - observables:
        raise ValueError(f"not an observable of the pinned profile: {sorted(named - observables)}")
    if any(leads[p.lead_id].subject_id != packet.subject_id for p in packet.paths):
        raise ValueError("the packet's subject differs from the profile's")

    eligible = tuple(e for e in packet.evidence if e.available_at is None or e.available_at < decision_time)
    seen = {e.evidence_id: e for e in eligible}
    content = []
    for item in packet.content:
        if item.evidence_id not in seen:
            continue
        source, binding = seen[item.evidence_id].citation, bindings[item.series_id]
        # A stated time of publication is a record. A date alone gives only the conservative cutoff, which is an assumption.
        content.append(Observation(series_id=item.series_id, session=item.session, value=item.value,
                                   available_at=source.available_at,
                                   availability_basis="recorded" if source.published_at is not None else "assumed",
                                   source_url=source.url))
    content = tuple(content)
    edges = []
    for path in packet.paths:
        records = [e for e in eligible if e.lead_id == path.lead_id]
        edges.append(MapEdge(lead=leads[path.lead_id], relevance=path.relevance,
                             evidence_ids=tuple(r.evidence_id for r in records),
                             supports=sum(r.assessment == "supports" for r in records),
                             contradicts=sum(r.assessment == "contradicts" for r in records),
                             inconclusive=sum(r.assessment == "inconclusive" for r in records),
                             gaps=sum(r.evidence_class == "data_gap" for r in records)))
    prior = edges[0].lead.source
    event_map = EventMap(event_id=packet.event_id, subject_id=packet.subject_id, observable=packet.observable,
                         decision_time=decision_time, event_time=packet.event_time, prior=prior,
                         prior_status="prior_available_at_decision" if frozen_at <= decision_time else "exploratory",
                         edges=tuple(edges), evidence=eligible, content_series=packet.recipe.content_series,
                         content_session=packet.recipe.content_session, content=content, inputs_sha256=inputs_sha256)

    spec = packet.recipe
    reported = any(o.series_id == spec.content_series and o.session == spec.content_session for o in content)
    requirements = [DataRequirement(series=i, availability=bindings[i].availability, status="observable") for i in spec.series_ids]
    requirements.append(DataRequirement(series=spec.content_series, availability=bindings[spec.content_series].availability,
                                        status="observable" if reported else "data_gap"))
    recipe = CandidateFactorRecipe(
        recipe_id=spec.recipe_id, subject_id=packet.subject_id, decision_time=decision_time,
        leads=tuple(e.lead for e in edges), evidence=eligible, trigger=spec.trigger,
        trigger_availability=spec.trigger_availability, expected_direction="unspecified", benchmark=spec.benchmark,
        horizons_sessions=(spec.horizon_sessions,), data_requirements=tuple(requirements))
    return event_map, recipe


def bindings_digest(packet: SourcePacket, bindings: dict[str, SeriesBinding]) -> str:
    """Hash of the bindings the packet uses and no others, so unrelated catalogue changes do not touch it."""
    used = sorted({*packet.recipe.series_ids, packet.recipe.content_series, *(c.series_id for c in packet.content)})
    text = json.dumps([bindings[i].model_dump(mode="json") for i in used], sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_from_files(root: Path, packet_path: Path, decision_time: datetime) -> tuple[EventMap, CandidateFactorRecipe]:
    """The documented entry point: everything is read from the repository at ``root`` and the packet file."""
    relationships = root / "knowledge_base/prior_knowledge/Relationships"
    bindings_path = relationships.parent / "bindings/series_bindings.json"
    packet = SourcePacket.model_validate_json(packet_path.read_text(encoding="utf-8"))
    git = lambda *args: subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()
    if subprocess.run(["git", "-C", str(root), "diff", "--quiet", packet.prior_commit, "--", str(relationships)]).returncode:
        raise ValueError("the research prior differs from the packet's pinned commit")
    frozen_at = datetime.fromisoformat(git("log", "-1", "--format=%cI", packet.prior_commit))
    catalog = json.loads(bindings_path.read_text(encoding="utf-8"))
    bindings = {s["series_id"]: SeriesBinding(**s) for s in catalog["series"]}
    if bindings_digest(packet, bindings) != packet.bindings_sha256:
        raise ValueError("a series binding this packet uses differs from the one it was pinned to")
    hashes = {"packet": hashlib.sha256(packet_path.read_bytes()).hexdigest(), "bindings_used": packet.bindings_sha256,
              "prior_commit": packet.prior_commit}
    return build_event_map(relationships, packet, bindings, frozen_at, decision_time, hashes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("packet", type=Path)
    parser.add_argument("--decision-time", required=True, type=datetime.fromisoformat, help="ISO time with a UTC offset")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args()
    event_map, recipe = build_from_files(args.root, args.packet, args.decision_time)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "event_map.json").write_text(event_map.model_dump_json(indent=1) + "\n", encoding="utf-8")
    (args.out / "recipe.json").write_text(recipe.model_dump_json(indent=1) + "\n", encoding="utf-8")
    print(f"{len(event_map.edges)} edges | {len(event_map.evidence)} evidence records | {len(event_map.content)} content values "
          f"| prior {event_map.prior_status} | recipe {recipe.observability}")


if __name__ == "__main__":
    main()
