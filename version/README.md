# version/

Project versioning — snapshot, restore, and apply updates via tarballs.

## version_manager.py

| Function | Description |
|---|---|
| `archive_version(root)` | Snapshot current state → `versions/v<ver>-<ts>.tar.gz`. Prunes beyond `keep` limit. |
| `apply_update(root, tarball, bump)` | Archive first, extract tarball, bump version. Returns `(new_version, archive_path)`. |
| `list_versions(root)` | List snapshots, newest first. |
| `compress_loose(root)` | Convert uncompressed snapshot dirs to `.tar.gz`. |

Tarball format: standard `.tar.gz` with project dir name as top-level prefix. Path traversal sanitised on extraction.

## Rollback

Every `apply_update` archives first. To roll back:

```bash
python main.py versions .                    # list snapshots
tar -xzf versions/v0.3.9-20260318.tar.gz --strip-components=1
```

## update.py

Picks the **highest-versioned** `s-ide-v*.tar.gz` from `~/Downloads/` (version parsed from filename, not modification time), archives current state, extracts update, bumps `side.project.json`, relaunches GUI.

```bash
python update.py                  # auto-pick from ~/Downloads/
python update.py --no-relaunch    # don't relaunch after update
python update.py --bump minor     # bump minor not patch
python update.py --yes            # skip confirmation
```
