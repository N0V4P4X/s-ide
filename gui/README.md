# gui/

Tkinter desktop GUI and optional HTTP server for S-IDE.

## app.py — Desktop GUI

Main application window. Requires Python's stdlib `tkinter` (included with most Python installs).

```bash
python gui/app.py
```

### Layout

```
┌──────────────────────────────────────────────────────────────┐
│ TOPBAR  logo · project · filter chips · search · LOG · BUILD │
├──────────┬─────────────────────────────────────┬────────────┤
│ SIDEBAR  │  CANVAS (infinite pan + zoom)        │ INSPECTOR  │
│ Projects │  • node cards (one per file)         │ (slides in │
│ RUN      │  • bezier dependency edges           │  on click) │
│ VERSIONS │  • minimap overlay                   │            │
├──────────┴─────────────────────────────────────┴────────────┤
│ STATUSBAR  language stats · parse time · slowest stage       │
└──────────────────────────────────────────────────────────────┘
```

Floating panels (each is a separate `Toplevel`):
- **PROC** — spawn commands, live stdout/stderr, CPU% + RSS per process
- **LOG** — tail `logs/s-ide.log` with auto-refresh
- **BUILD** — clean/minify/package options, parse timing chart, build history

### Canvas hit-testing

Hover and click are handled by a single `<Motion>` / `<ButtonPress-1>` canvas binding — no per-redraw `tag_bind` accumulation. Node detection is point-in-bounding-box; edge detection is point-to-bezier-segment proximity (≤8px threshold).

### Keyboard shortcuts

| Key | Action |
|---|---|
| `F` | Fit all nodes in view |
| `Esc` | Clear selection |
| Scroll | Zoom in/out |
| Left-drag canvas | Pan |
| Left-drag node | Move node |

## log.py — Logging

Writes to `logs/s-ide.log` (rotating, 2 MB × 5 backups) and an in-memory ring buffer (last 1000 lines). The LOG topbar button opens a live panel.

Log path is always printed to stderr on startup:
```
[s-ide] log → /home/you/DevOps/s-ide-py/logs/s-ide.log
```

## server.py — HTTP+SSE Bridge

Optional stdlib-only HTTP server for headless or remote operation.

```bash
python gui/server.py          # default port 7700
python main.py run . server   # via run system
```

Exposes REST endpoints for parse, projects, processes, versions, build, and an SSE stream for live process output. See `gui/server_README.md` for full endpoint documentation.
