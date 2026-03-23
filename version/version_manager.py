# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
version/version_manager.py
==========================
Handles all project versioning operations using the stdlib tarfile module.

Operations
----------
archive_version(root)
    Snapshot the current project into versions/<v>-<timestamp>.tar.gz
    Respects the ignore patterns from side.project.json.
    Prunes old snapshots beyond the configured keep limit.

apply_update(root, tarball_path, bump_part)
    1. Archive current state first (safety net)
    2. Extract the tarball over the project directory
    3. Bump the version in side.project.json
    Returns (new_version, archive_path)

list_versions(root)
    Return metadata for all snapshots in the versions/ directory,
    sorted newest-first.

compress_loose(root)
    Find any uncompressed snapshot directories in versions/ and
    convert them to .tar.gz, removing the originals.

The tarball format is standard .tar.gz with the project directory
name as the top-level prefix, matching what most tools expect.
Path traversal is sanitised on extract (no ../ escapes).
"""

from __future__ import annotations
import os
import tarfile
import fnmatch
from datetime import datetime, timezone
from pathlib import Path

from parser.project_config import load_project_config, save_project_config, bump_version, init_project_config


# ── Ignore patterns applied during archive ────────────────────────────────────
# These supplement the project's own ignore list and prevent the archive
# from including version snapshots of version snapshots.
_ARCHIVE_EXCLUDE_ALWAYS = {
    "__pycache__", ".git", ".venv", "venv", "env",
    "node_modules", ".mypy_cache", ".pytest_cache",
    ".cache", "dist", "build",
}


def _make_filter(versions_dir_name: str, extra_ignore: list[str]):
    """
    Return a tarfile filter function that excludes:
      - the versions directory itself
      - always-excluded dirs
      - glob patterns from extra_ignore
    """
    def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        # tarinfo.name is like 'projectname/subdir/file'
        parts = Path(tarinfo.name).parts
        # Check each path component
        for part in parts:
            if part in _ARCHIVE_EXCLUDE_ALWAYS:
                return None
            if part == versions_dir_name:
                return None
            for pattern in extra_ignore:
                if fnmatch.fnmatch(part, pattern):
                    return None
        return tarinfo
    return _filter


def archive_version(root_dir: str) -> str:
    """
    Create a compressed snapshot of root_dir in its versions/ subdirectory.

    Returns the path of the created .tar.gz file.
    """
    root_dir = os.path.abspath(root_dir)
    # init_project_config creates side.project.json if it doesn't exist yet,
    # ensuring fresh projects get versioned correctly from their first archive
    config = init_project_config(root_dir)
    versions_subdir = (config.get("versions") or {}).get("dir", "versions")
    keep = int((config.get("versions") or {}).get("keep", 20))
    version = config.get("version") or "0.0.0"
    extra_ignore = list(config.get("ignore") or [])

    versions_dir = os.path.join(root_dir, versions_subdir)
    os.makedirs(versions_dir, exist_ok=True)

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    archive_name = f"v{version}-{ts}.tar.gz"
    archive_path = os.path.join(versions_dir, archive_name)

    tar_filter = _make_filter(versions_subdir, extra_ignore)

    with tarfile.open(archive_path, "w:gz") as tar:
        # Add all files from root_dir, filtered
        project_name = os.path.basename(root_dir)
        for entry in sorted(os.listdir(root_dir)):
            full = os.path.join(root_dir, entry)
            arcname = os.path.join(project_name, entry)
            tar.add(full, arcname=arcname, recursive=True, filter=tar_filter)

    _prune_old_versions(versions_dir, keep)
    return archive_path


def apply_update(root_dir: str, tarball_path: str, bump_part: str = "patch") -> tuple[str, str]:
    """
    Apply a tarball update to root_dir:
      1. Archive current state
      2. Extract tarball over project (stripping top-level dir prefix)
      3. Bump version in side.project.json

    Returns (new_version, archive_path).
    Raises ValueError if tarball_path does not exist or is not a valid tar.
    """
    root_dir = os.path.abspath(root_dir)
    tarball_path = os.path.abspath(tarball_path)

    if not os.path.isfile(tarball_path):
        raise ValueError(f"Tarball not found: {tarball_path}")

    # 1. Safety archive
    archive_path = archive_version(root_dir)

    # 2. Extract (strip top-level directory component, sanitise paths)
    _extract_tarball(tarball_path, root_dir)

    # 3. Bump version
    config = load_project_config(root_dir)
    new_version = bump_version(config.get("version", "0.0.0"), bump_part)
    config["version"] = new_version
    save_project_config(root_dir, config)

    return new_version, archive_path


def list_versions(root_dir: str) -> list[dict]:
    """
    Return metadata for all snapshots in the versions/ directory,
    sorted newest-first.

    Each entry: {name, type, size, modified, path}
    """
    root_dir = os.path.abspath(root_dir)
    # init_project_config creates side.project.json if it doesn't exist yet,
    # ensuring fresh projects get versioned correctly from their first archive
    config = init_project_config(root_dir)
    versions_subdir = (config.get("versions") or {}).get("dir", "versions")
    versions_dir = os.path.join(root_dir, versions_subdir)

    if not os.path.isdir(versions_dir):
        return []

    results = []
    for entry in os.listdir(versions_dir):
        full = os.path.join(versions_dir, entry)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        is_tarball = entry.endswith(".tar.gz") or entry.endswith(".tgz")
        is_dir = os.path.isdir(full)
        if not (is_tarball or is_dir):
            continue
        results.append({
            "name":     entry,
            "type":     "tarball" if is_tarball else "directory",
            "size":     stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "path":     full,
        })

    results.sort(key=lambda x: x["modified"], reverse=True)
    return results


def compress_loose(root_dir: str) -> list[dict]:
    """
    Find uncompressed snapshot directories in versions/ and convert them
    to .tar.gz archives, removing the originals.

    Returns list of {name, tarball, error} dicts.
    """
    root_dir = os.path.abspath(root_dir)
    # init_project_config creates side.project.json if it doesn't exist yet,
    # ensuring fresh projects get versioned correctly from their first archive
    config = init_project_config(root_dir)
    versions_subdir = (config.get("versions") or {}).get("dir", "versions")
    versions_dir = os.path.join(root_dir, versions_subdir)

    if not os.path.isdir(versions_dir):
        return []

    results = []
    for entry in os.listdir(versions_dir):
        full = os.path.join(versions_dir, entry)
        if not os.path.isdir(full):
            continue
        tarball_path = full + ".tar.gz"
        try:
            with tarfile.open(tarball_path, "w:gz") as tar:
                tar.add(full, arcname=entry)
            # Remove the loose directory after successful compression
            import shutil
            shutil.rmtree(full)
            results.append({"name": entry, "tarball": tarball_path})
        except Exception as exc:
            results.append({"name": entry, "error": str(exc)})

    return results


# ── Internal helpers ──────────────────────────────────────────────────────────

def _extract_tarball(tarball_path: str, dest_dir: str) -> None:
    """
    Extract a .tar.gz into dest_dir, stripping the top-level directory
    prefix that most project tarballs include (e.g. 'myproject/src/...'
    becomes 'src/...' in dest_dir).

    Sanitises member paths to prevent path-traversal attacks.
    """
    with tarfile.open(tarball_path, "r:gz") as tar:
        members = tar.getmembers()

        # Detect common top-level prefix (e.g. all members start with 'myproject/')
        prefix = _detect_prefix(members)

        for member in members:
            # Strip prefix
            rel = member.name
            if prefix and rel.startswith(prefix):
                rel = rel[len(prefix):]
            # Sanitise: no absolute paths, no ../ traversal
            rel = rel.lstrip("/\\")
            rel = os.path.normpath(rel)
            if rel.startswith(".."):
                continue   # skip anything that would escape dest_dir
            if not rel or rel == ".":
                continue

            dest_path = os.path.join(dest_dir, rel)

            if member.isdir():
                os.makedirs(dest_path, exist_ok=True)
            elif member.isfile():
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with tar.extractfile(member) as src, open(dest_path, "wb") as dst:
                    dst.write(src.read())


def _detect_prefix(members: list[tarfile.TarInfo]) -> str:
    """
    If all members share a common first path component that contains no '.'
    (i.e. looks like a project directory name, not a file), return it with
    the trailing slash. Otherwise return ''.
    """
    prefixes = set()
    for m in members:
        parts = m.name.replace("\\", "/").split("/")
        if parts:
            prefixes.add(parts[0])
    if len(prefixes) == 1:
        candidate = prefixes.pop()
        # Only strip if it looks like a directory name (no extension)
        if "." not in candidate:
            return candidate + "/"
    return ""


def _prune_old_versions(versions_dir: str, keep: int) -> None:
    """Delete oldest snapshots beyond the keep limit."""
    if keep <= 0:
        return
    entries = []
    for name in os.listdir(versions_dir):
        full = os.path.join(versions_dir, name)
        try:
            entries.append((os.stat(full).st_mtime, full))
        except OSError:
            pass

    entries.sort(reverse=True)   # newest first
    for _, path in entries[keep:]:
        try:
            if os.path.isdir(path):
                import shutil
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError:
            pass

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
