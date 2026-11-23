"""Generate the causal map and evaluator-ready recipes for one subject at one decision time.

Entry point:
    python -m quant_causal_graph.causal_map SUBJECT --decision-time 2026-11-22T12:00:00-05:00 --out DIR [--packet FILE]

SUBJECT is a profile id ("fund:soxx") or a symbol ("SOXX"); companies and funds go through
the same path. No lead list, factor list or sector folder is supplied: candidates come from
the leads the frozen profile projects, the series bindings and the versioned factor
definitions. A packet of dated sources is optional and supplies evidence only. The same
inputs always give the same ``causal_map.json`` and ``recipes.json``. Nothing here looks at
prices or returns, and nothing calls an LLM.
"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess

from quant_contracts.causal_map import SUBJECT_CLOSE, Candidate, CausalMap, ResearchInputs, SourcePacket
from quant_contracts.pit import Observation, SeriesBinding
from quant_contracts.research import CandidateFactorRecipe, DataRequirement, ResearchLead
from quant_causal_graph.recipes import find_profile, project_profile

KINDS = {"company_research_profile": "company", "fund_research_profile": "fund"}


def sentence(text: str) -> str:
    return text if text.endswith((".", "?", "!")) else text + "."


def price_series(bindings: dict[str, SeriesBinding], subject: str) -> dict[str, str]:
    """The subject's own open and close series: the bindings anchored to it whose id ends in :open or :close."""
    return {role: b.series_id for b in bindings.values() for role in ("open", "close")
            if subject in b.anchors and b.series_id.endswith(f":{role}")}


def generate(leads: dict[str, ResearchLead], nodes: dict, profile: dict, subject: str, inputs: ResearchInputs,
             bindings: dict[str, SeriesBinding], packet: SourcePacket | None, frozen_at: datetime, decision_time: datetime,
             inputs_sha256: dict[str, str]) -> tuple[CausalMap, tuple[CandidateFactorRecipe, ...]]:
    """Candidates for ``subject`` from its projected leads, with what was available strictly before ``decision_time``.

    A factor definition becomes a candidate when a projected lead reaches the concept it
    measures, directly or through a link into another map. Every lead that reaches no
    definition is kept as an unmeasured candidate naming what would have to be observed.
    A candidate that cannot be measured leaves the others untouched.
    """
    if packet is not None and packet.subject_id != subject:
        raise ValueError("the packet is about a different subject")
    evidence = packet.evidence if packet else ()
    observables = set(profile["observables_to_research"])
    unknown = sorted({e.lead_id for e in evidence if e.lead_id is not None} - set(leads))
    if unknown:
        raise ValueError(f"evidence cites a lead the profile does not project: {unknown}")
    named = {e.observable for e in evidence if e.observable is not None} | ({packet.event.observable} if packet and packet.event else set())
    if named - observables:
        raise ValueError(f"not an observable of the profile: {sorted(named - observables)}")

    eligible = tuple(e for e in evidence if e.available_at is None or e.available_at < decision_time)
    seen = {e.evidence_id: e for e in eligible}
    content = []
    for item in packet.content if packet else ():
        if item.evidence_id in seen:
            source = seen[item.evidence_id].citation
            # A stated time of publication is a record. A date alone gives only the conservative cutoff, an assumption.
            content.append(Observation(series_id=item.series_id, session=item.session, value=item.value,
                                       available_at=source.available_at,
                                       availability_basis="recorded" if source.published_at is not None else "assumed",
                                       source_url=source.url))
    event = packet.event if packet else None

    # For each lead: the concepts it touches, and the concepts those link to in other maps, with the linking question.
    reach: dict[str, dict[str, tuple[str, str | None]]] = {}
    for lead in leads.values():
        found = {}
        for end in (lead.from_id, lead.to_id):
            if end == subject:
                continue
            found[end] = (end, None)
            for link in nodes.get(end, {}).get("related_concepts", ()):
                found.setdefault(link["concept_id"], (end, link["question"]))
        reach[lead.lead_id] = found

    def tally(origins: tuple[str, ...]) -> dict:
        records = [e for e in eligible if e.lead_id in origins]
        return {"evidence_ids": tuple(r.evidence_id for r in records),
                "supports": sum(r.assessment == "supports" for r in records),
                "contradicts": sum(r.assessment == "contradicts" for r in records),
                "inconclusive": sum(r.assessment == "inconclusive" for r in records),
                "gaps": sum(r.evidence_class == "data_gap" for r in records)}

    prices = price_series(bindings, subject)
    kind, benchmark = KINDS[profile["kind"]], inputs.benchmarks.get(subject)
    candidates, used = [], set()
    for definition in inputs.factors:
        factor, anchor = definition.factor, definition.anchor
        if anchor == subject:
            origins = tuple(i for i in definition.origin_lead_ids if i in leads)
            via, why = (), f"{definition.note} Origin: {sentence(leads[origins[0]].mechanism)}" if origins else ""
        elif ":" in anchor and anchor.split(":")[0] in ("company", "fund"):
            continue  # a factor that exists only for another subject
        else:
            origins = tuple(i for i in leads if anchor in reach[i])
            if origins:
                end, question = reach[origins[0]][anchor]
                via = (anchor,) if question is None else (end, anchor)
                link = f"Linked from {nodes[end]['name']}: {question}" if question else sentence(leads[origins[0]].mechanism)
                why = f"{definition.note} {link}"
        if not origins:
            continue
        used.update(origins)
        missing = None
        if SUBJECT_CLOSE in factor.series_ids:
            if "close" not in prices:
                missing = f"No closing-price series is bound for {subject}."
            else:
                factor = factor.model_copy(update={"series_ids": tuple(prices["close"] if i == SUBJECT_CLOSE else i
                                                                         for i in factor.series_ids)})
        unbound = [i for i in factor.series_ids if i != SUBJECT_CLOSE and i not in bindings]
        if unbound:
            missing = f"No binding for {', '.join(unbound)}."
        # A factor is tied to the event only when it is the event's own content. Every other factor is a state
        # read at the decision time, whether or not an event focuses the map.
        timing = "event_content" if event is not None and event.content_series in factor.series_ids else "decision_time"
        if missing is None and definition.values_from == "packet":
            have = [o for o in content if o.series_id in factor.series_ids]
            if timing == "event_content":
                if not any(o.session == event.content_session for o in have):
                    missing = (f"The value {event.event_id} reports for {event.content_session} was not available before the "
                               "decision time. An earlier period's value is not used in its place.")
            elif not have:
                missing = "No dated source reporting this value was available before the decision time."
        recipe_id = f"recipe:{subject}:{factor.factor_id}:{benchmark or 'absolute'}:{inputs.horizon_sessions[kind]}"
        candidates.append(dict(candidate_id=f"factor:{definition.factor.factor_id}",
                               status="unmeasured" if missing else "measurable", origin_lead_ids=origins,
                               relevance=why, via=via, factor=factor, reads=definition.reads, timing=timing,
                               missing=missing, recipe_id=None if missing else recipe_id, **tally(origins)))
    for lead in leads.values():
        if lead.lead_id in used:
            continue
        ends = [e for e in (lead.from_id, lead.to_id) if e != subject]
        watch = [w for e in ends for w in nodes.get(e, {}).get("watch", ())]
        names = ", ".join(nodes[e]["name"] if e in nodes else e for e in ends)
        candidates.append(dict(candidate_id=f"unmeasured:{lead.lead_id}", status="unmeasured", origin_lead_ids=(lead.lead_id,),
                               relevance=sentence(lead.mechanism), via=tuple(ends),
                               missing=f"No factor is defined on a bound series for {names}."
                                       + (f" The prior lists these observables: {'; '.join(watch)}." if watch else ""),
                               **tally((lead.lead_id,))))

    outcome = [(i, prices.get(role)) for i, role in ((f"{subject} open", "open"), (f"{subject} close", "close"))]
    if benchmark:
        other = price_series(bindings, benchmark)
        outcome += [(f"{benchmark} open", other.get("open")), (f"{benchmark} close", other.get("close"))]
    recipes = []
    for fields in candidates:
        if fields["status"] != "measurable":
            continue
        origin, factor = fields["origin_lead_ids"], fields["factor"]
        requirements = [DataRequirement(series=i, availability=bindings[i].availability, status="observable")
                        for i in factor.series_ids]
        requirements += [DataRequirement(series=series or label, availability=bindings[series].availability if series else None,
                                         status="observable" if series else "data_gap") for label, series in outcome]
        own = tuple(e for e in eligible if e.lead_id in origin or e.observable is not None)
        recipes.append(CandidateFactorRecipe(
            recipe_id=fields["recipe_id"], subject_id=subject, decision_time=decision_time, leads=tuple(leads[i] for i in origin),
            evidence=own, trigger=(f"{event.event_id}: the release of '{event.observable}', once its content is available"
                                   if fields["timing"] == "event_content" else "The decision time itself, on any US session"),
            trigger_availability="official_archive", expected_direction="unspecified", benchmark=benchmark,
            horizons_sessions=(inputs.horizon_sessions[kind],), data_requirements=tuple(requirements)))
        fields["recipe_sha256"] = recipe_digest(recipes[-1])

    prior = next(iter(leads.values())).source
    causal_map = CausalMap(subject_id=subject, decision_time=decision_time, event=event, prior=prior,
                           prior_status="prior_available_at_decision" if frozen_at <= decision_time else "exploratory",
                           leads=tuple(leads.values()), candidates=tuple(Candidate(**f) for f in candidates), evidence=eligible,
                           content=tuple(content), inputs_sha256=inputs_sha256)
    return causal_map, tuple(recipes)


def recipe_digest(recipe: CandidateFactorRecipe) -> str:
    """The hash an event table records for the recipe it was built from."""
    return hashlib.sha256(recipe.model_dump_json().encode("utf-8")).hexdigest()


def binding_digest(binding: SeriesBinding) -> str:
    return hashlib.sha256(json.dumps(binding.model_dump(mode="json"), sort_keys=True).encode("utf-8")).hexdigest()


def check_bindings(series_ids: set[str], bindings: dict[str, SeriesBinding], pinned: dict[str, str]) -> dict[str, str]:
    """The pinned hash of each binding a run used; refuses one that is not pinned or no longer matches."""
    changed = sorted(i for i in series_ids if pinned.get(i) != binding_digest(bindings[i]))
    if changed:
        raise ValueError(f"these bindings are not pinned by the factor definitions, or have changed since: {changed}")
    return {i: pinned[i] for i in sorted(series_ids)}


def check_frozen_prior(relationships: Path, commit: str) -> None:
    """Freeze map data and the code that projects it, independently of the visualizer."""
    git = ["git", "-C", str(relationships)]
    paths = [":(glob)**/*.json", "utils/research_map.py"]
    tracked = subprocess.run([*git, "diff", "--quiet", commit, "--", *paths])
    # Projection also discovers new profiles on disk, including untracked ones.
    new = subprocess.check_output([*git, "ls-files", "--others", "--", *paths])
    if tracked.returncode or new:
        raise ValueError("the research prior differs from the commit the factor definitions pin")


def generate_from_files(root: Path, subject: str, decision_time: datetime,
                        packet_path: Path | None = None) -> tuple[CausalMap, tuple[CandidateFactorRecipe, ...]]:
    """The documented entry point: everything is resolved from the repository at ``root``.

    The prior must equal the commit the factor definitions pin, and every binding the run
    uses must equal its pinned hash. The result records the hashes of the definitions, the
    packet and those bindings.
    """
    relationships = root / "knowledge_base/prior_knowledge/Relationships"
    bindings_dir = relationships.parent / "bindings"
    definitions_path = bindings_dir / "factor_definitions.json"
    inputs = ResearchInputs.model_validate_json(definitions_path.read_text(encoding="utf-8"))
    check_frozen_prior(relationships, inputs.prior_commit)
    frozen_at = datetime.fromisoformat(subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%cI", inputs.prior_commit], check=True, capture_output=True,
        text=True).stdout.strip())
    catalog = json.loads((bindings_dir / "series_bindings.json").read_text(encoding="utf-8"))
    bindings = {s["series_id"]: SeriesBinding(**s) for s in catalog["series"]}
    leads, nodes, profile = project_profile(relationships, find_profile(relationships, subject), frozen_at)
    subject_id = next(iter(leads.values())).subject_id
    packet = SourcePacket.model_validate_json(packet_path.read_text(encoding="utf-8")) if packet_path else None
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    hashes = {"factor_definitions": digest(definitions_path), "prior_commit": inputs.prior_commit}
    if packet_path:
        hashes["packet"] = digest(packet_path)
    causal_map, recipes = generate(leads, nodes, profile, subject_id, inputs, bindings, packet, frozen_at, decision_time, hashes)
    used = {r.series for recipe in recipes for r in recipe.data_requirements if r.series in bindings}
    used |= {o.series_id for o in causal_map.content}
    for series_id, value in check_bindings(used, bindings, inputs.bindings_sha256).items():
        hashes[f"binding:{series_id}"] = value
    return causal_map.model_copy(update={"inputs_sha256": hashes}), recipes


def write(out: Path, causal_map: CausalMap, recipes: tuple[CandidateFactorRecipe, ...]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "causal_map.json").write_text(causal_map.model_dump_json(indent=1) + "\n", encoding="utf-8")
    (out / "recipes.json").write_text(json.dumps([json.loads(r.model_dump_json()) for r in recipes], indent=1) + "\n",
                                      encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("subject", help='a profile id such as "fund:soxx", or a symbol such as "SOXX"')
    parser.add_argument("--decision-time", required=True, type=datetime.fromisoformat, help="ISO time with a UTC offset")
    parser.add_argument("--packet", type=Path, help="optional curated packet of dated sources")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args()
    causal_map, recipes = generate_from_files(args.root, args.subject, args.decision_time, args.packet)
    write(args.out, causal_map, recipes)
    measurable = [c for c in causal_map.candidates if c.status == "measurable"]
    print(f"{causal_map.subject_id}: {len(causal_map.leads)} leads | {len(causal_map.candidates)} candidates, "
          f"{len(measurable)} measurable ({', '.join(c.factor.factor_id for c in measurable) or 'none'}) | "
          f"{len(causal_map.evidence)} evidence records | prior {causal_map.prior_status}")


if __name__ == "__main__":
    main()
