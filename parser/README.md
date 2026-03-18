# parser/

Project analysis engine — walks a directory, extracts semantic structure from each file, resolves import relationships into a dependency graph, and audits documentation health.

## Pipeline (in order)

```
project_parser.py  ← orchestrator, call parse_project() here
    ↓
walker.py          ← discover files, apply ignore patterns
    ↓
parsers/           ← per-language extraction
    python_parser.py   (AST-based, accurate)
    js_parser.py       (regex, handles ES/CJS/TS)
    json_parser.py     (package.json, tsconfig, generic config)
    shell_parser.py    (source/export/function relationships)
    ↓
resolve_edges.py   ← turn import strings into graph edges
    ↓
layout.py          ← assign x/y positions for the node editor
    ↓
doc_check.py       ← README staleness, empty module audit
    ↓
→ ProjectGraph (auto-saved to .nodegraph.json)
```

## Output

`parse_project(path)` returns a `ProjectGraph`. The full dict is also written to `<project>/.nodegraph.json` automatically.

Performance timings for each stage are stored in `graph.to_dict()["meta"]["perf"]`.

## project_config.py

Reads and writes `side.project.json`. Auto-creates it on first parse.

```json
{
  "name": "my-project",
  "version": "0.1.0",
  "run":  { "dev": "python main.py", "test": "pytest" },
  "ignore": ["dist", "*.test.py"],
  "versions": { "dir": "versions", "compress": true, "keep": 20 }
}
```
