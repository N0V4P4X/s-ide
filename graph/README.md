# graph/

Core data model. All types shared between the parser, GUI, build pipeline, and AI tools.

## types.py

| Type | Description |
|---|---|
| `FileNode` | One source file: id, path, category, imports, exports, definitions, tags, errors, position |
| `Edge` | Directed dependency: source→target, type, symbols, line, isExternal |
| `ProjectGraph` | Container: meta + nodes + edges. `.to_dict()` → JSON-safe dict |
| `GraphMeta` | Project config, language stats, doc audit, parse timing, perf data |
| `ImportRecord` | One import statement |
| `ExportRecord` | One exported symbol |
| `Definition` | One named definition (function, class) with full data flow |
| `Position` | x/y canvas position |

## Definition — data flow fields

The Python parser populates these on every function and class definition:

```python
@dataclass
class Definition:
    name:        str
    kind:        str           # "function", "method", "class", "dunder"
    line:        int
    end_line:    int
    indent:      int
    is_async:    bool
    decorators:  list[str]
    bases:       list[str]     # class bases
    # Data flow:
    args:        list          # [(name, type_hint_str), ...]
    return_type: str
    calls:       list[str]     # function names called in body
    raises:      list[str]     # exception types raised
    complexity:  int           # cyclomatic complexity estimate
```

## GraphMeta.perf

```json
{
  "total_ms": 268,
  "slowest": "parse_files",
  "stages": [
    {"name": "config",        "ms": 0.5},
    {"name": "walk",          "ms": 3.6},
    {"name": "parse_files",   "ms": 254.3},
    {"name": "resolve_edges", "ms": 1.2},
    {"name": "layout",        "ms": 0.1},
    {"name": "doc_audit",     "ms": 0.1},
    {"name": "write_json",    "ms": 8.2}
  ]
}
```

## Layer rule

`graph/types.py` imports nothing from other S-IDE modules. It is the foundation layer. All other modules may import from it; it imports from none of them.
