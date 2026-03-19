# gui/

Tkinter desktop application for S-IDE.

## Modules

| File | Purpose |
|---|---|
| `app.py` | Main window, canvas, topbar, inspector, all core logic |
| `panels.py` | Bottom panel tab content builders |
| `markdown.py` | Markdown→Tk Text renderer (importable without a display) |
| `editor.py` | Syntax-highlighted source editor (Toplevel window) |
| `state.py` | Session persistence to `~/.s-ide-state.json` |
| `log.py` | Rotating file log + in-memory ring buffer |
| `server.py` | Optional HTTP+SSE bridge for headless/remote use |

---

## Layout

```
┌──────────────────────────────────────────────────────┐
│ TOPBAR  logo · project · [PY JS TS CFG DOCS] · search │
├─────────────────────────────────────────────┬─────────┤
│  CANVAS                                     │INSPECTOR│
│  • node cards (one per source file)         │         │
│  • bezier import edges                      │         │
│  • dashed doc→source links                  │         │
│  • live @timed metric overlays              │         │
│  • minimap (bottom right)                   │         │
├─────────────────────────────────────────────┴─────────┤
│  ▓ PanedWindow sash (drag to resize)                  │
├───────────────────────────────────────────────────────┤
│  Projects │ AI Chat │ Plan │ Playground │ Terminal     │
└───────────────────────────────────────────────────────┘
```

## Canvas interactions

| Action | Result |
|---|---|
| Double-click node | Open in editor |
| Right-click node | Context menu: Open, Inspect, Ask AI |
| Single-click node | Select + open inspector |
| Click+drag node | Move node |
| Click+drag canvas | Pan |
| Scroll | Zoom |
| `F` key | Fit all nodes in view |
| `Esc` | Clear selection |

## Filter chips

Multi-select. Clicking a chip toggles that file type on/off. **Docs** and **Config** are hidden by default — click them to reveal. **ALL** clears all filters and restores defaults.

The canvas only re-renders when selection or zoom changes — hit boxes are cached per redraw and rebuilt incrementally on drag.

## app.py structure

`app.py` contains `SIDE_App(tk.Tk)`. Methods group by prefix:

| Prefix | Concern |
|---|---|
| `_build_*` | Widget construction (called once at startup) |
| `_draw_*` | Canvas rendering (called on each redraw) |
| `_canvas_*` | Canvas event handlers |
| `_inspect_*` | Inspector panel content |
| `_bp_*` | Bottom panel tab switching/collapse |
| `_ai_*` | AI chat state |
| `_term_*` | Terminal state |
| `_load_*`, `_apply_*` | Project loading pipeline |
| `_run_*`, `_refresh_run_*` | Run scripts panel |

## state.py — session persistence

`SessionState` reads/writes `~/.s-ide-state.json`. Survives restarts and updates.

```python
from gui.state import SessionState
s = SessionState()
s.add_project("myapp", "/path/to/myapp")
s.set_ai_history("/path/to/myapp", messages)
s.set_viewport("/path/to/myapp", x=100, y=200, z=1.5)
s.bottom_tab = "ai"
s.save()
```

## markdown.py — no-display import

`ai_append_markdown(app, text)` and `_insert_inline(widget, text)` are safe to import without a display. Used in both the GUI (AI tab) and the test suite.

Supports: `# headers`, `**bold**`, `*italic*`, `` `inline code` ``, ```` ```code blocks``` ````, `- bullets`, `1. numbered lists`, `---` rules.

## editor.py

`EditorWindow(master, filepath, ...)` — a `Toplevel` window per file.

- Token-based syntax highlighting for Python, JS/TS, JSON, shell
- Line numbers gutter, current-line highlight
- Find/replace bar (Ctrl+F)
- Read-only by default; toggle with Edit button
- Save (Ctrl+S) triggers project re-parse
- "Ask AI" button when Ollama is available

## server.py

Optional REST+SSE server on port 7700. See `server_README.md` for endpoints.

```bash
python gui/server.py
python main.py run . server
```

## teams_canvas.py — AI Teams workflow designer

`TeamsCanvasMixin` added to `SIDE_App`. Click **⚡ TEAMS** in the topbar to
switch the canvas from project graph view to Teams workflow designer.

### Agent cards

Drag to reposition. Double-click to edit name/model. Right-click for context menu.
Role colours: Architect=blue, Implementer=green, Reviewer=amber, Tester=cyan,
Optimizer=purple, Documentarian=pink.

### Interactions

| Action | Result |
|---|---|
| Click **+** | Add agent |
| Double-click card | Edit name/model |
| Right-click card | Configure / Remove |
| Drag card | Reposition |

### Running a workflow

1. Type task in the **Plan** tab
2. Click **▶ Run Workflow**
3. Watch progress in AI Chat tab
4. Approve in the result dialog → **Apply to Project**

### State (TeamsCanvasMixin)

- `_tw_nodes` — list of `{id, role, model, name, x, y}` dicts
- `_tw_edges` — sequence edges `{source, target}`
- `canvas_mode` — `"graph"` or `"teams"`
- `_tw_running` — True while a TeamSession is executing
