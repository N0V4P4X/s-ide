# parser/parsers/

Language-specific semantic extractors. Each returns:

```python
{
    "imports":     [ImportRecord, ...],
    "exports":     [ExportRecord, ...],
    "definitions": [Definition, ...],
    "tags":        ["flask", "entrypoint", ...],
    "errors":      ["SyntaxError: ...", ...],
}
```

| File | Language | Method |
|---|---|---|
| `python_parser.py` | Python | Single-pass `ast.NodeVisitor` — args, return types, calls, raises, complexity |
| `js_parser.py` | JS/TS/JSX/TSX | Regex on comment-stripped source |
| `json_parser.py` | JSON | Structural — package.json deps/scripts, tsconfig aliases |
| `shell_parser.py` | Shell | Regex — source, exports, functions |
| `toml_yaml_parser.py` | TOML/YAML | Config key extraction |

`__init__.py` maps extensions to parser functions. To add a language: implement the function, add the mapping.

## Adding a parser

```python
# parser/parsers/my_lang_parser.py

def parse_my_lang(source: str, filepath: str = "") -> dict:
    """Returns the standard shape: imports, exports, definitions, tags, errors."""
    ...

# parser/parsers/__init__.py
from .my_lang_parser import parse_my_lang
PARSERS[".mylang"] = parse_my_lang
```
