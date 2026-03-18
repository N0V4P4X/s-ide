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
import time


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_dir(path: str) -> str:
    abs_path = os.path.abspath(path)
    if not os.path.isdir(abs_path):
        print(f"[s-ide] ERROR: not a directory: {path}", file=sys.stderr)
        sys.exit(1)
    return abs_path


def _fmt_size(n: int) -> str:
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


def cmd_self_check(args: argparse.Namespace) -> None:
    """
    Run a basic health check loop:
    - run unit tests
    - parse the project (emits .nodegraph.json)
    - report doc audit summary
    """
    import subprocess
    from parser.project_parser import parse_project

    root = _require_dir(args.project)

    started = time.time()
    result: dict = {"ok": True, "project": root, "checks": {}}

    # 1) Tests
    tests_path = os.path.join(os.path.dirname(__file__), "test", "test_suite.py")
    t0 = time.time()
    p = subprocess.run(
        [sys.executable, tests_path, "-q"],
        cwd=os.path.dirname(__file__),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=args.tests_timeout,
    )
    tests_ok = (p.returncode == 0)
    result["checks"]["tests"] = {
        "ok": tests_ok,
        "ms": int((time.time() - t0) * 1000),
        "returncode": p.returncode,
        "stderr_tail": (p.stderr or "")[-2000:],
    }
    if not tests_ok:
        result["ok"] = False

    # 2) Parse + doc audit (doc audit is embedded in meta)
    t1 = time.time()
    graph = parse_project(root)
    d = graph.to_dict()
    docs = (d.get("meta") or {}).get("docs") or {}
    docs_ok = bool(docs.get("healthy", True))
    result["checks"]["parse"] = {
        "ok": True,
        "ms": int((time.time() - t1) * 1000),
        "nodes": (d.get("meta") or {}).get("totalFiles"),
        "edges": (d.get("meta") or {}).get("totalEdges"),
        "nodegraph": os.path.join(root, ".nodegraph.json"),
    }
    result["checks"]["docs"] = {
        "ok": docs_ok,
        "summary": docs.get("summary") or {},
    }
    if args.strict_docs and not docs_ok:
        result["ok"] = False

    result["total_ms"] = int((time.time() - started) * 1000)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        ok = result["ok"]
        print(f"[s-ide] self-check: {'OK' if ok else 'FAIL'}  ({result['total_ms']} ms)")

        t = result["checks"]["tests"]
        print(f"  - tests: {'OK' if t['ok'] else 'FAIL'}  ({t['ms']} ms)")
        if not t["ok"] and t["stderr_tail"]:
            print("    stderr (tail):")
            for line in t["stderr_tail"].splitlines()[-20:]:
                print(f"      {line}")

        pchk = result["checks"]["parse"]
        print(f"  - parse: OK  ({pchk['ms']} ms)  → {pchk['nodegraph']}")

        dchk = result["checks"]["docs"]
        if dchk["ok"]:
            print("  - docs: OK")
        else:
            s = dchk.get("summary") or {}
            print(f"  - docs: WARN{' (strict)' if args.strict_docs else ''}")
            print(f"    missingReadmes={s.get('missingReadmes', 0)} staleReadmes={s.get('staleReadmes', 0)} emptyModules={s.get('emptyModules', 0)}")

    sys.exit(0 if result["ok"] else 1)


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
    sp.add_argument("--kind", choices=["tarball", "installer", "portable"],
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

    # self-check
    sp = sub.add_parser("self-check", help="Run tests + parse + doc audit")
    sp.add_argument("project", nargs="?", default=".",
                    help="Path to project directory (default: .)")
    sp.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON report")
    sp.add_argument("--tests-timeout", dest="tests_timeout", type=int, default=180,
                    help="Timeout (seconds) for running tests (default: 180)")
    sp.add_argument("--strict-docs", action="store_true",
                    help="Treat doc audit warnings as failures")

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
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
