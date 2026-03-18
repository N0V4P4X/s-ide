"""
gui/server.py
=============
Lightweight HTTP + WebSocket server that bridges the frontend (gui/app.html)
to the Python backend (parser, process manager, version manager).

Uses only stdlib: http.server, websockets is optional — falls back to
Server-Sent Events for process log streaming if websockets isn't installed.

Endpoints
---------
GET  /                          → serve app.html
GET  /api/projects              → list known projects
POST /api/projects/parse        → parse a project, return graph JSON
POST /api/projects/remove       → remove a project from the list
GET  /api/versions?root=...     → list archived versions
POST /api/versions/archive      → snapshot current project
POST /api/versions/update       → apply a tarball update
GET  /api/processes             → list running processes
POST /api/processes/start       → spawn a process
POST /api/processes/stop        → stop a process
POST /api/processes/suspend     → suspend a process
POST /api/processes/resume      → resume a process
GET  /api/processes/:id/logs    → get log buffer for a process
GET  /events                    → SSE stream for process events

Run
---
    python gui/server.py [port]        # default port 7700
"""

from __future__ import annotations
import json
import os
import sys
import time
import threading
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Any

_HERE     = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_HERE)
ROOT      = _ROOT_DIR   # keep for backward compat
for _p in (_ROOT_DIR, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from parser.project_parser import parse_project
from parser.project_config import load_project_config
from process.process_manager import ProcessManager
from version.version_manager import archive_version, apply_update, list_versions

# ── Persistence ───────────────────────────────────────────────────────────────

PROJECTS_FILE = os.path.join(ROOT, "projects.json")

def load_projects() -> list[dict]:
    """Load the known projects list from disk."""
    try:
        with open(PROJECTS_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def save_projects(projects: list[dict]) -> None:
    """Save the known projects list to disk."""
    with open(PROJECTS_FILE, "w") as f:
        json.dump(projects, f, indent=2)

# ── Global state ──────────────────────────────────────────────────────────────

proc_mgr = ProcessManager()
sse_clients: list[queue.Queue] = []   # one queue per connected SSE client
sse_lock = threading.Lock()

def broadcast_event(event_type: str, data: dict) -> None:
    """Push a Server-Sent Event to all connected clients."""
    msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    with sse_lock:
        dead = []
        for q in sse_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            sse_clients.remove(q)

# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        """Suppress default HTTP access log noise."""
        pass   # suppress default access log noise

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_GET(self):
        """Handle HTTP GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/" or path == "/app.html":
            # No web frontend — serve a minimal info page
            self._serve_info()
        elif path == "/api/projects":
            self._json(load_projects())
        elif path == "/api/versions":
            root = qs.get("root", [None])[0]
            if not root:
                self._error(400, "root query param required")
            else:
                self._json(list_versions(root))
        elif path == "/api/processes":
            self._json(proc_mgr.list())
        elif path.startswith("/api/processes/") and path.endswith("/logs"):
            proc_id = path.split("/")[-2]
            logs = proc_mgr.logs(proc_id)
            if logs is None:
                self._error(404, "process not found")
            else:
                self._json(logs)
        elif path == "/events":
            self._sse_stream()
        else:
            self._error(404, "not found")

    def do_POST(self):
        """Handle HTTP POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_body()

        if path == "/api/projects/parse":
            self._handle_parse(body)
        elif path == "/api/projects/remove":
            root = body.get("root")
            if not root:
                self._error(400, "root required")
            else:
                projects = [p for p in load_projects() if p["path"] != root]
                save_projects(projects)
                self._json({"ok": True})
        elif path == "/api/versions/archive":
            self._handle_archive(body)
        elif path == "/api/versions/update":
            self._handle_update(body)
        elif path == "/api/processes/start":
            self._handle_proc_start(body)
        elif path == "/api/processes/stop":
            pid = body.get("id")
            self._json({"ok": proc_mgr.stop(pid) if pid else False})
        elif path == "/api/processes/suspend":
            pid = body.get("id")
            self._json({"ok": proc_mgr.suspend(pid) if pid else False})
        elif path == "/api/processes/resume":
            pid = body.get("id")
            self._json({"ok": proc_mgr.resume(pid) if pid else False})
        elif path == "/api/build/clean":
            self._handle_build_clean(body)
        elif path == "/api/build/package":
            self._handle_build_package(body)
        else:
            self._error(404, "not found")

    # ── Route handlers ────────────────────────────────────────────────────────

    def _handle_parse(self, body: dict) -> None:
        root = body.get("root")
        if not root or not os.path.isdir(root):
            self._error(400, f"invalid project path: {root!r}")
            return
        try:
            graph = parse_project(root)
            gd = graph.to_dict()
            # Persist project
            projects = load_projects()
            name = os.path.basename(root)
            if not any(p["path"] == root for p in projects):
                projects.insert(0, {"path": root, "name": name})
                save_projects(projects)
            self._json(gd)
        except Exception as exc:
            self._error(500, str(exc))

    def _handle_archive(self, body: dict) -> None:
        root = body.get("root")
        if not root:
            self._error(400, "root required")
            return
        try:
            path = archive_version(root)
            self._json({"ok": True, "archivePath": path})
        except Exception as exc:
            self._error(500, str(exc))

    def _handle_update(self, body: dict) -> None:
        root = body.get("root")
        tarball = body.get("tarball")
        bump = body.get("bump", "patch")
        if not root or not tarball:
            self._error(400, "root and tarball required")
            return
        try:
            new_ver, arch = apply_update(root, tarball, bump)
            graph = parse_project(root)
            self._json({"ok": True, "newVersion": new_ver,
                        "archivePath": arch, "graph": graph.to_dict()})
        except Exception as exc:
            self._error(500, str(exc))

    def _handle_proc_start(self, body: dict) -> None:
        command = body.get("command", "").strip()
        name = body.get("name") or command.split()[0] if command else ""
        cwd = body.get("cwd") or ROOT
        if not command:
            self._error(400, "command required")
            return
        proc = proc_mgr.start(name=name, command=command, cwd=cwd)
        # Wire process events to SSE broadcast
        proc.on_stdout(lambda line: broadcast_event("stdout", {"id": proc.id, "line": line}))
        proc.on_stderr(lambda line: broadcast_event("stderr", {"id": proc.id, "line": line}))
        proc.on_exit(lambda code: broadcast_event("exit",   {"id": proc.id, "code": code}))
        broadcast_event("started", proc.info())
        self._json(proc.info())

    def _handle_build_clean(self, body: dict) -> None:
        root = body.get("root")
        if not root or not os.path.isdir(root):
            self._error(400, f"invalid project path: {root!r}"); return
        try:
            from build.cleaner import clean_project, CleanOptions
            tiers = body.get("tiers", ["cache", "logs"])
            dry_run = bool(body.get("dry_run", False))
            report = clean_project(root, CleanOptions(tiers=tiers, dry_run=dry_run))
            self._json({"ok": True, "removed": report.removed,
                        "freed_bytes": report.freed_bytes,
                        "errors": report.errors, "dry_run": dry_run})
        except Exception as exc:
            self._error(500, str(exc))

    def _handle_build_package(self, body: dict) -> None:
        root = body.get("root")
        if not root or not os.path.isdir(root):
            self._error(400, f"invalid project path: {root!r}"); return
        try:
            from build.packager import package_project, PackageOptions
            opts = PackageOptions(
                kind=body.get("kind", "tarball"),
                target_platform=body.get("platform", "auto"),
                minify=bool(body.get("minify", True)),
                clean=bool(body.get("clean", True)),
            )
            out_dir = body.get("out_dir") or os.path.join(root, "dist")
            result = package_project(root, out_dir, opts)
            self._json({"ok": True, "output": result.output_path,
                        "archive": result.archive_path,
                        "errors": result.errors,
                        "summary": result.summary()})
        except Exception as exc:
            self._error(500, str(exc))

    # ── SSE ───────────────────────────────────────────────────────────────────

    def _sse_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q: queue.Queue = queue.Queue(maxsize=200)
        with sse_lock:
            sse_clients.append(q)

        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
                except queue.Empty:
                    # Send a keepalive comment
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with sse_lock:
                if q in sse_clients:
                    sse_clients.remove(q)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _serve_info(self) -> None:
        body = json.dumps({
            "name": "S-IDE Python Server",
            "version": "0.2.0",
            "endpoints": [
                "GET  /api/projects",
                "POST /api/projects/parse     {root}",
                "POST /api/projects/remove    {root}",
                "GET  /api/versions?root=...",
                "POST /api/versions/archive   {root}",
                "POST /api/versions/update    {root, tarball, bump}",
                "GET  /api/processes",
                "POST /api/processes/start    {command, name, cwd}",
                "POST /api/processes/stop     {id}",
                "POST /api/processes/suspend  {id}",
                "POST /api/processes/resume   {id}",
                "GET  /api/processes/:id/logs",
                "GET  /events                 (SSE stream)",
            ]
        }, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, code: int, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: str, content_type: str) -> None:
        if not os.path.isfile(path):
            self._error(404, f"file not found: {path}")
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ── Entry point ───────────────────────────────────────────────────────────────

def run(port: int = 7700) -> None:
    """Start the HTTP server and block until KeyboardInterrupt."""
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"\n  ┌──────────────────────────────────┐")
    print(f"  │  S-IDE  Python  v0.2.0           │")
    print(f"  │  http://127.0.0.1:{port}          │")
    print(f"  └──────────────────────────────────┘\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[s-ide] shutting down...")
        proc_mgr.stop_all()
        server.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7700
    run(port)
