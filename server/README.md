# server/

Zero-dependency HTTP + WebSocket backend. No npm packages — pure Node.js built-ins (`node:http`, `node:crypto`, `node:zlib`, `node:child_process`).

## Files

| File | Purpose |
|------|---------|
| `index.js` | HTTP server, WebSocket upgrade, all route handlers, tar extraction |
| `router.js` | Tiny express-like router with `:param` support |
| `processManager.js` | Spawn, stream, kill, suspend, resume child processes |

## Starting

```bash
node server/index.js
# or with auto-restart on file change:
node --watch server/index.js
# custom port:
PORT=8080 node server/index.js
```

## REST API

| Method | Path | Body | Description |
|--------|------|------|-------------|
| GET | `/api/projects` | — | List known projects |
| POST | `/api/parse` | `{projectPath}` | Parse a project by absolute path |
| POST | `/api/upload-tarball` | multipart `.tar.gz` | Extract + parse in one shot |
| DELETE | `/api/projects` | `{projectPath}` | Remove from known list (no file deletion) |
| GET | `/api/processes` | — | List all managed processes |
| POST | `/api/processes/start` | `{command, cwd?, name?}` | Start a process |
| POST | `/api/processes/:id/stop` | — | SIGTERM (SIGKILL after 3s) |
| POST | `/api/processes/:id/suspend` | — | SIGSTOP — pauses without killing |
| POST | `/api/processes/:id/resume` | — | SIGCONT — resumes suspended |
| GET | `/api/processes/:id/logs` | — | Last 200 lines of stdout/stderr |

## WebSocket

Connect to `ws://localhost:7700`. Messages are JSON frames.

**Server → client push events:**

| `type` | Payload | When |
|--------|---------|------|
| `connected` | `{version}` | On connect |
| `pong` | — | Response to `ping` |
| `process:started` | `{process}` | New process spawned |
| `process:stdout` | `{id, line}` | stdout line |
| `process:stderr` | `{id, line}` | stderr line |
| `process:exit` | `{id, code}` | Process exited |
| `process:stopped` | `{id}` | Manually stopped |
| `process:suspended` | `{id}` | SIGSTOP sent |
| `process:resumed` | `{id}` | SIGCONT sent |

**Client → server:**

| `type` | Description |
|--------|-------------|
| `ping` | Keepalive |

## Tar extraction

Pure Node.js implementation in `index.js` — no `tar` npm package needed.
Handles multipart form upload, gunzip via `node:zlib`, custom TAR block parser.
Sanitizes paths (no `../` traversal). Extracted projects land in `../uploads/proj-{timestamp}/`.
