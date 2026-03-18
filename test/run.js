/**
 * test/run.js
 * Self-referential smoke test: parse the parser itself.
 * Also validates the output schema.
 */

import { parseProject } from '../parser/projectParser.js';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const parserRoot = resolve(__dirname, '..');

console.log('─'.repeat(60));
console.log('NodeGraph Parser — Self Test');
console.log('─'.repeat(60));

let passed = 0;
let failed = 0;

function assert(label, condition, detail = '') {
  if (condition) {
    console.log(`  ✓ ${label}`);
    passed++;
  } else {
    console.log(`  ✗ ${label}${detail ? ': ' + detail : ''}`);
    failed++;
  }
}

const graph = await parseProject(parserRoot);

// ── Meta ──────────────────────────────────────────────────────────────────────
console.log('\n[Meta]');
assert('Has version', graph.version === '1.0.0');
assert('Has meta.root', typeof graph.meta.root === 'string');
assert('Has parsedAt', typeof graph.meta.parsedAt === 'string');
assert('ParseTime is positive', graph.meta.parseTime > 0);
assert('Has language stats', typeof graph.meta.languages === 'object');

// ── Nodes ─────────────────────────────────────────────────────────────────────
console.log('\n[Nodes]');
assert('Has nodes array', Array.isArray(graph.nodes));
assert('Has at least 5 nodes', graph.nodes.length >= 5,
  `Got ${graph.nodes.length}`);

const parserNode = graph.nodes.find(n => n.path.includes('projectParser'));
assert('Found projectParser.js node', !!parserNode);

if (parserNode) {
  assert('Node has id', typeof parserNode.id === 'string');
  assert('Node has path', typeof parserNode.path === 'string');
  assert('Node has category', parserNode.category === 'javascript');
  assert('Node has line count', parserNode.lines > 0);
  assert('Node has imports', Array.isArray(parserNode.imports));
  assert('Node has exports', Array.isArray(parserNode.exports));
  assert('Node has definitions', Array.isArray(parserNode.definitions));
  assert('Node has position', parserNode.position !== null);
  assert('Position has x/y', 
    typeof parserNode.position?.x === 'number' &&
    typeof parserNode.position?.y === 'number'
  );
}

// ── Edges ─────────────────────────────────────────────────────────────────────
console.log('\n[Edges]');
assert('Has edges array', Array.isArray(graph.edges));
assert('Has internal edges', graph.edges.some(e => !e.isExternal),
  `Total edges: ${graph.edges.length}`);

const edge = graph.edges.find(e => !e.isExternal);
if (edge) {
  assert('Edge has id', typeof edge.id === 'string');
  assert('Edge has source', typeof edge.source === 'string');
  assert('Edge has target', typeof edge.target === 'string');
  assert('Edge has type', typeof edge.type === 'string');
  assert('Edge symbols is array', Array.isArray(edge.symbols));
}

// ── Parser Quality ────────────────────────────────────────────────────────────
console.log('\n[Parser Quality]');
const jsNodes = graph.nodes.filter(n => n.category === 'javascript');
assert('Detected JS files', jsNodes.length > 0);

const hasImports = graph.nodes.some(n => n.imports.length > 0);
assert('At least one node has imports', hasImports);

const hasDefinitions = graph.nodes.some(n => n.definitions.length > 0);
assert('At least one node has definitions', hasDefinitions);

const hasExports = graph.nodes.some(n => n.exports.length > 0);
assert('At least one node has exports', hasExports);

// ── Print summary ─────────────────────────────────────────────────────────────
console.log('\n─'.repeat(60));
console.log(`Results: ${passed} passed, ${failed} failed`);
console.log('─'.repeat(60));

console.log('\n[Sample Node — projectParser.js]');
if (parserNode) {
  console.log(`  imports:     ${parserNode.imports.length}`);
  console.log(`  exports:     ${parserNode.exports.length}`);
  console.log(`  definitions: ${parserNode.definitions.length}`);
  console.log(`  lines:       ${parserNode.lines}`);
  console.log(`  position:    (${parserNode.position?.x}, ${parserNode.position?.y})`);
}

console.log('\n[Edge Types Detected]');
const typeCounts = {};
for (const e of graph.edges) {
  typeCounts[e.type] = (typeCounts[e.type] || 0) + 1;
}
for (const [type, count] of Object.entries(typeCounts)) {
  console.log(`  ${type}: ${count}`);
}

console.log('\n[Language Breakdown]');
for (const [lang, stats] of Object.entries(graph.meta.languages)) {
  console.log(`  ${lang}: ${stats.files} files, ${stats.lines} lines`);
}

if (failed > 0) process.exit(1);
