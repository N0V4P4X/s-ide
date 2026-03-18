## Self-improvement loop

S-IDE is designed to validate itself the same way it validates other projects:

- **Tests**: `python test/test_suite.py -q`
- **Self-check (tests + parse + doc audit)**: `python main.py self-check .`
- **Strict docs mode**: `python main.py self-check . --strict-docs`

### What `self-check` does

- **Runs unit tests** (stdlib `unittest`) to catch regressions quickly.
- **Parses the project** into `.nodegraph.json` (dependency graph + perf metadata).
- **Reports doc audit status** (missing/stale READMEs, empty modules).
  - Doc warnings are **non-fatal by default**, but `--strict-docs` makes them fail CI.

### CI

GitHub Actions runs on every push/PR:

- `python test/test_suite.py -q`
- `python main.py self-check . --json`

It uploads `.nodegraph.json` and `logs/s-ide.log` as artifacts for debugging.

### Safer self-updates

`update.py` supports safer flows:

- **Preview**: `python update.py --dry-run`
- **Non-interactive**: `python update.py --yes --no-relaunch`
- **Post-update validation**: `python update.py --self-check`
- **Post-update strict docs**: `python update.py --self-check --self-check-strict-docs`

