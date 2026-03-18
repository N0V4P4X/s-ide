#!/usr/bin/env node
/**
 * NodeGraph Parser — Core Entry Point
 * Walks a project directory, extracts file relationships,
 * and emits a structured graph JSON for the node editor.
 */

import { parseProject } from './projectParser.js';
import { writeFileSync } from 'fs';
import { resolve } from 'path';

const [,, projectPath, outPath] = process.argv;

if (!projectPath) {
  console.error('Usage: node index.js <project-path> [output.json]');
  process.exit(1);
}

const absPath = resolve(projectPath);
const outputFile = outPath ? resolve(outPath) : resolve(projectPath, '.nodegraph.json');

console.log(`[NodeGraph] Parsing: ${absPath}`);

const graph = await parseProject(absPath);

writeFileSync(outputFile, JSON.stringify(graph, null, 2));
console.log(`[NodeGraph] Graph written to: ${outputFile}`);
console.log(`[NodeGraph] ${graph.nodes.length} nodes, ${graph.edges.length} edges`);
