import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve("dist");
const API = "http://127.0.0.1:8001";
const PORT = 5173;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".json": "application/json; charset=utf-8",
  ".woff2": "font/woff2",
};

http
  .createServer(async (req, res) => {
    const url = new URL(req.url, `http://localhost:${PORT}`);
    if (url.pathname.startsWith("/api")) {
      try {
        const upstream = await fetch(API + url.pathname + url.search, {
          method: req.method,
          headers: { ...req.headers, host: "127.0.0.1:8001" },
          body: ["GET", "HEAD"].includes(req.method) ? undefined : req,
          duplex: "half",
        });
        const buf = Buffer.from(await upstream.arrayBuffer());
        res.writeHead(upstream.status, {
          "content-type": upstream.headers.get("content-type") || "application/json",
        });
        res.end(buf);
      } catch (e) {
        res.writeHead(502, { "content-type": "application/json" });
        res.end(JSON.stringify({ error: "proxy_failed", detail: String(e) }));
      }
      return;
    }
    let file = path.join(ROOT, url.pathname);
    if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      file = path.join(ROOT, "index.html");
    }
    // index.html 必须每次回源校验，否则重建后浏览器可能一直用旧 hash 引用；
    // 带 hash 的 assets 内容不变，可长缓存。
    const isEntry = file.endsWith("index.html");
    const cacheControl = isEntry
      ? "no-cache"
      : "public, max-age=31536000, immutable";
    res.writeHead(200, {
      "content-type": MIME[path.extname(file)] || "application/octet-stream",
      "cache-control": cacheControl,
    });
    fs.createReadStream(file).pipe(res);
  })
  .listen(PORT, "127.0.0.1", () => console.log(`static server on http://127.0.0.1:${PORT}`));
