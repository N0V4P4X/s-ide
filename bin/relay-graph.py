#!/usr/bin/env python3
"""Generate plans.graph.json from Vault front matter.

A pure function of data that already exists: every note in 20-projects/ and
70-tasks/ becomes a node (tier -> category, status straight through, stage
carried in `stage`), and edges come from the `blocked-by:` field plus
`projects:` wikilinks. The plan graph must never be hand-maintained — a stale
plan graph is worse than none. Run it from the repo root:

    python bin/relay-graph.py [--vault ~/Documents/Vault]

Default vault is ~/Documents/Vault (override with SIDE_VAULT env or --vault).
Emits plans.graph.json in the current directory. Stdlib only.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
TIERS = ("top", "second", "third", "shelved", "unsorted")
STAGES = ("nigredo", "albedo", "rubedo", "exited")

PREAMBLE = "".join(
    line[2:] + "\n" for line in __doc__.strip().splitlines()[:5]
)


def read_front_matter(path):
    """Return (front_matter_dict, body_lines). Minimal YAML-front-matter
    reader for the fields the plan graph needs — no YAML dependency."""
    fm = {}
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines or lines[0].strip() != "---":
        return fm, lines
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            fm[key] = [
                t.strip().strip('"\'')
                for t in value[1:-1].split(",")
                if t.strip()
            ]
        else:
            fm[key] = value.strip('"\'')
    return fm, lines


def first_heading(lines):
    for line in lines:
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def one_liner(lines):
    for line in lines:
        s = line.strip()
        if s.startswith("**One line.**"):
            return s[len("**One line.**"):].strip()
        if s.startswith("*One line.*"):
            return s[len("*One line.*"):].strip()
    return ""


def collect_notes(vault):
    notes = []
    for rel in ("20-projects", "70-tasks"):
        d = os.path.join(vault, rel)
        if not os.path.isdir(d):
            print(f"warning: missing {d}", file=sys.stderr)
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(d, name)
            fm, lines = read_front_matter(path)
            notes.append({"rel": rel, "stem": name[:-3], "fm": fm,
                          "lines": lines})
    return notes


def node_id(stem, kind):
    return f"{kind[:3]}-{stem}"


def build_graph(notes):
    nodes, edges, warnings = [], [], []
    seen = {}

    # Pass 1: every note becomes a node.
    for n in notes:
        fm = n["fm"]
        rel = n["rel"]
        kind = "project" if rel == "20-projects" else "task"
        # Skip generated/non-project files in 20-projects (no type field).
        if rel == "20-projects" and "type" in fm and fm["type"] != "project":
            continue
        if rel == "20-projects" and "type" not in fm:
            # Generated status snapshots etc. — not a project note.
            continue
        if "status" not in fm:
            warnings.append(f"{n['stem']}: no status field — skipped")
            continue

        tier = fm.get("tier", "unsorted")
        if tier not in TIERS:
            warnings.append(
                f"{n['stem']}: tier {tier!r} not in {TIERS} — using 'unsorted'"
            )
            tier = "unsorted"
        stage = fm.get("stage", "")
        if stage not in STAGES:
            stage = ""

        title = fm.get("title") or first_heading(n["lines"]) or n["stem"]
        detail = one_liner(n["lines"]) or title

        nid = node_id(n["stem"], kind)
        seen[n["stem"]] = nid
        nodes.append({
            "id": nid,
            "label": title,
            "kind": kind,
            "category": tier,
            "stage": stage,
            "detail": detail,
            "estimateHours": 0,
            "tech": [],
            "status": fm.get("status", "unsorted"),
            "childIds": [],
        })

    node_ids = {nd["id"] for nd in nodes}

    def resolve(stem):
        if stem in seen:
            return seen[stem]
        warnings.append(f"wikilink [[{stem}]] resolves to no note — dropped")
        return None

    # Pass 2: edges from blocked-by (blocker -> blocked, type blocks) and
    # projects: wikilinks (project -> task, type schedules).
    for n in notes:
        fm = n["fm"]
        if "status" not in fm:
            continue
        kind = "project" if n["rel"] == "20-projects" else "task"
        target = node_id(n["stem"], kind)
        if target not in node_ids:
            continue

        for blocked in fm.get("blocked-by", []):
            src = resolve(WIKILINK.search(blocked).group(1) if WIKILINK.search(blocked) else blocked.strip())
            if src and src in node_ids:
                edges.append({"from": src, "to": target, "type": "blocks"})

        for proj in fm.get("projects", []):
            src = resolve(WIKILINK.search(proj).group(1) if WIKILINK.search(proj) else proj.strip())
            if src and src in node_ids and src != target:
                edges.append({"from": src, "to": target, "type": "schedules"})

    return {"nodes": nodes, "edges": edges, "warnings": warnings}


def main():
    vault = os.environ.get("SIDE_VAULT") or os.path.expanduser(
        "~/Documents/Vault")
    if "--vault" in sys.argv:
        vault = sys.argv[sys.argv.index("--vault") + 1]

    notes = collect_notes(vault)
    if not notes:
        print(f"error: no notes found under {vault} "
              f"(set --vault or SIDE_VAULT)", file=sys.stderr)
        return 1

    graph = build_graph(notes)
    graph.pop("warnings", None)

    out = {
        "version": "1.0.0",
        "type": "plans",
        "meta": {
            "name": "Vault plans graph",
            "description": "Tiers, stages, and blocking across every project "
                           "and task note in the vault. Generated — never "
                           "hand-edit.",
            "generatedAt": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
        },
        "nodes": graph["nodes"],
        "edges": graph["edges"],
    }

    with open("plans.graph.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"plans.graph.json: {len(out['nodes'])} nodes, "
          f"{len(out['edges'])} edges -> plans.graph.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
