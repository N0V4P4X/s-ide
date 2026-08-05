# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
gui/server.py  v0.6.0
=====================
S-IDE HTTP server — the single Python process the JS frontend talks to.
Tkinter is gone. This is the entire backend.

Start:  python gui/server.py [port]   (default 7700)
        python main.py serve ./my-project

GET  /                           → serve gui/app.html
GET  /api/projects               → [{path,name}]
POST /api/projects/open  {root}  → parse+register, return graph JSON
POST /api/projects/parse {root}  → re-parse, return graph JSON
POST /api/projects/remove{root}  → remove from list
GET  /api/file?root=&path=       → {path,content,lines}
POST /api/file/write {root,path,content}
GET  /api/file/list?root=&ext=&subdir=
GET  /api/file/defs?root=&path=
POST /api/git   {root,command,...}
GET  /api/processes
POST /api/processes/start  {root,command,name}
POST /api/processes/stop   {id}
POST /api/processes/suspend{id}
POST /api/processes/resume {id}
GET  /api/processes/:id/logs
GET  /events                     → SSE process events
GET  /api/state?root=
POST /api/state {root,key,value}
GET  /api/nodes?root=&category=&lang=  → SideNode-shaped JSON for MythOS bridge
POST /api/xp  {root, node_id, xp, skills}  → record quest completion XP
GET  /api/infra                            → web-infra graph nodes as SideNode JSON
GET  /api/metrics?root=&path=
"""

from __future__ import annotations
import json, os, sys, threading, queue, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Any

_HERE     = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_HERE)
for _p in (_ROOT_DIR, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from parser.project_parser import parse_project
from process.process_manager import ProcessManager

# ── Persistence ───────────────────────────────────────────────────────────────
PROJECTS_FILE = os.path.join(_ROOT_DIR, "projects.json")
_STATE_PATH   = os.path.join(os.path.expanduser("~"), ".s-ide-state.json")

def _load_projects():
    try:
        with open(PROJECTS_FILE) as f: return json.load(f)
    except Exception: return []

def _save_projects(p):
    with open(PROJECTS_FILE, "w") as f: json.dump(p, f, indent=2)

def _load_state():
    d = {"projects":[],"terminal_history":{},"viewport":{},
         "bottom_panel":{"height":260,"tab":"projects"},"editor_sessions":{}}
    try:
        if os.path.isfile(_STATE_PATH):
            raw = json.load(open(_STATE_PATH, encoding="utf-8"))
            d.update(raw)
    except Exception: pass
    return d

def _save_state(s):
    try:
        tmp = _STATE_PATH + ".tmp"
        with open(tmp,"w",encoding="utf-8") as f: json.dump(s,f,indent=2)
        os.replace(tmp, _STATE_PATH)
    except Exception: pass

def _load_graph(root):
    p = os.path.join(root, ".nodegraph.json")
    if os.path.isfile(p):
        try: return json.load(open(p, encoding="utf-8"))
        except Exception: pass
    return None

# ── Global state ──────────────────────────────────────────────────────────────
proc_mgr   = ProcessManager()
sse_clients = []
sse_lock   = threading.Lock()

def _broadcast(etype, data):
    msg = f"event: {etype}\ndata: {json.dumps(data)}\n\n"
    with sse_lock:
        dead = []
        for q in sse_clients:
            try: q.put_nowait(msg)
            except queue.Full: dead.append(q)
        for q in dead: sse_clients.remove(q)

# ── HTTP Handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        p   = urlparse(self.path)
        path = p.path.rstrip("/") or "/"
        qs  = parse_qs(p.query)

        if path in ("/", "/app.html"):    self._html(); return
        if path.startswith("/static/"):   self._static(path[8:]); return
        if path.startswith("/api/processes/") and path.endswith("/logs"):
            self._proc_logs(path.split("/")[-2]); return

        {
            "/api/projects": self._get_projects,
            "/api/file":     lambda: self._get_file(qs),
            "/api/file/list":lambda: self._get_file_list(qs),
            "/api/file/defs":lambda: self._get_file_defs(qs),
            "/api/metrics":  lambda: self._get_metrics(qs),
            "/api/nodes":    lambda: self._get_nodes(qs),
            "/api/infra":    self._get_infra,
            "/api/processes":self._get_processes,
            "/api/state":    lambda: self._get_state(qs),
            "/events":       self._sse,
        }.get(path, lambda: self._error(404, path))()

    def do_POST(self):
        body = self._body()
        path = urlparse(self.path).path.rstrip("/")
        {
            "/api/projects/open":    lambda: self._open(body),
            "/api/projects/parse":   lambda: self._open(body),
            "/api/projects/remove":  lambda: self._remove(body),
            "/api/file/write":       lambda: self._write_file(body),
            "/api/git":              lambda: self._git(body),
            "/api/processes/start":  lambda: self._proc_start(body),
            "/api/processes/stop":   lambda: self._json({"ok":proc_mgr.stop(body.get("id"))}),
            "/api/processes/suspend":lambda: self._json({"ok":proc_mgr.suspend(body.get("id"))}),
            "/api/processes/resume": lambda: self._json({"ok":proc_mgr.resume(body.get("id"))}),
            "/api/state":            lambda: self._save_state_key(body),
            "/api/xp":               lambda: self._record_xp(body),
        }.get(path, lambda: self._error(404, path))()

    # ── HTML / static ─────────────────────────────────────────────────────────
    def _html(self):
        f = os.path.join(_HERE, "app.html")
        if not os.path.isfile(f): self._error(404,"app.html not found"); return
        body = open(f,"rb").read()
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",len(body))
        self._cors(); self.end_headers(); self.wfile.write(body)

    def _static(self, rel):
        full = os.path.join(_HERE,"static",rel)
        if not os.path.isfile(full): self._error(404,rel); return
        ct = {".js":"application/javascript",".css":"text/css",
              ".svg":"image/svg+xml",".png":"image/png"}.get(
              os.path.splitext(rel)[1],"application/octet-stream")
        body = open(full,"rb").read()
        self.send_response(200); self.send_header("Content-Type",ct)
        self.send_header("Content-Length",len(body))
        self._cors(); self.end_headers(); self.wfile.write(body)

    # ── Projects ──────────────────────────────────────────────────────────────
    def _get_projects(self): self._json(_load_projects())

    def _open(self, body):
        root = body.get("root","").strip()
        if not root or not os.path.isdir(root):
            self._error(400,f"invalid path: {root!r}"); return
        try:
            graph = parse_project(root)
            gd = graph.to_dict()
            ps = _load_projects()
            nm = os.path.basename(root)
            if not any(p["path"]==root for p in ps):
                ps.insert(0,{"path":root,"name":nm}); _save_projects(ps)
            self._json(gd)
        except Exception as e: self._error(500,str(e))

    def _remove(self, body):
        root = body.get("root")
        if not root: self._error(400,"root required"); return
        _save_projects([p for p in _load_projects() if p["path"]!=root])
        self._json({"ok":True})

    # ── Files ─────────────────────────────────────────────────────────────────
    def _get_file(self, qs):
        root = (qs.get("root") or [""])[0]
        path = (qs.get("path") or [""])[0].lstrip("/")
        if not root or not path: self._error(400,"root and path required"); return
        full = os.path.join(root, path)
        if not os.path.isfile(full): self._error(404,path); return
        try:
            c = open(full,encoding="utf-8",errors="replace").read()
            self._json({"path":path,"content":c,"lines":c.count("\n")+1})
        except Exception as e: self._error(500,str(e))

    def _get_file_list(self, qs):
        root   = (qs.get("root") or [""])[0]
        ext    = (qs.get("ext") or [""])[0]
        subdir = (qs.get("subdir") or [""])[0]
        if not root: self._error(400,"root required"); return
        g = _load_graph(root); files = []
        if g:
            for n in g.get("nodes",[]):
                if ext and not n.get("path","").endswith(ext): continue
                if subdir and not n.get("path","").startswith(subdir.lstrip("/")): continue
                files.append({"path":n["path"],"category":n.get("category"),"lines":n.get("lines",0)})
        self._json({"files":files,"count":len(files)})

    def _get_file_defs(self, qs):
        root = (qs.get("root") or [""])[0]
        path = (qs.get("path") or [""])[0].lstrip("/")
        g = _load_graph(root)
        if not g: self._error(404,"no graph"); return
        node = next((n for n in g.get("nodes",[]) if n.get("path")==path), None)
        if not node: self._error(404,path); return
        self._json({"definitions":node.get("definitions",[]),
                    "imports":node.get("imports",[]),"exports":node.get("exports",[])})

    def _write_file(self, body):
        root = body.get("root",""); path = body.get("path","").lstrip("/")
        content = body.get("content","")
        if not root or not path: self._error(400,"root and path required"); return
        full = os.path.join(root,path)
        try:
            os.makedirs(os.path.dirname(full),exist_ok=True)
            with open(full,"w",encoding="utf-8") as f: f.write(content)
            self._json({"ok":True,"path":path,"bytes":len(content.encode())})
        except Exception as e: self._error(500,str(e))

    # ── Git ───────────────────────────────────────────────────────────────────
    def _git(self, body):
        import subprocess as _sp
        root = body.get("root","")
        if not root: self._error(400,"root required"); return
        cmd_name = body.get("command","status")
        extra = body.get("args","").strip()
        message = body.get("message","").strip()

        def _git_run(cmd, timeout=15):
            try:
                r = _sp.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=timeout)
                return {"command":cmd,"exit_code":r.returncode,"output":r.stdout[-6000:] if r.stdout else "","stderr":r.stderr[-800:] if r.stderr else "","ok":r.returncode==0}
            except _sp.TimeoutExpired:
                return {"command":cmd,"exit_code":-1,"output":"","stderr":"timed out","ok":False}
            except Exception as e:
                return {"command":cmd,"exit_code":-1,"output":"","stderr":str(e),"ok":False}

        if cmd_name == "status":           r = _git_run("git status --porcelain")
        elif cmd_name == "log":            r = _git_run(f"git log --oneline -{extra or 20}")
        elif cmd_name == "diff":           r = _git_run("git diff" + (f" -- {extra}" if extra else ""))
        elif cmd_name == "diff_staged":    r = _git_run("git diff --staged" + (f" -- {extra}" if extra else ""))
        elif cmd_name == "branch":         r = _git_run("git branch -a" if extra == "all" else "git branch")
        elif cmd_name == "add":            r = _git_run(f"git add -- {extra}" if extra else "git add .")
        elif cmd_name == "add_all":        r = _git_run("git add -A")
        elif cmd_name == "commit":         r = _git_run(f'git commit -m "{message}"' if message else "git commit")
        elif cmd_name == "commit_all":     r = _git_run(f'git commit -a -m "{message}"' if message else "git commit -a")
        elif cmd_name == "push":           r = _git_run("git push" + (f" {extra}" if extra else ""), timeout=30)
        elif cmd_name == "pull":           r = _git_run("git pull" + (f" {extra}" if extra else ""), timeout=30)
        elif cmd_name == "checkout":       r = _git_run(f"git checkout -- {extra}" if extra else "git checkout -- .")
        elif cmd_name == "checkout_new":   r = _git_run(f"git checkout -b {extra}" if extra else "git checkout -b")
        elif cmd_name == "stash":          r = _git_run("git stash" + (f" save \"{message}\"" if message else ""))
        elif cmd_name == "stash_pop":      r = _git_run("git stash pop")
        elif cmd_name == "stash_list":     r = _git_run("git stash list")
        elif cmd_name == "show":           r = _git_run(f"git show {extra}" if extra else "git show HEAD")
        elif cmd_name == "blame":          r = _git_run(f"git blame {extra}" if extra else "git blame", timeout=30)
        elif cmd_name == "init":           r = _git_run("git init")
        elif cmd_name == "remote":         r = _git_run("git remote -v")
        elif cmd_name == "reset":          r = _git_run(f"git reset {extra}" if extra else "git reset HEAD")
        elif cmd_name == "tag":            r = _git_run(f"git tag {extra}" if extra else "git tag")
        else: self._error(400,f"unknown git command: {cmd_name}"); return

        self._json(r)

    # ── Processes ─────────────────────────────────────────────────────────────
    def _get_processes(self): self._json(proc_mgr.list())
    def _proc_logs(self, pid):
        logs = proc_mgr.logs(pid)
        if logs is None: self._error(404,"not found")
        else: self._json(logs)

    def _proc_start(self, body):
        cmd  = body.get("command","").strip()
        name = body.get("name") or (cmd.split()[0] if cmd else "")
        cwd  = body.get("cwd") or body.get("root") or _ROOT_DIR
        if not cmd: self._error(400,"command required"); return
        proc = proc_mgr.start(name=name, command=cmd, cwd=cwd)
        proc.on_stdout(lambda l: _broadcast("stdout",{"id":proc.id,"line":l}))
        proc.on_stderr(lambda l: _broadcast("stderr",{"id":proc.id,"line":l}))
        proc.on_exit(lambda c:   _broadcast("exit",  {"id":proc.id,"code":c}))
        _broadcast("started", proc.info())
        self._json(proc.info())

    # ── State ─────────────────────────────────────────────────────────────────
    def _get_state(self, qs):
        root  = (qs.get("root") or [""])[0]
        state = _load_state()
        if root:
            self._json({"terminal_history":state["terminal_history"].get(root,[]),
                        "viewport":state["viewport"].get(root,{}),
                        "bottom_panel":state["bottom_panel"],
                        "editor_sessions":state["editor_sessions"].get(root,[])})
        else:
            self._json({"projects":state["projects"],"bottom_panel":state["bottom_panel"]})

    def _save_state_key(self, body):
        root=body.get("root",""); key=body.get("key",""); val=body.get("value")
        state = _load_state()
        per = ("terminal_history","viewport","editor_sessions")
        if key in per and root: state.setdefault(key,{})[root]=val
        elif key: state[key]=val
        _save_state(state); self._json({"ok":True})

    # ── Metrics / profiler ────────────────────────────────────────────────────
    def _get_metrics(self, qs):
        root = (qs.get("root") or [""])[0]
        pf   = (qs.get("path") or [""])[0]
        if not root: self._error(400,"root required"); return
        mf = os.path.join(root,".side-metrics.json")
        if not os.path.isfile(mf): self._json({"error":"No .side-metrics.json"}); return
        try:
            data = json.load(open(mf))
            files = {k:v for k,v in data.get("files",{}).items() if not pf or pf in k}
            fns   = {k:v for k,v in data.get("functions",{}).items() if not pf or pf in k}
            tf = sorted(files.items(),key=lambda x:-x[1].get("avg_ms",0))[:20]
            tn = sorted(fns.items(),  key=lambda x:-x[1].get("avg_ms",0))[:20]
            self._json({"pid":data.get("pid"),"updated":data.get("updated"),
                        "files":[{**v,"path":k} for k,v in tf],
                        "functions":[{**v,"name":k} for k,v in tn]})
        except Exception as e: self._error(500,str(e))

    # ── Nodes (SideNode adapter for MythOS bridge) ───────────────────────────
    # Converts FileNode graph entries into SideNode-shaped JSON that
    # calendarBridge.sideNodeToQuest() in MythOS can consume directly.
    _CATEGORY_TO_KIND = {
        "source": "task", "test": "task", "docs": "milestone",
        "config": "task", "script": "task",
        "python": "task", "javascript": "task", "typescript": "task",
        "html": "milestone", "css": "task", "go": "task", "rust": "task",
        "shell": "task", "markdown": "milestone",
    }
    _LANG_SKILL_HINTS = {
        ".py": ["python", "backend"],
        ".js": ["javascript", "web"],
        ".ts": ["typescript", "web"],
        ".html": ["html", "web"],
        ".css": ["css", "web"],
        ".go": ["go", "backend"],
        ".rs": ["rust", "backend"],
        ".sh": ["shell", "devops"],
        ".md": ["documentation"],
    }

    def _get_nodes(self, qs):
        root     = (qs.get("root") or [""])[0]
        cat_fill = (qs.get("category") or [""])[0]
        lang_fill = (qs.get("lang") or [""])[0]
        if not root: self._error(400,"root required"); return
        g = _load_graph(root)
        if not g: self._json([]); return

        nodes = []
        for n in g.get("nodes", []):
            ext = n.get("ext", "")
            category = n.get("category", "source")
            imports = n.get("imports", [])
            exports = n.get("exports", [])
            defs = n.get("definitions", [])

            # Derive skill hints from language + imports
            hints = list(self._LANG_SKILL_HINTS.get(ext, []))
            # Add import-derived hints (take the source module name)
            for imp in imports[:5]:
                src = imp.get("source", "") if isinstance(imp, dict) else str(imp)
                if src:
                    # Take the top-level module name
                    hints.append(src.strip("/").split(".")[0].strip("/").split("/")[-1])
            # Deduplicate preserving order
            seen = set()
            unique_hints = []
            for h in hints:
                if h not in seen:
                    seen.add(h)
                    unique_hints.append(h)

            # Estimate hours from lines (rough: 1h per 50 lines, min 0.5)
            lines = n.get("lines", 0)
            estimate = max(0.5, round(lines / 50, 1)) if lines else None

            # Build the SideNode shape
            side_node = {
                "id": f"side:{n.get('id', n.get('path', ''))}",
                "label": n.get("label", n.get("path", "")),
                "detail": (
                    f"File: {n.get('path', '')}\n"
                    f"{lines} lines, {len(imports)} imports, {len(exports)} exports, "
                    f"{len(defs)} definitions"
                ).strip(),
                "kind": self._CATEGORY_TO_KIND.get(category, "task"),
                "category": category if not cat_fill else cat_fill,
                "skillHints": unique_hints if not lang_fill else [lang_fill],
                "estimateHours": estimate,
                "childIds": [
                    f"side:{imp.get('source', '').strip('/').split('.')[-1] if isinstance(imp, dict) else imp.strip('/').split('/')[-1]}"
                    for imp in imports[:10]
                    if (imp.get('source', '').strip('/').split('.')[-1] if isinstance(imp, dict) else imp.strip('/').split('/')[-1])
                       in {nn.get("path", "").strip("/").split("/")[-1].split(".")[0]
                           for nn in g.get("nodes", [])}
                ],
                # Source metadata for provenance tracking
                "_source": {
                    "project": root,
                    "path": n.get("path"),
                    "category": category,
                    "ext": ext,
                    "position": n.get("position"),
                },
            }
            nodes.append(side_node)

        self._json(nodes)

    # ── XP recording (MythOS → S.I.D.E. feedback) ────────────────────────────
    def _record_xp(self, body):
        root = body.get("root", "")
        node_id = body.get("node_id", "")
        xp = body.get("xp", 0)
        skills = body.get("skills", [])
        if not root or not node_id:
            self._error(400, "root and node_id required"); return

        mf = os.path.join(root, ".side-metrics.json")
        try:
            data = json.load(open(mf)) if os.path.isfile(mf) else {}
        except Exception:
            data = {}

        xp_log = data.setdefault("xp_log", [])
        xp_log.append({
            "node_id": node_id,
            "xp": xp,
            "skills": skills,
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        data["total_xp"] = data.get("total_xp", 0) + xp

        try:
            tmp = mf + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, mf)
            self._json({"ok": True, "total_xp": data["total_xp"]})
        except Exception as e:
            self._error(500, str(e))

    # ── Infrastructure graph (web-infra.json → SideNode shape) ───────────────
    def _get_infra(self, infra_path=None):
        infra_path = infra_path or os.path.join(_ROOT_DIR, "web-infra.json")
        if not os.path.isfile(infra_path):
            self._json([]); return
        try:
            data = json.load(open(infra_path, encoding="utf-8"))
        except Exception as e:
            self._error(500, f"Failed to load web-infra.json: {e}"); return

        nodes_out = []
        for n in data.get("nodes", []):
            nodes_out.append({
                "id": f"infra:{n['id']}",
                "label": n["label"],
                "detail": n.get("detail", ""),
                "kind": n.get("kind", "service"),
                "category": n.get("category", "infra"),
                "skillHints": n.get("tech", []),
                "estimateHours": n.get("estimateHours", 0),
                "childIds": [f"infra:{cid}" for cid in n.get("childIds", [])],
                "_source": {
                    "type": "web-infra",
                    "status": n.get("status", "unknown"),
                    "tech": n.get("tech", []),
                },
            })
        self._json(nodes_out)

    # ── SSE (process events) ──────────────────────────────────────────────────
    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type","text/event-stream")
        self.send_header("Cache-Control","no-cache")
        self.send_header("Connection","keep-alive")
        self._cors(); self.end_headers()
        q = queue.Queue(maxsize=400)
        with sse_lock: sse_clients.append(q)
        try:
            while True:
                try:
                    self.wfile.write(q.get(timeout=15).encode()); self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError): pass
        finally:
            with sse_lock:
                if q in sse_clients: sse_clients.remove(q)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _body(self):
        l = int(self.headers.get("Content-Length",0))
        try: return json.loads(self.rfile.read(l)) if l else {}
        except Exception: return {}

    def _json(self, data):
        body = json.dumps(data,default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",len(body))
        self._cors(); self.end_headers(); self.wfile.write(body)

    def _error(self, code, msg):
        body = json.dumps({"error":str(msg)}).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",len(body))
        self._cors(); self.end_headers(); self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")

# ── Entry point ───────────────────────────────────────────────────────────────
def run(port=7700):
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"\n  S-IDE v0.6.0  →  http://127.0.0.1:{port}\n")
    try: server.serve_forever()
    except KeyboardInterrupt:
        print("\n[s-ide] shutting down...")
        proc_mgr.stop_all(); server.server_close()

if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv)>1 else 7700)

# ── GPLv3 ─────────────────────────────────────────────────────────────────────
def gplv3_notice():
    print("S-IDE  Copyright (C) 2026  N0V4-N3XU5")
    print("This program comes with ABSOLUTELY NO WARRANTY; for details type 'show w'.")
    print("This is free software, and you are welcome to redistribute it")
    print("under certain conditions; type 'show c' for details.")
