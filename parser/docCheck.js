/**
 * docCheck.js
 * Audits a parsed NodeGraph for documentation health.
 *
 * Checks:
 *   1. Every directory that contains source files has a README.md
 *   2. Every README.md was modified more recently than the files in its directory
 *   3. Flags files with no exports and no definitions as "orphan" candidates
 *
 * Returns a DocAudit object that gets merged into the NodeGraph meta.
 * The frontend uses this to render warning badges on nodes/folders.
 */

import { readdirSync, statSync } from 'fs';
import { join, dirname, basename } from 'path';

/**
 * @param {string} rootDir
 * @param {import('./projectParser.js').FileNode[]} nodes
 * @returns {DocAudit}
 */
export function auditDocs(rootDir, nodes) {
  const warnings = [];

  // Build a map: dirPath -> { readmeMtime, files: [{path, mtime}] }
  const dirMap = new Map();

  for (const node of nodes) {
    if (node.isExternal) continue;
    const dir = dirname(join(rootDir, node.path));
    const rel = dir.replace(rootDir, '').replace(/^[/\\]/, '') || '.';

    if (!dirMap.has(rel)) {
      dirMap.set(rel, { readmeMtime: null, readmePath: null, files: [] });
    }

    const entry = dirMap.get(rel);

    if (node.ext === '.md' && basename(node.path).toLowerCase() === 'readme.md') {
      entry.readmeMtime = node.modified ? new Date(node.modified).getTime() : null;
      entry.readmePath = node.path;
    } else {
      // Only count parseable source files, not docs themselves
      const ignoredExts = new Set(['.md', '.mdx', '.txt', '.lock', '.log']);
      if (!ignoredExts.has(node.ext)) {
        entry.files.push({
          path: node.path,
          mtime: node.modified ? new Date(node.modified).getTime() : null,
          id: node.id,
        });
      }
    }
  }

  // ── Check 1: Missing README ──────────────────────────────────────────────
  for (const [dir, entry] of dirMap) {
    if (entry.files.length === 0) continue; // skip dirs with only docs

    if (!entry.readmeMtime) {
      warnings.push({
        type: 'missing-readme',
        severity: 'warning',
        dir,
        message: `No README.md in ${dir || 'root'}`,
        affectedFiles: entry.files.map(f => f.id),
      });
      continue;
    }

    // ── Check 2: Stale README ──────────────────────────────────────────────
    const staleFiles = entry.files.filter(f =>
      f.mtime !== null && entry.readmeMtime !== null && f.mtime > entry.readmeMtime
    );

    if (staleFiles.length > 0) {
      warnings.push({
        type: 'stale-readme',
        severity: 'info',
        dir,
        readmePath: entry.readmePath,
        message: `README.md in ${dir || 'root'} is older than ${staleFiles.length} file(s)`,
        affectedFiles: staleFiles.map(f => f.id),
        staleSince: new Date(Math.max(...staleFiles.map(f => f.mtime))).toISOString(),
      });
    }
  }

  // ── Check 3: Undocumented nodes (no exports, no definitions, not a config/doc) ──
  const undocumentedCategories = new Set(['javascript', 'typescript', 'react', 'python', 'shell']);
  for (const node of nodes) {
    if (node.isExternal) continue;
    if (!undocumentedCategories.has(node.category)) continue;
    if ((node.definitions?.length || 0) === 0 && (node.exports?.length || 0) === 0) {
      // Could be a pure side-effect file or an entrypoint — only warn if it also has no imports
      if ((node.imports?.length || 0) === 0) {
        warnings.push({
          type: 'empty-module',
          severity: 'info',
          nodeId: node.id,
          message: `${node.path} has no imports, exports, or definitions`,
          affectedFiles: [node.id],
        });
      }
    }
  }

  const missingReadmes = warnings.filter(w => w.type === 'missing-readme').length;
  const staleReadmes = warnings.filter(w => w.type === 'stale-readme').length;
  const emptyModules = warnings.filter(w => w.type === 'empty-module').length;

  return {
    healthy: warnings.length === 0,
    summary: {
      missingReadmes,
      staleReadmes,
      emptyModules,
      total: warnings.length,
    },
    warnings,
  };
}
