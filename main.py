# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
main.py
=======
S-IDE command-line interface.

Sub-commands
------------
parse   <project-dir> [--out FILE]
    Walk and parse a project, write graph JSON to FILE
    (default: <project-dir>/.nodegraph.json).

run      <project-dir> <script-name>
    Look up script-name in side.project.json → run → and stream
    its output to the terminal.

serve   <project-dir> [--port PORT]
    Launch the S-IDE web interface.

Examples
--------
    python main.py parse ./my-project
    python main.py parse ./my-project --out /tmp/graph.json
    python main.py run ./my-project dev
    python main.py serve ./my-project
"""

from __future__ import annotations
import argparse
import json
import os
import sys

from logsetup import setup_logging

log = setup_logging()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_dir(path: str) -> str:
    abs_path = os.path.abspath(path)
    if not os.path.isdir(abs_path):
        print(f"[s-ide] ERROR: not a directory: {path}", file=sys.stderr)
        sys.exit(1)
    return abs_path


# ── Sub-command handlers ──────────────────────────────────────────────────────

def cmd_parse(args: argparse.Namespace) -> None:
    """Parse a project directory and write graph JSON."""
    from parser.project_parser import parse_project

    root = _require_dir(args.project)
    log.info("parse invoked: %s", root)
    print(f"[s-ide] Parsing: {root}")

    graph = parse_project(root)
    d = graph.to_dict()

    out_path = args.out or os.path.join(root, ".nodegraph.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)

    m = d["meta"]
    log.info("parse %s → %d nodes, %d edges (%s ms) → %s",
             root, m["totalFiles"], m["totalEdges"], m["parseTime"], out_path)
    print(f"[s-ide] {m['totalFiles']} nodes, {m['totalEdges']} edges "
          f"({m['parseTime']} ms) → {out_path}")

    if not m["docs"]["healthy"]:
        s = m["docs"]["summary"]
        print(f"[s-ide] Doc warnings: {s['missingReadmes']} missing README, "
              f"{s['staleReadmes']} stale, {s['emptyModules']} empty modules")


def cmd_run(args: argparse.Namespace) -> None:
    """Run a named script from the project's side.project.json."""
    import subprocess
    from parser.project_config import load_project_config

    root = _require_dir(args.project)
    config = load_project_config(root)
    scripts = config.get("run") or {}
    log.info("run invoked: %s %s", root, args.script)

    if not scripts:
        print("[s-ide] No 'run' scripts defined in side.project.json", file=sys.stderr)
        sys.exit(1)

    script_name = args.script
    if script_name not in scripts:
        print(f"[s-ide] Unknown script '{script_name}'. Available: {', '.join(scripts)}", file=sys.stderr)
        sys.exit(1)

    command = scripts[script_name]
    print(f"[s-ide] Running '{script_name}': {command}")
    print()

    result = subprocess.run(command, shell=True, cwd=root)
    sys.exit(result.returncode)


def cmd_self_check(args: argparse.Namespace) -> None:
    """Run all self-checks: tests, parse, doc audit."""
    import subprocess
    from parser.project_parser import parse_project

    root = _require_dir(args.project)
    use_json = args.json

    log.info("self-check invoked: %s", root)

    report = {
        "project": root,
        "ok": True,
        "stages": {},
    }

    def emit(code: int = 0) -> None:
        if use_json:
            print(json.dumps(report, indent=2))
        sys.exit(code)

    if not use_json:
        print(f"[s-ide] Self-checking: {root}")

    if not use_json:
        print("\n[s-ide] 1/3: Running unit tests...")
    test_res = subprocess.run(
        [sys.executable, "test/test_suite.py", "-q"], cwd=root,
        capture_output=use_json, text=True,
    )
    report["stages"]["tests"] = {
        "ok": test_res.returncode == 0,
        "returncode": test_res.returncode,
    }
    if test_res.returncode != 0:
        report["stages"]["tests"]["tail"] = (
            (test_res.stdout or test_res.stderr or "")[-2000:]
            if use_json else ""
        )
        report["ok"] = False
        if not use_json:
            print(f"[s-ide] FAILED: Unit tests exited with code {test_res.returncode}")
        emit(1)
    if not use_json:
        print("[s-ide] OK: Tests passed.")

    if not use_json:
        print("\n[s-ide] 2/3: Parsing graph & auditing docs...")
    graph = parse_project(root)
    d = graph.to_dict()
    m = d["meta"]
    h = m["docs"]["healthy"]
    s = m["docs"]["summary"]

    out_path = os.path.join(root, ".nodegraph.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    report["stages"]["parse"] = {
        "ok": True,
        "totalFiles": m["totalFiles"],
        "totalEdges": m["totalEdges"],
        "parseTimeMs": m["parseTime"],
        "graph": out_path,
    }
    if not use_json:
        print(f"[s-ide] OK: Parsing complete ({m['parseTime']} ms) → {out_path}")

    if not use_json:
        print("\n[s-ide] 3/3: Document report:")
        print(f"  Missing READMEs: {s['missingReadmes']}")
        print(f"  Stale READMEs:   {s['staleReadmes']}")
        print(f"  Empty modules:   {s['emptyModules']}")

    report["stages"]["docs"] = {
        "ok": h,
        "missingReadmes": s["missingReadmes"],
        "staleReadmes": s["staleReadmes"],
        "emptyModules": s["emptyModules"],
        "strict": args.strict_docs,
    }

    if not h:
        if not use_json:
            print("[s-ide] Doc health issues detected.")
        if args.strict_docs:
            report["ok"] = False
            if not use_json:
                print("[s-ide] FAILED: Strict-docs requirement not met.")
            emit(1)
        elif not use_json:
            print("[s-ide] OK: Continuing (non-fatal docs).")
    elif not use_json:
        print("[s-ide] OK: All docs are healthy.")

    if use_json:
        emit(0)
    print("\n[s-ide] SUMMARY: ALL CHECKS PASSED.")


def cmd_serve(args) -> None:
    """Launch the S-IDE web interface."""
    from gui.server import run as start_server
    root = _require_dir(args.project)
    log.info("serve invoked: %s (port %s)", root, args.port)
    graph_path = os.path.join(root, ".nodegraph.json")
    if not os.path.exists(graph_path):
        print(f"[s-ide] No graph found at {graph_path}. Parsing first...")
        from parser.project_parser import parse_project
        parse_project(root_dir=root)

    os.chdir(root)
    start_server(port=args.port)

# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Construct and return the argparse CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="s-ide",
        description="S-IDE — Systematic Integrated Development Environment (core CLI)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("parse", help="Parse a project and emit graph JSON")
    sp.add_argument("project", help="Path to project directory")
    sp.add_argument("--out", metavar="FILE", help="Output JSON path (default: <project>/.nodegraph.json)")

    sp = sub.add_parser("run", help="Run a script from side.project.json")
    sp.add_argument("project", help="Path to project directory")
    sp.add_argument("script", help="Script name (key in side.project.json → run)")

    sp = sub.add_parser("self-check", help="Run tests, parse, and doc audit")
    sp.add_argument("project", help="Path to project directory")
    sp.add_argument("--strict-docs", action="store_true", help="Fail if doc health issues found")
    sp.add_argument("--json", action="store_true",
                    help="Emit a machine-readable JSON report instead of human output")

    sp = sub.add_parser("serve", help="Launch the web interface")
    sp.add_argument("project", help="Path to project directory")
    sp.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")

    return p


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """S-IDE CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "parse":      cmd_parse,
        "run":        cmd_run,
        "self-check": cmd_self_check,
        "serve":      cmd_serve,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()

# ── GPLv3 interactive notice ──────────────────────────────────────────────────

_GPLv3_WARRANTY = (
    "THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY\n"
    "APPLICABLE LAW. EXCEPT WHEN OTHERWISE STATED IN WRITING THE COPYRIGHT\n"
    'HOLDERS AND/OR OTHER PARTIES PROVIDE THE PROGRAM "AS IS" WITHOUT\n'
    "WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT\n"
    "LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A\n"
    "PARTICULAR PURPOSE. THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE\n"
    "OF THE PROGRAM IS WITH YOU.  (GPL-3.0-or-later §15)"
)

_GPLv3_CONDITIONS = (
    "You may convey verbatim copies of the Program's source code as you\n"
    "receive it, in any medium, provided that you conspicuously and\n"
    "appropriately publish on each copy an appropriate copyright notice and\n"
    "disclaimer of warranty. (See GPL-3.0 §4-6 for full conditions.)\n"
    "Full license: <https://www.gnu.org/licenses/gpl-3.0.html>"
)


def gplv3_notice():
    """Print the short GPLv3 startup notice. Call this at program startup."""
    print("S-IDE  Copyright (C) 2026  N0V4-N3XU5")
    print("This program comes with ABSOLUTELY NO WARRANTY; for details type 'show w'.")
    print("This is free software, and you are welcome to redistribute it")
    print("under certain conditions; type 'show c' for details.")


def gplv3_handle(cmd: str) -> bool:
    """
    Check whether *cmd* is a GPLv3 license command and handle it.
    Returns True if the command was consumed (caller should skip normal processing).
    """
    match cmd.strip().lower():
        case "show w":
            print(_GPLv3_WARRANTY)
            return True
        case "show c":
            print(_GPLv3_CONDITIONS)
            return True
    return False

# ─────────────────────────────────────────────────────────────────────────────
