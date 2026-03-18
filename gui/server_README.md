# gui/server.py — HTTP + SSE Bridge

Lightweight stdlib-only HTTP server exposing the S-IDE backend as a REST+SSE API.
Useful for headless operation, remote dashboards, or building alternative frontends.

```bash
python gui/server.py          # port 7700
python gui/server.py 8080     # custom port
python main.py run . server   # via run system
```

See full endpoint docs in the module docstring (`gui/server.py` top comment).
