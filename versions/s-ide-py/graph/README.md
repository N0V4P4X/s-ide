# graph/

Core data model types for the S-IDE project graph.

## types.py

All dataclasses shared between the parser, GUI, and build pipeline.

| Type | Description |
|---|---|
| `FileNode` | One source file — id, path, category, imports, exports, definitions, tags, position |
| `Edge` | Directed dependency between two FileNodes (or a FileNode and an external package) |
| `ProjectGraph` | Top-level container: `meta` + `nodes` + `edges`. Call `.to_dict()` for JSON output. |
| `GraphMeta` | Project config, language stats, doc audit summary, parse timing, **per-stage perf** |
| `ImportRecord` | A single import statement extracted from source |
| `ExportRecord` | A single exported symbol |
| `Definition` | A named definition (function, class, variable) |
| `DocAudit` / `DocWarning` | README health check results |
| `Position` | x/y canvas position assigned by the layout engine |

### GraphMeta.perf

After every parse, `meta.perf` contains per-stage timing from `ParseTimer`:

```json
{
  "total_ms": 268,
  "slowest": "parse_files",
  "stages": [
    {"name": "config",       "ms": 0.5},
    {"name": "walk",         "ms": 3.6},
    {"name": "parse_files",  "ms": 254.3},
    {"name": "resolve_edges","ms": 1.2},
    {"name": "layout",       "ms": 0.1},
    {"name": "doc_audit",    "ms": 0.1},
    {"name": "write_json",   "ms": 8.2}
  ]
}
```

This is embedded in `.nodegraph.json` and displayed in the BUILD panel timing chart.
