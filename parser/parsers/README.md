# parser/parsers/

Language-specific semantic extractors. Each returns the same shape:

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
| `python_parser.py` | Python (.py, .pyw) | `ast` module — accurate, handles decorators, bases, dunders; falls back to regex on SyntaxError |
| `js_parser.py` | JS/TS/JSX/TSX | Regex on comment-stripped source — handles ES imports, CJS require, dynamic import, re-exports |
| `json_parser.py` | JSON | Structural extraction — package.json deps/scripts, tsconfig path aliases, generic config keys |
| `shell_parser.py` | Shell (.sh/.bash/.zsh) | Regex — source/., script calls, exported env vars, function definitions |

The dispatch table in `__init__.py` maps extensions to parser functions.
To add a new language, implement the function and add it there.
