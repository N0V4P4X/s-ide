"""
parser/parsers/__init__.py
==========================
Dispatch table mapping file extensions to their parser functions.
Import this module to get PARSERS without importing each parser individually.
"""

from .python_parser import parse_python
from .js_parser import parse_javascript
from .json_parser import parse_json
from .shell_parser import parse_shell
from .toml_yaml_parser import parse_toml, parse_yaml

PARSERS: dict[str, callable] = {
    ".py":   parse_python,
    ".pyw":  parse_python,
    ".js":   parse_javascript,
    ".mjs":  parse_javascript,
    ".cjs":  parse_javascript,
    ".jsx":  parse_javascript,
    ".ts":   parse_javascript,
    ".tsx":  parse_javascript,
    ".json": parse_json,
    ".sh":   parse_shell,
    ".bash": parse_shell,
    ".zsh":  parse_shell,
    ".fish": parse_shell,
    ".toml": parse_toml,
    ".yaml": parse_yaml,
    ".yml":  parse_yaml,
}
