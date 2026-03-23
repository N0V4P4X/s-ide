#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5
"""
run.py — S-IDE launcher
=======================
The single entry point for everything.

    python run.py                          # start server, open browser
    python run.py --port 7800             # custom port
    python run.py --no-browser            # server only
    python run.py --project ~/my-project  # pre-load a project
    python run.py new my-app ~/projects   # scaffold + open new project
    python run.py parse ~/my-project      # parse only, no server
    python run.py test                    # run test suite
"""

import argparse, os, subprocess, sys, threading, time, webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))

def _add_to_path():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

def cmd_serve(args):
    _add_to_path()
    from gui.server import run as serve, _load_projects, _save_projects

    # Pre-load project if specified
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

    url = f"http://localhost:{args.port}"
    print(f"\n  S-IDE v0.6.0")
    print(f"  → {url}")
    if args.project:
        print(f"  → project: {args.project}")
    print()

    if not args.no_browser:
        # Open browser after a short delay to let the server start
        def _open():
            time.sleep(0.8)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    serve(port=args.port)


def cmd_new(args):
    _add_to_path()
    from ai.manager import scaffold_new_project

    parent = os.path.abspath(args.parent or ".")
    name   = args.name
    desc   = args.description or ""

    print(f"[s-ide] Scaffolding: {name!r} in {parent}")
    project_root = scaffold_new_project(parent, name, desc)
    print(f"[s-ide] Created: {project_root}")

    # Parse it immediately
    from parser.project_parser import parse_project
    import json
    graph = parse_project(project_root)
    gd = graph.to_dict()
    out = os.path.join(project_root, ".nodegraph.json")
    with open(out, "w") as f: json.dump(gd, f, indent=2)
    print(f"[s-ide] Parsed: {gd['meta']['totalFiles']} nodes → {out}")

    # Register it
    from gui.server import _load_projects, _save_projects
    projects = _load_projects()
    if not any(p["path"] == project_root for p in projects):
        projects.insert(0, {"path": project_root, "name": name})
        _save_projects(projects)

    if not args.no_open:
        # Launch server with the new project pre-loaded
        argv = [sys.executable, __file__, "--project", project_root]
        if args.port: argv += ["--port", str(args.port)]
        if args.no_browser: argv.append("--no-browser")
        os.execv(sys.executable, argv)


def cmd_parse(args):
    _add_to_path()
    import json
    from parser.project_parser import parse_project
    root = os.path.abspath(args.project)
    if not os.path.isdir(root):
        print(f"[s-ide] ERROR: not a directory: {root}", file=sys.stderr); sys.exit(1)
    print(f"[s-ide] Parsing: {root}")
    graph = parse_project(root)
    gd = graph.to_dict()
    out = args.out or os.path.join(root, ".nodegraph.json")
    with open(out, "w") as f: json.dump(gd, f, indent=2)
    m = gd["meta"]
    print(f"[s-ide] {m['totalFiles']} nodes, {m['totalEdges']} edges ({m['parseTime']}ms) → {out}")


def cmd_test(args):
    _add_to_path()
    result = subprocess.run(
        [sys.executable, "test/test_suite.py"] + (["-v"] if args.verbose else []),
        cwd=ROOT
    )
    sys.exit(result.returncode)


def cmd_migrate(args):
    _add_to_path()
    # Import and run migrate.py
    migrate_path = os.path.join(ROOT, "migrate.py")
    if not os.path.isfile(migrate_path):
        print("[s-ide] migrate.py not found", file=sys.stderr); sys.exit(1)
    apply_flag = ["--apply"] if args.apply else []
    keep_flag  = ["--keep-tkinter"] if getattr(args, "keep_tkinter", False) else []
    result = subprocess.run([sys.executable, migrate_path] + apply_flag + keep_flag)
    sys.exit(result.returncode)


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        prog="run.py",
        description="S-IDE launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python run.py                             start server, open browser
  python run.py --project ~/my-project      pre-load a project
  python run.py --port 7800 --no-browser    headless server
  python run.py new my-app ~/projects       scaffold new project + open
  python run.py parse ~/my-project          parse only
  python run.py test                        run test suite
  python run.py migrate                     dry-run migration check
  python run.py migrate --apply             apply v0.5→v0.6 migration
""")
    p.add_argument("--port", type=int, default=7700, help="Server port (default: 7700)")
    p.add_argument("--no-browser", action="store_true", help="Don't open browser on start")
    p.add_argument("--project", metavar="DIR", help="Project directory to pre-load")

    sub = p.add_subparsers(dest="cmd")

    # new
    sn = sub.add_parser("new", help="Scaffold a new project and open it")
    sn.add_argument("name", help="Project name")
    sn.add_argument("parent", nargs="?", default=".", help="Parent directory (default: cwd)")
    sn.add_argument("--description", "-d", default="", help="Project description")
    sn.add_argument("--port", type=int, default=7700)
    sn.add_argument("--no-browser", action="store_true")
    sn.add_argument("--no-open", action="store_true", help="Don't launch server after scaffold")

    # parse
    sp = sub.add_parser("parse", help="Parse a project and write .nodegraph.json")
    sp.add_argument("project", help="Project directory")
    sp.add_argument("--out", metavar="FILE", help="Output path (default: <project>/.nodegraph.json)")

    # test
    st = sub.add_parser("test", help="Run the test suite")
    st.add_argument("-v", "--verbose", action="store_true")

    # migrate
    sm = sub.add_parser("migrate", help="Run the v0.5→v0.6 migration script")
    sm.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    sm.add_argument("--keep-tkinter", action="store_true", help="Backup Tkinter files instead of deleting")

    return p


def main():
    p = build_parser()
    args = p.parse_args()
    dispatch = {
        "new":     cmd_new,
        "parse":   cmd_parse,
        "test":    cmd_test,
        "migrate": cmd_migrate,
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
