# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
build/packager.py
=================
Cross-platform application packager for S-IDE projects.

Produces three kinds of output:

  "portable"   — a self-contained directory you can zip and ship.
                 Python projects get a venv baked in.
                 No installation required on the target machine
                 (as long as the same OS/arch).

  "installer"  — a platform-specific installer:
                 • Linux  : .tar.gz + optional shell install script
                 • macOS  : .zip (future: .app bundle)
                 • Windows: .zip (future: NSIS/InnoSetup .exe)

  "tarball"    — a standard .tar.gz of the minified source tree,
                 suitable for distribution and for the S-IDE update system.

The packager uses only stdlib (shutil, zipfile, tarfile, subprocess).
PyInstaller is used for true single-binary output if available, but
is entirely optional — the packager degrades gracefully.

Usage
-----
    from build.packager import package_project, PackageOptions

    opts = PackageOptions(
        kind="portable",
        platform="linux",
        include_venv=True,
        minify=True,
    )
    result = package_project("/my/project", "/my/project/dist", opts)
    print(result.output_path)   # e.g. dist/myapp-v1.0-linux-portable/

Build pipeline
--------------
1. Clean (optional — honours CleanOptions tiers)
2. Minify source to a staging directory (optional)
3. Copy/install runtime dependencies
4. Write launcher scripts
5. Create the final archive or directory
6. Record a build manifest (dist/build-manifest.json)
"""

from __future__ import annotations
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from parser.workspace import find_workspace_root, load_workspace, resolve_project_deps


@dataclass
class PackageOptions:
    kind:           str   = "tarball"    # "portable" | "installer" | "tarball" | "webapp"
    target_platform: str  = "auto"       # "linux" | "macos" | "windows" | "auto"
    minify:         bool  = True         # minify source before packing
    clean:          bool  = True         # clean caches/logs before packing
    clean_tiers:    list[str] = field(default_factory=lambda: ["cache", "logs"])
    include_venv:   bool  = False        # bundle a Python venv (portable only)
    entry_point:    str   = ""           # e.g. "gui/app.py" or "main.py"
    extra_files:    list[str] = field(default_factory=list)  # extra paths to include
    strip_tests:    bool  = True         # exclude test/ directory
    strip_docs_src: bool  = False        # exclude *.md source files
    generate_webapp: bool = False        # generate a logic-view web app


@dataclass
class PackageResult:
    output_path:  str
    archive_path: str       # path to .tar.gz or .zip, "" if directory only
    manifest:     dict
    errors:       list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Return a human-readable summary of the build result."""
        size = _fmt_size(os.path.getsize(self.archive_path)
                         if self.archive_path and os.path.isfile(self.archive_path)
                         else _dir_size(self.output_path))
        return f"Built → {self.output_path}  ({size})"


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def _detect_platform() -> str:
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s == "windows":
        return "windows"
    return "linux"


def _load_config(root_dir: str) -> dict:
    cfg_path = os.path.join(root_dir, "side.project.json")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _copy_source(src: str, dst: str, opts: PackageOptions) -> None:
    """Copy project source to staging directory, respecting exclusions."""
    skip_dirs = {
        "versions", ".git", "__pycache__", ".mypy_cache", ".pytest_cache",
        "node_modules", ".venv", "venv", "env", "logs", "dist", "build",
        ".eggs", "*.egg-info",
    }
    if opts.strip_tests:
        skip_dirs.add("test")

    for item in os.listdir(src):
        src_path = os.path.join(src, item)
        dst_path = os.path.join(dst, item)

        # Skip hidden files/dirs except .nodegraph.json
        if item.startswith(".") and item != ".nodegraph.json":
            continue
        if item in skip_dirs:
            continue
        if opts.strip_docs_src and item.endswith(".md"):
            continue

        if os.path.isdir(src_path):
            shutil.copytree(
                src_path, dst_path,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "*.pyo",
                    ".mypy_cache", ".pytest_cache",
                ),
            )
        else:
            shutil.copy2(src_path, dst_path)


def _write_launcher(out_dir: str, entry: str, name: str, target_platform: str) -> str:
    """Write a platform-appropriate launcher script. Returns the script path."""
    if not entry:
        return ""

    if target_platform == "windows":
        script = os.path.join(out_dir, f"run_{name}.bat")
        with open(script, "w") as f:
            f.write(f"@echo off\npython {entry} %*\n")
    else:
        script = os.path.join(out_dir, f"run_{name}.sh")
        with open(script, "w") as f:
            f.write(
                f"#!/usr/bin/env bash\n"
                f'cd "$(dirname "$0")"\n'
                f"python3 {entry} \"$@\"\n"
            )
        os.chmod(script, 0o755)
    return script


def _make_tarball(src_dir: str, out_path: str, root_name: str) -> str:
    """Create a .tar.gz of src_dir with root_name as the top-level prefix."""
    with tarfile.open(out_path, "w:gz") as tar:
        tar.add(src_dir, arcname=root_name)
    return out_path


def _make_zip(src_dir: str, out_path: str, root_name: str) -> str:
    """Create a .zip of src_dir."""
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _, filenames in os.walk(src_dir):
            for f in filenames:
                full = os.path.join(dirpath, f)
                arc  = os.path.join(root_name, os.path.relpath(full, src_dir))
                zf.write(full, arc)
    return out_path


def package_project(
    root_dir: str,
    out_dir:  str,
    opts:     PackageOptions | None = None,
) -> PackageResult:
    """
    Package a project according to opts.

    root_dir : project source directory
    out_dir  : where to write the package (created if needed)
    opts     : PackageOptions (defaults to tarball, no minify, no clean)
    """
    opts       = opts or PackageOptions()
    root_dir   = os.path.abspath(root_dir)
    out_dir    = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    config   = _load_config(root_dir)
    name     = config.get("name") or os.path.basename(root_dir)
    version  = config.get("version") or "0.0.0"
    plat     = opts.target_platform if opts.target_platform != "auto" else _detect_platform()
    ts       = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    pkg_name = f"{name}-v{version}-{plat}"
    pkg_dir  = os.path.join(out_dir, pkg_name)
    errors   = []
    output_path = pkg_dir  # Default initialization

    # ── Step 1: Clean ─────────────────────────────────────────────────────────
    if opts.clean:
        try:
            from build.cleaner import clean_project, CleanOptions
            clean_report = clean_project(root_dir, CleanOptions(tiers=opts.clean_tiers))
            if clean_report.errors:
                errors.extend(clean_report.errors)
        except Exception as e:
            errors.append(f"Clean error: {e}")

    # ── Step 2: Stage source ──────────────────────────────────────────────────
    import tempfile
    with tempfile.TemporaryDirectory(prefix="side-build-") as staging:
        stage_src = os.path.join(staging, "src")
        os.makedirs(stage_src)

        if opts.minify:
            try:
                from build.minifier import minify_project, MinifyOptions
                min_opts = MinifyOptions(strip_docstrings=True, strip_comments=True)
                minify_project(root_dir, stage_src, min_opts)
            except Exception as e:
                errors.append(f"Minify error: {e}, falling back to copy")
                _copy_source(root_dir, stage_src, opts)
        else:
            _copy_source(root_dir, stage_src, opts)

        # ── Step 2.5: Web App (Optional) ──────────────────────────────────────
        if opts.generate_webapp or opts.kind == "webapp":
            try:
                from build.webapp_generator import generate_webapp
                webapp_dir = os.path.join(out_dir, f"{name}-v{version}-webapp")
                generate_webapp(root_dir, webapp_dir)
                if opts.kind == "webapp":
                    # If kind is webapp, we use this as the primary output
                    pkg_dir = webapp_dir
                    output_path = webapp_dir
            except Exception as e:
                errors.append(f"Web app error: {e}")

        # ── Step 3: Write launcher ────────────────────────────────────────────
        entry = opts.entry_point or config.get("run", {}).get("gui", "")
        if entry:
            _write_launcher(stage_src, entry, name, plat)

        # ── Step 4: Resolve deps and write manifest ────────────────────────────
        ws_root = find_workspace_root(root_dir)
        needed_deps = {}
        if ws_root:
            ws_manifest = load_workspace(ws_root)
            needed_deps = resolve_project_deps(root_dir, ws_manifest)
            # Write a project-specific requirements.txt for the build
            with open(os.path.join(stage_src, "requirements.txt"), "w") as f:
                f.write("# Generated by S-IDE packager from workspace manifest\n")
                for pkg, spec in sorted(needed_deps.items()):
                    f.write(f"{pkg}{spec}\n")
        else:
            # Fallback: copy project's requirements.txt if it exists
            local_req = os.path.join(root_dir, "requirements.txt")
            if os.path.isfile(local_req):
                shutil.copy2(local_req, os.path.join(stage_src, "requirements.txt"))

        manifest = {
            "name":      name,
            "version":   version,
            "built_at":  ts,
            "platform":  plat,
            "kind":      opts.kind,
            "minified":  opts.minify,
            "entry":     entry,
            "dependencies": needed_deps,
            "errors":    errors,
        }
        with open(os.path.join(stage_src, "build-manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)

        # ── Step 5: Pack ─────────────────────────────────────────────────────
        archive_path = ""
        if opts.kind == "tarball":
            archive_path = os.path.join(out_dir, f"{pkg_name}-{ts}.tar.gz")
            _make_tarball(stage_src, archive_path, pkg_name)
            output_path = archive_path

        elif opts.kind == "installer":
            if plat == "windows":
                archive_path = os.path.join(out_dir, f"{pkg_name}-{ts}.zip")
                _make_zip(stage_src, archive_path, pkg_name)
            else:
                archive_path = os.path.join(out_dir, f"{pkg_name}-{ts}.tar.gz")
                _make_tarball(stage_src, archive_path, pkg_name)
            output_path = archive_path

        elif opts.kind == "portable":
            if os.path.exists(pkg_dir):
                shutil.rmtree(pkg_dir)
            shutil.copytree(stage_src, pkg_dir)
            output_path = pkg_dir

            # Bundle venv if requested and we're on the target platform
            if opts.include_venv and plat == _detect_platform():
                venv_dir = os.path.join(pkg_dir, ".venv")
                try:
                    subprocess.run(
                        [sys.executable, "-m", "venv", venv_dir],
                        check=True, capture_output=True,
                    )
                    req = os.path.join(stage_src, "requirements.txt")
                    if os.path.isfile(req):
                        pip = os.path.join(venv_dir, "bin", "pip") if plat != "windows" \
                              else os.path.join(venv_dir, "Scripts", "pip.exe")
                        subprocess.run(
                            [pip, "install", "-r", req],
                            check=True, capture_output=True,
                        )
                except Exception as e:
                    errors.append(f"venv creation failed: {e}")
        elif opts.kind == "webapp":
            # Already handled in Step 2.5
            pass
        else:
            raise ValueError(f"Unknown package kind: {opts.kind!r}")

    # ── Step 6: Record manifest in out_dir ────────────────────────────────────
    manifest_path = os.path.join(out_dir, "build-manifest.json")
    history = []
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path) as f:
                existing = json.load(f)
                history = existing.get("history", [])
        except Exception:
            pass
    history.insert(0, {**manifest, "output": output_path, "archive": archive_path})
    history = history[:20]   # keep last 20 builds
    with open(manifest_path, "w") as f:
        json.dump({"history": history}, f, indent=2)

    return PackageResult(
        output_path=output_path,
        archive_path=archive_path,
        manifest=manifest,
        errors=errors,
    )

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
