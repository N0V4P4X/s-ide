/**
 * parsers/json.js
 * Extracts structural info from JSON files (package.json, tsconfig, etc.)
 */

export function parseJSON(content, filePath) {
  const imports = [];
  const exports = [];
  const definitions = [];
  const tags = [];

  let parsed;
  try {
    parsed = JSON.parse(content);
  } catch (e) {
    return { imports, exports, definitions, calls: [], tags, errors: [`JSON parse error: ${e.message}`] };
  }

  const name = filePath.split('/').pop();

  // package.json
  if (name === 'package.json') {
    tags.push('package-manifest');

    if (parsed.dependencies) {
      for (const dep of Object.keys(parsed.dependencies)) {
        imports.push({ type: 'npm-dependency', source: dep, version: parsed.dependencies[dep], line: null });
      }
    }
    if (parsed.devDependencies) {
      for (const dep of Object.keys(parsed.devDependencies)) {
        imports.push({ type: 'npm-dev-dependency', source: dep, version: parsed.devDependencies[dep], line: null });
      }
    }
    if (parsed.main) {
      exports.push({ type: 'main-entry', path: parsed.main });
    }
    if (parsed.scripts) {
      for (const [script, cmd] of Object.entries(parsed.scripts)) {
        definitions.push({ name: script, kind: 'npm-script', value: cmd, line: null });
      }
    }
    if (parsed.name) tags.push(`pkg:${parsed.name}`);
  }

  // tsconfig.json
  else if (name.startsWith('tsconfig')) {
    tags.push('typescript-config');
    if (parsed.compilerOptions?.paths) {
      for (const alias of Object.keys(parsed.compilerOptions.paths)) {
        definitions.push({ name: alias, kind: 'path-alias', line: null });
      }
    }
  }

  // .env-like JSON (common pattern)
  else if (name.includes('config') || name.includes('settings')) {
    tags.push('config');
    for (const key of Object.keys(parsed)) {
      definitions.push({ name: key, kind: 'config-key', line: null });
    }
  }

  // Generic JSON schema
  else if (parsed.$schema) {
    tags.push('json-schema');
  }

  return { imports, exports, definitions, calls: [], tags, errors: [] };
}
