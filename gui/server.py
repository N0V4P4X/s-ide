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
POST /api/ai/chat  {root,model,messages,role,stream_id}  → SSE
POST /api/ai/cancel {stream_id}
POST /api/tool  {root,name,args,role}  → {name,result}
POST /api/git   {root,command,...}
GET  /api/processes
POST /api/processes/start  {root,command,name}
POST /api/processes/stop   {id}
POST /api/processes/suspend{id}
POST /api/processes/resume {id}
GET  /api/processes/:id/logs
GET  /events                     → SSE process events
GET  /api/versions?root=
POST /api/versions/archive {root}
POST /api/versions/update  {root,tarball,bump}
GET  /api/state?root=
POST /api/state {root,key,value}
GET  /api/metrics?root=&path=
POST /api/profile {root,entry,timeout}
POST /api/build/clean   {root,tiers,dry_run}
POST /api/build/package {root,kind,platform,minify}
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
    d = {"projects":[],"ai_history":{},"terminal_history":{},"viewport":{},
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
_ai_streams     = {}
_ai_streams_lock = threading.Lock()

def _broadcast(etype, data):
    msg = f"event: {etype}\ndata: {json.dumps(data)}\n\n"
    with sse_lock:
        dead = []
        for q in sse_clients:
            try: q.put_nowait(msg)
            except queue.Full: dead.append(q)
        for q in dead: sse_clients.remove(q)

# ── App context ───────────────────────────────────────────────────────────────
def _ctx(root, graph=None, role="chat"):
    from ai.context import AppContext
    ctx = AppContext()
    ctx.project_root = root
    ctx.graph = graph or _load_graph(root)
    ctx.role  = role
    ctx.session_dir = os.path.join(root, ".side","session","default")
    ctx.permitted_tools = None
    return ctx

# ── AI streaming ──────────────────────────────────────────────────────────────
def _run_ai_stream(root, model, messages, role, send_chunk, cancel):
    from ai.client import OllamaClient, ChatMessage
    from ai.context import ROLE_TOOLS
    from ai.tools import TOOLS, dispatch_tool
    custom_schemas = []
    try:
        from ai.tool_builder import get_custom_schemas, dispatch_custom, is_custom_tool
        custom_schemas = get_custom_schemas(root)
    except Exception: pass

    client = OllamaClient()
    if not client.is_available():
        send_chunk("error", {"message":"Ollama not running — start: ollama serve"})
        return

    ctx      = _ctx(root, role=role)
    permitted = ROLE_TOOLS.get(role, ROLE_TOOLS["chat"])
    all_tools = TOOLS + custom_schemas
    role_tools = [t for t in all_tools if t["function"]["name"] in permitted]
    chat_msgs  = [ChatMessage(**m) for m in messages]

    for _ in range(20):
        if cancel.is_set(): send_chunk("cancelled",{}); return
        resp = client.chat(model, chat_msgs, tools=role_tools, stream=False)

        if resp.tool_calls:
            for tc in resp.tool_calls:
                if cancel.is_set(): send_chunk("cancelled",{}); return
                send_chunk("tool_start",{"name":tc.name,"args":tc.arguments})
                try:
                    try:
                        from ai.tool_builder import is_custom_tool, dispatch_custom
                        if is_custom_tool(tc.name):
                            r = dispatch_custom(tc.name, tc.arguments, ctx)
                        else:
                            r = dispatch_tool(tc.name, tc.arguments, ctx)
                    except Exception:
                        r = dispatch_tool(tc.name, tc.arguments, ctx)
                    content = r.content
                except Exception as e:
                    content = json.dumps({"error":str(e)})
                send_chunk("tool_result",{"name":tc.name,"content":content})
                chat_msgs.append(ChatMessage(role="assistant",content="",
                    tool_calls=[{"function":{"name":tc.name,"arguments":tc.arguments}}]))
                chat_msgs.append(ChatMessage(role="tool",content=content,tool_call_id=tc.id or ""))
        else:
            if resp.content:
                for chunk in client.chat(model, chat_msgs, stream=True):
                    if cancel.is_set(): send_chunk("cancelled",{}); return
                    if chunk: send_chunk("text",{"delta":chunk})
            _persist_ai(root, messages[-1] if messages else None,
                        {"role":"assistant","content":resp.content})
            send_chunk("done",{}); return

    send_chunk("error",{"message":"Max tool iterations reached"})

def _persist_ai(root, user_msg, asst_msg):
    state = _load_state()
    h = state.setdefault("ai_history",{}).setdefault(root,[])
    if user_msg: h.append(user_msg)
    h.append(asst_msg)
    if len(h) > 200: state["ai_history"][root] = h[-200:]
    _save_state(state)

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
            "/api/versions": lambda: self._get_versions(qs),
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
            "/api/ai/chat":          lambda: self._ai_chat(body),
            "/api/ai/cancel":        lambda: self._ai_cancel(body),
            "/api/tool":             lambda: self._tool(body),
            "/api/git":              lambda: self._git(body),
            "/api/processes/start":  lambda: self._proc_start(body),
            "/api/processes/stop":   lambda: self._json({"ok":proc_mgr.stop(body.get("id"))}),
            "/api/processes/suspend":lambda: self._json({"ok":proc_mgr.suspend(body.get("id"))}),
            "/api/processes/resume": lambda: self._json({"ok":proc_mgr.resume(body.get("id"))}),
            "/api/versions/archive": lambda: self._archive(body),
            "/api/versions/update":  lambda: self._ver_update(body),
            "/api/state":            lambda: self._save_state_key(body),
            "/api/profile":          lambda: self._profile(body),
            "/api/build/clean":      lambda: self._build_clean(body),
            "/api/build/package":    lambda: self._build_pkg(body),
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

    # ── AI (SSE) ──────────────────────────────────────────────────────────────
    def _ai_chat(self, body):
        root      = body.get("root","").strip()
        model     = body.get("model","llama3.2")
        messages  = body.get("messages",[])
        role      = body.get("role","chat")
        sid       = body.get("stream_id",str(time.time()))
        if not root: self._error(400,"root required"); return

        self.send_response(200)
        self.send_header("Content-Type","text/event-stream")
        self.send_header("Cache-Control","no-cache")
        self.send_header("Connection","keep-alive")
        self._cors(); self.end_headers()

        cancel = threading.Event()
        with _ai_streams_lock: _ai_streams[sid] = cancel

        def send(etype, data):
            try:
                self.wfile.write(f"event: {etype}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
            except Exception: cancel.set()

        try: _run_ai_stream(root, model, messages, role, send, cancel)
        except Exception as e: send("error",{"message":str(e)})
        finally:
            with _ai_streams_lock: _ai_streams.pop(sid,None)

    def _ai_cancel(self, body):
        sid = body.get("stream_id","")
        with _ai_streams_lock: ev = _ai_streams.get(sid)
        if ev: ev.set(); self._json({"ok":True})
        else: self._json({"ok":False})

    # ── Tool dispatch ─────────────────────────────────────────────────────────
    def _tool(self, body):
        root = body.get("root",""); name = body.get("name","")
        args = body.get("args",{}); role = body.get("role","chat")
        if not name: self._error(400,"name required"); return
        try:
            from ai.tools import dispatch_tool
            result = dispatch_tool(name, args, _ctx(root,role=role))
            self._json({"name":name,"result":result.content})
        except Exception as e: self._error(500,str(e))

    # ── Git ───────────────────────────────────────────────────────────────────
    def _git(self, body):
        root = body.get("root","")
        if not root: self._error(400,"root required"); return
        try:
            from ai.tools import dispatch_tool
            args = {k:v for k,v in body.items() if k!="root"}
            r = dispatch_tool("git", args, _ctx(root))
            try:    self._json(json.loads(r.content))
            except: self._json({"output":r.content})
        except Exception as e: self._error(500,str(e))

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

    # ── Versions ──────────────────────────────────────────────────────────────
    def _get_versions(self, qs):
        root = (qs.get("root") or [""])[0]
        if not root: self._error(400,"root required"); return
        try:
            from version.version_manager import list_versions
            self._json(list_versions(root))
        except Exception as e: self._error(500,str(e))

    def _archive(self, body):
        root = body.get("root")
        if not root: self._error(400,"root required"); return
        try:
            from version.version_manager import archive_version
            self._json({"ok":True,"archivePath":archive_version(root)})
        except Exception as e: self._error(500,str(e))

    def _ver_update(self, body):
        root=body.get("root"); tb=body.get("tarball"); bump=body.get("bump","patch")
        if not root or not tb: self._error(400,"root and tarball required"); return
        try:
            from version.version_manager import apply_update
            nv, arch = apply_update(root,tb,bump)
            g = parse_project(root)
            self._json({"ok":True,"newVersion":nv,"archivePath":arch,"graph":g.to_dict()})
        except Exception as e: self._error(500,str(e))

    # ── State ─────────────────────────────────────────────────────────────────
    def _get_state(self, qs):
        root  = (qs.get("root") or [""])[0]
        state = _load_state()
        if root:
            self._json({"ai_history":state["ai_history"].get(root,[]),
                        "terminal_history":state["terminal_history"].get(root,[]),
                        "viewport":state["viewport"].get(root,{}),
                        "bottom_panel":state["bottom_panel"],
                        "editor_sessions":state["editor_sessions"].get(root,[])})
        else:
            self._json({"projects":state["projects"],"bottom_panel":state["bottom_panel"]})

    def _save_state_key(self, body):
        root=body.get("root",""); key=body.get("key",""); val=body.get("value")
        state = _load_state()
        per = ("ai_history","terminal_history","viewport","editor_sessions")
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

    def _profile(self, body):
        root=body.get("root",""); entry=body.get("entry","")
        timeout=int(body.get("timeout",60))
        if not root: self._error(400,"root required"); return
        try:
            from monitor.profiler import profile_project
            r = profile_project(root,entry_point=entry,timeout=timeout)
            self._json({"ok":r.ok,"entry_point":r.entry_point,"total_ms":round(r.total_ms,1),
                        "exit_code":r.exit_code,"error":r.error,"metrics_path":r.metrics_path,
                        "top_functions":[{"name":f.function_name,"module":f.module_path,
                            "calls":f.calls,"total_ms":f.total_ms,"per_call_ms":f.per_call_ms}
                            for f in r.top_functions(10)],
                        "summary":r.summary()})
        except Exception as e: self._error(500,str(e))

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build_clean(self, body):
        root = body.get("root")
        if not root: self._error(400,"root required"); return
        try:
            from build.cleaner import clean_project, CleanOptions
            r = clean_project(root,CleanOptions(tiers=body.get("tiers",["cache","logs"]),
                                                dry_run=bool(body.get("dry_run",False))))
            self._json({"ok":True,"removed":r.removed,"freed_bytes":r.freed_bytes,"errors":r.errors})
        except Exception as e: self._error(500,str(e))

    def _build_pkg(self, body):
        root = body.get("root")
        if not root: self._error(400,"root required"); return
        try:
            from build.packager import package_project, PackageOptions
            opts = PackageOptions(kind=body.get("kind","tarball"),
                                  target_platform=body.get("platform","auto"),
                                  minify=bool(body.get("minify",True)),
                                  clean=bool(body.get("clean",True)))
            out = body.get("out_dir") or os.path.join(root,"dist")
            r = package_project(root,out,opts)
            self._json({"ok":True,"output":r.output_path,"archive":r.archive_path,
                        "errors":r.errors,"summary":r.summary()})
        except Exception as e: self._error(500,str(e))

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
