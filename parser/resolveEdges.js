/**
 * resolveEdges.js
 * Converts raw import sources (strings) to actual graph edges.
 * Handles: relative paths, index files, extension inference,
 * external/npm packages (flagged as external).
 */

import { join, dirname, resolve, normalize, extname } from 'path';

// Edge types determine visual treatment in the node editor
const EDGE_TYPES = {
  'es-default':        'import',
  'es-named':          'import',
  'es-namespace':      'import',
  'es-side-effect':    'import-side-effect',
  'cjs-require':       'require',
  'dynamic-import':    'import-dynamic',
  're-export':         'reexport',
  're-export-all':     'reexport',
  'source':            'shell-source',
  'script-call':       'shell-call',
  'from-import':       'import',
  'from-import-all':   'import',
  'import':            'import',
  'npm-dependency':    'npm-dep',
};

const EXTENSION_CANDIDATES = [
  '', '.js', '.mjs', '.ts', '.jsx', '.tsx', '.py', '.json', '.sh'
];

const INDEX_CANDIDATES = [
  'index.js', 'index.ts', 'index.mjs', 'index.jsx', 'index.tsx', '__init__.py'
];

/**
 * Try to find a matching node for a given import source string.
 * Returns the node ID if found, or null.
 */
function resolveSource(source, fromNodePath, fileIndex, rootDir) {
  // Skip external/npm packages (no relative path indicator)
  const isRelative = source.startsWith('.') || source.startsWith('/');
  if (!isRelative) return { resolved: null, isExternal: true, externalName: source };

  const fromDir = dirname(join(rootDir, fromNodePath));
  const absoluteBase = resolve(fromDir, source);
  const relative = (p) => normalize(p).replace(/\\/g, '/');

  // Try exact path first, then with extension candidates
  for (const ext of EXTENSION_CANDIDATES) {
    const candidate = absoluteBase + ext;
    const rel = relative(candidate).replace(rootDir.replace(/\\/g, '/') + '/', '');
    if (fileIndex.has(rel)) {
      return { resolved: fileIndex.get(rel), isExternal: false };
    }
  }

  // Try as directory with index file
  for (const indexFile of INDEX_CANDIDATES) {
    const candidate = join(absoluteBase, indexFile);
    const rel = relative(candidate).replace(rootDir.replace(/\\/g, '/') + '/', '');
    if (fileIndex.has(rel)) {
      return { resolved: fileIndex.get(rel), isExternal: false };
    }
  }

  return { resolved: null, isExternal: false };
}

export function resolveEdges(nodes, fileIndex, rootDir) {
  const edges = [];
  const edgeSet = new Set(); // deduplicate

  for (const node of nodes) {
    const allImports = [...(node.imports || [])];

    // Also resolve re-exports (they create implicit import edges too)
    for (const exp of node.exports || []) {
      if (exp.source) {
        allImports.push({ type: 're-export', source: exp.source, names: exp.names });
      }
    }

    for (const imp of allImports) {
      if (!imp.source) continue;

      const { resolved, isExternal, externalName } = resolveSource(
        imp.source,
        node.path,
        fileIndex,
        rootDir
      );

      const edgeType = EDGE_TYPES[imp.type] || 'import';

      if (resolved) {
        const edgeKey = `${node.id}→${resolved}:${edgeType}`;
        if (!edgeSet.has(edgeKey)) {
          edgeSet.add(edgeKey);
          edges.push({
            id: `e_${edges.length}`,
            source: node.id,
            target: resolved,
            type: edgeType,
            // What symbols flow across this edge
            symbols: imp.names || (imp.name ? [imp.name] : imp.alias ? [imp.alias] : []),
            line: imp.line || null,
          });
        }
      } else if (isExternal) {
        // External dependency — create a virtual external node reference
        // These are rendered differently in the node editor (dimmed, dashed edge)
        const extId = `ext_${externalName.replace(/[^a-zA-Z0-9]/g, '_')}`;
        const edgeKey = `${node.id}→${extId}:external`;
        if (!edgeSet.has(edgeKey)) {
          edgeSet.add(edgeKey);
          edges.push({
            id: `e_${edges.length}`,
            source: node.id,
            target: extId,
            type: 'external',
            isExternal: true,
            externalPackage: externalName,
            symbols: imp.names || (imp.name ? [imp.name] : []),
            line: imp.line || null,
          });
        }
      }
      // Unresolved relative paths (broken imports) - we could flag these on the node
      else {
        node.errors = node.errors || [];
        if (!node.errors.some(e => e.includes(imp.source))) {
          node.errors.push(`Unresolved import: '${imp.source}'`);
        }
      }
    }
  }

  return edges;
}

/**
 * Build a map of external packages referenced across the project.
 * Useful for the "external packages" panel in the UI.
 */
export function collectExternalPackages(edges) {
  const externals = new Map();
  for (const edge of edges) {
    if (edge.isExternal && edge.externalPackage) {
      const pkg = edge.externalPackage.split('/')[0]; // handle scoped @org/pkg
      if (!externals.has(pkg)) {
        externals.set(pkg, { name: pkg, usedBy: [], symbols: new Set() });
      }
      externals.get(pkg).usedBy.push(edge.source);
      (edge.symbols || []).forEach(s => externals.get(pkg).symbols.add(s));
    }
  }
  return [...externals.values()].map(e => ({
    ...e,
    symbols: [...e.symbols],
  }));
}
