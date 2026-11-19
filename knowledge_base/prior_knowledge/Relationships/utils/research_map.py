"""Load, validate and project the prior research maps. No drawing.

A sector map is <sector>/<sector>.json; a company profile is <sector>/<SYMBOL>/company.json.
``load`` returns a sector as (view, nodes, relations). ``company_graph`` projects the
sector onto one company: links touching its business roles or the sector node become
candidate links to the company, subject to the profile's exclusions and conditions.

Validation failures raise ValueError.
"""

import hashlib
import json
from pathlib import Path

CENTRE = "@company"  # node id of the company in a projected graph

SCHEMA_VERSION = "2.0.0"
NODE_KINDS = {"jurisdiction", "institution", "variable", "resource", "business_role", "market_segment", "observation"}
RELATION_KINDS = {"mechanism_hypothesis", "observation_lead"}
INSTITUTION_KINDS = {"policy", "research_data", "investor"}  # each has a section of the same name
AVAILABILITY = {"filing", "official_archive", "news_only", "members_only"}
CONCEPT_LINK_KINDS = {"represents", "informs"}
# A profile is a company or a fund. Each kind has its own file name and identity field.
PROFILE_KINDS = {"company_research_profile": "company_id", "fund_research_profile": "fund_id"}
PROFILE_FILES = ("company.json", "fund.json")


def require(condition: bool, source: Path, message: str) -> None:
    if not condition:
        raise ValueError(f"{source}: {message}")


def indexed(items: list, source: Path, label: str) -> dict:
    result = {}
    for item in items:
        key = item.get("id")
        require(isinstance(key, str) and bool(key), source, f"{label} needs a non-empty id")
        require(key not in result, source, f"duplicate {label} id {key!r}")
        result[key] = item
    return result


def profile_id(profile: dict) -> str:
    return profile.get(PROFILE_KINDS.get(profile.get("kind"), "company_id"), "")


def profile_paths(sector_dir: Path) -> list[Path]:
    """Every company and fund profile in a sector folder, ordered by folder name."""
    return sorted(p for name in PROFILE_FILES for p in sector_dir.glob(f"*/{name}"))


def content_hash(data: dict) -> str:
    """Hash JSON content independently of whitespace and object-key order."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pretty(name: str) -> str:
    return name.replace("_", " ")


def validate_test_criteria(edge: dict, source: Path) -> None:
    if "test_criteria" not in edge:
        return
    criteria = edge["test_criteria"]
    fields = ("trigger", "observable", "window", "supports_if", "contradicts_if")
    require(isinstance(criteria, dict) and
            all(isinstance(criteria.get(key), str) and criteria[key].strip() for key in fields),
            source, f"test_criteria on {edge['id']} requires {', '.join(fields)}")


def validate_sector(data: dict, source: Path) -> None:
    require(data.get("schema_version") == SCHEMA_VERSION, source, "expected schema_version 2.0.0")
    require(bool(data.get("content_revision")), source, "missing content_revision")
    require(data.get("kind") == "industry_research_map", source, "expected industry_research_map")
    concepts = indexed(data["concepts"], source, "concept")
    branches = indexed(data["branches"], source, "branch")
    assigned = [c for b in branches.values() for c in b["concept_ids"]]
    require(len(assigned) == len(set(assigned)) and set(assigned) == set(concepts),
            source, "each concept must appear in exactly one display branch")
    levels = [b for level in data["levels"].values() for b in level["branch_ids"]]
    require(len(levels) == len(set(levels)) and set(levels) == set(branches),
            source, "each branch must appear in exactly one level")
    for concept in concepts.values():
        require(concept.get("node_kind") in NODE_KINDS, source, f"invalid node_kind on {concept['id']}")
    root = f"sector:{data['sector_id']}"
    require(data["usage"]["sector_node"]["id"] == root, source, "sector node id mismatch")
    for edge in indexed(data["concept_edges"], source, "edge").values():
        require(edge.get("from") in concepts and edge.get("to") in {*concepts, root},
                source, f"unknown endpoint on {edge['id']}")
        require(edge.get("relation_kind") in RELATION_KINDS, source, f"invalid relation_kind on {edge['id']}")
        validate_test_criteria(edge, source)


def validate_company(profile: dict, view: dict, nodes: dict, relations: list, source: Path) -> None:
    require(profile.get("schema_version") == SCHEMA_VERSION, source, "expected schema_version 2.0.0")
    require(profile.get("kind") in PROFILE_KINDS, source, "expected a company or fund research profile")
    require(bool(profile_id(profile)) and bool(profile.get("content_revision")),
            source, f"missing {PROFILE_KINDS[profile['kind']]} or content_revision")
    is_fund = profile["kind"] == "fund_research_profile"
    expected = {k: view[k] for k in ("schema_version", "content_revision", "content_sha256")}
    expected["id"] = view["sector_id"]
    require(profile.get("sector") == expected, source,
            "sector reference does not match the loaded content; review the sector changes, then set "
            f'"sector" to {json.dumps(expected, sort_keys=True)}')
    roles = profile.get("business_roles", [])
    role_ids = [r["concept_id"] for r in roles]
    # A sector fund holds the sector, so it stands in for the sector node only and has no business role.
    require(not roles if is_fund else bool(roles), source,
            "a fund has no business_roles" if is_fund else "business_roles must be non-empty")
    require(len(role_ids) == len(set(role_ids)), source, "business_roles must be unique")
    for role in roles:
        require(nodes.get(role["concept_id"], {}).get("node_kind") == "business_role",
                source, f"invalid business role {role['concept_id']!r}")
        require(bool(role.get("scope_note")), source, "business role needs scope_note")
    edges = {r["id"]: r for r in relations}
    rules = profile["projection"]
    excluded = rules["excluded_edge_ids"]
    require(len(excluded) == len(set(excluded)) and set(excluded) <= edges.keys(), source, "invalid excluded_edge_ids")
    conditions = rules["edge_conditions"]
    require(set(conditions) <= edges.keys() and not set(conditions) & set(excluded), source, "invalid or excluded edge condition")
    require(all(isinstance(v, str) and v.strip() for v in conditions.values()), source, "edge conditions must be non-empty text")
    for edge_id in {*excluded, *conditions}:
        edge = edges[edge_id]
        require(edge["from"] in role_ids or edge["to"] in [*role_ids, view["root_id"]],
                source, f"projection rule {edge_id!r} does not touch this company's roles or sector")
    exposures = indexed(profile["exposures"], source, "exposure")
    require(not exposures.keys() & edges.keys(), source, "exposure id collides with a sector edge")
    for exposure in exposures.values():
        require(exposure.get("concept") in nodes and exposure["concept"] not in role_ids,
                source, f"invalid exposure concept on {exposure['id']}")
        require(exposure.get("relation_kind") in RELATION_KINDS, source, f"invalid relation_kind on {exposure['id']}")
        validate_test_criteria(exposure, source)
    for record in indexed(profile["evidence"], source, "evidence").values():
        require(record.get("lead_id") in {*edges, *exposures}, source, "evidence refers to an unknown lead")
        require(record.get("assessment") in {"supports", "contradicts", "inconclusive"}, source, "invalid evidence assessment")
        citation = record.get("citation", {})
        require(all(isinstance(citation.get(k), str) and citation[k].strip()
                    for k in ("url", "locator", "published_on", "retrieved_on")), source, "evidence needs a dated, locatable citation")
        require(bool(record.get("summary")), source, "evidence needs a summary")


def load(path: Path) -> tuple[dict, dict, list]:
    """Return a sector as (view, nodes, relations).

    ``view`` has root, reviewed_on, notice, Level_1 (branch order) and one
    {"question": ...} per branch. ``nodes`` maps a node id to its branch, name, role,
    watch list and optional leads. ``relations`` hold from, to, type and mechanism.
    """
    return load_research_map(path, json.loads(path.read_text(encoding="utf-8")))


def load_research_map(path: Path, data: dict) -> tuple[dict, dict, list]:
    """Read the "industry_research_map" schema: flat concepts and concept_edges with ids."""
    validate_sector(data, path)
    level_of = {b: level["label"] for level in data.get("levels", {}).values() for b in level["branch_ids"]}
    usage = data.get("usage", {})
    view = {
        "sector_id": data["sector_id"],
        "schema_version": data["schema_version"],
        "content_revision": data["content_revision"],
        "content_sha256": content_hash(data),
        "usage": usage,
        "root": data.get("label", data.get("sector_id", path.stem)),
        "reviewed_on": data.get("compiled_on"),
        "notice": " ".join(filter(None, [usage.get("describe"), f"Status: {pretty(usage['status'])}." if usage.get("status") else None])),
        "Level_1": [b["id"] for b in data["branches"]],
        # Sector edges become candidate company exposures, subject to projection rules.
        "root_id": f"sector:{data['sector_id']}" if data.get("sector_id") else None,
    }
    concepts = {c["id"]: c for c in data["concepts"]}
    nodes, relations = {}, []
    for branch in data["branches"]:
        question = branch.get("question", "")
        if branch["id"] in level_of:
            question = f"[{level_of[branch['id']]}] {question}"
        view[branch["id"]] = {"question": question}
        for concept_id in branch["concept_ids"]:
            concept = concepts[concept_id]
            nodes[concept_id] = {
                "node_kind": concept["node_kind"],
                "branch": branch["id"],
                "name": concept.get("label", concept_id),
                "role": concept.get("description_lead", ""),
                "watch": concept.get("observables_to_research", []),
                **({"related_concepts": concept["related_concepts"]} if concept.get("related_concepts") else {}),
                **({"leads": concept["source_leads"]} if concept.get("source_leads") else {}),
            }
    for edge in data.get("concept_edges", []):
        relations.append(
            {**edge, "mechanism": edge.get("mechanism_hypothesis", ""), "status": "research_lead",
             "origin_edge_id": edge["id"], "origin_from": edge["from"], "origin_to": edge["to"],
             "origin_content_revision": view["content_revision"], "origin_content_sha256": view["content_sha256"]}
        )
    return view, nodes, relations


def company_graph(profile: dict, nodes: dict, relations: list, source: Path,
                  view: dict) -> tuple[dict, list, list, list]:
    """Build candidate company links, retaining original endpoints and provenance.

    Display projection is not causal inference: paths may mix hypotheses and
    observations, and evidence never automatically promotes a candidate to fact.
    """
    validate_company(profile, view, nodes, relations, source)
    root_id, sector_label = view["root_id"], view["root"]
    roles = {r["concept_id"]: r["scope_note"] for r in profile["business_roles"]}
    own = list(roles)
    rules = profile["projection"]

    def end(node_id: str) -> str:
        return CENTRE if node_id in own or node_id == root_id else node_id

    moved = []
    for relation in relations:
        if relation["id"] in rules["excluded_edge_ids"]:
            continue
        start, stop = end(relation["from"]), end(relation["to"])
        if start != stop:
            scope = "sector" if relation["to"] == root_id else "concept"
            basis = list(dict.fromkeys(n for n in (relation["from"], relation["to"]) if n in roles or n == root_id))
            notes = [roles[n] for n in basis if n in roles]
            if root_id in basis:
                notes.append("Sector relevance only; verify company-specific applicability and materiality.")
            if relation["id"] in rules["edge_conditions"]:
                notes.append(rules["edge_conditions"][relation["id"]])
            moved.append({**relation,
                          "id": f"projection:{profile_id(profile)}:{relation['id']}" if basis else relation["id"],
                          "from": start, "to": stop, "scope": scope,
                          "status": "candidate" if basis else "research_lead",
                          "projection_basis": basis, "applicability_notes": notes,
                          "source_branch": nodes[relation["from"]]["branch"]})
    for exposure in profile.get("exposures", []):
        concept = exposure.get("concept")
        moved.append({**exposure, "from": concept, "to": CENTRE, "type": exposure.get("type", ""), "scope": "company",
                      "status": "research_lead", "origin_edge_id": exposure["id"],
                      "origin_from": concept, "origin_to": profile_id(profile),
                      "origin_content_revision": profile["content_revision"], "origin_content_sha256": content_hash(profile),
                      "projection_basis": [], "applicability_notes": [],
                      "mechanism": exposure.get("mechanism_hypothesis", ""), "source_branch": nodes[concept]["branch"]})

    for relation in moved:
        relation["evidence"] = [record for record in profile["evidence"]
                                if record["lead_id"] == relation["origin_edge_id"]]

    inbound = list(dict.fromkeys(r["from"] for r in moved if r["to"] == CENTRE))
    outbound = list(dict.fromkeys(r["to"] for r in moved if r["from"] == CENTRE))
    direct = list(dict.fromkeys(inbound + outbound))
    second = list(dict.fromkeys(r["from"] for r in moved if r["to"] in inbound and r["from"] != CENTRE and r["from"] not in direct))
    placed = {CENTRE, *direct, *second}
    kept = [r for r in moved if CENTRE in (r["from"], r["to"]) or (r["to"] in inbound and r["from"] in placed)]

    labels = ", ".join(pretty(nodes[c]["name"]) for c in own) or "ETF"
    picked = {
        CENTRE: {
            "branch": nodes[own[0]]["branch"] if own else "",
            "name": profile.get("symbol", source.parent.name),
            "role": profile.get("name", ""),
            "tag": f"Business roles: {labels} · sector: {sector_label}" if own else f"Sector fund · sector: {sector_label}",
            "node_kind": "company" if own else "fund",
            "kind": labels,
            "sector": sector_label,
            "watch": profile.get("observables_to_research", []),
            **({"leads": profile["source_leads"]} if profile.get("source_leads") else {}),
        }
    }
    picked.update({i: nodes[i] for i in direct + second})
    return picked, kept, direct, second


def load_institutions(path: Path, sector_nodes: dict[str, dict], company_ids: set[str]) -> dict:
    """Read and validate the institution registry. Returns institutions keyed by id.

    ``sector_nodes`` maps a sector_id to that sector's nodes, and ``company_ids`` holds the
    known company ids, so every concept and company link can be checked.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    require(data.get("schema_version") == SCHEMA_VERSION, path, "expected schema_version 2.0.0")
    require(data.get("kind") == "institution_registry", path, "expected institution_registry")
    require(bool(data.get("content_revision")), path, "missing content_revision")
    institutions = indexed(data["institutions"], path, "institution")
    for key, item in institutions.items():
        kind = item.get("institution_kind")
        require(kind in INSTITUTION_KINDS, path, f"invalid institution_kind on {key}")
        require(isinstance(item.get(kind), dict), path, f"{key} needs a {kind!r} section")
        require(all(item.get(k) for k in ("label", "jurisdiction", "description_lead", "source_leads")),
                path, f"{key} needs label, jurisdiction, description_lead and source_leads")
        for publication in item["publications"]:
            require(all(publication.get(k) for k in ("name", "cadence", "typical_lag")), path,
                    f"a publication of {key} needs name, cadence and typical_lag")
            require(publication.get("availability") in AVAILABILITY, path, f"invalid availability on a publication of {key}")
        for link in item["concept_links"]:
            require(link.get("concept_id") in sector_nodes.get(link.get("sector_id"), {}), path,
                    f"{key} links to unknown concept {link.get('concept_id')!r} in sector {link.get('sector_id')!r}")
            require(link.get("link_kind") in CONCEPT_LINK_KINDS, path, f"invalid concept link_kind on {key}")
        for link in item["company_links"]:
            require(link.get("company_id") in company_ids, path, f"{key} links to unknown company {link.get('company_id')!r}")
    return institutions


def find_maps(root: Path) -> tuple[list[Path], Path | None]:
    """Return the sector map files and the institution registry found one folder below ``root``."""
    sectors, registry = [], None
    for path in sorted(root.glob("*/*.json")):
        kind = json.loads(path.read_text(encoding="utf-8")).get("kind")
        if kind == "industry_research_map":
            sectors.append(path)
        elif kind == "institution_registry":
            registry = path
    return sectors, registry


def load_registry(root: Path) -> tuple[dict, dict, Path | None]:
    """Load the registry under ``root``, checking its links against every sector there.

    Returns (institutions, labels, registry path). ``labels`` maps each concept and
    company id to a display name. Without a registry the first two are empty.
    """
    sectors, registry = find_maps(root)
    if registry is None:
        return {}, {}, None
    sector_nodes, labels, companies = {}, {}, set()
    for sector in sectors:
        view, nodes, _ = load(sector)
        sector_nodes[view["sector_id"]] = nodes
        labels.update({i: pretty(n["name"]) for i, n in nodes.items()})
        for profile_path in profile_paths(sector.parent):
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            if profile.get("kind") == "company_research_profile":
                companies.add(profile_id(profile))
            labels[profile_id(profile)] = profile.get("symbol", profile_id(profile))
    return load_institutions(registry, sector_nodes, companies), labels, registry
