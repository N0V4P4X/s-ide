# Calculator

PEMDAS-correct calculator with a Tkinter GUI and a command-line interface.

Built as a demonstration project for S-IDE's AI Teams system. Load it in
S-IDE to see the dependency graph and run the AI team workflow on it.

## PEMDAS correctness

The evaluator uses a hand-written recursive-descent parser — no `eval()`.
Operator precedence is enforced structurally by the grammar:

| Priority | Operators | Associativity |
|---|---|---|
| 1 (highest) | `( )` | — |
| 2 | `**` / `^` | Right |
| 3 | Unary `-` `+` | — |
| 4 | `*` `/` `//` `%` | Left |
| 5 | `+` `-` | Left |

Key examples:
- `3 + 4 * 2` → `11` (not 14)
- `2 ** 3 ** 2` → `512` (not 64 — right-associative)
- `8 / 2 * (2 + 2)` → `16` (left-to-right)

## Quick start

```bash
cd examples/calculator

# GUI
python src/gui_app.py

# CLI interactive REPL
python src/cli.py

# CLI single expression
python src/cli.py "3 + 4 * 2"

# Tests
python -m unittest discover test/ -v
```

## Architecture

```
calculator/
├── src/
│   ├── pemdas.py    — tokeniser + recursive-descent parser + evaluate()
│   ├── cli.py       — REPL and single-expression CLI
│   └── gui_app.py   — Tkinter calculator with live preview
└── test/
    └── test_pemdas.py — 55 tests (tokeniser, arithmetic, PEMDAS, errors)
```

## Using with S-IDE AI Teams

Open this project in S-IDE, type a task in the Plan tab, and run the workflow:

```
Task: "Add a history panel to the GUI that shows the last 10 calculations"
```

The Architect will analyse the codebase, the Implementer will write the code,
the Reviewer will check it, and the Tester will verify correctness — all
without touching the working tree until you approve.
