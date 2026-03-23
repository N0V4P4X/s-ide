# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 N0V4-N3XU5

"""
ai/client.py
============
Minimal Ollama HTTP client for S-IDE.

Talks to the Ollama API at http://localhost:11434 (configurable).
Supports streaming chat completions and tool (function) calls.

No external dependencies — uses only urllib from stdlib.

Usage
-----
    from ai.client import OllamaClient

    client = OllamaClient()
    if not client.is_available():
        print("Ollama not running — start with: ollama serve")

    # Simple chat
    for chunk in client.chat("llama3.2", messages, stream=True):
        print(chunk, end="", flush=True)

    # With tools
    response = client.chat("llama3.2", messages, tools=TOOLS, stream=False)
    if response.tool_calls:
        for call in response.tool_calls:
            result = dispatch_tool(call.name, call.arguments)
            messages.append(result.to_message())
"""

from __future__ import annotations
import json
import threading
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Iterator, Callable, Optional


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class ChatMessage:
    role:       str           # "system" | "user" | "assistant" | "tool"
    content:    str = ""
    tool_calls: list = field(default_factory=list)  # [{function: {name, arguments}}]
    tool_call_id: str = ""

    def to_dict(self) -> dict:
        d: dict = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass
class ToolCall:
    name:      str
    arguments: dict
    id:        str = ""


@dataclass
class ChatResponse:
    content:    str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model:      str = ""
    done:       bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "ChatResponse":
        msg = d.get("message", {})
        tool_calls = []
        for tc in msg.get("tool_calls", []):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            tool_calls.append(ToolCall(
                name=fn.get("name", ""),
                arguments=args,
                id=tc.get("id", ""),
            ))
        return cls(
            content=msg.get("content", ""),
            tool_calls=tool_calls,
            model=d.get("model", ""),
            done=d.get("done", True),
        )


@dataclass
class ToolResult:
    tool_call_id: str
    name:         str
    content:      str   # JSON string or plain text

    def to_message(self) -> ChatMessage:
        return ChatMessage(
            role="tool",
            content=self.content,
            tool_call_id=self.tool_call_id,
        )


# ── Client ────────────────────────────────────────────────────────────────────

class OllamaClient:
    """
    Thin wrapper around the Ollama HTTP API.
    Thread-safe — all requests are independent.
    """

    DEFAULT_HOST  = "http://localhost:11434"
    DEFAULT_MODEL = "llama3.2"
    TIMEOUT       = 300   # Increased to 5 min for higher quality / slower models

    def __init__(self, host: str = DEFAULT_HOST):
        self.host = host.rstrip("/")

    def is_available(self) -> bool:
        """Return True if Ollama is reachable."""
        try:
            urllib.request.urlopen(f"{self.host}/api/tags", timeout=2)
            return True
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Return names of locally available models."""
        try:
            resp = urllib.request.urlopen(f"{self.host}/api/tags", timeout=5)
            data = json.loads(resp.read())
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    def pull(self, model: str, on_progress: Callable[[str], None] | None = None) -> bool:
        """Pull a model. Calls on_progress with status strings. Returns True on success."""
        url  = f"{self.host}/api/pull"
        body = json.dumps({"name": model}).encode()
        req  = urllib.request.Request(url, data=body,
                                       headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                for raw_line in resp:
                    line = raw_line.decode().strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        status = d.get("status", "")
                        if on_progress is not None:
                            on_progress(status)
                        if d.get("error"):
                            return False
                    except json.JSONDecodeError:
                        pass
            return True
        except Exception:
            return False

    def chat(
        self,
        model:    str,
        messages: list[ChatMessage],
        tools:    list[dict] | None = None,
        stream:   bool = True,
        options:  dict | None = None,
    ) -> Iterator[str] | ChatResponse:
        """
        Send a chat request.
        stream=True  → yields text chunks as they arrive
        stream=False → blocks and returns a ChatResponse
        """
        payload: dict = {
            "model":    model,
            "messages": [m.to_dict() for m in messages],
            "stream":   stream,
        }
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options

        url  = f"{self.host}/api/chat"
        body = json.dumps(payload).encode()
        req  = urllib.request.Request(url, data=body,
                                       headers={"Content-Type": "application/json"})

        if stream:
            return self._stream(req)
        else:
            return self._blocking(req)

    def _stream_raw(self, req) -> Iterator[dict]:
        """Generator that yields raw JSON dictionaries from a streaming response."""
        try:
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                for raw_line in resp:
                    line = raw_line.decode().strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        pass
        except urllib.error.URLError as e:
            yield {"error": str(e), "done": True}

    def _stream(self, req) -> Iterator[str]:
        """Generator that yields text chunks from a streaming response."""
        for d in self._stream_raw(req):
            if d.get("error"):
                yield f"\n[error: {d['error']}]"
                return
            chunk = d.get("message", {}).get("content", "")
            if chunk:
                yield chunk

    def _blocking(self, req) -> ChatResponse:
        """Blocking request — returns full ChatResponse."""
        try:
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                data = json.loads(resp.read())
                return ChatResponse.from_dict(data)
        except urllib.error.URLError as e:
            return ChatResponse(content=f"[connection error: {e}]", done=True)
        except json.JSONDecodeError as e:
            return ChatResponse(content=f"[json error: {e}]", done=True)

    def chat_with_tools(
        self,
        model:      str,
        messages:   list[ChatMessage],
        tools:      list[dict],
        dispatch_fn: Callable[[str, dict], ToolResult],
        max_rounds: int = 10,
        on_text:    Callable[[str], None] | None = None,
        stop_event: Optional[threading.Event] = None,
    ) -> ChatResponse:
        """
        Agentic loop with real-time streaming and troubleshooting support.
        """
        msgs = list(messages)
        final_content = ""
        
        for _ in range(max_rounds):
            if stop_event and stop_event.is_set():
                break
            payload: dict = {
                "model":    model,
                "messages": [m.to_dict() for m in msgs],
                "stream":   True,
                "tools":    tools,
                "options":  {
                    "num_ctx": 16384,
                    "temperature": 0.1,
                    "num_predict": 4096,
                }
            }
            url  = f"{self.host}/api/chat"
            body = json.dumps(payload).encode()
            req  = urllib.request.Request(url, data=body,
                                           headers={"Content-Type": "application/json"})
            
            curr_content = ""
            curr_tool_calls = []
            is_finished = True
            
            try:
                for d in self._stream_raw(req):
                    if d.get("error"):
                        if on_text is not None:
                            on_text(f"\n[error: {d['error']}]")
                        break
                    
                    msg = d.get("message", {})
                    content = msg.get("content", "")
                    if content:
                        curr_content += content
                        if on_text is not None:
                            on_text(content)
                    
                    tcs = msg.get("tool_calls", [])
                    if tcs:
                        for tc in tcs:
                            fn = tc.get("function", {})
                            curr_tool_calls.append(ToolCall(
                                name=fn.get("name", ""),
                                arguments=fn.get("arguments", {}),
                                id=tc.get("id", "")
                            ))
                    
                    # Detect truncation
                    if "done" in d:
                        is_finished = d["done"]
            except Exception as e:
                if on_text is not None:
                    on_text(f"\n[HTTP Error: {e}]")
                break

            # --- SIMULATION INTERCEPTOR ---
            # Catch all formats the model uses to fake tool calls.
            # Three patterns observed:
#   1. ```python\nlist_files(subdir='src')\n```
#   2. [Tool Call: list_files(subdir='src')]
#   3. bare prose:  list_files(subdir='src')
            import re as _re
            _tool_names = {t['function']['name'] for t in tools}

            def _parse_sim_args(raw_args):
                args = {}
                if not raw_args or '<' in raw_args or '>' in raw_args:
                    return args
                for part in _re.split(r',\s*(?=[a-z_]+=)', raw_args):
                    if '=' in part:
                        k, v = part.split('=', 1)
                        v = v.strip().strip("'").strip('"')
                        if v.startswith('[') and v.endswith(']'):
                            try: v = json.loads(v.replace("'", '"'))
                            except: pass
                        args[k.strip()] = v
                return args

            if not curr_tool_calls:
                # Pattern 1: [Tool Call: fn(args)] or [call fn(args)]
                for m in _re.finditer(
                        r'\[(?:Tool Call|call):\s*([a-z_]+)\(([^)]*)\)\]',
                        curr_content, _re.IGNORECASE):
                    fn, raw = m.group(1), m.group(2)
                    if fn in _tool_names and '<' not in raw and '>' not in raw:
                        curr_tool_calls.append(ToolCall(
                            name=fn, arguments=_parse_sim_args(raw),
                            id='sim_br_' + fn))
                # Pattern 2: ```python\n...fn(args)...\n```
                if not curr_tool_calls:
                    for block in _re.findall(
                            r'```(?:python)?\s*(.*?)```', curr_content, _re.DOTALL):
                        for m in _re.finditer(r'([a-z_]+)\(([^)]*)\)', block):
                            fn, raw = m.group(1), m.group(2)
                            if fn in _tool_names and not any(
                                    tc.name == fn for tc in curr_tool_calls):
                                curr_tool_calls.append(ToolCall(
                                    name=fn, arguments=_parse_sim_args(raw),
                                    id='sim_code_' + fn))
                # Pattern 3: prose fn(args) — last resort, only well-known names
                if not curr_tool_calls:
                    for m in _re.finditer(
                            r'\b([a-z_]{4,})\(([^)]{0,200})\)', curr_content):
                        fn, raw = m.group(1), m.group(2)
                        if fn in _tool_names:
                            curr_tool_calls.append(ToolCall(
                                name=fn, arguments=_parse_sim_args(raw),
                                id='sim_prose_' + fn))
                            break  # one at a time to avoid false positives
            # Save the message from this round
            msgs.append(ChatMessage(
                role="assistant",
                content=curr_content,
                tool_calls=[{
                    "id": tc.id or f"call_{i}",
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                } for i, tc in enumerate(curr_tool_calls)],
            ))
            final_content = curr_content
            
            if not curr_tool_calls and is_finished:
                return ChatResponse(content=final_content, done=True)
            
            if not curr_tool_calls and not is_finished:
                # Truncated! Loop back and let the model continue
                if on_text is not None:
                    on_text("\n[auto-continuing truncated message...]\n")
                continue
            
            # Execute tool calls and append results
            for tc in curr_tool_calls:
                if on_text is not None:
                    on_text(f"\n[executing tool: {tc.name}...]\n")
                result = dispatch_fn(tc.name, tc.arguments)
                if on_text is not None:
                    on_text(f"[tool result: {result.name}]\n")
                msgs.append(result.to_message())
        
        return ChatResponse(content=final_content or "[max rounds reached]", done=True)

# ── GPLv3 interactive notice ──────────────────────────────────────────────────

_GPLv3_WARRANTY = (
    "THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY\n"
    "APPLICABLE LAW. EXCEPT WHEN OTHERWISE STATED IN WRITING THE COPYRIGHT\n"
    'HOLDERS AND/OR OTHER PARTIES PROVIDE THE PROGRAM \"AS IS\" WITHOUT\n'
    "WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT\n"
    "LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A\n"
    "PARTICULAR PURPOSE. THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE\n"
    "OF THE PROGRAM IS WITH YOU.  (GPL-3.0-or-later §15)"
)

_GPLv3_CONDITIONS = (
    "You may convey verbatim copies of the Program's source code as you\n"
    "receive it, in any medium, provided that you conspicuously and\n"
    "appropriately publish on each copy an appropriate copyright notice and\n"
    "disclaimer of warranty. (See GPL-3.0 §4-6 for full conditions.)\n"
    "Full license: <https://www.gnu.org/licenses/gpl-3.0.html>"
)


def gplv3_notice():
    """Print the short GPLv3 startup notice. Call this at program startup."""
    print("S-IDE  Copyright (C) 2026  N0V4-N3XU5")
    print("This program comes with ABSOLUTELY NO WARRANTY; for details type 'show w'.")
    print("This is free software, and you are welcome to redistribute it")
    print("under certain conditions; type 'show c' for details.")


def gplv3_handle(cmd: str) -> bool:
    """
    Check whether *cmd* is a GPLv3 license command and handle it.
    Returns True if the command was consumed (caller should skip normal processing).
    """
    match cmd.strip().lower():
        case "show w":
            print(_GPLv3_WARRANTY)
            return True
        case "show c":
            print(_GPLv3_CONDITIONS)
            return True
    return False

# ─────────────────────────────────────────────────────────────────────────────
