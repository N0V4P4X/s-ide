# ai/

Ollama-powered AI assistant embedded in S-IDE.

## Architecture

```
ai/
├── client.py    — Ollama HTTP client (streaming, tool loop)
├── tools.py     — 8 tool definitions + dispatch
├── context.py   — AppContext built from live graph state
└── standards.py — Hidden system prompt (dev standards, behaviour rules)
```

## client.py

Talks to `http://localhost:11434` (standard Ollama port). No external deps — pure `urllib`.

```python
from ai.client import OllamaClient, ChatMessage

client = OllamaClient()
client.is_available()        # True if ollama serve is running
client.list_models()         # ["llama3.2", "codellama", ...]

# Streaming chat
for chunk in client.chat("llama3.2", messages, stream=True):
    print(chunk, end="", flush=True)

# Tool-calling agentic loop
response = client.chat_with_tools(
    model="llama3.2",
    messages=messages,
    tools=TOOLS,
    dispatch_fn=dispatch_tool,
    on_text=lambda chunk: print(chunk, end=""),
)
```

## tools.py

| Tool | What it does |
|---|---|
| `read_file` | Read any project file (capped at 8000 chars) |
| `list_files` | List files, filter by extension or subdirectory |
| `get_file_summary` | Imports, exports, definitions with args/complexity |
| `search_definitions` | Find functions/classes by name across all files |
| `get_graph_overview` | Project structure, language stats, doc health |
| `get_metrics` | Live timing from `.side-metrics.json` |
| `run_command` | Run a `side.project.json` script (30s timeout) |
| `get_definition_source` | Source lines for a specific function/class |

## standards.py

The system prompt injected at the start of every conversation. Never shown to the user directly. Covers:
- Code quality rules (one responsibility per function, complexity limits)
- Python specifics (type hints, f-strings, dataclasses, context managers)
- Testing standards (isolation, naming, tempfile for fs tests)
- Architecture rules (layer imports, side effects at edges)
- Performance guidelines (ParseTimer, @timed, canvas throttle)
- Response style (direct, show code, quantify problems)

## GUI usage

- Click `✦ AI` in the topbar to open the panel
- The AI sees the current project graph, focused file, and live metrics
- Right-click any node → "Ask AI about this file"
- Double-click any node → open in editor → "Ask AI" button in toolbar
- The AI uses tools proactively — no need to ask it to read files first

## Running Ollama

```bash
# Install: https://ollama.ai
ollama serve              # start the server
ollama pull llama3.2      # download a model
ollama pull codellama     # better for code tasks
```
