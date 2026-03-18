/**
 * parsers/python.js
 * Extracts semantic structure from Python files.
 */

const PATTERNS = {
  // import module
  importModule: /^import\s+([\w.,\s]+)/gm,

  // from module import X, Y
  fromImport: /^from\s+([\w.]+)\s+import\s+(.+)/gm,

  // function def (including async)
  funcDef: /^(?:\s*)(?:async\s+)?def\s+(\w+)\s*\(/gm,

  // class def
  classDef: /^(?:\s*)class\s+(\w+)(?:\s*\(([^)]*)\))?/gm,

  // decorator
  decorator: /^(?:\s*)@([\w.]+)/gm,

  // __all__ = [...]
  allExport: /__all__\s*=\s*\[([^\]]+)\]/,

  // if __name__ == '__main__'
  mainGuard: /if\s+__name__\s*==\s*['"]__main__['"]/,
};

function getLineNumber(content, index) {
  return content.substring(0, index).split('\n').length;
}

function parseImports(content) {
  const imports = [];

  // import X, Y, Z
  let match;
  const importRe = new RegExp(PATTERNS.importModule.source, PATTERNS.importModule.flags);
  while ((match = importRe.exec(content)) !== null) {
    const modules = match[1].split(',').map(m => m.trim());
    for (const mod of modules) {
      imports.push({
        type: 'import',
        source: mod.replace(/\s+as\s+\w+/, '').trim(),
        alias: mod.includes(' as ') ? mod.split(' as ')[1].trim() : null,
        line: getLineNumber(content, match.index),
      });
    }
  }

  // from X import Y, Z
  const fromRe = new RegExp(PATTERNS.fromImport.source, PATTERNS.fromImport.flags);
  while ((match = fromRe.exec(content)) !== null) {
    const source = match[1].trim();
    const names = match[2].trim();

    if (names === '*') {
      imports.push({
        type: 'from-import-all',
        source,
        line: getLineNumber(content, match.index),
      });
    } else {
      const parsed = names
        .replace(/[()]/g, '')
        .split(',')
        .map(n => n.trim())
        .filter(Boolean)
        .map(n => ({
          name: n.replace(/\s+as\s+\w+/, '').trim(),
          alias: n.includes(' as ') ? n.split(' as ')[1].trim() : null,
        }));
      imports.push({
        type: 'from-import',
        source,
        names: parsed,
        line: getLineNumber(content, match.index),
      });
    }
  }

  return imports;
}

function parseDefinitions(content) {
  const defs = [];

  // Collect decorators first
  const decoratorMap = new Map(); // line -> [decorator names]
  let match;
  const decRe = new RegExp(PATTERNS.decorator.source, PATTERNS.decorator.flags);
  while ((match = decRe.exec(content)) !== null) {
    const line = getLineNumber(content, match.index);
    if (!decoratorMap.has(line)) decoratorMap.set(line, []);
    decoratorMap.get(line).push(match[1]);
  }

  // Functions
  const funcRe = new RegExp(PATTERNS.funcDef.source, PATTERNS.funcDef.flags);
  while ((match = funcRe.exec(content)) !== null) {
    const name = match[1];
    const line = getLineNumber(content, match.index);
    const indent = match[0].match(/^(\s*)/)[1].length;
    const decorators = decoratorMap.get(line - 1) || [];

    defs.push({
      name,
      kind: name.startsWith('__') && name.endsWith('__') ? 'dunder' : 
            indent > 0 ? 'method' : 'function',
      line,
      indent,
      decorators,
      isAsync: match[0].includes('async'),
    });
  }

  // Classes
  const classRe = new RegExp(PATTERNS.classDef.source, PATTERNS.classDef.flags);
  while ((match = classRe.exec(content)) !== null) {
    const name = match[1];
    const bases = match[2] ? match[2].split(',').map(b => b.trim()) : [];
    const line = getLineNumber(content, match.index);
    const decorators = decoratorMap.get(line - 1) || [];

    defs.push({
      name,
      kind: 'class',
      bases,
      line,
      decorators,
    });
  }

  return defs.sort((a, b) => a.line - b.line);
}

function parseExports(content) {
  const exports = [];

  // __all__
  const allMatch = PATTERNS.allExport.exec(content);
  if (allMatch) {
    const names = allMatch[1]
      .split(',')
      .map(n => n.trim().replace(/['"]/g, ''))
      .filter(Boolean);
    exports.push({
      type: '__all__',
      names,
      line: getLineNumber(content, allMatch.index),
    });
  }

  // Top-level public definitions (no leading underscore)
  // are implicitly exported in Python — we tag them
  const topLevelDefs = parseDefinitions(content).filter(d => 
    d.indent === 0 && !d.name.startsWith('_')
  );
  for (const def of topLevelDefs) {
    if (def.kind === 'class' || def.kind === 'function') {
      exports.push({
        type: 'implicit',
        name: def.name,
        kind: def.kind,
        line: def.line,
      });
    }
  }

  return exports;
}

function detectFrameworks(content) {
  const tags = [];
  if (/^from\s+flask\s+import|^import\s+flask/im.test(content)) tags.push('flask');
  if (/^from\s+fastapi\s+import|^import\s+fastapi/im.test(content)) tags.push('fastapi');
  if (/^from\s+django\s+|^import\s+django/im.test(content)) tags.push('django');
  if (/^import\s+asyncio|^from\s+asyncio/im.test(content)) tags.push('asyncio');
  if (/^import\s+subprocess|subprocess\.run/im.test(content)) tags.push('subprocess');
  if (/^import\s+sqlite3|^from\s+sqlalchemy/im.test(content)) tags.push('database');
  if (/^import\s+requests|^from\s+requests|^import\s+httpx/im.test(content)) tags.push('http-client');
  if (PATTERNS.mainGuard.test(content)) tags.push('entrypoint');
  return tags;
}

export function parsePython(content) {
  const stripped = content
    .replace(/'''[\s\S]*?'''/g, ' ')  // triple single-quote docstrings
    .replace(/"""[\s\S]*?"""/g, ' ')  // triple double-quote docstrings
    .replace(/#[^\n]*/g, '');         // line comments

  return {
    imports: parseImports(stripped),
    exports: parseExports(content),   // use original for __all__
    definitions: parseDefinitions(stripped),
    calls: [],
    tags: detectFrameworks(content),
    errors: [],
  };
}
