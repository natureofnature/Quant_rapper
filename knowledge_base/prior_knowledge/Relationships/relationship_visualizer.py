"""Draw a sector folder as one interactive HTML page with a tab per stock.

    python relationship_visualizer.py semiconductor     # a sector folder
    python relationship_visualizer.py Institutions      # the institution registry
    python relationship_visualizer.py                   # every sector folder and the registry

Sector folders live beside this script. A sector folder holds <sector>/<sector>.json and one <sector>/<SYMBOL>/company.json per
stock. The page is written to <sector>/<sector>.html and has:

  Sector tab   a radial tree: the sector in the centre, then its branches, then each
               branch's concepts. Named source leads appear in the details panel. Relations are hidden until you
               click a branch or concept, or tick "Show all relations".

  Stock tabs   one per company.json or fund.json. A fund has no business role: it stands
               in for the sector node, so it takes the sector-wide links and its own
               exposures only. The stock is in the centre. It takes relations
               from three places:
                 - candidate links through its "business_roles" (solid arrows);
                 - the sector as a whole: edges in the sector map whose "to" is the
                   sector node "sector:<sector_id>" suggest sector exposure (dashed arrows);
                 - its own "exposures" list in company.json (solid arrows).
               The inner ring holds every concept with a direct relation to the stock;
               the outer ring holds the concepts one step further upstream. So every
               incoming path is at most two hops. Outgoing leads are also shown.
               Projected links are candidates, never confirmed company relationships.

The institution registry gets its own page, <folder>/<name>.html: institutions in the
centre, then the three kinds, then each institution. Its publications and the sector
concepts and companies it is linked to are in the details panel. Sector pages also list, on each concept and stock, the
institutions that act for it or publish about it.

Click anything to read its details in the side panel. Needs only the standard library.
Loading, validation and company projection live in utils/research_map.py; this file only draws.
"""

import html
import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import quote

from utils.research_map import CENTRE, company_graph, content_hash, find_maps, load, load_registry, pretty, profile_id, profile_paths, require

PALETTE = ["#c0392b", "#2166ac", "#3a8a2e", "#c9730a", "#7b3fa8", "#16877d", "#8a6d1c", "#b0457a"]

# Sector tab.
R_BRANCH = 230  # ring 1
R_ENTITY = 390  # ring 2: dot, with the label written outward from it
HALF = 650  # half the canvas size
BRANCH_GAP = 0.6  # empty space between branches, in entity slots

# Stock tabs.
R_CENTRE = 100

# ---------------------------------------------------------------- drawing helpers


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
    unit = 2 * math.pi / (len(nodes) + BRANCH_GAP * len(branches))

    angle, branch_angle = {}, {}
    cursor = -math.pi / 2  # start at the top, go clockwise
    wedges = []
    for branch in branches:
        start = cursor + BRANCH_GAP * unit / 2
        cursor = start
        for node_id, node in nodes.items():
            if node["branch"] == branch:
                angle[node_id] = cursor + unit / 2
                cursor += unit
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

    # Keep the graph focused on concepts; source leads remain in the details panel.
    for node_id, node in nodes.items():
        x, y = polar(R_ENTITY, angle[node_id])
        parts = [
            f'<g class="node" data-n="{html.escape(node_id)}">',
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{colour[node["branch"]]}"/>',
            radial_text(pretty(node["name"]), R_ENTITY + 13, angle[node_id], "entity"),
        ]
        out.append("".join(parts) + "</g>")

    # Ring 1 branches and the root, drawn last so they sit on top of the curves.
    for branch in branches:
        x, y = polar(R_BRANCH, branch_angle[branch])
        label = pretty(branch)
        width = 15 * len(label) + 36
        out.append(
            f'<g class="branch" data-b="{html.escape(branch)}">'
            f'<rect x="{x - width / 2:.1f}" y="{y - 25:.1f}" width="{width:.1f}" height="50" rx="25" fill="{colour[branch]}"/>'
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" dy="0.35em">{html.escape(label)}</text></g>'
        )
    out.append(
        f'<g class="root node" data-n="{html.escape(root_id or "")}"><circle r="{root_r:.0f}"/>'
        f'<text text-anchor="middle" dy="0.35em">{html.escape(root)}</text></g>'
    )
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------- stock tabs


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
    size = {i: (17.8 * len(pretty(nodes[i]["name"])) + 40, 62) for i in direct}

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

    out = svg_open(key, half, branches, colour, "company-map")
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
    role_count = len(profile.get("business_roles", []))
    centre_caption = nodes[CENTRE]["kind"]
    if role_count > 1 or len(centre_caption) > 14:
        centre_caption = f"{role_count} role{'s' if role_count != 1 else ''}"
    out.append(
        f'<g class="node centre" data-n="{CENTRE}"><circle r="{R_CENTRE}"/>'
        f'<text class="symbol" text-anchor="middle" dy="-0.25em">{html.escape(nodes[CENTRE]["name"])}</text>'
        f'<text class="kind" text-anchor="middle" dy="1.1em">{html.escape(centre_caption)}</text>'
        '</g>'
    )

    out.append("</svg>")
    return "\n".join(out)


def company_overview(profile: dict, nodes: dict, relations: list) -> dict:
    listing = [f"{pretty(k)}: {v}" for k, v in profile.get("listing", {}).items()]
    sourced = [f"{r['assessment']}: {r['summary']} — {r['citation']['url']} ({r['citation']['locator']})"
               for r in profile["evidence"]]
    touching = [r for r in relations if r["hop"] == "direct"]
    count = {scope: sum(1 for r in touching if r["scope"] == scope) for scope in ("concept", "sector", "company")}
    hops = {hop: sum(1 for r in relations if r["hop"] == hop) for hop in ("second_hop", "lateral")}
    return {
        "title": f"{nodes[CENTRE]['name']} — {profile.get('name', '')}".rstrip(" —"),
        "tag": nodes[CENTRE]["tag"],
        "notice": (
            "Inner ring: direct candidate links and company research leads. Outer ring: one step further upstream. "
            "Solid arrows follow business roles or company exposures; dashed arrows suggest sector exposure. "
            "Company applicability must be checked. Paths can mix mechanism hypotheses and observations; "
            "they are not causal conclusions. Evidence assessments remain separate from the leads."
        ),
        "sections": [
            {"h": "Research links", "items": [{"node": CENTRE, "text": (
                f"{count['concept']} role candidates, {count['sector']} sector candidates, "
                f"{count['company']} company leads; {hops['second_hop']} second-hop links, "
                f"{hops['lateral']} lateral links between direct concepts"
            )}]},
            {"h": "Applicability", "items": [r["scope_note"] for r in profile["business_roles"]]
                + list(profile["projection"]["edge_conditions"].values())},
            {"h": "Listing", "items": listing + ([f"fiscal calendar: {profile['fiscal_calendar']}"] if profile.get("fiscal_calendar") else [])},
            {"h": "What to watch", "items": list(profile.get("observables_to_research", []))},
            {"h": "Evidence assessments", "items": sourced or ["None recorded yet."]},
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
body { margin:0; font:17px/1.6 system-ui, sans-serif; background:var(--bg); color:var(--fg);
       display:grid; grid-template-columns:minmax(0,1fr) clamp(360px,30vw,460px); grid-template-rows:auto minmax(0,1fr); height:100vh; }
header { grid-column:1/-1; padding:12px 20px; border-bottom:1px solid var(--line); display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
header h1 { font-size:21px; margin:0; }
header select { font:inherit; max-width:100%; min-height:40px; padding:4px 10px; border:1px solid var(--line); border-radius:8px; background:var(--panel); color:var(--fg); }
header label { font-size:15px; }
header button { font:inherit; min-height:40px; padding:4px 12px; border:1px solid var(--line); border-radius:8px; background:var(--panel); color:var(--fg); cursor:pointer; }
header button:hover, aside a:hover { background:var(--line); }
button:focus-visible, a:focus-visible { outline:3px solid #418ad6; outline-offset:3px; }
nav { display:flex; gap:4px; flex:1; flex-wrap:wrap; }
nav button { padding:4px 14px; border-radius:999px; }
nav button.on { background:var(--fg); color:var(--bg); border-color:var(--fg); font-weight:600; }
.graph-area { min-width:0; min-height:0; display:flex; flex-direction:column; }
.map-guide { padding:12px 20px; border-bottom:1px solid var(--line); font-size:14px; }
#legend { display:flex; gap:8px 18px; flex-wrap:wrap; }
.legend-item { display:inline-flex; align-items:center; gap:7px; }
.legend-item i { width:10px; height:10px; border-radius:3px; }
.map-guide p { margin:6px 0 0; color:var(--muted); }
#map { overflow:auto; padding:8px; flex:1; min-height:0; }
#map svg { display:block; margin:0 auto; font-family:system-ui, sans-serif; }
svg .wedge { opacity:.07; }
svg .guide { fill:none; stroke:var(--line); stroke-dasharray:3 6; }
svg .tree { fill:none; stroke-width:1.2; opacity:.4; }
svg .entity { font-size:26px; fill:var(--fg); }
svg.company-map .entity { font-size:30px; }
svg .branch text, svg .pill text { font-weight:600; fill:#fff; }
svg .branch text { font-size:26px; }
svg .pill text { font-size:32px; }
svg .root circle, svg .centre circle { fill:var(--fg); }
svg .root text { font-size:19px; font-weight:700; fill:var(--bg); }
svg .centre .symbol { font-size:40px; font-weight:700; fill:var(--bg); }
svg .centre .kind { font-size:26px; fill:var(--bg); }
svg .rel .line { fill:none; stroke-width:1.6; opacity:.55; }
svg .rel.primary .line { stroke-width:2.2; opacity:.9; }
svg .rel.cross .line { stroke-dasharray:6 4; }
svg .rel .hit { fill:none; stroke:transparent; stroke-width:24; }
svg .rel text { display:none; font-size:28px; font-weight:600; paint-order:stroke; stroke:var(--bg); stroke-width:6px; }
svg .rel:hover text { display:block; }
svg.norel .rel:not(.lit), svg .rel.side:not(.lit) { display:none; }
svg .node, svg .branch, svg .rel { cursor:pointer; transition:opacity .15s; }
svg.focus .node:not(.lit), svg.focus .branch:not(.lit), svg.focus .rel:not(.lit), svg.focus .tree { opacity:.13; }
svg .rel.lit .line { opacity:1; stroke-width:2.6; }
svg .rel.picked text { display:block; }
svg .node.picked circle, svg .node.picked rect, svg .branch.picked rect { stroke:var(--fg); stroke-width:3; }
svg .centre.picked circle, svg .root.picked circle { stroke:var(--muted); }
svg .node.picked .entity { font-weight:700; }
aside { border-left:1px solid var(--line); background:var(--panel); padding:24px; overflow:auto; overflow-wrap:anywhere; font-size:18px; line-height:1.65; }
aside h2 { font-size:24px; line-height:1.35; margin:0 0 10px; }
aside .tag { color:var(--muted); font-size:16px; margin-bottom:16px; }
aside h3 { font-size:15px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:24px 0 8px; }
aside ul { margin:0; padding-left:22px; }
aside li { margin-bottom:12px; overflow-wrap:anywhere; }
aside a { color:inherit; text-decoration:underline dotted; cursor:pointer; }
aside .edge-link { display:inline-block; padding:3px 10px; margin:4px 0; border:1px solid var(--line); border-radius:6px; text-decoration:none; font-weight:600; }
aside .notice { color:var(--muted); font-size:16px; }
@media (max-width: 1000px) {
  body { grid-template-columns:1fr; grid-template-rows:auto 65vh auto; height:auto; }
  aside { border-left:0; border-top:1px solid var(--line); padding:20px; }
  header { padding:12px; gap:8px; }
  header h1 { width:100%; }
  nav { flex-basis:100%; }
}
</style></head><body>
<header><select id="maps" aria-label="知识地图"></select><h1>__TITLE__</h1><nav id="tabs"></nav>
<label id="relsBox"><input type="checkbox" id="rels"> Show all relations</label>
<button id="out" aria-label="Zoom out">−</button><button id="fit">全图</button><button id="read">阅读大小</button><button id="in" aria-label="Zoom in">+</button></header>
<main class="graph-area"><div class="map-guide"><div id="legend"></div><p>点击节点查看研究内容 · 点击连线或右侧关系按钮查看适用条件 · 可滚动查看画布</p></div>
<div id="map">__SECTIONS__</div></main>
<aside id="panel"></aside>
<script>
const PAGE = __DATA__;
const maps = document.getElementById('maps');
for (const entry of PAGE.maps || []) {
  const option = new Option(entry.label, entry.href, entry.current, entry.current);
  maps.add(option);
}
maps.hidden = maps.options.length < 2;
maps.onchange = () => { location.href = maps.value; };
const map = document.getElementById('map'), panel = document.getElementById('panel'), tabs = document.getElementById('tabs');
let V, svg, key, zoom = 1, zoomMode = 'read';
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const nice = s => String(s).replace(/_/g, ' ');
const find = (attr, value) => svg.querySelector(`[data-${attr}="${CSS.escape(value)}"]`);
const nodeLink = id => `<a data-node="${esc(id)}">${esc(nice(V.nodes[id].name))}</a>`;
const list = items => items.length ? '<ul>' + items.map(i => `<li>${i}</li>`).join('') + '</ul>' : '<p class="notice">None.</p>';
const scope = r => r.scope === 'sector' ? ' <span class="notice">[sector-wide]</span>' : r.scope === 'company' ? ' <span class="notice">[company-specific]</span>' : '';
const relLine = (r, other) => `<a class="edge-link" tabindex="0" role="button" data-edge="${r.i}" title="查看关系详情">${esc(nice(r.type || 'relation'))} ↗</a> ${other}${scope(r)} <br><span class="notice">${esc(r.mechanism || '')}</span>`;
const item = i => typeof i === 'string' ? esc(i)
  : i.branch ? `<a data-branch="${esc(i.branch)}"><b>${esc(nice(i.branch))}</b></a><br>${esc(i.text)}`
  : i.view ? `<a data-view="${esc(i.view)}"><b>${esc(i.view)}</b></a> ${esc(i.text)}`
  : `<a data-node="${esc(i.node)}">${esc(i.text)}</a>`;

const fitSide = () => Math.max(1, Math.min(map.clientWidth, map.clientHeight) - 16);
const setZoom = z => {
  zoom = Math.max(0.4, Math.min(5, z));
  const side = fitSide();
  svg.style.width = svg.style.height = side * zoom + 'px';
  map.scrollLeft = Math.max(0, (svg.clientWidth + 16 - map.clientWidth) / 2);
  map.scrollTop = Math.max(0, (svg.clientHeight + 16 - map.clientHeight) / 2);
};
const readingSize = () => {
  zoomMode = 'read';
  setZoom(Math.max(1, 960 / fitSide(), svg.viewBox.baseVal.width * 0.56 / fitSide()));
};
addEventListener('resize', () => zoomMode === 'read' ? readingSize() : setZoom(zoomMode === 'fit' ? 1 : zoom));
document.getElementById('in').onclick = () => { zoomMode = 'manual'; setZoom(zoom * 1.25); };
document.getElementById('out').onclick = () => { zoomMode = 'manual'; setZoom(zoom / 1.25); };
document.getElementById('fit').onclick = () => { zoomMode = 'fit'; setZoom(1); };
document.getElementById('read').onclick = readingSize;
document.getElementById('rels').onchange = e => svg.classList.toggle('norel', !e.target.checked);

function openView(name, node = '') {
  key = PAGE.views[name] ? name : PAGE.order[0];
  V = PAGE.views[key];
  document.getElementById('legend').innerHTML = V.branches.map(b => `<span class="legend-item"><i style="background:${esc(b.colour)}"></i>${esc(nice(b.name))}</span>`).join('');
  map.querySelectorAll('section').forEach(s => s.hidden = s.dataset.view !== key);
  svg = map.querySelector(`section[data-view="${CSS.escape(key)}"] svg`);
  // Enlarged labels need space beyond the original drawing bounds.
  const bounds = svg.getBBox();
  const half = Math.max(Math.abs(bounds.x), Math.abs(bounds.y), Math.abs(bounds.x + bounds.width), Math.abs(bounds.y + bounds.height)) + 24;
  svg.setAttribute('viewBox', `${-half} ${-half} ${2 * half} ${2 * half}`);
  tabs.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.tab === key));
  document.getElementById('relsBox').hidden = key !== PAGE.order[0] || !V.relations.length;
  document.getElementById('rels').checked = !svg.classList.contains('norel');
  const focusNode = Object.hasOwn(V.nodes, node) && node !== '';
  history.replaceState(null, '', '#' + encodeURIComponent(key) + (focusNode ? '?node=' + encodeURIComponent(node) : ''));
  readingSize(); showOverview();
  if (focusNode) showNode(node);
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
    <p>${esc(n.role || '')}</p><p class="notice">${esc(nice(n.node_kind || ''))}</p>
    ${n.observability ? `<h3>Observability</h3><p><strong>${esc(n.observability)}</strong>${n.observable_with ? ' — ' + esc(n.observable_with) : ''}</p>` : ''}
    ${n.facts ? '<h3>Profile</h3>' + list(n.facts.map(esc)) : ''}
    ${n.leads ? `<h3>${esc(n.leads_heading || 'Leads')}</h3><p>` + n.leads.map(esc).join(', ') + '</p>' : ''}
    <h3>${esc(n.watch_heading || 'What to watch')}</h3>${list((n.watch || []).map(esc))}
    ${n.institutions ? '<h3>Institutions</h3>' + list(n.institutions.map(esc)) : ''}
    ${n.related_concepts ? '<h3>Related research maps</h3>' + list(n.related_concepts.map(link =>
      `<a href="${esc(link.href)}">${esc(link.label)}</a><br>${esc(link.question)}`)) : ''}
    ${V.relations.length ? `<h3>Outgoing research links</h3>${list(outgoing.map(r => relLine(r, '→ ' + nodeLink(r.to))))}
    <h3>Incoming research links</h3>${list(incoming.map(r => relLine(r, '← ' + nodeLink(r.from))))}` : ''}`;
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
    <p class="notice">${esc(nice(r.status))} · ${esc(nice(r.relation_kind))}. This link is not a confirmed company fact.</p>
    <h3>Hypothesis / observation</h3><p>${esc(r.mechanism || 'Not recorded.')}</p>
    ${r.test_criteria ? '<h3>How to test this link</h3>' + list(Object.entries(r.test_criteria).map(
      ([key, value]) => `<strong>${esc(nice(key))}</strong><br>${esc(value)}`)) : ''}
    <h3>Applicability</h3>${list((r.applicability_notes || []).map(esc))}
    <h3>Original research link</h3><p>${esc(r.origin_from)} → ${esc(r.origin_to)}</p>
    <p class="notice">${esc(r.origin_edge_id)}<br>Revision ${esc(r.origin_content_revision)}</p>
    <h3>Evidence assessments</h3>${list((r.evidence || []).map(e => esc(e.assessment + ': ' + e.summary + ' — ' + e.citation.url + ' (' + e.citation.locator + ')')))}`;
}
function showOverview() {
  clear(); svg.classList.remove('focus');
  const o = V.overview;
  panel.innerHTML = `<h2>${esc(o.title)}</h2><div class="tag">${esc(o.tag || '')}</div><p class="notice">${esc(o.notice || '')}</p>` +
    o.sections.filter(s => s.items.length).map(s => `<h3>${esc(s.h)}</h3>${list(s.items.map(item))}`).join('');
}
tabs.innerHTML = PAGE.order.map((k, i) => `<button data-tab="${esc(k)}">${i ? esc(k) : esc(PAGE.home || 'Sector')}</button>`).join('');
document.addEventListener('click', e => {
  const tab = e.target.closest('[data-tab],[data-view]');
  if (tab && !tab.matches('section')) return openView(tab.dataset.tab || tab.dataset.view);
  const link = e.target.closest('[data-node],[data-edge],[data-branch]');
  if (link) return link.dataset.node ? showNode(link.dataset.node) : link.dataset.branch ? showBranch(link.dataset.branch) : showEdge(+link.dataset.edge);
  const g = e.target.closest('svg [data-n], svg [data-e], svg [data-b]');
  if (g) return g.dataset.n ? showNode(g.dataset.n) : g.dataset.b ? showBranch(g.dataset.b) : showEdge(+g.dataset.e);
  if (e.target.closest('#map')) showOverview();
});
document.addEventListener('keydown', e => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.edge-link')) {
    e.preventDefault(); e.target.click();
  }
});
const [initialView, query] = location.hash.slice(1).split('?');
openView(decodeURIComponent(initialView), new URLSearchParams(query).get('node') || '');
</script></body></html>
"""


def map_links(current: Path) -> list[dict]:
    """Navigation between the generated sector and institution pages."""
    sectors, registry = find_maps(current.parent.parent)
    return [{"label": json.loads(path.read_text(encoding="utf-8")).get("label", path.stem),
             "href": f"../{path.parent.name}/{path.stem}.html", "current": path.resolve() == current.resolve()}
            for path in [*sectors, *([registry] if registry else [])]]


def resolve_related_concepts(source: Path, nodes: dict) -> list[dict]:
    """Resolve curated references and record the target map versions used for their labels."""
    links = [link for node in nodes.values() for link in node.get("related_concepts", [])]
    if not links:
        return []
    by_sector = {}
    for path in find_maps(source.parent.parent)[0]:
        data = json.loads(path.read_text(encoding="utf-8"))
        by_sector[data["sector_id"]] = (path, data)
    inputs = {}
    for link in links:
        sector, concept = link["sector_id"], link["concept_id"]
        require(sector in by_sector, source, f"unknown related sector {sector}")
        path, data = by_sector[sector]
        target = next((c for c in data["concepts"] if c["id"] == concept), None)
        require(target is not None, source, f"unknown related concept {sector}/{concept}")
        link["label"] = f"{data['label']} → {target['label']}"
        link["href"] = (f"../{quote(path.parent.name)}/{quote(path.stem)}.html"
                        f"#{quote(path.stem)}?node={quote(concept, safe='')}")
        if sector not in inputs:
            inputs[sector] = {"id": sector, "content_revision": data["content_revision"],
                              "content_sha256": content_hash(data)}
    return list(inputs.values())


def render(sector_file: Path) -> Path:
    view, nodes, relations = load(sector_file)
    related_inputs = resolve_related_concepts(sector_file, nodes)
    branches = view["Level_1"]
    colour = {b: PALETTE[i % len(PALETTE)] for i, b in enumerate(branches)}
    branch_info = [{"name": b, "question": view[b].get("question"), "colour": colour[b]} for b in branches]
    title = str(view.get("root", sector_file.stem))
    sector_key = sector_file.stem
    root_id = view.get("root_id")
    sector_nodes = dict(nodes)
    if root_id:
        sector_nodes[root_id] = {
            "branch": "", "name": title, "tag": "Sector", "watch": [],
            "node_kind": "sector",
            "role": "Sector research leads suggest candidate company exposures, subject to applicability review.",
        }

    institutions, _, registry = load_registry(sector_file.parent.parent)
    registry_input = {}
    if registry is not None:
        data = json.loads(registry.read_text(encoding="utf-8"))
        registry_input["registry"] = {"id": data["registry_id"], "content_revision": data["content_revision"],
                                      "content_sha256": content_hash(data)}
    for institution in institutions.values():
        for link in institution["concept_links"]:
            if link["sector_id"] == view["sector_id"]:
                nodes[link["concept_id"]].setdefault("institutions", []).append(institution_line(institution, link["link_kind"]))

    profiles = []
    for path in profile_paths(sector_file.parent):
        profile = json.loads(path.read_text(encoding="utf-8"))
        issuer = profile.get("issuer_institution_id")
        require(issuer is None or not institutions or issuer in institutions, path, f"unknown issuer institution {issuer!r}")
        profiles.append((profile.get("symbol", path.parent.name), profile, path))
    symbols = [symbol for symbol, _, _ in profiles]
    require(len(symbols) == len(set(symbols)) and sector_key not in symbols,
            sector_file, "company tab symbols must be unique and distinct from the sector tab")

    views = {
        sector_key: {
            "nodes": sector_nodes,
            "relations": [{**r, "i": i, **({"scope": "sector"} if r["to"] == root_id else {})} for i, r in enumerate(relations)],
            "branches": branch_info,
            "overview": {
                "title": title,
                "tag": f"Compiled {view.get('reviewed_on') or 'n/a'} · revision {view['content_revision']}",
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
        picked, kept, direct, second = company_graph(profile, nodes, relations, path, view)
        venues = [institution_line(i, link["link_kind"]) for i in institutions.values()
                  for link in i["company_links"] if link["company_id"] == profile_id(profile)]
        issuer = institutions.get(profile.get("issuer_institution_id"))
        if issuer:
            venues.insert(0, institution_line(issuer, "issuer"))
        if venues:
            picked[CENTRE]["institutions"] = venues
        if profile.get("index_tracked"):
            picked[CENTRE]["facts"] = [f"index tracked: {profile['index_tracked']}"] + [
                f"member to check: {m['symbol']} — {m['note']}" for m in profile.get("member_leads", [])]
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

    payload = {
        "title": title, "order": list(views), "views": views, "maps": map_links(sector_file),
        "inputs": {
            **registry_input,
            "related_sectors": related_inputs,
            "sector": {"id": view["sector_id"], "content_revision": view["content_revision"], "content_sha256": view["content_sha256"]},
            "companies": [{"id": profile_id(profile), "content_revision": profile["content_revision"],
                           "content_sha256": content_hash(profile)} for _, profile, _ in profiles],
        },
        "usage": view["usage"],
    }
    page = (
        PAGE.replace("__TITLE__", html.escape(title))
        .replace("__SECTIONS__", "\n".join(sections))
        .replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    )
    target = sector_file.with_suffix(".html")
    target.write_text(page, encoding="utf-8")
    return target


def institution_line(institution: dict, link_kind: str) -> str:
    """One panel line: who, how they relate, and what they publish."""
    publications = "; ".join(p["name"] for p in institution["publications"])
    return f"{institution['label']} ({pretty(link_kind)}): {publications}"


def render_institutions(registry: Path) -> Path:
    """Draw the registry: institutions -> kind -> institution. Links and publications go in the panel."""
    institutions, labels, _ = load_registry(registry.parent.parent)
    data = json.loads(registry.read_text(encoding="utf-8"))
    kinds = [k for k in data.get("institution_kinds", {}) if any(i["institution_kind"] == k for i in institutions.values())]
    title = data.get("label", "Institutions")
    view = {"root": title, "root_id": None, "Level_1": kinds,
            **{k: {"question": data["institution_kinds"][k]} for k in kinds}}
    nodes = {}
    for key, item in institutions.items():
        kind = item["institution_kind"]
        linked = [f"{labels[l['concept_id']]} ({pretty(l['link_kind'])})" for l in item["concept_links"]]
        linked += [f"{labels[l['company_id']]} ({pretty(l['link_kind'])})" for l in item["company_links"]]
        nodes[key] = {
            "branch": kind, "node_kind": kind,
            "name": item.get("short_label", item["label"]),
            "role": f"{item['label']} · {item['jurisdiction']}. {item['description_lead']}.",
            "facts": [f"{pretty(k)}: {', '.join(v) if isinstance(v, list) else v}" for k, v in item[kind].items()]
                     + [f"source: {url}" for url in item["source_leads"]],
            "watch_heading": "Publications",
            "watch": [f"{p['name']} — {p['cadence']}; lag: {p['typical_lag']}; dated by: {pretty(p['availability'])}"
                      + (f". {p['note']}" if p.get("note") else "") for p in item["publications"]],
            "leads_heading": "Linked to",
            **({"leads": linked} if linked else {}),
        }
    colour = {b: PALETTE[i % len(PALETTE)] for i, b in enumerate(kinds)}
    branch_info = [{"name": k, "question": view[k]["question"], "colour": colour[k]} for k in kinds]
    usage = data.get("usage", {})
    key = registry.stem
    payload = {
        "title": title, "home": title, "order": [key], "maps": map_links(registry),
        "views": {key: {
            "nodes": nodes, "relations": [], "branches": branch_info,
            "overview": {
                "title": title,
                "tag": f"Compiled {data.get('compiled_on') or 'n/a'} · revision {data['content_revision']}",
                "notice": " ".join(filter(None, [usage.get("describe"), usage.get("time_varying_data_note")])),
                "sections": [{"h": "Kinds", "items": [{"branch": b["name"], "text": b["question"]} for b in branch_info]}],
            },
        }},
        "inputs": {"registry": {"id": data.get("registry_id"), "content_revision": data["content_revision"],
                                "content_sha256": content_hash(data)}},
        "usage": usage,
    }
    page = (
        PAGE.replace("__TITLE__", html.escape(title))
        .replace("__SECTIONS__", f'<section data-view="{html.escape(key)}">{sector_svg("s", view, nodes, [], colour)}</section>')
        .replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    )
    target = registry.with_suffix(".html")
    target.write_text(page, encoding="utf-8")
    return target


def sector_file(argument: str, here: Path) -> Path:
    """Accept a sector folder (by path, or by name beside this script) or its JSON file."""
    path = Path(argument)
    if not path.exists() and (here / argument).exists():
        path = here / argument
    if path.is_dir():  # the folder's own map, whatever the letter case of its name
        named = [p for p in path.glob("*.json") if p.stem.lower() == path.resolve().name.lower()]
        path = named[0] if named else path / f"{path.resolve().name}.json"
    if not path.is_file():
        raise SystemExit(f"No map at {path}")
    return path


def main(arguments: list[str]) -> None:
    here = Path(__file__).parent  # the sector folders live beside this script
    sectors, registry = find_maps(here)
    paths = [sector_file(a, here) for a in arguments] or [*sectors, *([registry] if registry else [])]
    if arguments:
        # A first single-map build must also materialise its navigation targets.
        for root in {p.resolve().parent.parent for p in paths}:
            siblings, sibling_registry = find_maps(root)
            if sibling_registry:
                siblings.append(sibling_registry)
            paths.extend(p for p in siblings if not p.with_suffix(".html").exists())
        paths = list(dict.fromkeys(p.resolve() for p in paths))
    if not paths:
        raise SystemExit(f"No sector folders found under {here}")
    for path in paths:
        try:
            is_registry = json.loads(path.read_text(encoding="utf-8")).get("kind") == "institution_registry"
            print(f"wrote {render_institutions(path) if is_registry else render(path)}")
        except ValueError as problem:  # a validation failure: report it without a traceback
            raise SystemExit(f"error: {problem}") from None


if __name__ == "__main__":
    main(sys.argv[1:])
