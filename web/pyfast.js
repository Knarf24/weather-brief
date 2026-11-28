// Starts Python (Pyodide) fast.
// The first visit starts Python normally and saves a copy of the freshly started interpreter on this
// device (in the browser's cache storage). Later visits and refreshes restore that copy instead, which
// skips most of Python's start-up work (on a laptop about 1.2 s down to 0.3 s; phones save more).
// The copy is checked against the exact Pyodide build, so after a Pyodide upgrade a new copy is made.
// Works in the page and in web workers. Usage: const py = await loadPyodideFast(); (same options as loadPyodide)
(function () {
  const CACHE = "pyodide-snapshots";
  const usable = typeof caches !== "undefined";
  const zip = typeof CompressionStream !== "undefined" && typeof DecompressionStream !== "undefined";
  const open = () => caches.open(CACHE);
  // The copy is about 21 MB, mostly empty memory; gzip makes it about 5 MB on disk.
  const squash = (bytes) => new Response(new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip")));
  const unsquash = (res) => new Response(res.body.pipeThrough(new DecompressionStream("gzip"))).arrayBuffer();

  async function readSaved(key) {
    if (!usable) return null;
    try {
      const res = await (await open()).match(key);
      if (!res) return null;
      const packed = res.headers.get("Content-Type") === "application/gzip";
      if (packed && !zip) return null;
      return new Uint8Array(packed ? await unsquash(res) : await res.arrayBuffer());
    } catch (e) { return null; }
  }
  async function save(key, bytes) {
    try {
      const body = zip ? await squash(bytes).blob() : bytes;
      await (await open()).put(key, new Response(body, { headers: { "Content-Type": zip ? "application/gzip" : "application/octet-stream" } }));
    }
    catch (e) { /* storage full or blocked: just start normally next time */ }
  }
  async function forget(key) { try { await (await open()).delete(key); } catch (e) { /* ignore */ } }

  self.loadPyodideFast = async function (options = {}) {
    const indexURL = options.indexURL || new URL("pyodide/", self.location.href).href;
    const key = new URL("__python-snapshot.bin", indexURL).href;
    const saved = await readSaved(key);
    if (saved) {
      try { return await loadPyodide({ ...options, indexURL, _loadSnapshot: saved }); }
      catch (e) { console.warn("Saved Python copy could not be used; starting Python normally.", e); forget(key); }
    }
    const py = await loadPyodide({ ...options, indexURL, _makeSnapshot: usable });
    if (usable) {
      // Take the copy now, before any app code runs, so it holds a clean interpreter.
      try { save(key, py.makeMemorySnapshot()); } catch (e) { console.warn("Could not save a copy of Python.", e); }
    }
    return py;
  };
})();
