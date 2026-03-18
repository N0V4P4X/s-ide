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
from typing import Iterator, Callable


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
    TIMEOUT       = 120   # seconds for non-streaming

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
                        if on_progress:
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
        max_rounds: int = 10,  # Increased for better troubleshooting
        on_text:    Callable[[str], None] | None = None,
    ) -> ChatResponse:
        """
        Agentic loop with real-time streaming and troubleshooting support.
        """
        msgs = list(messages)
        final_content = ""
        
        for _ in range(max_rounds):
            payload: dict = {
                "model":    model,
                "messages": [m.to_dict() for m in msgs],
                "stream":   True,
                "tools":    tools,
            }
            url  = f"{self.host}/api/chat"
            body = json.dumps(payload).encode()
            req  = urllib.request.Request(url, data=body,
                                           headers={"Content-Type": "application/json"})
            
            curr_content = ""
            curr_tool_calls = []
            
            try:
                for d in self._stream_raw(req):
                    if d.get("error"):
                        if on_text: on_text(f"\n[error: {d['error']}]")
                        break
                    
                    msg = d.get("message", {})
                    content = msg.get("content", "")
                    if content:
                        curr_content += content
                        if on_text: on_text(content)
                    
                    tcs = msg.get("tool_calls", [])
                    if tcs:
                        for tc in tcs:
                            fn = tc.get("function", {})
                            curr_tool_calls.append(ToolCall(
                                name=fn.get("name", ""),
                                arguments=fn.get("arguments", {}),
                                id=tc.get("id", "")
                            ))
            except Exception as e:
                if on_text: on_text(f"\n[HTTP Error: {e}]")
                break

            # --- SIMULATION INTERCEPTOR ---
            # If the model didn't use formal tools, but wrote something like `create_plan(steps=["X"])`
            if not curr_tool_calls and "```python" in curr_content:
                import re
                # Look for calls like name(arg="val", arg2=["list"])
                sim_matches = re.finditer(r'([a-z_]+)\(([^)]+)\)', curr_content)
                for m in sim_matches:
                    fn_name = m.group(1)
                    raw_args = m.group(2)
                    # Check if this name is in our tool list
                    if any(t['function']['name'] == fn_name for t in tools):
                        # Attempt to parse args (very loose)
                        try:
                            # Try to extract key-value pairs
                            args = {}
                            arg_parts = re.split(r',\s*(?=[a-z_]+=)', raw_args)
                            for part in arg_parts:
                                if '=' in part:
                                    k, v = part.split('=', 1)
                                    # Basic json-ification of the value
                                    v = v.strip().strip("'").strip('"')
                                    if v.startswith('[') and v.endswith(']'):
                                        try: v = json.loads(v.replace("'", '"'))
                                        except: pass
                                    args[k.strip()] = v
                            curr_tool_calls.append(ToolCall(name=fn_name, arguments=args, id=f"sim_{m.start()}"))
                        except: pass
            
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
            
            if not curr_tool_calls:
                return ChatResponse(content=final_content, done=True)
            
            # Execute tool calls and append results
            for tc in curr_tool_calls:
                if on_text: on_text(f"\n[executing tool: {tc.name}...]\n")
                result = dispatch_fn(tc.name, tc.arguments)
                if on_text: on_text(f"[tool result: {result.name}]\n")
                msgs.append(result.to_message())
        
        return ChatResponse(content=final_content or "[max rounds reached]", done=True)
