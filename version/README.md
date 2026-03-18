# version/

Project versioning — snapshot, restore, and update via tarballs.

## version_manager.py

### Operations

| Function | Description |
|---|---|
| `archive_version(root)` | Snapshot current project → `versions/v<ver>-<ts>.tar.gz`. Prunes old archives beyond `keep` limit. |
| `apply_update(root, tarball, bump)` | Archive current state, extract tarball over project, bump version in `side.project.json`. Returns `(new_version, archive_path)`. |
| `list_versions(root)` | List all snapshots in `versions/`, sorted newest-first. |
| `compress_loose(root)` | Convert any uncompressed snapshot directories to `.tar.gz`. |

### Tarball format

Standard `.tar.gz` with the project directory name as top-level prefix (`myproject/src/...`).
Path traversal is sanitised on extraction (no `../` escapes).

### Rollback

Every `apply_update` archives first. To roll back:

```bash
python main.py versions .               # list snapshots
tar -xzf versions/v0.1.3-20250101T120000.tar.gz --strip-components=1
```
