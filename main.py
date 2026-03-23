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

versions <project-dir>
    List archived snapshots for a project.

archive  <project-dir>
    Create a new snapshot of the project's current state.

update   <project-dir> <tarball.tar.gz> [--bump major|minor|patch]
    Archive current state, then extract the tarball over the project
    and bump its version. Default bump level: patch.

run      <project-dir> <script-name>
    Look up script-name in side.project.json → run → and stream
    its output to the terminal.

compress <project-dir>
    Convert any loose snapshot directories in versions/ to .tar.gz.

Examples
--------
    python main.py parse ./my-project
    python main.py parse ./my-project --out /tmp/graph.json
    python main.py versions ./my-project
    python main.py archive ./my-project
    python main.py update ./my-project new-release.tar.gz --bump minor
    python main.py run ./my-project dev
    python main.py compress ./my-project
"""

from __future__ import annotations
import argparse
import json
import os
import sys


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_dir(path: str) -> str:
    abs_path = os.path.abspath(path)
    if not os.path.isdir(abs_path):
        print(f"[s-ide] ERROR: not a directory: {path}", file=sys.stderr)
        sys.exit(1)
    return abs_path


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ── Sub-command handlers ──────────────────────────────────────────────────────

def cmd_parse(args: argparse.Namespace) -> None:
    """Parse a project directory and write graph JSON."""
    from parser.project_parser import parse_project

    root = _require_dir(args.project)
    print(f"[s-ide] Parsing: {root}")

    graph = parse_project(root)
    d = graph.to_dict()

    out_path = args.out or os.path.join(root, ".nodegraph.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)

    m = d["meta"]
    print(f"[s-ide] {m['totalFiles']} nodes, {m['totalEdges']} edges "
          f"({m['parseTime']} ms) → {out_path}")

    if not m["docs"]["healthy"]:
        s = m["docs"]["summary"]
        print(f"[s-ide] Doc warnings: {s['missingReadmes']} missing README, "
              f"{s['staleReadmes']} stale, {s['emptyModules']} empty modules")


def cmd_versions(args: argparse.Namespace) -> None:
    """List archived version snapshots for a project."""
    from version.version_manager import list_versions

    root = _require_dir(args.project)
    versions = list_versions(root)

    if not versions:
        print("[s-ide] No versions found.")
        return

    print(f"[s-ide] {len(versions)} version(s) in '{root}':\n")
    for v in versions:
        print(f"  {v['name']:<40} {_fmt_size(v['size']):>10}   {v['modified'][:19]}")


def cmd_archive(args: argparse.Namespace) -> None:
    """Create a compressed snapshot of the current project state."""
    from version.version_manager import archive_version

    root = _require_dir(args.project)
    print(f"[s-ide] Archiving: {root}")
    path = archive_version(root)
    print(f"[s-ide] Snapshot created: {path}")


def cmd_update(args: argparse.Namespace) -> None:
    """Apply a tarball update to a project, archiving first."""
    from version.version_manager import apply_update

    root = _require_dir(args.project)
    tarball = os.path.abspath(args.tarball)
    if not os.path.isfile(tarball):
        print(f"[s-ide] ERROR: tarball not found: {tarball}", file=sys.stderr)
        sys.exit(1)

    bump = args.bump or "patch"
    print(f"[s-ide] Updating '{root}' from '{tarball}' (bump: {bump})")

    new_version, archive_path = apply_update(root, tarball, bump)
    print(f"[s-ide] Archived previous state → {archive_path}")
    print(f"[s-ide] Update applied. New version: {new_version}")


def cmd_run(args: argparse.Namespace) -> None:
    """Run a named script from the project's side.project.json."""
    import subprocess
    from parser.project_config import load_project_config

    root = _require_dir(args.project)
    config = load_project_config(root)
    scripts = config.get("run") or {}

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

    # Run directly in the terminal (stdout/stderr pass through)
    result = subprocess.run(command, shell=True, cwd=root)
    sys.exit(result.returncode)


def cmd_compress(args: argparse.Namespace) -> None:
    """Compress any loose version directories to .tar.gz."""
    from version.version_manager import compress_loose

    root = _require_dir(args.project)
    results = compress_loose(root)

    if not results:
        print("[s-ide] Nothing to compress.")
        return

    for r in results:
        if "error" in r:
            print(f"  [FAIL] {r['name']}: {r['error']}")
        else:
            print(f"  [OK]   {r['name']} → {r['tarball']}")


def cmd_self_check(args: argparse.Namespace) -> None:
    """Run all self-checks: tests, parse, doc audit."""
    import subprocess
    from parser.project_parser import parse_project

    root = _require_dir(args.project)
    print(f"[s-ide] Self-checking: {root}")

    # 1. Run tests
    print("\n[s-ide] 1/3: Running unit tests...")
    test_res = subprocess.run([sys.executable, "test/test_suite.py", "-q"], cwd=root)
    if test_res.returncode != 0:
        print(f"[s-ide] FAILED: Unit tests exited with code {test_res.returncode}")
        sys.exit(1)
    print("[s-ide] OK: Tests passed.")

    # 2. Parse & Graph metadata
    print("\n[s-ide] 2/3: Parsing graph & auditing docs...")
    graph = parse_project(root)
    d = graph.to_dict()
    m = d["meta"]
    
    # Write graph for persistence
    out_path = os.path.join(root, ".nodegraph.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    print(f"[s-ide] OK: Parsing complete ({m['parseTime']} ms) → {out_path}")

    # 3. Doc audit report
    print("\n[s-ide] 3/3: Document report:")
    h = m["docs"]["healthy"]
    s = m["docs"]["summary"]
    print(f"  Missing READMEs: {s['missingReadmes']}")
    print(f"  Stale READMEs:   {s['staleReadmes']}")
    print(f"  Empty modules:   {s['emptyModules']}")
    
    if not h:
        print("[s-ide] Doc health issues detected.")
        if args.strict_docs:
            print("[s-ide] FAILED: Strict-docs requirement not met.")
            sys.exit(1)
        else:
            print("[s-ide] OK: Continuing (non-fatal docs).")
    else:
        print("[s-ide] OK: All docs are healthy.")

    print("\n[s-ide] SUMMARY: ALL CHECKS PASSED.")


def cmd_build(args: argparse.Namespace) -> None:
    """Run the full build pipeline: clean, minify, package."""
    from build.packager import package_project, PackageOptions

    root = _require_dir(args.project)
    out  = os.path.join(root, "dist")
    opts = PackageOptions(
        kind=args.kind,
        target_platform=args.platform,
        minify=not args.no_minify,
        clean=not args.no_clean,
        clean_tiers=["cache", "logs"],
        entry_point=args.entry or "",
        strip_tests=not args.keep_tests,
        generate_webapp=args.webapp,
    )
    print(f"[s-ide] Building '{root}' → {out}  (kind={args.kind})")
    result = package_project(root, out, opts)
    if result.errors:
        for e in result.errors:
            print(f"  [WARN] {e}")
    print(f"[s-ide] {result.summary()}")
    if args.bump:
        from version.version_manager import apply_update
        # bump version only — no tarball to apply, just config write
        from parser.project_config import load_project_config, save_project_config, bump_version
        cfg = load_project_config(root)
        new_ver = bump_version(cfg.get("version", "0.0.0"), args.bump)
        cfg["version"] = new_ver
        save_project_config(root, cfg)
        print(f"[s-ide] Version bumped to {new_ver}")


def cmd_serve(args) -> None:
    """Launch the S-IDE Web Port and API bridge."""
    from gui.server import run as start_server
    root = _require_dir(args.project)
    # Ensure graph exists
    graph_path = os.path.join(root, ".nodegraph.json")
    if not os.path.exists(graph_path):
        print(f"[s-ide] No graph found at {graph_path}. Parsing first...")
        from parser.project_parser import parse_project
        parse_project(root_dir=root)
    
    # Change to project root so bridge sees the right files
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

    # parse
    sp = sub.add_parser("parse", help="Parse a project and emit graph JSON")
    sp.add_argument("project", help="Path to project directory")
    sp.add_argument("--out", metavar="FILE", help="Output JSON path (default: <project>/.nodegraph.json)")

    # versions
    sp = sub.add_parser("versions", help="List archived versions")
    sp.add_argument("project", help="Path to project directory")

    # archive
    sp = sub.add_parser("archive", help="Snapshot current project state")
    sp.add_argument("project", help="Path to project directory")

    # update
    sp = sub.add_parser("update", help="Apply a tarball update to a project")
    sp.add_argument("project", help="Path to project directory")
    sp.add_argument("tarball", help="Path to .tar.gz update file")
    sp.add_argument("--bump", choices=["major", "minor", "patch"], default="patch",
                    help="Version component to increment (default: patch)")

    # run
    sp = sub.add_parser("run", help="Run a script from side.project.json")
    sp.add_argument("project", help="Path to project directory")
    sp.add_argument("script", help="Script name (key in side.project.json → run)")

    # compress
    sp = sub.add_parser("compress", help="Compress loose version directories to .tar.gz")
    sp.add_argument("project", help="Path to project directory")

    # build
    sp = sub.add_parser("build", help="Clean, minify, and package a project")
    sp.add_argument("project", help="Path to project directory")
    sp.add_argument("--kind", choices=["tarball", "installer", "portable", "webapp"],
                    default="tarball", help="Package type (default: tarball)")
    sp.add_argument("--platform", default="auto",
                    choices=["auto", "linux", "macos", "windows"],
                    help="Target platform (default: auto-detect)")
    sp.add_argument("--no-minify",  action="store_true", help="Skip minification")
    sp.add_argument("--no-clean",   action="store_true", help="Skip pre-build clean")
    sp.add_argument("--keep-tests", action="store_true", help="Include test/ directory")
    sp.add_argument("--entry",      default="", metavar="FILE",
                    help="Entry point script (e.g. gui/app.py)")
    sp.add_argument("--bump", choices=["major", "minor", "patch"], default=None,
                    help="Bump version after build")
    sp.add_argument("--webapp", action="store_true", help="Generate logic-view web app in dist/")

    # serve
    sp = sub.add_parser("serve", help="Launch the high-fidelity web interface")
    sp.add_argument("project", help="Path to project directory")
    sp.add_argument("--port", type=int, default=8080, help="Bridge port (default: 8080)")

    return p


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """S-IDE CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "parse":    cmd_parse,
        "versions": cmd_versions,
        "archive":  cmd_archive,
        "update":   cmd_update,
        "run":      cmd_run,
        "compress": cmd_compress,
        "build":    cmd_build,
        "self-check": cmd_self_check,
        "serve":    cmd_serve,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()

# ── GPLv3 interactive notice ──────────────────────────────────────────────────

_GPLv3_WARRANTY = (
    "THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY\n"
    "APPLICABLE LAW. EXCEPT WHEN OTHERWISE STATED IN WRITING THE COPYRIGHT\n"
    'HOLDERS AND/OR OTHER PARTIES PROVIDE THE PROGRAM \"AS IS\" WITHOUT\n'
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
