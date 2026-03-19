#!/usr/bin/env python3
"""
update.py
=========
S-IDE self-update script.

Finds the newest s-ide-py-*.tar.gz in a watch directory (default:
~/Downloads/), archives the current installation, extracts the tarball
over it, bumps the version, and optionally re-launches the app.

Usage
-----
    # One-shot: pick up the newest tarball automatically
    python update.py

    # Explicit tarball path
    python update.py /path/to/s-ide-py-v0.2.0.tar.gz

    # Custom watch directory
    python update.py --watch /tmp/releases

    # Choose version bump level (default: patch)
    python update.py --bump minor

    # Don't re-launch the GUI after update
    python update.py --no-relaunch

    # Via the CLI (from the s-ide-py directory):
    python main.py run . self-update

How it works
------------
1. Locate the newest matching tarball in the watch directory.
2. Confirm with the user (shows current version → new tarball name).
3. Archive current state to versions/ (safety net — always reversible).
4. Extract the tarball over the current installation directory.
5. Bump side.project.json version (patch by default).
6. Print the archive path so you can roll back if needed.
7. Optionally exec() the new gui/app.py in-place (same process, clean state).

Rollback
--------
If anything goes wrong, your previous state is in versions/:
    python main.py versions .
    # Then manually extract the snapshot you want
"""

from __future__ import annotations
import argparse
import fnmatch
import glob
import os
import sys
import time
import tarfile

# ── Make sure we can import from the s-ide-py package root ───────────────────
SELF_DIR = os.path.dirname(os.path.abspath(__file__))
if SELF_DIR not in sys.path:
    sys.path.insert(0, SELF_DIR)

# Default watch directory — where Claude drops new tarballs
DEFAULT_WATCH = os.path.expanduser("~/Downloads")

# Pattern that identifies s-ide update tarballs
# Match both  s-ide-py-v0.1.5.tar.gz  and  s-ide-v0.1.5.tar.gz
TARBALL_GLOB = "s-ide*.tar.gz"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _colour(text: str, code: str) -> str:
    """ANSI colour if stdout is a tty."""
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text

def green(t):  # noqa
    """Wrap text in ANSI green if stdout is a tty."""
    return _colour(t, "32")
def yellow(t):  # noqa
    """Wrap text in ANSI yellow if stdout is a tty."""
    return _colour(t, "33")
def red(t):  # noqa
    """Wrap text in ANSI red if stdout is a tty."""
    return _colour(t, "31")
def bold(t):  # noqa
    """Wrap text in ANSI bold if stdout is a tty."""
    return _colour(t, "1")
def dim(t):  # noqa
    """Wrap text in ANSI dim if stdout is a tty."""
    return _colour(t, "2")


def _fmt_size(b: int) -> str:
    """Wrap text in ANSI green if stdout is a tty."""
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def _version_key(path: str):
    """
    Extract a sortable version tuple from a tarball name.
    s-ide-v0.4.0.tar.gz → (0, 4, 0, 0)   (0 = no patch suffix)
    s-ide-v0.4.0p1.tar.gz → (0, 4, 0, 1)
    Falls back to mtime for unrecognised names.
    """
    import re
    name = os.path.basename(path)
    m = re.search(r'v?(\d+)\.(\d+)\.(\d+)(?:p(\d+))?', name)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4) or 0))
    # fallback: use mtime as a large tuple so version-named files rank higher
    return (-1, -1, -1, int(os.path.getmtime(path)))


def find_newest_tarball(watch_dir: str) -> str | None:
    """
    Return the absolute path of the highest-versioned s-ide*.tar.gz in
    watch_dir, or None if none found.  Version is parsed from the filename;
    unversioned files sort below any versioned file.
    """
    pattern = os.path.join(watch_dir, TARBALL_GLOB)
    matches = [p for p in glob.glob(pattern) if os.path.getsize(p) > 0]
    if not matches:
        return None
    matches.sort(key=_version_key)
    return matches[-1]


def get_current_version(project_dir: str) -> str:
    """Read the current version from side.project.json, or '?' if missing."""
    try:
        from parser.project_config import load_project_config
        cfg = load_project_config(project_dir)
        return cfg.get("version") or "?"
    except Exception:
        return "?"


def confirm(prompt: str) -> bool:
    """Ask a yes/no question. Returns True for yes."""
    try:
        answer = input(prompt + " [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


# ── Core update logic ─────────────────────────────────────────────────────────

def run_update(
    tarball_path: str,
    project_dir: str,
    bump_part: str = "patch",
    relaunch: bool = True,
    yes: bool = False,
    dry_run: bool = False,
    self_check: bool = False,
    self_check_strict_docs: bool = False,
) -> int:
    """
    Perform the full update sequence.
    Returns 0 on success, 1 on failure.
    """
    tarball_path = os.path.abspath(tarball_path)
    project_dir  = os.path.abspath(project_dir)

    # ── Pre-flight checks ─────────────────────────────────────────────────────
    if not os.path.isfile(tarball_path):
        print(red(f"[update] Tarball not found: {tarball_path}"))
        return 1
    if not tarfile.is_tarfile(tarball_path):
        print(red(f"[update] Not a valid tar archive: {tarball_path}"))
        return 1

    if not os.path.isdir(project_dir):
        print(red(f"[update] Project directory not found: {project_dir}"))
        return 1

    tarball_name = os.path.basename(tarball_path)
    tarball_size = _fmt_size(os.path.getsize(tarball_path))
    current_ver  = get_current_version(project_dir)

    # ── Confirmation ─────────────────────────────────────────────────────────
    print()
    print(bold("  S-IDE Self-Update"))
    print(dim("  " + "─" * 44))
    print(f"  Current version : {yellow(current_ver)}")
    print(f"  Tarball         : {green(tarball_name)}  ({tarball_size})")
    print(f"  Install dir     : {dim(project_dir)}")
    print(f"  Version bump    : {bump_part}")
    if dry_run:
        print(f"  Mode            : {yellow('dry-run')}")
    if self_check:
        print(f"  Post-check      : {green('self-check')}{' + strict-docs' if self_check_strict_docs else ''}")
    print()

    if not yes and not confirm("  Apply this update?"):
        print(dim("  Update cancelled."))
        return 0

    print()

    if dry_run:
        print(dim("  Dry-run: no files will be modified."))
        print(dim("  Would archive current state, extract tarball, bump version, and optionally relaunch."))
        return 0

    # ── Step 1: Archive current state ────────────────────────────────────────
    print(f"  {dim('1/3')} Archiving current state…", end=" ", flush=True)
    try:
        from version.version_manager import archive_version
        archive_path = archive_version(project_dir)
        archive_name = os.path.basename(archive_path)
        print(green("✓") + f"  {dim(archive_name)}")
    except Exception as exc:
        print(red("✗"))
        print(red(f"      Archive failed: {exc}"))
        print(red("      Aborting — your files are untouched."))
        return 1

    # ── Step 2: Extract tarball ───────────────────────────────────────────────
    print(f"  {dim('2/3')} Extracting update…", end=" ", flush=True)
    try:
        from version.version_manager import _extract_tarball
        _extract_tarball(tarball_path, project_dir)
        print(green("✓"))
    except Exception as exc:
        print(red("✗"))
        print(red(f"      Extraction failed: {exc}"))
        print(yellow(f"      Your previous state was archived to: {archive_path}"))
        print(yellow("      To roll back, extract that archive manually."))
        return 1

    # ── Step 3: Bump version ──────────────────────────────────────────────────
    print(f"  {dim('3/3')} Updating version…", end=" ", flush=True)
    try:
        from parser.project_config import load_project_config, save_project_config, bump_version
        config      = load_project_config(project_dir)
        new_version = bump_version(config.get("version", "0.0.0"), bump_part)
        config["version"] = new_version
        save_project_config(project_dir, config)
        print(green("✓") + f"  {yellow(current_ver)} → {green(new_version)}")
    except Exception as exc:
        print(red("✗"))
        print(red(f"      Version bump failed: {exc}"))
        # Non-fatal — code is already updated

    # ── Optional self-check ───────────────────────────────────────────────────
    if self_check:
        print()
        print("  Running self-check…", end=" ", flush=True)
        try:
            import subprocess
            cmd = [sys.executable, os.path.join(project_dir, "main.py"), "self-check", project_dir]
            if self_check_strict_docs:
                cmd.append("--strict-docs")
            p = subprocess.run(cmd, cwd=project_dir)
            if p.returncode == 0:
                print(green("✓"))
            else:
                print(red("✗"))
                print(red(f"  Self-check failed (exit {p.returncode})."))
                print(yellow(f"  Your previous state was archived to: {archive_path}"))
                print(yellow("  Fix forward or roll back by extracting that archive manually."))
                return 1
        except Exception as exc:
            print(red("✗"))
            print(red(f"  Self-check errored: {exc}"))
            print(yellow(f"  Your previous state was archived to: {archive_path}"))
            return 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print(green("  Update complete!"))
    print(f"  Rollback archive : {dim(archive_path)}")
    print()

    # ── Optional re-launch ────────────────────────────────────────────────────
    if relaunch:
        gui_path = os.path.join(project_dir, "gui", "app.py")
        if os.path.isfile(gui_path):
            print(f"  Relaunching GUI…  {dim(gui_path)}")
            print()
            time.sleep(0.4)
            # exec() replaces this process — clean state, no import cache issues
            os.execv(sys.executable, [sys.executable, gui_path])
            # If exec fails we fall through to return 0
        else:
            print(yellow(f"  GUI not found at {gui_path} — skipping relaunch."))
            print(f"  Start manually: {bold('python gui/app.py')}")

    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Build the update CLI argument parser."""
    p = argparse.ArgumentParser(
        prog="update",
        description="S-IDE self-update — applies a tarball from ~/Downloads/ (or a given path).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python update.py                         # pick newest s-ide-py-*.tar.gz from ~/Downloads/
  python update.py ~/Downloads/s-ide-py-v0.2.0.tar.gz
  python update.py --watch /tmp/releases --bump minor
  python update.py --no-relaunch --yes     # non-interactive (CI/scripting)
        """,
    )
    p.add_argument(
        "tarball",
        nargs="?",
        default=None,
        help="Path to update tarball. Omit to auto-pick newest from --watch dir.",
    )
    p.add_argument(
        "--watch", "-w",
        default=DEFAULT_WATCH,
        metavar="DIR",
        help=f"Directory to scan for tarballs (default: {DEFAULT_WATCH})",
    )
    p.add_argument(
        "--bump", "-b",
        choices=["major", "minor", "patch"],
        default="patch",
        help="Version component to increment after update (default: patch)",
    )
    p.add_argument(
        "--no-relaunch",
        dest="relaunch",
        action="store_false",
        default=True,
        help="Don't re-launch the GUI after updating",
    )
    p.add_argument(
        "--yes", "-y",
        action="store_true",
        default=False,
        help="Skip confirmation prompt (for scripting)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would happen, but don't modify anything",
    )
    p.add_argument(
        "--self-check",
        action="store_true",
        default=False,
        help="Run `python main.py self-check` after applying the update",
    )
    p.add_argument(
        "--self-check-strict-docs",
        action="store_true",
        default=False,
        help="When running --self-check, treat doc warnings as failures",
    )
    p.add_argument(
        "--dir", "-d",
        default=SELF_DIR,
        metavar="PROJECT_DIR",
        help=f"S-IDE installation directory (default: {SELF_DIR})",
    )
    return p


def main() -> None:
    """Update CLI entry point."""
    args = build_parser().parse_args()

    # Resolve tarball path
    if args.tarball:
        tarball = os.path.abspath(os.path.expanduser(args.tarball))
        if not os.path.isfile(tarball):
            print(red(f"[update] File not found: {tarball}"))
            sys.exit(1)
    else:
        watch = os.path.abspath(os.path.expanduser(args.watch))
        if not os.path.isdir(watch):
            print(red(f"[update] Watch directory not found: {watch}"))
            print(f"         Use --watch to specify a different directory.")
            sys.exit(1)

        tarball = find_newest_tarball(watch)
        if not tarball:
            print(yellow(f"[update] No {TARBALL_GLOB} found in {watch}"))
            print(f"         Drop an s-ide-py-*.tar.gz there, or pass the path directly.")
            sys.exit(1)

    sys.exit(run_update(
        tarball_path=tarball,
        project_dir=args.dir,
        bump_part=args.bump,
        relaunch=args.relaunch,
        yes=args.yes,
        dry_run=args.dry_run,
        self_check=args.self_check,
        self_check_strict_docs=args.self_check_strict_docs,
    ))


if __name__ == "__main__":
    main()
