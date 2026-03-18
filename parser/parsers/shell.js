/**
 * parsers/shell.js
 * Extracts relationships from shell scripts.
 */

const PATTERNS = {
  source: /^(?:\.|source)\s+([^\s#]+)/gm,
  scriptCall: /\b(\.\/[\w./]+\.sh|bash\s+[\w./]+\.sh|sh\s+[\w./]+\.sh)/gm,
  envVar: /^(?:export\s+)?([A-Z_][A-Z0-9_]{2,})\s*=/gm,
  funcDef: /^(?:function\s+)?(\w+)\s*\(\s*\)\s*\{/gm,
  shebang: /^#!(.+)/,
};

function getLineNumber(content, index) {
  return content.substring(0, index).split('\n').length;
}

export function parseShell(content) {
  const imports = [];
  const exports = [];
  const definitions = [];
  const tags = [];

  const shebang = PATTERNS.shebang.exec(content);
  if (shebang) tags.push(`shebang:${shebang[1].trim()}`);

  // Source/include
  let match;
  const srcRe = new RegExp(PATTERNS.source.source, PATTERNS.source.flags);
  while ((match = srcRe.exec(content)) !== null) {
    imports.push({
      type: 'source',
      source: match[1].trim(),
      line: getLineNumber(content, match.index),
    });
  }

  // Script calls
  const callRe = new RegExp(PATTERNS.scriptCall.source, PATTERNS.scriptCall.flags);
  while ((match = callRe.exec(content)) !== null) {
    imports.push({
      type: 'script-call',
      source: match[1].trim(),
      line: getLineNumber(content, match.index),
    });
  }

  // Exported env vars
  const envRe = new RegExp(PATTERNS.envVar.source, PATTERNS.envVar.flags);
  while ((match = envRe.exec(content)) !== null) {
    exports.push({
      type: 'env-var',
      name: match[1],
      line: getLineNumber(content, match.index),
    });
  }

  // Function definitions
  const funcRe = new RegExp(PATTERNS.funcDef.source, PATTERNS.funcDef.flags);
  while ((match = funcRe.exec(content)) !== null) {
    definitions.push({
      name: match[1],
      kind: 'shell-function',
      line: getLineNumber(content, match.index),
    });
  }

  if (/systemctl|service\s/i.test(content)) tags.push('systemd');
  if (/docker\s|docker-compose/i.test(content)) tags.push('docker');
  if (/apt|apt-get|yum|pacman|dnf/i.test(content)) tags.push('package-manager');
  if (/ssh|scp|rsync/i.test(content)) tags.push('remote');
  if (/curl\s|wget\s/i.test(content)) tags.push('http-client');

  return { imports, exports, definitions, calls: [], tags, errors: [] };
}
