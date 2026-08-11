#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5
"""
run.py — S-IDE launcher
=======================
The single entry point for everything.

    python3 run.py                          # start server, open browser
    python3 run.py --port 7800             # custom port
    python3 run.py --no-browser            # server only
    python3 run.py --project ~/my-project  # pre-load a project
    python3 run.py parse ~/my-project      # parse only, no server
    python3 run.py test                    # run test suite
"""

import argparse, os, subprocess, sys, threading, time, webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from logsetup import setup_logging

log = setup_logging()

def _add_to_path():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

def cmd_serve(args):
    _add_to_path()
    from gui.server import run as serve, _load_projects, _save_projects

    if args.project:
        project_root = os.path.abspath(args.project)
        if not os.path.isdir(project_root):
            print(f"[s-ide] ERROR: not a directory: {project_root}", file=sys.stderr)
            sys.exit(1)
        projects = _load_projects()
        name = os.path.basename(project_root)
        if not any(p["path"] == project_root for p in projects):
            projects.insert(0, {"path": project_root, "name": name})
            _save_projects(projects)
            print(f"[s-ide] Project registered: {project_root}")
            log.info("project registered: %s", project_root)

        from parser.project_parser import parse_project
        log.info("parse invoked (--project): %s", project_root)
        print(f"[s-ide] Pre-loading project: {project_root}")
        try:
            graph = parse_project(project_root)
            gd = graph.to_dict()
            m = gd["meta"]
            print(f"[s-ide] {m['totalFiles']} nodes, {m['totalEdges']} edges "
                  f"({m['parseTime']} ms)")
            log.info("parse %s → %d nodes, %d edges (%s ms)",
                     project_root, m["totalFiles"], m["totalEdges"], m["parseTime"])
        except Exception as e:
            print(f"[s-ide] WARNING: failed to pre-parse {project_root}: {e}",
                  file=sys.stderr)
            log.exception("parse %s → FAILED: %s", project_root, e)

    url = f"http://localhost:{args.port}"
    log.info("server starting: %s", url)
    print(f"\n  S-IDE v0.6.0")
    print(f"  → {url}")
    if args.project:
        print(f"  → project: {args.project}")
    print()

    if not args.no_browser:
        def _open():
            time.sleep(0.8)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    serve(port=args.port)


def cmd_parse(args):
    _add_to_path()
    import json
    from parser.project_parser import parse_project
    root = os.path.abspath(args.project)
    if not os.path.isdir(root):
        print(f"[s-ide] ERROR: not a directory: {root}", file=sys.stderr); sys.exit(1)
    log.info("parse invoked: %s", root)
    print(f"[s-ide] Parsing: {root}")
    graph = parse_project(root)
    gd = graph.to_dict()
    out = args.out or os.path.join(root, ".nodegraph.json")
    with open(out, "w") as f: json.dump(gd, f, indent=2)
    m = gd["meta"]
    log.info("parse %s → %d nodes, %d edges (%s ms) → %s",
             root, m["totalFiles"], m["totalEdges"], m["parseTime"], out)
    print(f"[s-ide] {m['totalFiles']} nodes, {m['totalEdges']} edges ({m['parseTime']}ms) → {out}")


def cmd_test(args):
    _add_to_path()
    result = subprocess.run(
        [sys.executable, "test/test_suite.py"] + (["-v"] if args.verbose else []),
        cwd=ROOT
    )
    sys.exit(result.returncode)


def build_parser():
    p = argparse.ArgumentParser(
        prog="run.py",
        description="S-IDE launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 run.py                             start server, open browser
  python3 run.py --project ~/my-project      pre-load a project
  python3 run.py --port 7800 --no-browser    headless server
  python3 run.py parse ~/my-project          parse only
  python3 run.py test                        run test suite
""")
    p.add_argument("--port", type=int, default=7700, help="Server port (default: 7700)")
    p.add_argument("--no-browser", action="store_true", help="Don't open browser on start")
    p.add_argument("--project", metavar="DIR", help="Project directory to pre-load")

    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("parse", help="Parse a project and write .nodegraph.json")
    sp.add_argument("project", help="Project directory")
    sp.add_argument("--out", metavar="FILE", help="Output path (default: <project>/.nodegraph.json)")

    st = sub.add_parser("test", help="Run the test suite")
    st.add_argument("-v", "--verbose", action="store_true")

    return p


def main():
    p = build_parser()
    args = p.parse_args()
    dispatch = {
        "parse":   cmd_parse,
        "test":    cmd_test,
    }
    if args.cmd in dispatch:
        dispatch[args.cmd](args)
    else:
        cmd_serve(args)


if __name__ == "__main__":
    main()

# ── GPLv3 ─────────────────────────────────────────────────────────────────────
def gplv3_notice():
    print("S-IDE  Copyright (C) 2026  N0V4-N3XU5")
    print("This program comes with ABSOLUTELY NO WARRANTY.")
