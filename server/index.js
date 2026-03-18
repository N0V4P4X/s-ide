#!/usr/bin/env node
/**
 * server/index.js — S-IDE backend, zero npm dependencies.
 */

import http   from 'node:http';
import fs     from 'node:fs';
import path   from 'node:path';
import crypto from 'node:crypto';
import zlib   from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { parseProject }  from '../parser/projectParser.js';
import { ProcessManager } from './processManager.js';
import { Router }         from './router.js';
import { archiveVersion, compressVersions, applyUpdate, listVersions } from './versionManager.js';

const __dirname     = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR    = path.join(__dirname, '..', 'renderer');
const PROJECTS_FILE = path.join(__dirname, '..', 'projects.json');
const EXTRACT_DIR   = path.join(__dirname, '..', 'uploads');
const PORT          = process.env.PORT || 7700;

fs.mkdirSync(EXTRACT_DIR, { recursive: true });

// ─── Projects persistence ─────────────────────────────────────────────────────
function loadProjects() {
  try { return JSON.parse(fs.readFileSync(PROJECTS_FILE, 'utf8')); }
  catch { return []; }
}
function saveProjects(list) {
  fs.writeFileSync(PROJECTS_FILE, JSON.stringify(list, null, 2));
}

// ─── Process manager ──────────────────────────────────────────────────────────
const procMgr = new ProcessManager();

// ─── WebSocket (RFC 6455, pure Node.js) ──────────────────────────────────────
const wsClients = new Set();

function wsHandshake(req, socket) {
  const key    = req.headers['sec-websocket-key'];
  const accept = crypto.createHash('sha1')
    .update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
    .digest('base64');
  socket.write(
    'HTTP/1.1 101 Switching Protocols\r\n' +
    'Upgrade: websocket\r\nConnection: Upgrade\r\n' +
    `Sec-WebSocket-Accept: ${accept}\r\n\r\n`
  );
  wsClients.add(socket);
  socket.on('close', () => wsClients.delete(socket));
  socket.on('error', () => wsClients.delete(socket));
  socket.on('data',  buf => wsOnData(socket, buf));
  wsSend(socket, { type: 'connected', version: '0.1.0' });
}

function wsOnData(socket, buf) {
  try {
    const opcode = buf[0] & 0x0f;
    if (opcode === 0x8) { wsClients.delete(socket); socket.destroy(); return; }
    const masked = (buf[1] & 0x80) !== 0;
    let len = buf[1] & 0x7f;
    let off = 2;
    if (len === 126) { len = buf.readUInt16BE(2); off = 4; }
    const mask = masked ? buf.slice(off, off + 4) : null;
    off += masked ? 4 : 0;
    const data = buf.slice(off, off + len);
    if (masked && mask) for (let i = 0; i < data.length; i++) data[i] ^= mask[i % 4];
    if (opcode === 0x1) {
      try { const msg = JSON.parse(data.toString()); if (msg.type === 'ping') wsSend(socket, { type: 'pong' }); } catch {}
    }
  } catch {}
}

function wsSend(socket, obj) {
  if (!socket.writable) return;
  try {
    const data = Buffer.from(JSON.stringify(obj));
    const len  = data.length;
    const frame = len < 126
      ? Buffer.concat([Buffer.from([0x81, len]), data])
      : Buffer.concat([Buffer.from([0x81, 126, len >> 8, len & 0xff]), data]);
    socket.write(frame);
  } catch {}
}

function wsBroadcast(obj) { for (const s of wsClients) wsSend(s, obj); }

// ─── Utilities ────────────────────────────────────────────────────────────────
function readBody(req) {
  return new Promise((res, rej) => {
    const c = [];
    req.on('data', d => c.push(d));
    req.on('end',  () => res(Buffer.concat(c)));
    req.on('error', rej);
  });
}

function mimeType(ext) {
  return ({
    '.html': 'text/html', '.js': 'application/javascript',
    '.css': 'text/css',   '.json': 'application/json',
    '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
  })[ext] || 'text/plain';
}

// ─── Multipart parser ─────────────────────────────────────────────────────────
// Parses multipart/form-data into { fields: {name: value}, file: Buffer }
function parseMultipart(raw, boundary) {
  const sep = Buffer.from(`--${boundary}`);
  const fields = {};
  let file = null;

  let start = raw.indexOf(sep);
  while (start !== -1) {
    const after = start + sep.length;
    if (raw[after] === 0x2d && raw[after+1] === 0x2d) break; // final --

    const headerEnd = raw.indexOf(Buffer.from('\r\n\r\n'), after);
    if (headerEnd === -1) break;
    const header = raw.slice(after + 2, headerEnd).toString('utf8');

    const nextBound = raw.indexOf(sep, headerEnd + 4);
    const dataEnd   = nextBound > 0 ? nextBound - 2 : raw.length;
    const data      = raw.slice(headerEnd + 4, dataEnd);

    const nameMatch = header.match(/name="([^"]+)"/);
    const isFile    = header.includes('filename=');
    if (nameMatch) {
      if (isFile) {
        file = data;
      } else {
        fields[nameMatch[1]] = data.toString('utf8');
      }
    }
    start = nextBound;
  }
  return { fields, file };
}

// ─── Tarball extraction ───────────────────────────────────────────────────────
function parseTar(tarBuf, outDir) {
  let off = 0;
  while (off + 512 <= tarBuf.length) {
    const hdr  = tarBuf.slice(off, off + 512);
    const name = hdr.slice(0, 100).toString('utf8').replace(/\0/g, '').trim();
    if (!name) { off += 512; continue; }
    const size     = parseInt(hdr.slice(124, 136).toString().replace(/\0/g, '').trim(), 8) || 0;
    const typeflag = String.fromCharCode(hdr[156]);
    off += 512;
    const safe = name.replace(/^[./\\]+/, '').replace(/\.\.\//g, '');
    if (safe) {
      const dest = path.join(outDir, safe);
      if (typeflag === '5') {
        fs.mkdirSync(dest, { recursive: true });
      } else if (typeflag === '0' || typeflag === '\0') {
        fs.mkdirSync(path.dirname(dest), { recursive: true });
        fs.writeFileSync(dest, tarBuf.slice(off, off + size));
      }
    }
    off += Math.ceil(size / 512) * 512;
  }
}

async function handleTarball(req) {
  const ct       = req.headers['content-type'] || '';
  const boundary = ct.split('boundary=')[1]?.trim();
  if (!boundary) throw new Error('No multipart boundary');
  const raw  = await readBody(req);
  const { file: gz } = parseMultipart(raw, boundary);
  if (!gz) throw new Error('No file in upload');

  const tarBuf = await new Promise((res, rej) => {
    zlib.gunzip(gz, (err, buf) => err ? rej(err) : res(buf));
  });

  const outDir = path.join(EXTRACT_DIR, `proj-${Date.now()}`);
  fs.mkdirSync(outDir, { recursive: true });
  parseTar(tarBuf, outDir);

  const entries = fs.readdirSync(outDir);
  if (entries.length === 1 && fs.statSync(path.join(outDir, entries[0])).isDirectory()) {
    return path.join(outDir, entries[0]);
  }
  return outDir;
}

// ─── Routes ───────────────────────────────────────────────────────────────────
const router = new Router();

router.get('/api/projects', async (req, res) => {
  res.json(loadProjects());
});

router.post('/api/parse', async (req, res) => {
  try {
    const { projectPath } = JSON.parse((await readBody(req)).toString());
    if (!projectPath) return res.status(400).json({ error: 'projectPath required' });
    const abs = path.resolve(projectPath);
    if (!fs.existsSync(abs)) return res.status(400).json({ error: `Path does not exist: ${abs}` });
    const graph = await parseProject(abs);
    const list  = loadProjects();
    if (!list.find(p => p.path === abs)) {
      list.unshift({ path: abs, name: path.basename(abs), addedAt: new Date().toISOString() });
      saveProjects(list);
    }
    res.json(graph);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/api/upload-tarball', async (req, res) => {
  try {
    const projDir = await handleTarball(req);
    const graph   = await parseProject(projDir);
    const list    = loadProjects();
    if (!list.find(p => p.path === projDir)) {
      list.unshift({ path: projDir, name: path.basename(projDir), addedAt: new Date().toISOString(), fromTarball: true });
      saveProjects(list);
    }
    res.json({ graph, extractDir: projDir });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.delete('/api/projects', async (req, res) => {
  const { projectPath } = JSON.parse((await readBody(req)).toString());
  saveProjects(loadProjects().filter(p => p.path !== projectPath));
  res.json({ ok: true });
});

router.get('/api/processes', async (req, res) => {
  res.json(procMgr.list());
});

router.post('/api/processes/start', async (req, res) => {
  try {
    const { name, command, cwd } = JSON.parse((await readBody(req)).toString());
    if (!command) return res.status(400).json({ error: 'command required' });
    const proc = procMgr.start({ name: name || command, command, cwd });
    proc.on('stdout', line => wsBroadcast({ type: 'process:stdout', id: proc.id, line }));
    proc.on('stderr', line => wsBroadcast({ type: 'process:stderr', id: proc.id, line }));
    proc.on('exit',   code => wsBroadcast({ type: 'process:exit',   id: proc.id, code }));
    wsBroadcast({ type: 'process:started', process: proc.info() });
    res.json(proc.info());
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/api/processes/:id/stop', async (req, res) => {
  if (!procMgr.stop(req.params.id)) return res.status(404).json({ error: 'Not found' });
  wsBroadcast({ type: 'process:stopped', id: req.params.id });
  res.json({ ok: true });
});

router.post('/api/processes/:id/suspend', async (req, res) => {
  if (!procMgr.suspend(req.params.id)) return res.status(404).json({ error: 'Not found' });
  wsBroadcast({ type: 'process:suspended', id: req.params.id });
  res.json({ ok: true });
});

router.post('/api/processes/:id/resume', async (req, res) => {
  if (!procMgr.resume(req.params.id)) return res.status(404).json({ error: 'Not found' });
  wsBroadcast({ type: 'process:resumed', id: req.params.id });
  res.json({ ok: true });
});

router.get('/api/processes/:id/logs', async (req, res) => {
  const logs = procMgr.logs(req.params.id);
  if (!logs) return res.status(404).json({ error: 'Not found' });
  res.json(logs);
});

// ─── Version management routes ────────────────────────────────────────────────

// GET /api/projects/:encodedPath/versions — list archived versions
router.get('/api/versions', async (req, res) => {
  const projectPath = new URL('http://x' + req.url).searchParams.get('path');
  if (!projectPath || !fs.existsSync(projectPath)) return res.status(400).json({ error: 'Invalid path' });
  res.json(listVersions(projectPath));
});

// POST /api/versions/archive — snapshot current project to versions/
router.post('/api/versions/archive', async (req, res) => {
  const { projectPath } = JSON.parse((await readBody(req)).toString());
  if (!projectPath || !fs.existsSync(projectPath)) return res.status(400).json({ error: 'Invalid path' });
  try {
    const archivePath = await archiveVersion(projectPath);
    res.json({ ok: true, archivePath });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

// POST /api/versions/compress — compress loose version dirs to .tar.gz
router.post('/api/versions/compress', async (req, res) => {
  const { projectPath } = JSON.parse((await readBody(req)).toString());
  if (!projectPath || !fs.existsSync(projectPath)) return res.status(400).json({ error: 'Invalid path' });
  try {
    const results = await compressVersions(projectPath);
    res.json({ ok: true, results });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

// POST /api/versions/update — upload new tarball, archive old, apply, bump version
router.post('/api/versions/update', async (req, res) => {
  const ct       = req.headers['content-type'] || '';
  const boundary = ct.split('boundary=')[1]?.trim();
  if (!boundary) return res.status(400).json({ error: 'No multipart boundary' });

  try {
    const raw  = await readBody(req);
    // Extract fields: projectPath, bumpPart, file
    const parts = parseMultipart(raw, boundary);
    const projectPath = parts.fields?.projectPath;
    const bumpPart    = parts.fields?.bumpPart || 'patch';
    const fileData    = parts.file;

    if (!projectPath || !fs.existsSync(projectPath)) return res.status(400).json({ error: 'Invalid projectPath' });
    if (!fileData) return res.status(400).json({ error: 'No file uploaded' });

    const { newVersion, archivePath } = await applyUpdate(projectPath, fileData, bumpPart);

    // Re-parse after update
    const graph = await parseProject(path.resolve(projectPath));
    res.json({ ok: true, newVersion, archivePath, graph });
  } catch(e) {
    res.status(500).json({ error: e.message, stack: e.stack });
  }
});

// ─── HTTP + WS server ─────────────────────────────────────────────────────────
const server = http.createServer(async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,DELETE,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  if (await router.handle(req, res)) return;

  const filePath = req.url.split('?')[0] === '/' ? '/index.html' : req.url.split('?')[0];
  const abs      = path.join(PUBLIC_DIR, filePath);
  if (fs.existsSync(abs) && fs.statSync(abs).isFile()) {
    res.writeHead(200, { 'Content-Type': mimeType(path.extname(abs)) });
    fs.createReadStream(abs).pipe(res);
  } else {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    fs.createReadStream(path.join(PUBLIC_DIR, 'index.html')).pipe(res);
  }
});

server.on('upgrade', (req, socket) => {
  if (req.headers.upgrade?.toLowerCase() === 'websocket') wsHandshake(req, socket);
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`\n  ╔══════════════════════════════════════════╗`);
  console.log(`  ║  S-IDE  v0.1.0  —  Phase 1              ║`);
  console.log(`  ║  http://localhost:${PORT}                 ║`);
  console.log(`  ╚══════════════════════════════════════════╝\n`);
  console.log(`  Public dir:  ${PUBLIC_DIR}`);
  console.log(`  Projects:    ${PROJECTS_FILE}\n`);
});

process.on('SIGTERM', () => { procMgr.stopAll(); process.exit(0); });
process.on('SIGINT',  () => { procMgr.stopAll(); process.exit(0); });
