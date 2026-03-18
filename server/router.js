/**
 * server/router.js
 * Tiny express-like router, zero dependencies.
 * Supports GET, POST, DELETE with :param segments.
 */

export class Router {
  constructor() {
    this.routes = [];
  }

  _add(method, pattern, handler) {
    // Convert '/api/processes/:id/stop' → regex + param names
    const paramNames = [];
    const regexStr = pattern
      .replace(/:[^/]+/g, m => { paramNames.push(m.slice(1)); return '([^/]+)'; })
      .replace(/\//g, '\\/');
    this.routes.push({
      method,
      regex: new RegExp(`^${regexStr}$`),
      paramNames,
      handler,
    });
  }

  get(pattern, handler)    { this._add('GET',    pattern, handler); }
  post(pattern, handler)   { this._add('POST',   pattern, handler); }
  delete(pattern, handler) { this._add('DELETE', pattern, handler); }

  async handle(req, res) {
    const url = req.url.split('?')[0];
    for (const route of this.routes) {
      if (route.method !== req.method) continue;
      const match = url.match(route.regex);
      if (!match) continue;
      req.params = {};
      route.paramNames.forEach((name, i) => { req.params[name] = match[i + 1]; });
      // Attach helpers
      res.json = (obj, status = 200) => {
        res.writeHead(status, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(obj));
      };
      res.status = (code) => {
        const orig = res.json;
        res.json = (obj) => orig(obj, code);
        return res;
      };
      await route.handler(req, res);
      return true;
    }
    return false;
  }
}
