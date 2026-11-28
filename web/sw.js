// Makes repeat visits fast. Python (Pyodide) and its packages are kept on this device after the first
// visit, in one cache that every app on this site shares, so a refresh (or another app) never downloads
// them again. The app's own files are fetched fresh when online and fall back to the saved copy when the
// connection is offline or slow, so updates show up right away.
const APP = "weather-brief";
const PYODIDE_VERSION = "dev"; // build_site.sh writes the real version here (from pyodide/package.json)
const SHARED = "pyodide-" + PYODIDE_VERSION;
const OWN = APP + "-files-v1";
const SLOW_MS = 4000;
const OLD = []; // caches from the earlier version of this file

const PRECACHE = ["./"]; // saved when the worker installs, so the app also opens offline
const PY_CORE = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"];

self.addEventListener("install", (e) => {
  // These usually come straight from the browser's own cache (the page has just loaded them).
  e.waitUntil(Promise.all([
    caches.open(OWN).then((c) => c.addAll(PRECACHE)).catch(() => {}),
    ...PY_CORE.map((f) => {
      const url = new URL("pyodide/" + f, self.registration.scope);
      const key = sharedKey(url);
      return key ? caches.open(SHARED).then(async (c) => (await c.match(key)) || fromShared(new Request(url), key)).catch(() => {}) : null;
    }),
  ]).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  // Only clear this app's own old caches: other apps on this site use the same cache storage.
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => (k.startsWith(APP + "-") || OLD.includes(k)) && k !== OWN).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Pyodide's own files have no version in their names, so they are shared only when the version is known.
// Package wheels carry their version in the file name, so they are always safe to share.
function sharedKey(url) {
  const m = url.pathname.match(/\/pyodide\/([^/]+)$/);
  if (!m) return null;
  if (m[1].endsWith(".whl") || PYODIDE_VERSION !== "dev") return new URL("/__pyodide/" + (m[1].endsWith(".whl") ? "" : PYODIDE_VERSION + "/") + m[1], url.origin).href;
  return null;
}

async function fromShared(req, key) {
  const cache = await caches.open(SHARED);
  const hit = await cache.match(key);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok && res.status === 200) cache.put(key, res.clone()).catch(() => {});
  return res;
}

async function networkFirst(req) {
  const cache = await caches.open(OWN);
  const net = fetch(req).then((res) => {
    if (res.ok && res.status === 200) cache.put(req, res.clone()).catch(() => {});
    return res;
  });
  net.catch(() => {}); // handled below
  const saved = cache.match(req, { ignoreSearch: req.mode === "navigate" });
  const slow = new Promise((r) => setTimeout(r, SLOW_MS)).then(() => saved);
  try {
    const res = await Promise.race([net, slow.then((hit) => hit || net)]);
    return res || net;
  } catch (err) {
    const hit = await saved;
    if (hit) return hit;
    throw err;
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || req.headers.has("range")) return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  const key = sharedKey(url);
  e.respondWith(key ? fromShared(req, key) : networkFirst(req));
});
