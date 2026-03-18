"""
build/
======
S-IDE build pipeline — clean, minify, and package projects.

Modules
-------
cleaner.py   — remove dev artifacts, caches, logs
minifier.py  — strip comments/docstrings, combine modules
packager.py  — produce portable directories, tarballs, or platform installers

Quick usage
-----------
    from build.cleaner  import clean_project, CleanOptions
    from build.minifier import minify_project, MinifyOptions
    from build.packager import package_project, PackageOptions

    # Full build pipeline
    clean_project(root, CleanOptions(tiers=["cache", "logs"]))
    minify_project(root, root + "/dist/src", MinifyOptions())
    result = package_project(root, root + "/dist",
                              PackageOptions(kind="tarball", minify=True))
    print(result.summary())
"""

from .cleaner  import clean_project,   CleanOptions,   CleanReport
from .minifier import minify_project,  MinifyOptions,  MinifyReport,  minify_file
from .packager import package_project, PackageOptions, PackageResult

from .sandbox import SandboxRun, SandboxOptions, list_sandbox_logs
