/**
 * server/versionManager.js
 * Handles project versioning operations:
 *   - archiveVersion()    snapshot current project into versions/
 *   - compressVersions()  gzip any loose version dirs in versions/
 *   - applyUpdate()       extract a tarball over the project, archive first
 *   - listVersions()      list all versions with metadata
 */

import fs   from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { loadProjectConfig, saveProjectConfig, bumpVersion } from '../parser/projectConfig.js';

// ─── Tar writer (pure Node.js) ────────────────────────────────────────────────

function encodeOctal(n, len) {
  return n.toString(8).padStart(len - 1, '0') + '\0';
}

function makeTarHeader(filePath, size, mtime, isDir) {
  const buf = Buffer.alloc(512, 0);
  const name = filePath.slice(0, 100);
  buf.write(name, 0, 'utf8');                             // name
  buf.write(encodeOctal(isDir ? 0o755 : 0o644, 8), 100); // mode
  buf.write(encodeOctal(0, 8), 108);                      // uid
  buf.write(encodeOctal(0, 8), 116);                      // gid
  buf.write(encodeOctal(size, 12), 124);                  // size
  buf.write(encodeOctal(Math.floor(mtime / 1000), 12), 136); // mtime
  buf.write('        ', 148);                             // checksum placeholder
  buf[156] = isDir ? 0x35 : 0x30;                        // typeflag: '5' dir, '0' file
  buf.write('ustar\0', 257);                              // magic
  buf.write('00', 263);                                   // version
  // Compute checksum
  let sum = 0;
  for (let i = 0; i < 512; i++) sum += buf[i];
  buf.write(encodeOctal(sum, 7) + ' ', 148);
  return buf;
}

function tarDirectory(srcDir, tarStream) {
  const BLOCK = 512;

  function addEntry(absPath, relPath) {
    const stat = fs.statSync(absPath);
    if (stat.isDirectory()) {
      tarStream.push(makeTarHeader(relPath + '/', 0, stat.mtimeMs, true));
      for (const entry of fs.readdirSync(absPath).sort()) {
        addEntry(path.join(absPath, entry), relPath + '/' + entry);
      }
    } else {
      const data = fs.readFileSync(absPath);
      tarStream.push(makeTarHeader(relPath, data.length, stat.mtimeMs, false));
      // Data padded to 512-byte blocks
      const padded = Buffer.alloc(Math.ceil(data.length / BLOCK) * BLOCK, 0);
      data.copy(padded);
      tarStream.push(padded);
    }
  }

  const name = path.basename(srcDir);
  addEntry(srcDir, name);
  // End-of-archive: two 512-byte zero blocks
  tarStream.push(Buffer.alloc(1024, 0));
}

function dirToTarGz(srcDir, destFile) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    tarDirectory(srcDir, chunks);
    const tarBuf = Buffer.concat(chunks);
    zlib.gzip(tarBuf, (err, gz) => {
      if (err) return reject(err);
      fs.writeFileSync(destFile, gz);
      resolve(destFile);
    });
  });
}

// ─── Tar reader (for applying updates) ───────────────────────────────────────

function parseTar(tarBuf, outDir) {
  let off = 0;
  while (off + 512 <= tarBuf.length) {
    const hdr      = tarBuf.slice(off, off + 512);
    const name     = hdr.slice(0, 100).toString('utf8').replace(/\0/g, '').trim();
    if (!name) { off += 512; continue; }
    const size     = parseInt(hdr.slice(124, 136).toString().replace(/\0/g, '').trim(), 8) || 0;
    const typeflag = String.fromCharCode(hdr[156]);
    off += 512;
    // Sanitize path
    const safe = name.replace(/^[./\\]+/, '').replace(/\.\.\//g, '');
    // Strip top-level dir prefix if present (common in tarballs)
    const parts = safe.split('/');
    const stripped = parts.length > 1 && !parts[0].includes('.') ? parts.slice(1).join('/') : safe;
    if (stripped) {
      const dest = path.join(outDir, stripped);
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

async function extractTarGz(gzBuf, destDir) {
  const tarBuf = await new Promise((res, rej) => {
    zlib.gunzip(gzBuf, (err, buf) => err ? rej(err) : res(buf));
  });
  parseTar(tarBuf, destDir);
}

// ─── Version operations ───────────────────────────────────────────────────────

/**
 * Copy/snapshot the current project into versions/<version>-<timestamp>/
 * Returns the path of the snapshot dir.
 */
export async function archiveVersion(projectRoot) {
  const config     = loadProjectConfig(projectRoot);
  const versionsDir = path.join(projectRoot, config.versions?.dir || 'versions');
  const version    = config.version || '0.0.0';
  const ts         = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const snapName   = `v${version}-${ts}`;
  const snapDir    = path.join(versionsDir, snapName);

  fs.mkdirSync(versionsDir, { recursive: true });

  if (config.versions?.compress) {
    // Compress directly to .tar.gz
    const tarPath = snapDir + '.tar.gz';
    await dirToTarGz(projectRoot, tarPath);
    pruneOldVersions(versionsDir, config.versions?.keep ?? 20);
    return tarPath;
  } else {
    // Copy as loose directory
    copyDirSync(projectRoot, snapDir, [config.versions?.dir || 'versions']);
    pruneOldVersions(versionsDir, config.versions?.keep ?? 20);
    return snapDir;
  }
}

/**
 * Compress any loose version directories in versions/ to .tar.gz
 * and remove the originals. Returns list of compressed paths.
 */
export async function compressVersions(projectRoot) {
  const config      = loadProjectConfig(projectRoot);
  const versionsDir = path.join(projectRoot, config.versions?.dir || 'versions');
  if (!fs.existsSync(versionsDir)) return [];

  const entries = fs.readdirSync(versionsDir, { withFileTypes: true });
  const results = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const dirPath = path.join(versionsDir, entry.name);
    const tarPath = dirPath + '.tar.gz';
    try {
      await dirToTarGz(dirPath, tarPath);
      fs.rmSync(dirPath, { recursive: true, force: true });
      results.push({ name: entry.name, tarball: tarPath });
    } catch(e) {
      results.push({ name: entry.name, error: e.message });
    }
  }
  return results;
}

/**
 * Apply an update tarball to a project:
 *   1. Archive current version first
 *   2. Extract the tarball over the project dir
 *   3. Bump version in side.project.json
 *   4. Return new version string
 */
export async function applyUpdate(projectRoot, gzBuf, bumpPart = 'patch') {
  // 1. Archive current state
  const archivePath = await archiveVersion(projectRoot);

  // 2. Extract new version over project
  await extractTarGz(gzBuf, projectRoot);

  // 3. Bump version
  const config     = loadProjectConfig(projectRoot);
  const newVersion = bumpVersion(config.version, bumpPart);
  config.version   = newVersion;
  saveProjectConfig(projectRoot, config);

  return { newVersion, archivePath };
}

/**
 * List all versions in the versions/ dir with size + date metadata.
 */
export function listVersions(projectRoot) {
  const config      = loadProjectConfig(projectRoot);
  const versionsDir = path.join(projectRoot, config.versions?.dir || 'versions');
  if (!fs.existsSync(versionsDir)) return [];

  return fs.readdirSync(versionsDir, { withFileTypes: true })
    .filter(e => e.isFile() && e.name.endsWith('.tar.gz') || e.isDirectory())
    .map(e => {
      const fullPath = path.join(versionsDir, e.name);
      const stat     = fs.statSync(fullPath);
      return {
        name:      e.name,
        type:      e.isDirectory() ? 'dir' : 'tarball',
        size:      stat.size,
        modified:  stat.mtime.toISOString(),
        path:      fullPath,
      };
    })
    .sort((a, b) => b.modified.localeCompare(a.modified));
}

// ─── Utilities ────────────────────────────────────────────────────────────────

function copyDirSync(src, dest, excludeDirs = []) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue;
    if (excludeDirs.includes(entry.name)) continue;
    const s = path.join(src, entry.name);
    const d = path.join(dest, entry.name);
    if (entry.isDirectory()) copyDirSync(s, d, excludeDirs);
    else fs.copyFileSync(s, d);
  }
}

function pruneOldVersions(versionsDir, keep = 20) {
  if (!fs.existsSync(versionsDir)) return;
  const entries = fs.readdirSync(versionsDir, { withFileTypes: true })
    .map(e => ({
      name: e.name,
      path: path.join(versionsDir, e.name),
      mtime: fs.statSync(path.join(versionsDir, e.name)).mtimeMs,
    }))
    .sort((a, b) => b.mtime - a.mtime);

  for (const entry of entries.slice(keep)) {
    fs.rmSync(entry.path, { recursive: true, force: true });
  }
}
