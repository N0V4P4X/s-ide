/**
 * main.js — S-IDE Electron main process
 *
 * Replaces server/index.js. All backend logic runs here:
 *   - Window management
 *   - IPC handlers (parse, projects, processes, versions)
 *   - Process manager
 *   - Version manager
 *   - File dialogs
 */

import { app, BrowserWindow, ipcMain, dialog, shell } from 'electron';
import path from 'node:path';
import fs   from 'node:fs';
import { fileURLToPath } from 'node:url';

import { parseProject }   from './parser/projectParser.js';
import { ProcessManager } from './server/processManager.js';
import {
  archiveVersion, compressVersions,
  applyUpdate, listVersions,
} from './server/versionManager.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ─── Projects persistence ─────────────────────────────────────────────────────
const PROJECTS_FILE = path.join(app.getPath('userData'), 'projects.json');

function loadProjects() {
  try { return JSON.parse(fs.readFileSync(PROJECTS_FILE, 'utf8')); }
  catch { return []; }
}
function saveProjects(list) {
  fs.mkdirSync(path.dirname(PROJECTS_FILE), { recursive: true });
  fs.writeFileSync(PROJECTS_FILE, JSON.stringify(list, null, 2));
}

// ─── Process manager ──────────────────────────────────────────────────────────
const procMgr = new ProcessManager();

// ─── Window ───────────────────────────────────────────────────────────────────
let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width:  1400,
    height: 900,
    minWidth:  900,
    minHeight: 600,
    title: 'S-IDE',
    backgroundColor: '#080808',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    webPreferences: {
      preload:          path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration:  false,
      sandbox:          false,
    },
    icon: path.join(__dirname, 'renderer', 'icon.png'),
    show: false,
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    // Open devtools in dev mode
    if (process.env.SIDE_DEV) mainWindow.webContents.openDevTools();
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  procMgr.stopAll();
  if (process.platform !== 'darwin') app.quit();
});

// ─── IPC: Projects ────────────────────────────────────────────────────────────

ipcMain.handle('projects:list', () => {
  return loadProjects();
});

ipcMain.handle('projects:parse', async (_, { projectPath }) => {
  if (!projectPath) throw new Error('projectPath required');
  const abs = path.resolve(projectPath);
  if (!fs.existsSync(abs)) throw new Error(`Path does not exist: ${abs}`);
  const graph = await parseProject(abs);
  const list = loadProjects();
  if (!list.find(p => p.path === abs)) {
    list.unshift({ path: abs, name: path.basename(abs), addedAt: new Date().toISOString() });
    saveProjects(list);
  }
  return graph;
});

ipcMain.handle('projects:remove', (_, { projectPath }) => {
  saveProjects(loadProjects().filter(p => p.path !== projectPath));
  return { ok: true };
});

// Native folder picker dialog
ipcMain.handle('projects:pickFolder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: 'Open Project Folder',
  });
  return result.canceled ? null : result.filePaths[0];
});

// ─── IPC: Processes ───────────────────────────────────────────────────────────

ipcMain.handle('processes:list', () => procMgr.list());

ipcMain.handle('processes:start', (_, { name, command, cwd }) => {
  if (!command) throw new Error('command required');
  const proc = procMgr.start({ name: name || command, command, cwd });

  // Forward stdout/stderr to renderer via webContents.send
  proc.on('stdout', line => mainWindow?.webContents.send('process:stdout', { id: proc.id, line }));
  proc.on('stderr', line => mainWindow?.webContents.send('process:stderr', { id: proc.id, line }));
  proc.on('exit',   code => mainWindow?.webContents.send('process:exit',   { id: proc.id, code }));
  mainWindow?.webContents.send('process:started', proc.info());

  return proc.info();
});

ipcMain.handle('processes:stop',    (_, { id }) => {
  const ok = procMgr.stop(id);
  if (ok) mainWindow?.webContents.send('process:stopped', { id });
  return { ok };
});
ipcMain.handle('processes:suspend', (_, { id }) => {
  const ok = procMgr.suspend(id);
  if (ok) mainWindow?.webContents.send('process:suspended', { id });
  return { ok };
});
ipcMain.handle('processes:resume',  (_, { id }) => {
  const ok = procMgr.resume(id);
  if (ok) mainWindow?.webContents.send('process:resumed', { id });
  return { ok };
});
ipcMain.handle('processes:logs',    (_, { id }) => procMgr.logs(id) ?? []);

// ─── IPC: Versions ────────────────────────────────────────────────────────────

ipcMain.handle('versions:list', (_, { projectPath }) => {
  return listVersions(projectPath);
});

ipcMain.handle('versions:archive', async (_, { projectPath }) => {
  const archivePath = await archiveVersion(projectPath);
  return { ok: true, archivePath };
});

ipcMain.handle('versions:compress', async (_, { projectPath }) => {
  const results = await compressVersions(projectPath);
  return { ok: true, results };
});

// Update: pick a tarball via dialog, then apply
ipcMain.handle('versions:pickAndUpdate', async (_, { projectPath, bumpPart }) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Select Update Tarball',
    filters: [{ name: 'Tarballs', extensions: ['tar.gz', 'tgz'] }],
    properties: ['openFile'],
  });
  if (result.canceled) return { canceled: true };

  const gzBuf = fs.readFileSync(result.filePaths[0]);
  const { newVersion, archivePath } = await applyUpdate(projectPath, gzBuf, bumpPart || 'patch');
  const graph = await parseProject(path.resolve(projectPath));
  return { ok: true, newVersion, archivePath, graph };
});

// Also support drop (renderer sends the file path)
ipcMain.handle('versions:applyUpdate', async (_, { projectPath, filePath, bumpPart }) => {
  const gzBuf = fs.readFileSync(filePath);
  const { newVersion, archivePath } = await applyUpdate(projectPath, gzBuf, bumpPart || 'patch');
  const graph = await parseProject(path.resolve(projectPath));
  return { ok: true, newVersion, archivePath, graph };
});

// ─── IPC: Shell utilities ─────────────────────────────────────────────────────

ipcMain.handle('shell:openPath', (_, { filePath }) => {
  shell.openPath(filePath);
  return { ok: true };
});

ipcMain.handle('shell:openExternal', (_, { url }) => {
  shell.openExternal(url);
  return { ok: true };
});
