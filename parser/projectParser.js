/**
 * projectParser.js
 * Orchestrates directory walking and delegates to language-specific parsers.
 * Builds the unified NodeGraph data structure.
 */

import { readdirSync, statSync, readFileSync } from 'fs';
import { join, relative, extname, basename } from 'path';
import { parseJavaScript } from './parsers/javascript.js';
import { parsePython } from './parsers/python.js';
import { parseJSON } from './parsers/json.js';
import { parseShell } from './parsers/shell.js';
import { resolveEdges } from './resolveEdges.js';
import { auditDocs } from './docCheck.js';
import { loadProjectConfig, initProjectConfig } from './projectConfig.js';

// Global ignore patterns — always applied regardless of project config
const GLOBAL_IGNORE = [
  // dependency / build artifacts
  'node_modules', '__pycache__', '.venv', 'venv',
  'dist', 'build', '.next', '.nuxt', 'coverage',
  '.cache', '.idea', '.vscode',
  // minified files
  '*.min.js', '*.bundle.js',
  // s-ide internals
  '.nodegraph.json', 'side.project.json',
  // version archives — snapshots, not live source
  'versions', 'releases', 'archive', 'archives',
  'VERSIONS', 'RELEASES', 'ARCHIVE',
  // git
  '.git',
  // uploads dir from s-ide itself
  'uploads',
];

const PARSERS = {
  '.js':   parseJavaScript,
  '.mjs':  parseJavaScript,
  '.cjs':  parseJavaScript,
  '.jsx':  parseJavaScript,
  '.ts':   parseJavaScript,
  '.tsx':  parseJavaScript,
  '.py':   parsePython,
  '.json': parseJSON,
  '.sh':   parseShell,
  '.bash': parseShell,
};

// File categories for visual grouping in the node editor
const FILE_CATEGORIES = {
  '.js': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
  '.jsx': 'react', '.tsx': 'react',
  '.ts': 'typescript',
  '.py': 'python',
  '.json': 'config',
  '.sh': 'shell', '.bash': 'shell',
  '.css': 'style', '.scss': 'style', '.less': 'style',
  '.md': 'docs', '.mdx': 'docs',
  '.env': 'config', '.toml': 'config', '.yaml': 'config', '.yml': 'config',
  '.html': 'markup', '.htm': 'markup',
  '.sql': 'database',
};

function shouldIgnore(name, extraPatterns = []) {
  const patterns = [...GLOBAL_IGNORE, ...extraPatterns];
  return patterns.some(pattern => {
    if (pattern.startsWith('*')) {
      return name.endsWith(pattern.slice(1));
    }
    return name === pattern || name.startsWith('.');
  });
}

function walkDirectory(dir, rootDir, fileList = [], extraIgnore = []) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return fileList;
  }

  for (const entry of entries) {
    if (shouldIgnore(entry.name, extraIgnore)) continue;

    const fullPath = join(dir, entry.name);

    if (entry.isDirectory()) {
      walkDirectory(fullPath, rootDir, fileList, extraIgnore);
    } else if (entry.isFile()) {
      const ext = extname(entry.name).toLowerCase();
      fileList.push({
        fullPath,
        relativePath: relative(rootDir, fullPath),
        ext,
        name: entry.name,
        category: FILE_CATEGORIES[ext] || 'other',
      });
    }
  }

  return fileList;
}

function getFileStats(fullPath) {
  try {
    const stat = statSync(fullPath);
    return {
      size: stat.size,
      modified: stat.mtime.toISOString(),
    };
  } catch {
    return { size: 0, modified: null };
  }
}

function readFileSafe(fullPath) {
  try {
    return readFileSync(fullPath, 'utf-8');
  } catch {
    return null;
  }
}

function countLines(content) {
  return content ? content.split('\n').length : 0;
}

export async function parseProject(rootDir) {
  const startTime = Date.now();

  // Load per-project config (side.project.json) — graceful if missing
  // Load config, auto-creating side.project.json if absent
  const projectConfig = initProjectConfig(rootDir);
  const extraIgnore = projectConfig.ignore || [];
  if (extraIgnore.length) {
    // Also add the versions dir name from config in case it's non-standard
    const versionsDir = projectConfig.versions?.dir;
    if (versionsDir && !extraIgnore.includes(versionsDir)) extraIgnore.push(versionsDir);
  }

  const files = walkDirectory(rootDir, rootDir, [], extraIgnore);

  const nodes = [];
  const rawEdges = []; // edges before path resolution

  const fileIndex = new Map(); // relativePath -> nodeId

  // First pass: build all nodes
  for (const file of files) {
    const content = readFileSafe(file.fullPath);
    const stats = getFileStats(file.fullPath);
    const parser = PARSERS[file.ext];

    let parsed = {
      imports: [],
      exports: [],
      definitions: [],
      calls: [],
      errors: [],
    };

    if (parser && content !== null) {
      try {
        parsed = parser(content, file.fullPath);
      } catch (err) {
        parsed.errors.push(`Parse error: ${err.message}`);
      }
    }

    const nodeId = file.relativePath.replace(/[^a-zA-Z0-9]/g, '_');

    const node = {
      id: nodeId,
      label: file.name,
      path: file.relativePath,
      fullPath: file.fullPath,
      category: file.category,
      ext: file.ext,
      lines: countLines(content),
      size: stats.size,
      modified: stats.modified,
      // Semantic data
      imports: parsed.imports,
      exports: parsed.exports,
      definitions: parsed.definitions,
      calls: parsed.calls,
      errors: parsed.errors,
      // Layout hint for the node editor (set later by layout engine)
      position: null,
    };

    nodes.push(node);
    fileIndex.set(file.relativePath, nodeId);
  }

  // Second pass: resolve edges from raw imports
  const edges = resolveEdges(nodes, fileIndex, rootDir);

  // Collect language stats
  const langStats = {};
  for (const node of nodes) {
    const cat = node.category;
    if (!langStats[cat]) langStats[cat] = { files: 0, lines: 0 };
    langStats[cat].files++;
    langStats[cat].lines += node.lines;
  }

  // Auto-layout: assign positions using a layered approach
  assignPositions(nodes, edges);

  // Documentation health audit
  const docs = auditDocs(rootDir, nodes);

  return {
    version: '1.0.0',
    meta: {
      root: rootDir,
      parsedAt: new Date().toISOString(),
      parseTime: Date.now() - startTime,
      totalFiles: nodes.length,
      totalEdges: edges.length,
      languages: langStats,
      docs,
      project: {
        name:        projectConfig.name || rootDir.split('/').pop(),
        version:     projectConfig.version,
        description: projectConfig.description,
        run:         projectConfig.run,
        versions:    projectConfig.versions,
        hasConfig:   projectConfig._exists,
      },
    },
    nodes,
    edges,
  };
}

/**
 * Simple layered layout: group by category, spread nodes out
 * so the node editor has reasonable starting positions.
 * The user can rearrange in the UI afterward.
 */
function assignPositions(nodes, edges) {
  // Build a dependency depth map
  const depthMap = new Map();
  const nodeMap = new Map(nodes.map(n => [n.id, n]));
  
  // Build adjacency for depth calculation
  const incomingCount = new Map(nodes.map(n => [n.id, 0]));
  for (const edge of edges) {
    incomingCount.set(edge.target, (incomingCount.get(edge.target) || 0) + 1);
  }

  // Topological sort for layering
  const queue = nodes.filter(n => (incomingCount.get(n.id) || 0) === 0);
  const depths = new Map(nodes.map(n => [n.id, 0]));
  const visited = new Set();

  while (queue.length > 0) {
    const node = queue.shift();
    if (visited.has(node.id)) continue;
    visited.add(node.id);

    const nodeEdges = edges.filter(e => e.source === node.id);
    for (const edge of nodeEdges) {
      const newDepth = (depths.get(node.id) || 0) + 1;
      if (newDepth > (depths.get(edge.target) || 0)) {
        depths.set(edge.target, newDepth);
      }
      const targetNode = nodeMap.get(edge.target);
      if (targetNode) queue.push(targetNode);
    }
  }

  // Group nodes by depth
  const layers = new Map();
  for (const [id, depth] of depths) {
    if (!layers.has(depth)) layers.set(depth, []);
    layers.get(depth).push(id);
  }

  const NODE_W = 240;
  const NODE_H = 160;
  const LAYER_GAP = 320;
  const NODE_GAP = 200;

  for (const [depth, ids] of layers) {
    ids.forEach((id, i) => {
      const node = nodeMap.get(id);
      if (node) {
        node.position = {
          x: depth * LAYER_GAP,
          y: i * NODE_GAP - (ids.length * NODE_GAP) / 2,
        };
      }
    });
  }

  // Catch any unpositioned nodes (cycles, etc.)
  let orphanX = 0;
  for (const node of nodes) {
    if (!node.position) {
      node.position = { x: orphanX, y: -600 };
      orphanX += NODE_W + 40;
    }
  }
}
