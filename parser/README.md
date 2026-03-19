# parser/

Project analysis pipeline. Walks a directory, extracts semantic structure from each file, resolves import relationships into a dependency graph, and audits documentation health.

## Pipeline

`project_parser.py` is the entry point. Call `parse_project(root)` here. Implements **Delta-Parsing** for 10x performance on incremental updates.

```
project_parser.parse_project(root)
    │
    ├─ project_config.py   load side.project.json, apply ignore rules
    ├─ walker.py            discover files → [FileNode stubs]
    ├─ parsers/             per-language extraction (**Parallel-Safe**)
    │   ├─ python_parser.py    single-pass AST visitor
    │   ├─ js_parser.py        regex on comment-stripped source
    │   ├─ json_parser.py      structural extraction
    │   ├─ shell_parser.py     regex
    │   └─ toml_yaml_parser.py
    ├─ resolve_edges.py     import strings → Edge objects
    ├─ layout.py            topological x/y position assignment
    ├─ doc_check.py         README staleness audit
    └─ → ProjectGraph (auto-saved to .nodegraph.json)
```

Each stage is timed via `ParseTimer`. Timings live in `graph.meta.perf`.

## python_parser.py

Single-pass `ast.NodeVisitor`. One `ast.walk` call per file extracts everything:

- Imports (`import X`, `from X import Y`, relative imports)
- Exports (`__all__`, or implicit: all public top-level names)
- Definitions — functions and classes with:
  - `args`: `[(name, type_hint), ...]`
  - `return_type`: annotation string
  - `calls`: function names called in the body (capped at 20)
  - `raises`: exception types raised
  - `complexity`: cyclomatic complexity estimate
  - `end_line`: last line of the body
- Framework tags (flask, fastapi, django, pytest, asyncio, …)
- Entrypoint detection (`if __name__ == "__main__"`)

Falls back to regex extraction on `SyntaxError`.

## project_config.py

```python
from parser.project_config import load_project_config, save_project_config, bump_version

cfg = load_project_config("/path/to/project")  # auto-creates if missing
cfg["version"] = bump_version(cfg["version"], "minor")
save_project_config("/path/to/project", cfg)
```

`side.project.json` fields: `name`, `version`, `description`, `ignore`, `run`, `versions`.

## doc_check.py

Audits each directory in the project for:
- Missing `README.md`
- Empty modules (files with no definitions, imports, or meaningful content)
- Stale READMEs (README older than the newest source file in the directory)

Results in `graph.meta.docs`: `{"healthy": bool, "summary": {...}, "warnings": [...]}`
