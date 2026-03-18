/**
 * parser/projectConfig.js
 * Reads and validates side.project.json from a project root.
 * Returns a merged config with defaults applied.
 *
 * side.project.json schema:
 * {
 *   "name": "my-project",
 *   "version": "0.1.4",
 *   "description": "...",
 *   "ignore": ["versions", "dist", "*.test.js"],
 *   "run": {
 *     "dev":   "node server/index.js",
 *     "test":  "node test/run.js",
 *     "build": "npm run build"
 *   },
 *   "versions": {
 *     "dir": "versions",
 *     "compress": true,
 *     "keep": 10
 *   },
 *   "meta": {}
 * }
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const CONFIG_FILE = 'side.project.json';

const DEFAULTS = {
  name:        null,   // inferred from dir name if null
  version:     '0.1.0',
  description: '',
  ignore:      [],
  run:         {},
  versions: {
    dir:      'versions',
    compress: true,
    keep:     20,
  },
  meta: {},
};

export function loadProjectConfig(rootDir) {
  const configPath = join(rootDir, CONFIG_FILE);

  if (!existsSync(configPath)) {
    return { ...DEFAULTS, name: rootDir.split('/').pop(), _path: configPath, _exists: false };
  }

  try {
    const raw = JSON.parse(readFileSync(configPath, 'utf8'));
    return {
      ...DEFAULTS,
      ...raw,
      versions: { ...DEFAULTS.versions, ...(raw.versions || {}) },
      run:      { ...DEFAULTS.run,      ...(raw.run      || {}) },
      _path:    configPath,
      _exists:  true,
    };
  } catch (e) {
    return { ...DEFAULTS, name: rootDir.split('/').pop(), _path: configPath, _exists: false, _error: e.message };
  }
}

export function saveProjectConfig(rootDir, config) {
  const configPath = join(rootDir, CONFIG_FILE);
  // Strip internal _ keys before saving
  const clean = Object.fromEntries(
    Object.entries(config).filter(([k]) => !k.startsWith('_'))
  );
  writeFileSync(configPath, JSON.stringify(clean, null, 2));
  return configPath;
}

export function bumpVersion(current, part = 'patch') {
  const parts = String(current || '0.0.0').split('.').map(Number);
  while (parts.length < 3) parts.push(0);
  if (part === 'major') { parts[0]++; parts[1] = 0; parts[2] = 0; }
  else if (part === 'patch') { parts[2]++; }
  else { parts[1]++; parts[2] = 0; } // minor
  return parts.join('.');
}

/**
 * Create a side.project.json with sensible defaults if one doesn't exist.
 * Called automatically by the parser on first parse of a project.
 */
export function initProjectConfig(rootDir) {
  const configPath = join(rootDir, 'side.project.json');
  if (existsSync(configPath)) return loadProjectConfig(rootDir);

  const name = rootDir.split('/').pop();
  // Try to detect version from package.json
  let version = '0.1.0';
  try {
    const pkg = JSON.parse(readFileSync(join(rootDir, 'package.json'), 'utf8'));
    if (pkg.version) version = pkg.version;
  } catch {}

  const config = {
    name,
    version,
    description: '',
    ignore: [],
    run: {},
    versions: { dir: 'versions', compress: true, keep: 20 },
    meta: {},
  };

  writeFileSync(configPath, JSON.stringify(config, null, 2));
  return { ...config, _path: configPath, _exists: true, _created: true };
}
