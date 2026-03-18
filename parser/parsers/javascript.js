/**
 * parsers/javascript.js
 * Extracts semantic structure from JS/TS/JSX/TSX files.
 * Uses regex-based parsing — fast, dependency-free, good enough for
 * graph relationship extraction (not a full AST compiler).
 */

// ─── Import patterns ──────────────────────────────────────────────────────────

const PATTERNS = {
  // ES module: import X from 'y'
  esImportDefault: /^import\s+(\w+)\s+from\s+['"]([^'"]+)['"]/gm,

  // ES module: import { X, Y } from 'y'
  esImportNamed: /^import\s+\{([^}]+)\}\s+from\s+['"]([^'"]+)['"]/gm,

  // ES module: import * as X from 'y'
  esImportNamespace: /^import\s+\*\s+as\s+(\w+)\s+from\s+['"]([^'"]+)['"]/gm,

  // ES module: import 'y' (side effect)
  esImportSideEffect: /^import\s+['"]([^'"]+)['"]/gm,

  // CommonJS: require('y') or const x = require('y')
  cjsRequire: /(?:const|let|var)\s+([\w{}\s,]+)\s*=\s*require\s*\(\s*['"]([^'"]+)['"]\s*\)/gm,

  // Dynamic import: import('y')
  dynamicImport: /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/gm,

  // Export named: export { X, Y }
  exportNamed: /^export\s+\{([^}]+)\}/gm,

  // Export default
  exportDefault: /^export\s+default\s+(\w+)/gm,

  // Export function/class/const
  exportDeclaration: /^export\s+(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)/gm,

  // Re-export: export { X } from 'y'
  reExport: /^export\s+\{([^}]+)\}\s+from\s+['"]([^'"]+)['"]/gm,

  // Re-export all: export * from 'y'
  reExportAll: /^export\s+\*\s+from\s+['"]([^'"]+)['"]/gm,

  // Function declarations
  functionDecl: /^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(/gm,

  // Arrow function assigned to const
  arrowFunc: /^(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>/gm,

  // Class declarations
  classDecl: /^(?:export\s+)?class\s+(\w+)/gm,

  // React component (function returning JSX, heuristic)
  reactComponent: /^(?:export\s+)?(?:const|function)\s+([A-Z]\w+)/gm,
};

function extractMatches(content, pattern, groupMap) {
  const results = [];
  let match;
  const re = new RegExp(pattern.source, pattern.flags);
  while ((match = re.exec(content)) !== null) {
    const result = {};
    for (const [key, idx] of Object.entries(groupMap)) {
      result[key] = match[idx]?.trim() ?? null;
    }
    result.line = content.substring(0, match.index).split('\n').length;
    results.push(result);
  }
  return results;
}

function parseImports(content) {
  const imports = [];

  // ES default imports
  for (const m of extractMatches(content, PATTERNS.esImportDefault, { name: 1, source: 2 })) {
    imports.push({ type: 'es-default', name: m.name, source: m.source, line: m.line });
  }

  // ES named imports
  for (const m of extractMatches(content, PATTERNS.esImportNamed, { names: 1, source: 2 })) {
    const names = m.names.split(',').map(n => n.trim().replace(/\s+as\s+\w+/, ''));
    imports.push({ type: 'es-named', names, source: m.source, line: m.line });
  }

  // ES namespace imports
  for (const m of extractMatches(content, PATTERNS.esImportNamespace, { alias: 1, source: 2 })) {
    imports.push({ type: 'es-namespace', alias: m.alias, source: m.source, line: m.line });
  }

  // Side-effect imports
  for (const m of extractMatches(content, PATTERNS.esImportSideEffect, { source: 1 })) {
    // Don't double-count if already captured by other patterns
    const alreadyCounted = imports.some(i => i.source === m.source && i.line === m.line);
    if (!alreadyCounted) {
      imports.push({ type: 'es-side-effect', source: m.source, line: m.line });
    }
  }

  // CommonJS require
  for (const m of extractMatches(content, PATTERNS.cjsRequire, { binding: 1, source: 2 })) {
    imports.push({ type: 'cjs-require', binding: m.binding?.trim(), source: m.source, line: m.line });
  }

  // Dynamic imports
  for (const m of extractMatches(content, PATTERNS.dynamicImport, { source: 1 })) {
    imports.push({ type: 'dynamic-import', source: m.source, line: m.line });
  }

  return imports;
}

function parseExports(content) {
  const exports = [];

  // Re-export from another module
  for (const m of extractMatches(content, PATTERNS.reExport, { names: 1, source: 2 })) {
    const names = m.names.split(',').map(n => n.trim());
    exports.push({ type: 're-export', names, source: m.source, line: m.line });
  }

  for (const m of extractMatches(content, PATTERNS.reExportAll, { source: 1 })) {
    exports.push({ type: 're-export-all', source: m.source, line: m.line });
  }

  // Named export block
  for (const m of extractMatches(content, PATTERNS.exportNamed, { names: 1 })) {
    const names = m.names.split(',').map(n => n.trim().replace(/\s+as\s+\w+/, ''));
    exports.push({ type: 'named', names, line: m.line });
  }

  // Export default
  for (const m of extractMatches(content, PATTERNS.exportDefault, { name: 1 })) {
    exports.push({ type: 'default', name: m.name, line: m.line });
  }

  // Export declarations (function/class/const)
  for (const m of extractMatches(content, PATTERNS.exportDeclaration, { name: 1 })) {
    exports.push({ type: 'declaration', name: m.name, line: m.line });
  }

  return exports;
}

function parseDefinitions(content) {
  const defs = [];
  const seen = new Set();

  function addDef(name, kind, line) {
    const key = `${kind}:${name}:${line}`;
    if (!seen.has(key)) {
      seen.add(key);
      defs.push({ name, kind, line });
    }
  }

  // Functions
  for (const m of extractMatches(content, PATTERNS.functionDecl, { name: 1 })) {
    addDef(m.name, 'function', m.line);
  }

  // Arrow functions
  for (const m of extractMatches(content, PATTERNS.arrowFunc, { name: 1 })) {
    addDef(m.name, 'arrow-function', m.line);
  }

  // Classes
  for (const m of extractMatches(content, PATTERNS.classDecl, { name: 1 })) {
    addDef(m.name, 'class', m.line);
  }

  // React components (capital-named — deduplicate with functions/arrows)
  for (const m of extractMatches(content, PATTERNS.reactComponent, { name: 1 })) {
    const exists = defs.some(d => d.name === m.name);
    if (!exists) {
      addDef(m.name, 'component', m.line);
    } else {
      // Upgrade kind to component if it starts with uppercase
      const def = defs.find(d => d.name === m.name);
      if (def && /^[A-Z]/.test(m.name)) def.kind = 'component';
    }
  }

  return defs.sort((a, b) => a.line - b.line);
}

function detectFrameworks(content, filePath) {
  const tags = [];
  if (/from\s+['"]react['"]/i.test(content)) tags.push('react');
  if (/from\s+['"]vue['"]/i.test(content)) tags.push('vue');
  if (/from\s+['"]svelte['"]/i.test(content)) tags.push('svelte');
  if (/from\s+['"]express['"]/i.test(content)) tags.push('express');
  if (/from\s+['"]fastify['"]/i.test(content)) tags.push('fastify');
  if (/from\s+['"]next['"]/i.test(content) || filePath.includes('/pages/') || filePath.includes('/app/')) tags.push('next');
  if (/from\s+['"]electron['"]/i.test(content)) tags.push('electron');
  if (/WebSocket|ws\.on|socket\.io/i.test(content)) tags.push('websocket');
  if (/fetch\(|axios\.|\.get\(|\.post\(/i.test(content)) tags.push('http-client');
  return tags;
}

export function parseJavaScript(content, filePath) {
  // Strip comments to avoid false positives
  const stripped = content
    .replace(/\/\*[\s\S]*?\*\//g, ' ') // block comments
    .replace(/\/\/[^\n]*/g, '');        // line comments

  return {
    imports: parseImports(stripped),
    exports: parseExports(stripped),
    definitions: parseDefinitions(stripped),
    calls: [],
    tags: detectFrameworks(content, filePath),
    errors: [],
  };
}
