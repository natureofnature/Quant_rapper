"""Load method and study entries, resolving references to existing market profiles."""

import json
import os
from pathlib import Path


def load_catalogue(knowledge: Path, subjects) -> dict:
    output = knowledge / "math"
    entries = {}

    def add(entry, path, category):
        key = entry["id"]
        if key in entries:
            raise ValueError(f"{path}: duplicate catalogue id {key}")
        entries[key] = {**entry, "category": category, "file": os.path.relpath(path, output)}

    for path in sorted(output.glob("*/*.json")):
        add(json.loads(path.read_text(encoding="utf-8")), path, path.parent.name)
    for path in sorted((knowledge / "experiences").glob("*.json")):
        add(json.loads(path.read_text(encoding="utf-8")), path, "experiences")

    referenced = {link["target"] for entry in entries.values() for link in entry["links"]}
    for key, profile, path, page in subjects:
        if key in referenced:
            add({"id": key, "name": profile["symbol"], "description": profile["name"],
                 "kind": profile["kind"], "links": [],
                 "market_page": os.path.relpath(page, output) + "#" + profile["symbol"]},
                path, "subjects")
    for entry in entries.values():
        for link in entry["links"]:
            if link["target"] not in entries:
                raise ValueError(f"{entry['file']}: unknown {link['relation']} target {link['target']}")
    return entries


def render_catalogue(knowledge: Path, subjects, maps, draw_graph, palette) -> Path:
    target = knowledge / "math/methods.html"
    entries = load_catalogue(knowledge, subjects)
    # Broad branches keep the overview legible; the catalogue retains the finer categories.
    families = {category: family for family, categories in {
        "观测": ("feature",), "建模": ("labeling", "predictor"), "配置": ("strategy", "portfolio"),
        "风险": ("valuation", "risk"), "验证": ("evaluation", "execution"),
        "工具": ("optimizer", "tools"), "实例": ("experiences", "subjects")
    }.items() for category in categories}
    nodes = {key: {"branch": families.get(e["category"], e["category"]), "name": e.get("name_zh", e["name"])}
             for key, e in entries.items()}
    branches = list(dict.fromkeys(n["branch"] for n in nodes.values()))
    edges = [{"from": key, "to": link["target"], "type": link["relation"]}
             for key, e in entries.items() for link in e["links"]]
    colours = {name: palette[i % len(palette)] for i, name in enumerate(branches)}
    graph = draw_graph("methods", {"root": "Methods", "Level_1": branches}, nodes, edges, colours)
    data = {"entries": entries, "maps": maps, "families": families}
    template = Path(__file__).with_suffix(".html.in").read_text(encoding="utf-8")
    target.write_text(template.replace("__GRAPH__", graph).replace("__DATA__", json.dumps(data, ensure_ascii=False)
                      .replace("<", "\\u003c")), encoding="utf-8")
    return target
