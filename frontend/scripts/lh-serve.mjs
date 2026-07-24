// Minimal static server for Lighthouse CI. It serves the built ./dist bundle
// and stubs the same-origin API surface (/health, /scenarios) with 200s, so the
// landing-page audit mirrors a real deployment (where the FastAPI container
// serves both) instead of logging console errors for a missing backend — which
// would otherwise depress the best-practices score. SPA fallback -> index.html.
import http from "node:http";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";

const DIST = path.resolve("dist");
const PORT = Number(process.env.LH_PORT || 4180);
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".css": "text/css",
  ".jpg": "image/jpeg",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

function json(res, obj) {
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(obj));
}

http
  .createServer(async (req, res) => {
    const { pathname } = new URL(req.url ?? "/", "http://localhost");
    if (pathname === "/health") {
      return json(res, {
        status: "ok", service: "claimscene", mode: "offline",
        provider: "fake-media", extractor: "fake-vlm", storage: "memory",
      });
    }
    if (pathname === "/scenarios") return json(res, { scenarios: [] });

    const rel = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
    let file = path.join(DIST, rel);
    if (!file.startsWith(DIST) || !existsSync(file)) file = path.join(DIST, "index.html");
    try {
      const data = await readFile(file);
      res.writeHead(200, { "content-type": TYPES[path.extname(file)] ?? "application/octet-stream" });
      res.end(data);
    } catch {
      res.writeHead(404).end();
    }
  })
  .listen(PORT, () => console.log(`lh-serve ready on http://localhost:${PORT}`));
