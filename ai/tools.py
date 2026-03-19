"""
ai/tools.py
===========
Tool definitions and dispatch for the S-IDE AI agent.

Tools give the AI direct access to the project graph:
  read_file         — read any source file by path
  write_file        — propose a file edit (shown to user before applying)
  search_definitions — find functions/classes by name across the project
  get_file_summary  — get imports, exports, definitions for a file
  get_graph_overview — project structure: node count, languages, edges
  list_files        — list all source files (optionally filtered)
  get_metrics       — live timing data from .side-metrics.json
  run_command       — run a project command (test, lint, etc.) safely

Each tool is defined in TOOLS (Ollama function-calling format) and
dispatched through dispatch_tool(name, args, context) where context
is the AppContext from ai/context.py.
"""

from __future__ import annotations
import json
import os
import subprocess
import sys
from typing import Any

from ai.client import ToolResult


# ── Tool schemas (Ollama function-calling format) ─────────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full content of a source file in the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from project root, e.g. 'src/parser.py'"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List source files in the project, optionally filtered by extension or subdirectory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ext": {
                        "type": "string",
                        "description": "Filter by extension, e.g. '.py'. Empty = all files."
                    },
                    "subdir": {
                        "type": "string",
                        "description": "Only files under this subdirectory, e.g. 'src/'. Empty = whole project."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_summary",
            "description": "Get structured data for a file: imports, exports, function definitions with signatures and complexity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_definitions",
            "description": "Search for function or class definitions by name across all project files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Name or partial name to search for"
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["function", "class", "method", "any"],
                        "description": "Filter by definition kind. Default: any"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_graph_overview",
            "description": "Get a high-level summary of the project graph: file counts, languages, dependency structure, doc health.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_metrics",
            "description": "Get live or last-known performance metrics from .side-metrics.json. Shows per-file and per-function timing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional: filter to a specific file path"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a named command from side.project.json (e.g. 'test', 'lint'). Returns stdout/stderr. Timeout 30s.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Command name from side.project.json run scripts"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_definition_source",
            "description": "Get the source lines for a specific function or class definition.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path"
                    },
                    "name": {
                        "type": "string",
                        "description": "Function or class name"
                    }
                },
                "required": ["path", "name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or overwrite a file in the project. Use this for applying approved changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "Relative path to file" },
                    "content": { "type": "string", "description": "Full new content" }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_plan",
            "description": "Initialize a task.md with a list of steps for the current objective.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": { "type": "string" },
                        "description": "List of task descriptions"
                    }
                },
                "required": ["steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "Mark a task as done, in-progress, or pending in task.md.",
            "parameters": {
                "type": "object",
                "properties": {
                    "step_idx": { "type": "integer", "description": "0-based index of the step" },
                    "status": { "type": "string", "enum": ["todo", "doing", "done"], "description": "New status" }
                },
                "required": ["step_idx", "status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_agent_note",
            "description": "Leave a persistent note for future agents in README.md or AGENT_NOTES.md.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": { "type": "string", "description": "The note content" },
                    "path": { "type": "string", "description": "Target file (e.g. 'README.md' or 'src/module/README.md'). Default: 'AGENT_NOTES.md'" }
                },
                "required": ["note"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_in_playground",
            "description": "Send Python code to the IDE Playground for execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": { "type": "string", "description": "Python snippet" }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_session_file",
            "description": (
                "Write a file to the session workspace (.side/session/). "
                "This is the ONLY write tool available to Reviewer, Tester, and Documentarian roles. "
                "Use it to save review reports, test results, documentation drafts, and agent notes. "
                "Files here are NOT part of the project source — they are scratch space for agents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within session workspace, e.g. 'review/findings.md'"
                    },
                    "content": {
                        "type": "string",
                        "description": "File content to write"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_session_file",
            "description": "Read a file from the session workspace written by this or a prior agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within session workspace"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_session_files",
            "description": "List all files in the session workspace to see what prior agents have written.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "profile_project",
            "description": (
                "Profile a project's entry point with cProfile to get real per-module "
                "and per-function timing. Writes results to .side-metrics.json so the "
                "graph shows live performance overlays. Use this instead of @timed decorators."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_point": {
                        "type": "string",
                        "description": "Relative path to the script to run (e.g. 'src/main.py'). "
                                       "Leave empty to auto-detect."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to run (default 60)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git",
            "description": (
                "Run git operations on the project repository. "
                "Supports the full development workflow: status, log, diff, "
                "staging, committing, branching, push/pull, stash, blame, reset."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": [
                            "status", "log", "diff", "diff_staged",
                            "branch", "add", "add_all",
                            "commit", "commit_all",
                            "push", "pull",
                            "checkout", "checkout_new",
                            "stash", "stash_pop", "stash_list",
                            "show", "blame", "init", "remote", "reset", "tag"
                        ],
                        "description": (
                            "Git operation. "
                            "commit/commit_all require 'message'. "
                            "add/checkout/blame require 'args' (path or branch). "
                            "push/pull accept optional 'remote' and 'branch'."
                        )
                    },
                    "message": {
                        "type": "string",
                        "description": "Commit message (for commit, commit_all, stash)"
                    },
                    "args": {
                        "type": "string",
                        "description": "File path, branch name, or commit ref"
                    },
                    "remote": {
                        "type": "string",
                        "description": "Remote name (default: origin)"
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch name for push/pull"
                    },
                    "n": {
                        "type": "integer",
                        "description": "Number of log entries (default 20)"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clone_project",
            "description": "Create an isolated copy of the current project for optimization experiments. Excludes .git, __pycache__, and other build artifacts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_path": {
                        "type": "string",
                        "description": "Absolute or relative path where the project should be cloned. E.g. '../s-ide-optimized'"
                    }
                },
                "required": ["target_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "audit_project",
            "description": "Run a comprehensive health audit: unit tests, project graph parsing, and documentation health. Returns a 'ready-to-ship' status.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
]


# ── Dispatch ──────────────────────────────────────────────────────────────────

def dispatch_tool(name: str, args: dict, context: "AppContext") -> ToolResult:  # type: ignore
    """
    Route a tool call to the appropriate handler.
    Checks role-based permissions before dispatching.
    context is the AppContext from ai/context.py.
    """
    try:
        handler = _HANDLERS.get(name)
        if handler is None:
            return ToolResult(
                tool_call_id="",
                name=name,
                content=json.dumps({"error": f"Unknown tool: {name}"}),
            )
        # Permission check (after existence check so unknown tools give clear error)
        if not context.can_use(name):
            return ToolResult(
                tool_call_id="",
                name=name,
                content=json.dumps({
                    "error": (
                        f"Tool '{name}' is not permitted for role '{context.role}'. "
                        f"Use write_session_file to save output to the session workspace instead."
                    )
                }),
            )
        result = handler(args, context)
        return ToolResult(
            tool_call_id="",
            name=name,
            content=result if isinstance(result, str) else json.dumps(result, indent=2),
        )
    except Exception as e:
        return ToolResult(
            tool_call_id="",
            name=name,
            content=json.dumps({"error": str(e)}),
        )


# ── Handlers ──────────────────────────────────────────────────────────────────

def _read_file(args: dict, ctx: Any) -> str:
    path = args.get("path", "").lstrip("/")
    full = os.path.join(ctx.project_root, path)
    if not os.path.isfile(full):
        return json.dumps({"error": f"File not found: {path}"})
    try:
        content = open(full, encoding="utf-8", errors="replace").read()
        # Cap at 8000 chars to stay within context
        if len(content) > 8000:
            content = content[:8000] + f"\n... [truncated, {len(content)} total chars]"
        return content
    except Exception as e:
        return json.dumps({"error": str(e)})


def _list_files(args: dict, ctx: Any) -> dict:
    ext    = args.get("ext", "")
    subdir = args.get("subdir", "")
    root   = ctx.project_root
    files  = []
    for node in (ctx.graph.get("nodes", []) if ctx.graph else []):
        path = node.get("path", "")
        if ext and not path.endswith(ext):
            continue
        if subdir and not path.startswith(subdir.lstrip("/")):
            continue
        files.append({
            "path":     path,
            "category": node.get("category"),
            "lines":    node.get("lines", 0),
        })
    return {"files": files, "count": len(files)}


def _get_file_summary(args: dict, ctx: Any) -> dict:
    path = args.get("path", "").lstrip("/")
    node = next(
        (n for n in (ctx.graph.get("nodes", []) if ctx.graph else [])
         if n.get("path") == path or n.get("path", "").endswith(path)),
        None,
    )
    if not node:
        return {"error": f"File not in graph: {path}"}
    defs = []
    for d in node.get("definitions", []):
        defs.append({
            "name":       d.get("name"),
            "kind":       d.get("kind"),
            "line":       d.get("line"),
            "args":       d.get("args", []),
            "returnType": d.get("returnType", ""),
            "calls":      d.get("calls", []),
            "raises":     d.get("raises", []),
            "complexity": d.get("complexity", 0),
        })
    return {
        "path":        path,
        "lines":       node.get("lines"),
        "category":    node.get("category"),
        "imports":     [{"source": i.get("source"), "type": i.get("type")}
                        for i in node.get("imports", [])],
        "definitions": defs,
        "tags":        node.get("tags", []),
        "errors":      node.get("errors", []),
    }


def _search_definitions(args: dict, ctx: Any) -> dict:
    query = args.get("query", "").lower()
    kind  = args.get("kind", "any")
    results = []
    for node in (ctx.graph.get("nodes", []) if ctx.graph else []):
        for d in node.get("definitions", []):
            name = d.get("name", "")
            if query not in name.lower():
                continue
            if kind != "any" and d.get("kind") != kind:
                continue
            results.append({
                "name":       name,
                "kind":       d.get("kind"),
                "file":       node.get("path"),
                "line":       d.get("line"),
                "args":       d.get("args", []),
                "returnType": d.get("returnType", ""),
                "complexity": d.get("complexity", 0),
            })
    return {"results": results, "count": len(results)}


def _get_graph_overview(args: dict, ctx: Any) -> dict:
    if not ctx.graph:
        return {"error": "No project loaded"}
    meta  = ctx.graph.get("meta", {})
    nodes = ctx.graph.get("nodes", [])
    edges = ctx.graph.get("edges", [])
    return {
        "project":    meta.get("project", {}),
        "files":      meta.get("totalFiles"),
        "edges":      meta.get("totalEdges"),
        "languages":  meta.get("languages", {}),
        "parseTime":  meta.get("parseTime"),
        "perf":       meta.get("perf", {}),
        "docHealth":  meta.get("docs", {}).get("healthy"),
        "warnings":   meta.get("docs", {}).get("summary", {}),
        "topNodes": sorted(
            [{"path": n["path"], "lines": n.get("lines", 0),
              "imports": len(n.get("imports", [])),
              "defs": len(n.get("definitions", []))} for n in nodes],
            key=lambda x: -x["lines"]
        )[:10],
    }


def _get_metrics(args: dict, ctx: Any) -> dict:
    filter_path = args.get("path", "")
    metrics_file = os.path.join(ctx.project_root, ".side-metrics.json")
    if not os.path.isfile(metrics_file):
        return {"error": "No .side-metrics.json found. Run the project with @timed decorators first."}
    try:
        import json as _json
        data = _json.load(open(metrics_file))
        files = data.get("files", {})
        fns   = data.get("functions", {})
        if filter_path:
            files = {k: v for k, v in files.items() if filter_path in k}
            fns   = {k: v for k, v in fns.items() if filter_path in k}
        # Sort by avg_ms descending
        top_files = sorted(files.items(), key=lambda x: -x[1].get("avg_ms", 0))[:20]
        top_fns   = sorted(fns.items(),   key=lambda x: -x[1].get("avg_ms", 0))[:20]
        return {
            "pid":      data.get("pid"),
            "updated":  data.get("updated"),
            "files":    [{"path": k, **v} for k, v in top_files],
            "functions":[{"name": k, **v} for k, v in top_fns],
        }
    except Exception as e:
        return {"error": str(e)}


def _run_command(args: dict, ctx: Any) -> dict:
    name = args.get("name", "")
    run_scripts = {}
    if ctx.graph:
        run_scripts = ctx.graph.get("meta", {}).get("project", {}).get("run", {})
    cmd = run_scripts.get(name)
    if not cmd:
        return {"error": f"No command '{name}' in side.project.json run scripts. Available: {list(run_scripts)}"}
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=ctx.project_root,
            capture_output=True, text=True, timeout=30
        )
        return {
            "command":   cmd,
            "exit_code": result.returncode,
            "stdout":    result.stdout[-3000:] if result.stdout else "",
            "stderr":    result.stderr[-1000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 30s"}
    except Exception as e:
        return {"error": str(e)}


def _get_definition_source(args: dict, ctx: Any) -> dict:
    path = args.get("path", "").lstrip("/")
    name = args.get("name", "")
    full = os.path.join(ctx.project_root, path)
    if not os.path.isfile(full):
        return {"error": f"File not found: {path}"}
    try:
        source = open(full, encoding="utf-8", errors="replace").read()
        lines  = source.splitlines()
        # Find via graph data first
        node = next(
            (n for n in (ctx.graph.get("nodes", []) if ctx.graph else [])
             if n.get("path", "").endswith(path.lstrip("./"))),
            None,
        )
        start_line = None
        end_line   = None
        if node:
            for d in node.get("definitions", []):
                if d.get("name") == name:
                    start_line = d.get("line")
                    end_line   = d.get("endLine")
                    break
        # Fallback: search source
        if start_line is None:
            import re as _re
            for i, line in enumerate(lines, 1):
                if _re.match(rf"(async\s+)?def\s+{_re.escape(name)}\s*\(|class\s+{_re.escape(name)}\s*[:(]", line.strip()):
                    start_line = i
                    break
        if start_line is None:
            return {"error": f"Definition '{name}' not found in {path}"}
        start = max(0, start_line - 1)
        end   = min(len(lines), (end_line or start_line + 40))
        snippet = "\n".join(lines[start:end])
        return {
            "file":      path,
            "name":      name,
            "startLine": start_line,
            "endLine":   end_line,
            "source":    snippet,
        }
    except Exception as e:
        return {"error": str(e)}


def _write_file(args: dict, ctx: Any) -> str:
    path = args.get("path", "").lstrip("/")
    content = args.get("content", "")
    full = os.path.join(ctx.project_root, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    try:
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return f"File written successfully: {path}"
    except Exception as e:
        return json.dumps({"error": str(e)})


def _create_plan(args: dict, ctx: Any) -> str:
    steps = args.get("steps", [])
    path = os.path.join(ctx.project_root, ".side", "task.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# Project Plan", ""]
    for i, s in enumerate(steps):
        lines.append(f"- [ ] {s}")
    content = "\n".join(lines)
    with open(path, "w") as f: f.write(content)
    return "Plan created in .side/task.md"


def _update_plan(args: dict, ctx: Any) -> str:
    idx = args.get("step_idx", 0)
    status = args.get("status", "done")
    path = os.path.join(ctx.project_root, ".side", "task.md")
    if not os.path.isfile(path): path = os.path.join(ctx.project_root, "task.md")
    if not os.path.isfile(path): return "No task.md found to update."
    
    with open(path, "r") as f: lines = f.readlines()
    
    count = 0
    for i, line in enumerate(lines):
        if "[ ]" in line or "[x]" in line or "[/]" in line:
            if count == idx:
                s = "[x]" if status == "done" else "[/]" if status == "doing" else "[ ]"
                lines[i] = line.replace("[ ]", s).replace("[x]", s).replace("[/]", s)
                break
            count += 1
            
    with open(path, "w") as f: f.writelines(lines)
    return f"Step {idx} updated to {status}."


def _write_agent_note(args: dict, ctx: Any) -> str:
    note = args.get("note", "")
    rel_path = args.get("path", "AGENT_NOTES.md").lstrip("/")
    path = os.path.join(ctx.project_root, rel_path)
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n\n## ◈ Agent Note ({ts})\n"
    # If file is a README, we might want to append to a specific section
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{header}{note}\n\n---\n")
        return f"Note added to {rel_path}"
    except Exception as e:
        return f"Error writing note: {e}"


def _run_in_playground(args: dict, ctx: Any) -> dict:
    """Run a Python snippet in an isolated sandbox copy of the project."""
    code  = args.get("code", "").strip()
    setup = args.get("setup", "")
    if not code:
        return {"error": "code is required"}
    if not ctx.project_root:
        return {"error": "No project root — cannot create sandbox"}
    from ai.playground import run_snippet
    return run_snippet(code, ctx.project_root)




def _write_session_file(args: dict, ctx: Any) -> dict:
    """
    Write a file to the session workspace (.side/session/).
    Safe for all roles — does NOT touch project source files.
    Used by Reviewer, Tester, Documentarian to save reports and drafts.
    """
    path    = args.get("path", "").lstrip("/")
    content = args.get("content", "")
    if not path:
        return {"error": "path is required"}
    if not ctx.session_dir:
        return {"error": "No session workspace configured"}
    # Prevent escape from session dir
    full = os.path.realpath(os.path.join(ctx.session_dir, path))
    if not full.startswith(os.path.realpath(ctx.session_dir)):
        return {"error": "Path escapes session workspace"}
    os.makedirs(os.path.dirname(full), exist_ok=True)
    try:
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        rel = os.path.relpath(full, ctx.session_dir)
        return {"written": rel, "bytes": len(content.encode())}
    except Exception as e:
        return {"error": str(e)}


def _read_session_file(args: dict, ctx: Any) -> str:
    """Read a file from the session workspace."""
    path = args.get("path", "").lstrip("/")
    if not path or not ctx.session_dir:
        return json.dumps({"error": "path and session_dir required"})
    full = os.path.realpath(os.path.join(ctx.session_dir, path))
    if not full.startswith(os.path.realpath(ctx.session_dir)):
        return json.dumps({"error": "Path escapes session workspace"})
    if not os.path.isfile(full):
        return json.dumps({"error": f"Not found: {path}"})
    try:
        content = open(full, encoding="utf-8", errors="replace").read()
        if len(content) > 8000:
            content = content[:8000] + f"\n... [truncated, {len(content)} chars total]"
        return content
    except Exception as e:
        return json.dumps({"error": str(e)})


def _list_session_files(args: dict, ctx: Any) -> dict:
    """List files in the session workspace."""
    if not ctx.session_dir or not os.path.isdir(ctx.session_dir):
        return {"files": [], "note": "Session workspace is empty or not created yet"}
    files = []
    for root, dirs, fnames in os.walk(ctx.session_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in fnames:
            full = os.path.join(root, f)
            rel  = os.path.relpath(full, ctx.session_dir)
            files.append({
                "path":    rel,
                "bytes":   os.path.getsize(full),
                "modified": os.path.getmtime(full),
            })
    files.sort(key=lambda x: x["modified"], reverse=True)
    return {"files": files, "count": len(files), "workspace": ctx.session_dir}


def _git_run(cmd: str, cwd: str, timeout: int = 15) -> dict:
    """Internal helper: run a git command, return structured result."""
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        return {
            "command":   cmd,
            "exit_code": result.returncode,
            "output":    result.stdout[-6000:] if result.stdout else "",
            "stderr":    result.stderr[-800:]  if result.stderr else "",
            "ok":        result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"command": cmd, "exit_code": -1, "output": "",
                "stderr": "timed out", "ok": False,
                "error": f"git timed out after {timeout}s"}
    except Exception as e:
        return {"command": cmd, "exit_code": -1, "output": "",
                "stderr": str(e), "ok": False, "error": str(e)}


def _git(args: dict, ctx: Any) -> dict:
    """Run a git operation in the project root. Supports all common workflows."""
    if not ctx.project_root:
        return {"error": "No project root set"}
    root     = ctx.project_root
    cmd_name = args.get("command", "status")
    extra    = args.get("args", "").strip()
    message  = args.get("message", "").strip()

    # Map command names → git command strings
    # Commands that require extra args return an error if not provided.
    def need(field: str, label: str):
        v = args.get(field, "").strip()
        if not v:
            return None, {"error": f"'{cmd_name}' requires '{label}' argument"}
        return v, None

    match cmd_name:
        case "status":
            return _git_run("git status --short --branch", root)
        case "log":
            n = int(args.get("n", 20))
            fmt = args.get("format", "oneline")
            flag = "--oneline" if fmt == "oneline" else "--format='%h %an %ar %s'"
            return _git_run(f"git log {flag} -{n}", root)
        case "diff":
            target = extra or "HEAD"
            return _git_run(f"git diff {target}", root)
        case "diff_staged":
            return _git_run("git diff --cached", root)
        case "branch":
            return _git_run("git branch -vv", root)
        case "add":
            path, err = need("args", "args (file path or '.')")
            if err: return err
            return _git_run(f"git add {path}", root)
        case "add_all":
            return _git_run("git add -A", root)
        case "commit":
            msg, err = need("message", "message")
            if err: return err
            # Escape message for shell
            safe_msg = msg.replace('"', '\"')
            return _git_run(f'git commit -m "{safe_msg}"', root)
        case "commit_all":
            msg, err = need("message", "message")
            if err: return err
            safe_msg = msg.replace('"', '\"')
            return _git_run(f'git add -A && git commit -m "{safe_msg}"', root)
        case "push":
            remote = args.get("remote", "origin").strip()
            branch = args.get("branch", "").strip()
            target = f"{remote} {branch}" if branch else remote
            return _git_run(f"git push {target}", root, timeout=30)
        case "pull":
            remote = args.get("remote", "origin").strip()
            branch = args.get("branch", "").strip()
            target = f"{remote} {branch}" if branch else ""
            return _git_run(f"git pull {remote} {target}".strip(), root, timeout=30)
        case "checkout":
            target, err = need("args", "args (branch or file)")
            if err: return err
            return _git_run(f"git checkout {target}", root)
        case "checkout_new":
            branch, err = need("args", "args (new branch name)")
            if err: return err
            return _git_run(f"git checkout -b {branch}", root)
        case "stash":
            msg_part = f' -m "{message}"' if message else ""
            return _git_run(f"git stash{msg_part}", root)
        case "stash_pop":
            return _git_run("git stash pop", root)
        case "stash_list":
            return _git_run("git stash list", root)
        case "show":
            target = extra or "HEAD"
            return _git_run(f"git show --stat {target}", root)
        case "blame":
            path, err = need("args", "args (file path)")
            if err: return err
            return _git_run(f"git blame {path}", root)
        case "init":
            return _git_run("git init", root)
        case "remote":
            return _git_run("git remote -v", root)
        case "reset":
            target = extra or "HEAD"
            mode   = args.get("mode", "soft")  # soft | mixed | hard
            if mode not in ("soft", "mixed", "hard"):
                mode = "soft"
            return _git_run(f"git reset --{mode} {target}", root)
        case "tag":
            name, err = need("args", "args (tag name)")
            if err: return err
            return _git_run(f"git tag {name}", root)
        case _:
            # Pass-through for any unlisted command with extra args
            if extra:
                return _git_run(f"git {cmd_name} {extra}", root)
            return {"error": f"Unknown git command: '{cmd_name}'. "
                             f"Supported: status, log, diff, diff_staged, branch, "
                             f"add, add_all, commit, commit_all, push, pull, "
                             f"checkout, checkout_new, stash, stash_pop, stash_list, "
                             f"show, blame, init, remote, reset, tag"}

def _profile_project(args: dict, ctx: Any) -> dict:
    """Profile a project entry point with cProfile, write metrics JSON."""
    if not ctx.project_root:
        return {"error": "No project root set"}
    from monitor.profiler import profile_project
    entry   = args.get("entry_point", "")
    timeout = int(args.get("timeout", 60))
    result  = profile_project(
        project_root = ctx.project_root,
        entry_point  = entry,
        timeout      = timeout,
    )
    return {
        "ok":           result.ok,
        "entry_point":  result.entry_point,
        "total_ms":     round(result.total_ms, 1),
        "exit_code":    result.exit_code,
        "error":        result.error,
        "metrics_path": result.metrics_path,
        "top_functions": [
            {"name": f.function_name, "module": f.module_path,
             "calls": f.calls, "total_ms": f.total_ms,
             "per_call_ms": f.per_call_ms}
            for f in result.top_functions(10)
        ],
        "summary": result.summary(),
    }


def _audit_project(args: dict, ctx: Any) -> dict:
    """Run tests, parse graph, check docs. Returns health report."""
    if not ctx.project_root:
        return {"error": "No project root set"}
    
    # 1. Run tests
    test_res = _run_command({"name": "test"}, ctx)
    tests_ok = test_res.get("exit_code") == 0
    
    # 2. Check docs (from last graph parse)
    docs_ok = True
    doc_warnings = []
    if ctx.graph:
        docs = ctx.graph.get("meta", {}).get("docs", {})
        docs_ok = docs.get("healthy", True)
        doc_warnings = [w.get("message") for w in docs.get("warnings", [])]
        
    return {
        "healthy": tests_ok and docs_ok,
        "tests": {
            "ok": tests_ok,
            "exit_code": test_res.get("exit_code"),
            "summary": "Passed" if tests_ok else "Failed",
        },
        "docs": {
            "ok": docs_ok,
            "warnings": doc_warnings,
        },
        "recommendation": "Ready to ship" if tests_ok and docs_ok else "Fix tests/docs before commit"
    }


def _clone_project(args: dict, ctx: Any) -> ToolResult:
    """Clone current project to target_path for optimization."""
    target = args.get("target_path")
    if not target:
        return ToolResult(name="clone_project", ok=False, error="target_path required")
    
    if not os.path.isabs(target):
        target = os.path.abspath(os.path.join(ctx.project_root, target))
    
    import shutil
    def ignore_build(path: str, names: list[str]) -> list[str]:
        to_ignore = ['.git', '__pycache__', 'venv', '.side', '.gemini']
        return [str(n) for n in names if str(n) in to_ignore]

    try:
        if os.path.exists(target):
            return ToolResult(name="clone_project", ok=False, error="Target already exists")
        
        shutil.copytree(ctx.project_root, target, ignore=ignore_build)
        
        # Notify GUI via a custom field in ToolResult if needed, 
        # but for now we'll just return the path.
        return ToolResult(
            name="clone_project", 
            ok=True, 
            content=f"Project cloned to {target}. You can now start 'Optimization Teams' there."
        )
    except Exception as e:
        return ToolResult(name="clone_project", ok=False, error=str(e))


_HANDLERS = {
    "read_file":             _read_file,
    "list_files":            _list_files,
    "get_file_summary":      _get_file_summary,
    "search_definitions":    _search_definitions,
    "get_graph_overview":    _get_graph_overview,
    "get_metrics":           _get_metrics,
    "run_command":           _run_command,
    "get_definition_source": _get_definition_source,
    "write_file":            _write_file,
    "create_plan":           _create_plan,
    "update_plan":           _update_plan,
    "write_agent_note":      _write_agent_note,
    "run_in_playground":     _run_in_playground,
    "git":                   _git,
    "write_session_file":    _write_session_file,
    "read_session_file":     _read_session_file,
    "list_session_files":    _list_session_files,
    "profile_project":       _profile_project,
    "audit_project":         _audit_project,
    "clone_project":         _clone_project,
}
