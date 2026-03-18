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

from .client import ToolResult


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
]


# ── Dispatch ──────────────────────────────────────────────────────────────────

def dispatch_tool(name: str, args: dict, context: "AppContext") -> ToolResult:  # type: ignore
    """
    Route a tool call to the appropriate handler.
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


_HANDLERS = {
    "read_file":             _read_file,
    "list_files":            _list_files,
    "get_file_summary":      _get_file_summary,
    "search_definitions":    _search_definitions,
    "get_graph_overview":    _get_graph_overview,
    "get_metrics":           _get_metrics,
    "run_command":           _run_command,
    "get_definition_source": _get_definition_source,
}
