/**
 * preload.js — Electron context bridge (CommonJS — required by Electron)
 *
 * MUST be CommonJS. Electron preload scripts cannot use ESM import/export.
 * Exposes window.api to the renderer via contextBridge.
 */

'use strict';
const { contextBridge, ipcRenderer } = require('electron');

// ─── Helpers ──────────────────────────────────────────────────────────────────
const invoke = (channel, args) => ipcRenderer.invoke(channel, args !== undefined ? args : {});

function on(channel, callback) {
  const handler = (_, data) => callback(data);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

// ─── Expose API ───────────────────────────────────────────────────────────────
contextBridge.exposeInMainWorld('api', {

  projects: {
    list:       ()            => invoke('projects:list'),
    parse:      (projectPath) => invoke('projects:parse',     { projectPath }),
    remove:     (projectPath) => invoke('projects:remove',    { projectPath }),
    pickFolder: ()            => invoke('projects:pickFolder'),
  },

  processes: {
    list:    ()                   => invoke('processes:list'),
    start:   (name, command, cwd) => invoke('processes:start',   { name, command, cwd }),
    stop:    (id)                 => invoke('processes:stop',    { id }),
    suspend: (id)                 => invoke('processes:suspend', { id }),
    resume:  (id)                 => invoke('processes:resume',  { id }),
    logs:    (id)                 => invoke('processes:logs',    { id }),
  },

  versions: {
    list:          (projectPath)                     => invoke('versions:list',          { projectPath }),
    archive:       (projectPath)                     => invoke('versions:archive',       { projectPath }),
    compress:      (projectPath)                     => invoke('versions:compress',      { projectPath }),
    pickAndUpdate: (projectPath, bumpPart)           => invoke('versions:pickAndUpdate', { projectPath, bumpPart }),
    applyUpdate:   (projectPath, filePath, bumpPart) => invoke('versions:applyUpdate',   { projectPath, filePath, bumpPart }),
  },

  shell: {
    openPath:     (filePath) => invoke('shell:openPath',     { filePath }),
    openExternal: (url)      => invoke('shell:openExternal', { url }),
  },

  on: {
    processStarted:   (cb) => on('process:started',   cb),
    processStopped:   (cb) => on('process:stopped',   cb),
    processSuspended: (cb) => on('process:suspended', cb),
    processResumed:   (cb) => on('process:resumed',   cb),
    processExit:      (cb) => on('process:exit',      cb),
    processStdout:    (cb) => on('process:stdout',    cb),
    processStderr:    (cb) => on('process:stderr',    cb),
  },

});
