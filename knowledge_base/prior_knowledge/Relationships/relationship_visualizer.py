"""Draw a sector folder as one interactive HTML page with a tab per stock.

    python relationship_visualizer.py semiconductor     # a sector folder
    python relationship_visualizer.py                   # every sector folder beside this script

A sector folder holds <sector>/<sector>.json and one <sector>/<SYMBOL>/company.json per
stock. The page is written to <sector>/<sector>.html and has:

  Sector tab   a radial tree: the sector in the centre, then its branches, then each
               branch's concepts, then any named leads. Relations are hidden until you
               click a branch or concept, or tick "Show all relations".

  Stock tabs   one per company.json. The stock is in the centre. It takes relations
               from three places:
                 - the sector concepts listed in its "instance_of" (solid arrows);
                 - the sector as a whole: edges in the sector map whose "to" is the
                   sector node "sector:<sector_id>" reach every stock (dashed arrows);
                 - its own "exposures" list in company.json (solid arrows).
               The inner ring holds every concept with a direct relation to the stock;
               the outer ring holds the concepts one step further upstream. So every
               arrow is a path of at most two hops that ends at the stock.

Click anything to read its details in the side panel. Needs only the standard library.
"""

import html
import json
import math
import re
import sys
from pathlib import Path

PALETTE = ["#c0392b", "#2166ac", "#3a8a2e", "#c9730a", "#7b3fa8", "#16877d", "#8a6d1c", "#b0457a"]

# Sector tab.
R_BRANCH = 178  # ring 1
R_ENTITY = 330  # ring 2: dot, with the label written outward from it
R_LEAD = 522  # ring 3: bracket, with lead names written outward from it
HALF = 650  # half the canvas size
BRANCH_GAP = 0.6  # empty space between branches, in entity slots

# Stock tabs.
CENTRE = "@company"  # node id of the stock itself
R_CENTRE = 80


# ---------------------------------------------------------------- loading


def load(path: Path) -> tuple[dict, dict, list]:
    """Return a sector as (view, nodes, relations).

    ``view`` has root, reviewed_on, notice, Level_1 (branch order) and one
    {"question": ...} per branch. ``nodes`` maps a node id to its branch, name, role,
    watch list and optional leads. ``relations`` hold from, to, type and mechanism.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind") == "industry_research_map":
        return load_research_map(path, data)
    nodes, relations = {}, []
    for branch in data["Level_1"]:
        for name, info in data[branch].get("Entities", {}).items():
            nodes[f"{branch}.{name}"] = {"branch": branch, "name": name, **info}
    for branch in data["Level_1"]:
        for relation in data[branch].get("Relations", []):
            ends = {}
            for side in ("from", "to"):
                end = relation[side]
                ends[side] = end if "." in end else f"{branch}.{end}"
                if ends[side] not in nodes:
                    raise SystemExit(f"{path}: {branch} relation points at unknown entity {end!r}")
            relations.append({**relation, **ends})
    return data, nodes, relations


def load_research_map(path: Path, data: dict) -> tuple[dict, dict, list]:
    """Read the "industry_research_map" schema: flat concepts and concept_edges with ids."""
    level_of = {b: level["label"] for level in data.get("levels", {}).values() for b in level["branch_ids"]}
    usage = data.get("usage", {})
    view = {
        "root": data.get("label", data.get("sector_id", path.stem)),
        "reviewed_on": data.get("compiled_on"),
        "notice": " ".join(filter(None, [usage.get("describe"), f"Status: {pretty(usage['status'])}." if usage.get("status") else None])),
        "Level_1": [b["id"] for b in data["branches"]],
        # Edges may point at the sector itself; such an edge reaches every stock in it.
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
            if concept_id not in concepts:
                raise SystemExit(f"{path}: branch {branch['id']} lists unknown concept {concept_id!r}")
            concept = concepts[concept_id]
            nodes[concept_id] = {
                "branch": branch["id"],
                "name": concept.get("label", concept_id),
                "role": concept.get("description_lead", ""),
                "watch": concept.get("observables_to_research", []),
                **({"leads": concept["source_leads"]} if concept.get("source_leads") else {}),
            }
    for edge in data.get("concept_edges", []):
        if edge["from"] not in nodes:
            raise SystemExit(f"{path}: edge {edge.get('id')} starts at unknown concept {edge['from']!r}")
        if edge["to"] not in nodes and edge["to"] != view["root_id"]:
            raise SystemExit(f"{path}: edge {edge.get('id')} points at unknown concept {edge['to']!r}")
        relations.append(
            {"from": edge["from"], "to": edge["to"], "type": edge.get("type", ""), "mechanism": edge.get("mechanism_hypothesis", "")}
        )
    return view, nodes, relations


# ---------------------------------------------------------------- drawing helpers


def pretty(name: str) -> str:
    return name.replace("_", " ")


def polar(radius: float, angle: float) -> tuple[float, float]:
    return radius * math.cos(angle), radius * math.sin(angle)


def radial_text(text: str, radius: float, angle: float, css: str) -> str:
    """Text written outward along a radius, flipped on the left half so it never reads upside down."""
    degrees = math.degrees(angle)
    if math.cos(angle) >= 0:
        transform, anchor = f"rotate({degrees:.2f}) translate({radius},0)", "start"
    else:
        transform, anchor = f"rotate({degrees:.2f}) translate({radius},0) rotate(180)", "end"
    return f'<text class="{css}" transform="{transform}" text-anchor="{anchor}" dy="0.35em">{html.escape(text)}</text>'


def arc(radius: float, start: float, end: float) -> str:
    (x1, y1), (x2, y2) = polar(radius, start), polar(radius, end)
    return f"M{x1:.1f},{y1:.1f} A{radius},{radius} 0 {int(end - start > math.pi)} 1 {x2:.1f},{y2:.1f}"


def svg_open(key: str, half: float, branches: list, colour: dict, css: str = "") -> list[str]:
    """Opening tag plus one arrowhead per branch colour. Marker ids are unique per tab."""
    return [
        f'<svg class="{css}" xmlns="http://www.w3.org/2000/svg" viewBox="{-half} {-half} {2 * half} {2 * half}">',
        "<defs>",
        *(
            f'<marker id="arrow-{key}-{i}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"'
            f' orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{colour[b]}"/></marker>'
            for i, b in enumerate(branches)
        ),
        "</defs>",
    ]


def relation_svg(index: int, key: str, branches: list, colour: dict, source_branch: str, css: str,
                 start: tuple, control: tuple, end: tuple, label: str) -> str:
    (x1, y1), (mx, my), (x2, y2) = start, control, end
    # Label a third of the way along, so arrows converging on one target keep their labels apart.
    lx, ly = 0.4225 * x1 + 0.455 * mx + 0.1225 * x2, 0.4225 * y1 + 0.455 * my + 0.1225 * y2
    path = f"M{x1:.1f},{y1:.1f} Q{mx:.1f},{my:.1f} {x2:.1f},{y2:.1f}"
    return (
        f'<g class="rel {css}" data-e="{index}"><path class="hit" d="{path}"/>'
        f'<path class="line" stroke="{colour[source_branch]}" marker-end="url(#arrow-{key}-{branches.index(source_branch)})" d="{path}"/>'
        f'<text x="{lx:.1f}" y="{ly:.1f}" fill="{colour[source_branch]}" text-anchor="middle" dy="0.35em">'
        f"{html.escape(pretty(label))}</text></g>"
    )


# ---------------------------------------------------------------- sector tab


def sector_svg(key: str, view: dict, nodes: dict, relations: list, colour: dict) -> str:
    branches = view["Level_1"]
    root_id = view.get("root_id")
    root = pretty(str(view.get("root", "")))
    root_r = max(52, 5.8 * len(root) + 14)
    # A concept with many leads gets a wider slot so its ring-3 names have room.
    weight = {i: max(1.0, 0.5 * len(n.get("leads", []))) for i, n in nodes.items()}
    unit = 2 * math.pi / (sum(weight.values()) + BRANCH_GAP * len(branches))

    angle, span, branch_angle = {}, {}, {}
    cursor = -math.pi / 2  # start at the top, go clockwise
    wedges = []
    for branch in branches:
        start = cursor + BRANCH_GAP * unit / 2
        cursor = start
        for node_id, node in nodes.items():
            if node["branch"] == branch:
                span[node_id] = (cursor, cursor + weight[node_id] * unit)
                angle[node_id] = cursor + weight[node_id] * unit / 2
                cursor += weight[node_id] * unit
        branch_angle[branch] = (start + cursor) / 2
        outer, inner = HALF - 6, R_BRANCH + 34
        (ax, ay), (bx, by), (cx, cy), (dx, dy) = polar(outer, start), polar(outer, cursor), polar(inner, cursor), polar(inner, start)
        large = int(cursor - start > math.pi)
        wedges.append(
            f'<path class="wedge" fill="{colour[branch]}" d="M{ax:.1f},{ay:.1f} A{outer},{outer} 0 {large} 1 {bx:.1f},{by:.1f}'
            f' L{cx:.1f},{cy:.1f} A{inner},{inner} 0 {large} 0 {dx:.1f},{dy:.1f} Z"/>'
        )
        cursor += BRANCH_GAP * unit / 2

    out = [*svg_open(key, HALF, branches, colour, "norel"), *wedges]

    # Tree links: root -> branch -> concept.
    for branch in branches:
        x, y = polar(R_BRANCH, branch_angle[branch])
        out.append(f'<line class="tree" x1="0" y1="0" x2="{x:.1f}" y2="{y:.1f}" stroke="{colour[branch]}"/>')
        for node_id, node in nodes.items():
            if node["branch"] == branch:
                ex, ey = polar(R_ENTITY, angle[node_id])
                out.append(f'<path class="tree" stroke="{colour[branch]}" d="M{x:.1f},{y:.1f} Q{x * 1.5:.1f},{y * 1.5:.1f} {ex:.1f},{ey:.1f}"/>')

    # Relations: curves bowed towards the centre, more strongly the further apart the ends are.
    pairs = {(r["from"], r["to"]) for r in relations}
    for index, relation in enumerate(relations):
        if relation["to"] == root_id:  # sector-wide: straight in to the centre
            a = angle[relation["from"]]
            start, stop = polar(R_ENTITY - 9, a), polar(root_r + 6, a)
            middle = ((start[0] + stop[0]) / 2, (start[1] + stop[1]) / 2)
            out.append(relation_svg(index, key, branches, colour, nodes[relation["from"]]["branch"], "cross",
                                    start, middle, stop, relation.get("type", "")))
            continue
        a, b = angle[relation["from"]], angle[relation["to"]]
        apart = abs((a - b + math.pi) % (2 * math.pi) - math.pi)
        pull = max(0.2, 0.92 - 0.9 * apart / math.pi)
        if (relation["to"], relation["from"]) in pairs and relation["from"] > relation["to"]:
            pull *= 0.72  # keep a two-way pair from lying on top of each other
        (x1, y1), (x2, y2) = polar(R_ENTITY - 9, a), polar(R_ENTITY - 11, b)
        source = nodes[relation["from"]]["branch"]
        crosses = source != nodes[relation["to"]]["branch"]
        out.append(relation_svg(index, key, branches, colour, source, "cross" if crosses else "",
                                (x1, y1), (pull * (x1 + x2) / 2, pull * (y1 + y2) / 2), (x2, y2), relation.get("type", "")))

    # Ring 2 concepts, each with its ring 3 leads.
    for node_id, node in nodes.items():
        x, y = polar(R_ENTITY, angle[node_id])
        parts = [
            f'<g class="node" data-n="{html.escape(node_id)}">',
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{colour[node["branch"]]}"/>',
            radial_text(pretty(node["name"]), R_ENTITY + 13, angle[node_id], "entity"),
        ]
        leads = node.get("leads", [])
        if leads:
            start, end = span[node_id]
            pad = (end - start) / len(leads) / 2
            parts.append(f'<path class="bracket" stroke="{colour[node["branch"]]}" d="{arc(R_LEAD - 8, start + pad * 0.5, end - pad * 0.5)}"/>')
            parts.extend(radial_text(lead, R_LEAD, start + (2 * j + 1) * pad, "lead") for j, lead in enumerate(leads))
        out.append("".join(parts) + "</g>")

    # Ring 1 branches and the root, drawn last so they sit on top of the curves.
    for branch in branches:
        x, y = polar(R_BRANCH, branch_angle[branch])
        label = pretty(branch)
        width = 9.2 * len(label) + 26
        out.append(
            f'<g class="branch" data-b="{html.escape(branch)}">'
            f'<rect x="{x - width / 2:.1f}" y="{y - 17:.1f}" width="{width:.1f}" height="34" rx="17" fill="{colour[branch]}"/>'
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" dy="0.35em">{html.escape(label)}</text></g>'
        )
    out.append(
        f'<g class="root node" data-n="{html.escape(root_id or "")}"><circle r="{root_r:.0f}"/>'
        f'<text text-anchor="middle" dy="0.35em">{html.escape(root)}</text></g>'
    )
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------- stock tabs


def company_graph(profile: dict, nodes: dict, relations: list, source: Path,
                  root_id: str | None, sector_label: str) -> tuple[dict, list, list, list]:
    """Cut the sector map down to the paths of at most two hops that touch one stock.

    Returns (nodes, relations, direct ids, second-hop ids). The stock replaces the
    concepts it is an instance of, and the sector node, and gets the node id CENTRE.
    Each relation records its "scope": "concept" (through instance_of), "sector"
    (sector-wide) or "company" (from the profile's own exposures).
    """
    own = list(profile.get("instance_of", []))
    for concept in own:
        if concept not in nodes:
            raise SystemExit(f"{source}: instance_of names unknown concept {concept!r}")
    if not own:
        raise SystemExit(f"{source}: instance_of is empty, so the stock has no place in the sector map")

    def end(node_id: str) -> str:
        return CENTRE if node_id in own or node_id == root_id else node_id

    moved = []
    for relation in relations:
        start, stop = end(relation["from"]), end(relation["to"])
        if start != stop:
            scope = "sector" if relation["to"] == root_id else "concept"
            moved.append({**relation, "from": start, "to": stop, "scope": scope,
                          "source_branch": nodes[relation["from"]]["branch"]})
    for exposure in profile.get("exposures", []):
        concept = exposure.get("concept")
        if concept not in nodes:
            raise SystemExit(f"{source}: exposure names unknown concept {concept!r}")
        if concept in own:
            raise SystemExit(f"{source}: exposure to {concept!r}, which the stock is already an instance of")
        moved.append({"from": concept, "to": CENTRE, "type": exposure.get("type", ""), "scope": "company",
                      "mechanism": exposure.get("mechanism_hypothesis", ""), "source_branch": nodes[concept]["branch"]})

    inbound = list(dict.fromkeys(r["from"] for r in moved if r["to"] == CENTRE))
    outbound = list(dict.fromkeys(r["to"] for r in moved if r["from"] == CENTRE))
    direct = list(dict.fromkeys(inbound + outbound))
    second = list(dict.fromkeys(r["from"] for r in moved if r["to"] in inbound and r["from"] != CENTRE and r["from"] not in direct))
    placed = {CENTRE, *direct, *second}
    kept = [r for r in moved if CENTRE in (r["from"], r["to"]) or (r["to"] in inbound and r["from"] in placed)]

    labels = ", ".join(pretty(nodes[c]["name"]) for c in own)
    picked = {
        CENTRE: {
            "branch": nodes[own[0]]["branch"],
            "name": profile.get("symbol", source.parent.name),
            "role": profile.get("name", ""),
            "tag": f"Instance of {labels} · member of {sector_label}",
            "kind": labels,
            "sector": sector_label,
            "watch": profile.get("observables_to_research", []),
            **({"leads": profile["source_leads"]} if profile.get("source_leads") else {}),
        }
    }
    picked.update({i: nodes[i] for i in direct + second})
    return picked, kept, direct, second


def spread(wanted: dict[str, float], gap: float) -> dict[str, float]:
    """Keep each angle near where it wants to be while holding neighbours at least ``gap`` apart."""
    order = sorted(wanted, key=wanted.get)
    angles = [wanted[i] for i in order]
    count = len(angles)
    if count > 1:
        gap = min(gap, 2 * math.pi / count)
        for _ in range(300):
            for i in range(count):
                j = (i + 1) % count
                # Signed difference, so two nodes that want the same angle are pushed apart
                # in order instead of being read as a full turn away from each other.
                short = gap - (angles[j] - angles[i] + (2 * math.pi if j == 0 else 0))
                if short > 1e-6:
                    angles[i] -= short / 2
                    angles[j] += short / 2
    return dict(zip(order, angles))


def company_svg(key: str, profile: dict, branches: list, colour: dict, nodes: dict, relations: list,
                direct: list, second: list) -> str:
    r_direct = max(270, 30 * len(direct))
    r_second = r_direct + 230
    half = r_second + 200

    direct = sorted(direct, key=lambda i: branches.index(nodes[i]["branch"]))
    angle = {i: -math.pi / 2 + 2 * math.pi * k / max(1, len(direct)) for k, i in enumerate(direct)}
    wanted = {}
    for node_id in second:
        targets = [angle[r["to"]] for r in relations if r["from"] == node_id and r["to"] in angle]
        wanted[node_id] = math.atan2(sum(map(math.sin, targets)), sum(map(math.cos, targets))) % (2 * math.pi)
    second_angle = spread(wanted, 0.17)

    centre_xy = {CENTRE: (0.0, 0.0)}
    centre_xy.update({i: polar(r_direct, a) for i, a in angle.items()})
    centre_xy.update({i: polar(r_second, a) for i, a in second_angle.items()})
    size = {i: (7.8 * len(pretty(nodes[i]["name"])) + 26, 32) for i in direct}

    def anchor(node_id: str, towards: tuple[float, float]) -> tuple[float, float]:
        """Where an arrow meets a node's edge on the side facing ``towards``."""
        cx, cy = centre_xy[node_id]
        dx, dy = towards[0] - cx, towards[1] - cy
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        if node_id == CENTRE:
            reach = R_CENTRE + 6
        elif node_id in size:
            width, height = size[node_id]
            reach = min(width / 2 / abs(ux) if ux else math.inf, height / 2 / abs(uy) if uy else math.inf) + 6
        else:
            reach = 12
        return cx + ux * reach, cy + uy * reach

    out = svg_open(key, half, branches, colour)
    out.append(f'<circle class="guide" r="{r_direct}"/><circle class="guide" r="{r_second}"/>')

    between: dict[frozenset, list[int]] = {}
    for index, relation in enumerate(relations):
        between.setdefault(frozenset((relation["from"], relation["to"])), []).append(index)
    for index, relation in enumerate(relations):
        a, b = relation["from"], relation["to"]
        start, stop = anchor(a, centre_xy[b]), anchor(b, centre_xy[a])
        mx, my = (start[0] + stop[0]) / 2, (start[1] + stop[1]) / 2
        if a in angle and b in angle:
            # Two inner-ring nodes: curve through the band between the centre and the ring,
            # so the arrow never hides behind the stock, even when the two sit opposite.
            sx, sy = math.cos(angle[a]) + math.cos(angle[b]), math.sin(angle[a]) + math.sin(angle[b])
            middle = math.atan2(sy, sx) if math.hypot(sx, sy) > 0.05 else angle[a] + math.pi / 2
            apart = abs((angle[a] - angle[b] + math.pi) % (2 * math.pi) - math.pi)
            mx, my = polar(r_direct * (1.2 - math.cos(apart / 2)), middle)
        siblings = between[frozenset((a, b))]
        if len(siblings) > 1:  # several arrows between the same two nodes: fan them out
            bow = (siblings.index(index) - (len(siblings) - 1) / 2) * 56 * (1 if a < b else -1)
            dx, dy = stop[0] - start[0], stop[1] - start[1]
            length = math.hypot(dx, dy) or 1.0
            mx, my = mx - dy / length * bow, my + dx / length * bow
        # Arrows between two inner-ring nodes are side links; they stay hidden until one end is clicked.
        css = "primary" if CENTRE in (a, b) else "side" if a in angle and b in angle else ""
        css += " cross" if relation.get("scope") == "sector" else ""
        out.append(relation_svg(index, key, branches, colour, relation["source_branch"], css, start, (mx, my), stop, relation.get("type", "")))

    for node_id in second:
        x, y = centre_xy[node_id]
        out.append(
            f'<g class="node" data-n="{html.escape(node_id)}"><circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{colour[nodes[node_id]["branch"]]}"/>'
            + radial_text(pretty(nodes[node_id]["name"]), r_second + 13, second_angle[node_id], "entity") + "</g>"
        )
    for node_id in direct:
        (x, y), (width, height) = centre_xy[node_id], size[node_id]
        out.append(
            f'<g class="node pill" data-n="{html.escape(node_id)}">'
            f'<rect x="{x - width / 2:.1f}" y="{y - height / 2:.1f}" width="{width:.1f}" height="{height}" rx="{height / 2}" fill="{colour[nodes[node_id]["branch"]]}"/>'
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" dy="0.35em">{html.escape(pretty(nodes[node_id]["name"]))}</text></g>'
        )
    out.append(
        f'<g class="node centre" data-n="{CENTRE}"><circle r="{R_CENTRE}"/>'
f'<text class="symbol" text-anchor="middle" dy="-0.45em">{html.escape(nodes[CENTRE]["name"])}</text>'
        f'<text class="kind" text-anchor="middle" dy="1.1em">{html.escape(nodes[CENTRE]["kind"])}</text>'
        f'<text class="kind" text-anchor="middle" dy="2.4em">in {html.escape(nodes[CENTRE]["sector"])}</text></g>'
    )

    used = [b for b in branches if any(n["branch"] == b for i, n in nodes.items() if i != CENTRE)]
    for row, branch in enumerate(used):
        y = -half + 26 + 24 * row
        out.append(
            f'<rect x="{-half + 16}" y="{y - 8}" width="16" height="16" rx="4" fill="{colour[branch]}"/>'
            f'<text class="legend" x="{-half + 40}" y="{y}" dy="0.35em">{html.escape(pretty(branch))}</text>'
        )
    out.append("</svg>")
    return "\n".join(out)


def company_overview(profile: dict, nodes: dict, relations: list) -> dict:
    listing = [f"{pretty(k)}: {v}" for k, v in profile.get("listing", {}).items()]
    sourced = [json.dumps(r, ensure_ascii=False) for r in profile.get("relationships", [])]
    touching = [r for r in relations if CENTRE in (r["from"], r["to"])]
    count = {scope: sum(1 for r in touching if r["scope"] == scope) for scope in ("concept", "sector", "company")}
    return {
        "title": f"{nodes[CENTRE]['name']} — {profile.get('name', '')}".rstrip(" —"),
        "tag": nodes[CENTRE]["tag"],
        "notice": (
            "Inner ring: concepts with a direct relation to this stock. Outer ring: concepts one step further "
            "upstream. Solid arrows come through the concept this stock is an instance of, or from its own "
            "exposures. Dashed arrows are sector-wide and reach every stock in the sector. Links between two "
            "inner-ring concepts appear when you click one of them. All are leads to research, not sourced "
            "company relationships."
        ),
        "sections": [
            {"h": "Direct relations", "items": [{"node": CENTRE, "text": (
                f"{count['concept']} as {nodes[CENTRE]['kind']}, {count['sector']} sector-wide, "
                f"{count['company']} company-specific; {len(relations) - len(touching)} second-hop"
            )}]},
            {"h": "Listing", "items": listing + ([f"fiscal calendar: {profile['fiscal_calendar']}"] if profile.get("fiscal_calendar") else [])},
            {"h": "What to watch", "items": list(profile.get("observables_to_research", []))},
            {"h": "Sourced company relationships", "items": sourced or ["None recorded yet."]},
        ],
    }


# ---------------------------------------------------------------- page


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ relationship map</title>
<style>
:root { --bg:#fafafa; --fg:#1f2328; --muted:#5c6570; --panel:#ffffff; --line:#d5d9de; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#15181c; --fg:#e6e8eb; --muted:#a0a8b2; --panel:#1e2227; --line:#3a4048; }
}
* { box-sizing:border-box; }
body { margin:0; font:15px/1.5 system-ui, sans-serif; background:var(--bg); color:var(--fg);
       display:grid; grid-template-columns:minmax(0,1fr) 360px; grid-template-rows:auto minmax(0,1fr); height:100vh; }
header { grid-column:1/-1; padding:8px 16px; border-bottom:1px solid var(--line); display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
header h1 { font-size:18px; margin:0; }
header label { font-size:13px; }
header button { font:inherit; padding:2px 10px; border:1px solid var(--line); border-radius:6px; background:var(--panel); color:var(--fg); cursor:pointer; }
nav { display:flex; gap:4px; flex:1; flex-wrap:wrap; }
nav button { padding:4px 14px; border-radius:999px; }
nav button.on { background:var(--fg); color:var(--bg); border-color:var(--fg); font-weight:600; }
#map { overflow:auto; padding:8px; }
#map svg { display:block; margin:0 auto; font-family:system-ui, sans-serif; }
svg .wedge { opacity:.07; }
svg .guide { fill:none; stroke:var(--line); stroke-dasharray:3 6; }
svg .tree { fill:none; stroke-width:1.2; opacity:.4; }
svg .entity { font-size:15px; fill:var(--fg); }
svg .lead { font-size:12px; fill:var(--muted); }
svg .legend { font-size:14px; fill:var(--fg); }
svg .bracket { fill:none; stroke-width:1.5; opacity:.6; }
svg .branch text, svg .pill text { font-weight:600; fill:#fff; }
svg .branch text { font-size:16px; }
svg .pill text { font-size:14px; }
svg .root circle, svg .centre circle { fill:var(--fg); }
svg .root text { font-size:19px; font-weight:700; fill:var(--bg); }
svg .centre .symbol { font-size:26px; font-weight:700; fill:var(--bg); }
svg .centre .kind { font-size:13px; fill:var(--bg); }
svg .rel .line { fill:none; stroke-width:1.6; opacity:.55; }
svg .rel.primary .line { stroke-width:2.2; opacity:.9; }
svg .rel.cross .line { stroke-dasharray:6 4; }
svg .rel .hit { fill:none; stroke:transparent; stroke-width:12; }
svg .rel text { display:none; font-size:13px; font-weight:600; paint-order:stroke; stroke:var(--bg); stroke-width:4px; }
svg .rel.primary text { display:block; }
svg.norel .rel:not(.lit), svg .rel.side:not(.lit) { display:none; }
svg .node, svg .branch, svg .rel { cursor:pointer; transition:opacity .15s; }
svg.focus .node:not(.lit), svg.focus .branch:not(.lit), svg.focus .rel:not(.lit), svg.focus .tree { opacity:.13; }
svg .rel.lit .line { opacity:1; stroke-width:2.6; }
svg .rel.lit text { display:block; }
svg .node.picked circle, svg .node.picked rect, svg .branch.picked rect { stroke:var(--fg); stroke-width:3; }
svg .centre.picked circle, svg .root.picked circle { stroke:var(--muted); }
svg .node.picked .entity { font-weight:700; }
aside { border-left:1px solid var(--line); background:var(--panel); padding:16px; overflow:auto; }
aside h2 { font-size:17px; margin:0 0 2px; }
aside .tag { color:var(--muted); font-size:13px; margin-bottom:10px; }
aside h3 { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); margin:16px 0 4px; }
aside ul { margin:0; padding-left:18px; }
aside li { margin-bottom:6px; overflow-wrap:anywhere; }
aside a { color:inherit; text-decoration:underline dotted; cursor:pointer; }
aside .notice { color:var(--muted); font-size:13px; }
@media (max-width: 800px) {
  body { grid-template-columns:1fr; grid-template-rows:auto 70vh auto; height:auto; }
  aside { border-left:0; border-top:1px solid var(--line); }
}
</style></head><body>
<header><h1>__TITLE__</h1><nav id="tabs"></nav>
<label id="relsBox"><input type="checkbox" id="rels"> Show all relations</label>
<button id="out">−</button><button id="fit">Fit</button><button id="in">+</button></header>
<div id="map">__SECTIONS__</div>
<aside id="panel"></aside>
<script>
const PAGE = __DATA__;
const map = document.getElementById('map'), panel = document.getElementById('panel'), tabs = document.getElementById('tabs');
let V, svg, key, zoom = 1;
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const nice = s => String(s).replace(/_/g, ' ');
const find = (attr, value) => svg.querySelector(`[data-${attr}="${CSS.escape(value)}"]`);
const nodeLink = id => `<a data-node="${esc(id)}">${esc(nice(V.nodes[id].name))}</a>`;
const list = items => items.length ? '<ul>' + items.map(i => `<li>${i}</li>`).join('') + '</ul>' : '<p class="notice">None.</p>';
const scope = r => r.scope === 'sector' ? ' <span class="notice">[sector-wide]</span>' : r.scope === 'company' ? ' <span class="notice">[company-specific]</span>' : '';
const relLine = (r, other) => `<a data-edge="${r.i}">${esc(nice(r.type || 'relation'))}</a> ${other}${scope(r)} <br><span class="notice">${esc(r.mechanism || '')}</span>`;
const item = i => typeof i === 'string' ? esc(i)
  : i.branch ? `<a data-branch="${esc(i.branch)}"><b>${esc(nice(i.branch))}</b></a><br>${esc(i.text)}`
  : i.view ? `<a data-view="${esc(i.view)}"><b>${esc(i.view)}</b></a> ${esc(i.text)}`
  : `<a data-node="${esc(i.node)}">${esc(i.text)}</a>`;

const setZoom = z => {
  zoom = Math.max(0.4, Math.min(5, z));
  const side = Math.min(map.clientWidth, map.clientHeight) - 16;
  svg.style.width = svg.style.height = side * zoom + 'px';
};
addEventListener('resize', () => setZoom(zoom));
document.getElementById('in').onclick = () => setZoom(zoom * 1.25);
document.getElementById('out').onclick = () => setZoom(zoom / 1.25);
document.getElementById('fit').onclick = () => setZoom(1);
document.getElementById('rels').onchange = e => svg.classList.toggle('norel', !e.target.checked);

function openView(name) {
  key = PAGE.views[name] ? name : PAGE.order[0];
  V = PAGE.views[key];
  map.querySelectorAll('section').forEach(s => s.hidden = s.dataset.view !== key);
  svg = map.querySelector(`section[data-view="${CSS.escape(key)}"] svg`);
  tabs.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.tab === key));
  document.getElementById('relsBox').hidden = key !== PAGE.order[0];
  document.getElementById('rels').checked = !svg.classList.contains('norel');
  history.replaceState(null, '', '#' + encodeURIComponent(key));
  setZoom(1); showOverview();
}
function clear() { svg.querySelectorAll('.lit,.picked').forEach(e => e.classList.remove('lit', 'picked')); }
function light(nodeIds, edgeIds, branchNames, picked) {
  clear(); svg.classList.add('focus');
  nodeIds.forEach(id => find('n', id)?.classList.add('lit'));
  edgeIds.forEach(i => find('e', String(i))?.classList.add('lit'));
  branchNames.forEach(b => find('b', b)?.classList.add('lit'));
  picked?.classList.add('picked');
}
function showNode(id) {
  const n = V.nodes[id];
  const outgoing = V.relations.filter(r => r.from === id), incoming = V.relations.filter(r => r.to === id);
  light([id, ...outgoing.map(r => r.to), ...incoming.map(r => r.from)], [...outgoing, ...incoming].map(r => r.i), [n.branch], find('n', id));
  const tag = n.tag ? esc(n.tag) : `<a data-branch="${esc(n.branch)}">${esc(nice(n.branch))}</a>`;
  panel.innerHTML = `<h2>${esc(nice(n.name))}</h2><div class="tag">${tag}</div>
    <p>${esc(n.role || '')}</p>
    ${n.leads ? '<h3>Leads</h3><p>' + n.leads.map(esc).join(', ') + '</p>' : ''}
    <h3>What to watch</h3>${list((n.watch || []).map(esc))}
    <h3>Affects</h3>${list(outgoing.map(r => relLine(r, '→ ' + nodeLink(r.to))))}
    <h3>Affected by</h3>${list(incoming.map(r => relLine(r, '← ' + nodeLink(r.from))))}`;
}
function showBranch(name) {
  const b = V.branches.find(x => x.name === name);
  const ids = Object.keys(V.nodes).filter(id => V.nodes[id].branch === name && !V.nodes[id].tag);
  const inside = V.relations.filter(r => ids.includes(r.from) || ids.includes(r.to));
  const crossing = inside.filter(r => !(ids.includes(r.from) && ids.includes(r.to)));
  light(ids, inside.map(r => r.i), [name], find('b', name));
  panel.innerHTML = `<h2>${esc(nice(name))}</h2><div class="tag">Branch of ${esc(PAGE.title)}</div>
    <p>${esc(b?.question || '')}</p>
    <h3>Shown here</h3>${list(ids.map(id => nodeLink(id) + ` <span class="notice">— ${esc(V.nodes[id].role || '')}</span>`))}
    <h3>Links to other branches</h3>${list(crossing.map(r => relLine(r, nodeLink(r.from) + ' → ' + nodeLink(r.to))))}`;
}
function showEdge(i) {
  const r = V.relations[i];
  light([r.from, r.to], [i], [], find('e', String(i)));
  panel.innerHTML = `<h2>${nodeLink(r.from)} → ${nodeLink(r.to)}</h2><div class="tag">${esc(nice(r.type || 'relation'))}${scope(r)}</div>
    <h3>Mechanism</h3><p>${esc(r.mechanism || 'Not recorded.')}</p>`;
}
function showOverview() {
  clear(); svg.classList.remove('focus');
  const o = V.overview;
  panel.innerHTML = `<h2>${esc(o.title)}</h2><div class="tag">${esc(o.tag || '')}</div><p class="notice">${esc(o.notice || '')}</p>` +
    o.sections.filter(s => s.items.length).map(s => `<h3>${esc(s.h)}</h3>${list(s.items.map(item))}`).join('');
}
tabs.innerHTML = PAGE.order.map((k, i) => `<button data-tab="${esc(k)}">${i ? esc(k) : 'Sector'}</button>`).join('');
document.addEventListener('click', e => {
  const tab = e.target.closest('[data-tab],[data-view]');
  if (tab && !tab.matches('section')) return openView(tab.dataset.tab || tab.dataset.view);
  const link = e.target.closest('[data-node],[data-edge],[data-branch]');
  if (link) return link.dataset.node ? showNode(link.dataset.node) : link.dataset.branch ? showBranch(link.dataset.branch) : showEdge(+link.dataset.edge);
  const g = e.target.closest('svg [data-n], svg [data-e], svg [data-b]');
  if (g) return g.dataset.n ? showNode(g.dataset.n) : g.dataset.b ? showBranch(g.dataset.b) : showEdge(+g.dataset.e);
  if (e.target.closest('#map')) showOverview();
});
openView(decodeURIComponent(location.hash.slice(1)));
</script></body></html>
"""


def render(sector_file: Path) -> Path:
    view, nodes, relations = load(sector_file)
    branches = view["Level_1"]
    colour = {b: PALETTE[i % len(PALETTE)] for i, b in enumerate(branches)}
    branch_info = [{"name": b, "question": view[b].get("question")} for b in branches]
    title = str(view.get("root", sector_file.stem))
    sector_key = sector_file.stem
    root_id = view.get("root_id")
    sector_nodes = dict(nodes)
    if root_id:
        sector_nodes[root_id] = {
            "branch": "", "name": title, "tag": "Sector", "watch": [],
            "role": "The sector as a whole. Arrows into it are sector-wide drivers that reach every stock in it.",
        }

    profiles = []
    for path in sorted(sector_file.parent.glob("*/company.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))
        profiles.append((profile.get("symbol", path.parent.name), profile, path))

    views = {
        sector_key: {
            "nodes": sector_nodes,
            "relations": [{**r, "i": i, **({"scope": "sector"} if r["to"] == root_id else {})} for i, r in enumerate(relations)],
            "branches": branch_info,
            "overview": {
                "title": title,
                "tag": f"Reviewed {view.get('reviewed_on') or 'n/a'}",
                "notice": view.get("notice") or "",
                "sections": [
                    {"h": "Branches and their questions", "items": [{"branch": b["name"], "text": b["question"] or ""} for b in branch_info]},
                    {"h": "Stocks", "items": [{"view": symbol, "text": profile.get("name", "")} for symbol, profile, _ in profiles]},
                ],
            },
        }
    }
    sections = [f'<section data-view="{html.escape(sector_key)}">{sector_svg("s", view, nodes, relations, colour)}</section>']
    for symbol, profile, path in profiles:
        picked, kept, direct, second = company_graph(profile, nodes, relations, path, root_id, title)
        marker_key = "c" + re.sub(r"\W", "", symbol)
        views[symbol] = {
            "nodes": picked,
            "relations": [{**r, "i": i} for i, r in enumerate(kept)],
            "branches": branch_info,
            "overview": company_overview(profile, picked, kept),
        }
        sections.append(
            f'<section data-view="{html.escape(symbol)}" hidden>'
            f"{company_svg(marker_key, profile, branches, colour, picked, kept, direct, second)}</section>"
        )

    payload = {"title": title, "order": list(views), "views": views}
    page = (
        PAGE.replace("__TITLE__", html.escape(title))
        .replace("__SECTIONS__", "\n".join(sections))
        .replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    )
    target = sector_file.with_suffix(".html")
    target.write_text(page, encoding="utf-8")
    return target


def sector_file(argument: str, here: Path) -> Path:
    """Accept a sector folder (by path or by name beside this script) or its JSON file."""
    path = Path(argument)
    if not path.exists() and (here / argument).exists():
        path = here / argument
    if path.is_dir():
        path = path / f"{path.resolve().name}.json"
    if not path.is_file():
        raise SystemExit(f"No sector map at {path}")
    return path


def main(arguments: list[str]) -> None:
    here = Path(__file__).parent
    paths = [sector_file(a, here) for a in arguments] or sorted(p for p in here.glob("*/*.json") if p.stem == p.parent.name)
    if not paths:
        raise SystemExit(f"No sector folders found under {here}")
    for path in paths:
        print(f"wrote {render(path)}")


if __name__ == "__main__":
    main(sys.argv[1:])
