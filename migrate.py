#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5
"""
migrate.py -- S-IDE v0.5.x to v0.6.0 migration & cleanup

Removes the Tkinter frontend, dead prototype code, stale build artifacts,
malformed extraction dirs, and temp clones. Leaves only what runs.

    python migrate.py             # dry run (shows what would change)
    python migrate.py --apply     # apply changes
    python migrate.py --apply --keep-tkinter  # backup gui/*.py as *.bak
"""
import argparse, json, os, shutil, sys

ROOT = os.path.dirname(os.path.abspath(__file__))


# ── Removal manifest ──────────────────────────────────────────────────────────
#
# Each entry is (relative_path, reason).
# Dirs are removed with rmtree. Files with os.remove.
# Malformed/glob paths use a custom handler below.

REMOVE_FILES = [
    # Tkinter frontend -- superseded by gui/app.html + gui/server.py
    ("gui/app.py",               "Tkinter main window"),
    ("gui/app_legacy.py",        "Tkinter legacy copy"),
    ("gui/ai_mixin.py",          "Tkinter mixin"),
    ("gui/canvas_mixin.py",      "Tkinter mixin"),
    ("gui/dialogs_mixin.py",     "Tkinter mixin"),
    ("gui/inspector_mixin.py",   "Tkinter mixin"),
    ("gui/panels.py",            "Tkinter panels"),
    ("gui/teams_canvas.py",      "Tkinter teams canvas"),
    ("gui/editor.py",            "Tkinter editor"),
    ("gui/markdown.py",          "Tkinter markdown renderer"),
    ("gui/log.py",               "Tkinter log widget"),
    ("gui/state.py",             "Tkinter state -- logic moved into server.py"),
    ("gui/__init__.py",          "Tkinter package init -- will be recreated"),
    ("gui/server.py.bak",        "server.py backup"),
    ("gui/server_README.md",     "stale README for old server"),
    # Old bridge -- superseded by gui/server.py
    ("api/bridge.py",            "old API bridge"),
    # Dead prototype -- imports from models.ollama_client which doesn't exist
    ("agent_loop.py",            "pre-S-IDE prototype, broken imports"),
    ("tools/file_ops.py",        "pre-S-IDE prototype"),
    # Stale build duplicate -- parser/pseudocode.py is authoritative
    ("build/pseudocode_gen.py",  "duplicate of parser/pseudocode.py"),
    # Stale run log
    ("check.json",               "stale self-check output"),
]

REMOVE_DIRS = [
    # Old webapp outputs -- gui/app.html replaces these
    ("dist",                     "old build outputs (tarballs + webapp dirs)"),
    # Session scratch space -- not source
    (".side",                    "agent session scratch space"),
    # Empty nested project dir (bad extraction artifact)
    ("s-ide-py",                 "empty nested project dir from bad extraction"),
    # Temp clone from optimization run
    ("projects/calculator-opt",  "temp clone from optimizer run"),
    # Malformed dir names from bad tar extraction (literal brace chars)
    ("{monitor,build,logs}",     "malformed extraction artifact"),
    ("{parser",                  "malformed extraction artifact"),
    # tools/ only contained file_ops.py (removed above); dir will be empty
    ("tools",                    "empty after file_ops.py removal"),
]

# gui/web was the old static webapp directory
REMOVE_DIRS_OPTIONAL = [
    ("gui/web",                  "old static webapp dir"),
]

SIDE_PROJECT_PATCH = {
    "version": "0.6.0",
    "description": "S-IDE -- project graph editor + AI assistant, JS frontend",
    "run": {
        "server":      "python run.py",
        "test":        "python test/test_suite.py",
        "self-update": "python update.py",
        "build":       "python main.py build . --kind tarball",
        "clean":       "python main.py build . --no-minify --kind tarball",
    },
}

GUI_INIT = (
    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
    "# Copyright (C) 2026 N0V4-N3XU5\n"
    "# gui/ -- JS frontend (gui/app.html) served by gui/server.py\n"
)

AGENT_NOTES_RESET = (
    "# Agent Notes\n\n"
    "This file is maintained by the AI assistant via the `write_agent_note` tool.\n"
    "Notes record decisions, findings, and context that persist across sessions.\n\n"
    "---\n"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def p(path):
    return os.path.join(ROOT, path)


def announce(tag, path_or_label, dry, reason=""):
    COLORS = {
        "REMOVE": "\033[31m", "WIPE":   "\033[31m",
        "CREATE": "\033[32m", "UPDATE": "\033[33m",
        "BACKUP": "\033[35m", "SKIP":   "\033[90m",
    }
    reset  = "\033[0m"
    prefix = "  [DRY] " if dry else "  "
    color  = COLORS.get(tag, "")
    label  = os.path.relpath(path_or_label, ROOT) if os.path.isabs(path_or_label) else path_or_label
    suffix = f"  \033[90m({reason})\033[0m" if reason else ""
    print(f"{prefix}{color}{tag:7}{reset}  {label}{suffix}")


def remove_file(full, dry, errors):
    if not dry:
        try:
            os.remove(full)
        except Exception as e:
            errors.append(f"Could not remove {full}: {e}")


def remove_dir(full, dry, errors):
    if not dry:
        try:
            shutil.rmtree(full)
        except Exception as e:
            errors.append(f"Could not remove dir {full}: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry=True, keep_tkinter=False):
    mode = "DRY RUN" if dry else "APPLYING"
    print(f"\nS-IDE migration v0.5.x -> v0.6.0  ({mode})\n")

    # Guard: new files must already be in place
    for required in ("gui/server.py", "gui/app.html"):
        if not os.path.isfile(p(required)):
            print(f"  ERROR: {required} not found.")
            print(f"         Place gui/server.py and gui/app.html before running migrate.")
            sys.exit(1)

    errors = []
    total_removed = 0

    # ── Files ─────────────────────────────────────────────────────────────────
    for rel_path, reason in REMOVE_FILES:
        full = p(rel_path)
        if not os.path.exists(full):
            announce("SKIP", rel_path, dry)
            continue
        is_tkinter = "gui/" in rel_path and rel_path.endswith(".py")
        if keep_tkinter and is_tkinter:
            announce("BACKUP", rel_path, dry, reason)
            if not dry:
                shutil.copy2(full, full + ".bak")
        announce("REMOVE", full, dry, reason)
        remove_file(full, dry, errors)
        total_removed += 1

    # ── Dirs ──────────────────────────────────────────────────────────────────
    all_dirs = REMOVE_DIRS + REMOVE_DIRS_OPTIONAL
    for rel_path, reason in all_dirs:
        full = p(rel_path)
        if not os.path.exists(full):
            announce("SKIP", rel_path, dry)
            continue
        announce("REMOVE", full, dry, reason)
        remove_dir(full, dry, errors)
        total_removed += 1

    # ── Recreate gui/__init__.py ──────────────────────────────────────────────
    gui_init = p("gui/__init__.py")
    announce("CREATE", gui_init, dry, "clean package stub")
    if not dry:
        with open(gui_init, "w") as f:
            f.write(GUI_INIT)

    # ── Wipe AGENT_NOTES.md content ───────────────────────────────────────────
    notes = p("AGENT_NOTES.md")
    if os.path.isfile(notes):
        current = open(notes).read()
        if len(current) > len(AGENT_NOTES_RESET):
            announce("WIPE", notes, dry, "reset 6x duplicate 'ready to ship' stamps")
            if not dry:
                with open(notes, "w") as f:
                    f.write(AGENT_NOTES_RESET)

    # ── Patch side.project.json ───────────────────────────────────────────────
    cfg_path = p("side.project.json")
    if os.path.isfile(cfg_path):
        with open(cfg_path) as f:
            cfg = json.load(f)
        changed = any(cfg.get(k) != v for k, v in SIDE_PROJECT_PATCH.items())
        if changed:
            announce("UPDATE", cfg_path, dry, "version 0.6.0 + run scripts")
            if not dry:
                cfg.update(SIDE_PROJECT_PATCH)
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f, indent=2)
        else:
            announce("SKIP", cfg_path, dry)

    # ── Patch main.py serve command ───────────────────────────────────────────
    main_py = p("main.py")
    if os.path.isfile(main_py):
        src = open(main_py).read()
        OLD = "from api.bridge import start_server"
        NEW = "from gui.server import run as start_server"
        if OLD in src:
            announce("UPDATE", main_py, dry, "serve now uses gui.server")
            if not dry:
                with open(main_py, "w") as f:
                    f.write(src.replace(OLD, NEW))
        else:
            announce("SKIP", main_py, dry)

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    for e in errors:
        print(f"  \033[31mError:\033[0m {e}")

    if dry:
        print(f"  Dry run: {total_removed} paths would be removed.")
        print("  Run with --apply to make changes.\n")
    else:
        removed = total_removed - len(errors)
        print(f"  Done. {removed} paths removed, {len(errors)} errors.\n")
        print("  Start S-IDE:  python run.py")
        print("  Open:         http://localhost:7700\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="S-IDE v0.5 -> v0.6 migration & cleanup")
    ap.add_argument("--apply", action="store_true",
                    help="Apply changes (default: dry run)")
    ap.add_argument("--keep-tkinter", action="store_true",
                    help="Backup gui/*.py as *.bak instead of deleting")
    args = ap.parse_args()
    run(dry=not args.apply, keep_tkinter=args.keep_tkinter)
