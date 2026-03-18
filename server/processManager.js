/**
 * server/processManager.js
 * Manages child processes spawned from the IDE.
 * Supports start, stop (SIGTERM), suspend (SIGSTOP), resume (SIGCONT).
 * Streams stdout/stderr via EventEmitter to the WebSocket layer.
 */

import { spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { randomBytes } from 'node:crypto';

class ManagedProcess extends EventEmitter {
  constructor({ id, name, command, cwd }) {
    super();
    this.id = id;
    this.name = name;
    this.command = command;
    this.cwd = cwd;
    this.status = 'running'; // running | stopped | suspended | crashed
    this.startedAt = new Date().toISOString();
    this.exitCode = null;
    this.pid = null;
    this.recentLines = [];   // ring buffer: last 200 lines
    this._proc = null;
  }

  _pushLine(stream, line) {
    const entry = { stream, line, ts: Date.now() };
    this.recentLines.push(entry);
    if (this.recentLines.length > 200) this.recentLines.shift();
    this.emit(stream, line);
  }

  spawn() {
    // Split command string into executable + args
    const parts = this.command.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g) || [];
    const [cmd, ...args] = parts.map(p => p.replace(/^['"]|['"]$/g, ''));

    this._proc = spawn(cmd, args, {
      cwd: this.cwd || process.cwd(),
      shell: true,
      env: process.env,
    });

    this.pid = this._proc.pid;

    this._proc.stdout.on('data', chunk => {
      chunk.toString().split('\n').filter(Boolean).forEach(l => this._pushLine('stdout', l));
    });
    this._proc.stderr.on('data', chunk => {
      chunk.toString().split('\n').filter(Boolean).forEach(l => this._pushLine('stderr', l));
    });
    this._proc.on('exit', (code, signal) => {
      this.exitCode = code;
      this.status = code === 0 ? 'stopped' : 'crashed';
      this.emit('exit', code, signal);
    });
    this._proc.on('error', err => {
      this.status = 'crashed';
      this._pushLine('stderr', `spawn error: ${err.message}`);
      this.emit('exit', -1);
    });

    return this;
  }

  stop() {
    if (!this._proc || this.status === 'stopped') return false;
    try {
      this._proc.kill('SIGTERM');
      setTimeout(() => {
        if (this.status === 'running') this._proc.kill('SIGKILL');
      }, 3000);
      this.status = 'stopped';
      return true;
    } catch { return false; }
  }

  suspend() {
    if (!this._proc || this.status !== 'running') return false;
    try { process.kill(this.pid, 'SIGSTOP'); this.status = 'suspended'; return true; }
    catch { return false; }
  }

  resume() {
    if (!this._proc || this.status !== 'suspended') return false;
    try { process.kill(this.pid, 'SIGCONT'); this.status = 'running'; return true; }
    catch { return false; }
  }

  info() {
    return {
      id:         this.id,
      name:       this.name,
      command:    this.command,
      cwd:        this.cwd,
      status:     this.status,
      pid:        this.pid,
      startedAt:  this.startedAt,
      exitCode:   this.exitCode,
      lines:      this.recentLines.length,
    };
  }
}

export class ProcessManager {
  constructor() {
    this.processes = new Map(); // id -> ManagedProcess
  }

  start({ name, command, cwd }) {
    const id = randomBytes(4).toString('hex');
    const proc = new ManagedProcess({ id, name, command, cwd });
    proc.spawn();
    this.processes.set(id, proc);
    return proc;
  }

  stop(id) {
    const proc = this.processes.get(id);
    return proc ? proc.stop() : false;
  }

  suspend(id) {
    const proc = this.processes.get(id);
    return proc ? proc.suspend() : false;
  }

  resume(id) {
    const proc = this.processes.get(id);
    return proc ? proc.resume() : false;
  }

  get(id) {
    return this.processes.get(id);
  }

  list() {
    return [...this.processes.values()].map(p => p.info());
  }

  stopAll() {
    for (const proc of this.processes.values()) proc.stop();
  }

  // Return log lines for a process (for /api/processes/:id/logs)
  logs(id) {
    const proc = this.processes.get(id);
    return proc ? proc.recentLines : null;
  }
}
