// Browser test: serves _site, answers Open-Meteo calls from tests/fixtures, checks the page and takes screenshots.
//   sh build_site.sh && node tests/browser_test.js
const { chromium } = require("playwright");
const http = require("http"), fs = require("fs"), path = require("path");
const root = path.resolve(__dirname, "..", "_site"), fix = path.resolve(__dirname, "fixtures");
const types = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".wasm": "application/wasm", ".json": "application/json", ".py": "text/plain", ".zip": "application/zip" };
const server = http.createServer((req, res) => {
  let p = path.join(root, decodeURIComponent(req.url.split("?")[0]));
  if (p.endsWith("/")) p += "index.html";
  fs.readFile(p, (e, d) => { if (e) { res.writeHead(404); res.end(); return; } res.writeHead(200, { "Content-Type": types[path.extname(p)] || "application/octet-stream" }); res.end(d); });
}).listen(8765);

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: +(process.env.DSF || 1) });
  const page = await ctx.newPage();
  const urls = [];
  page.on("console", (m) => { if (m.type() === "error") console.log("console:", m.text()); });
  const serve = (name) => (route) => route.fulfill({ status: 200, contentType: "application/json", headers: { "Access-Control-Allow-Origin": "*" }, body: fs.readFileSync(path.join(fix, name)) });
  let forecast = "forecast_rainy.json", air = "air_clean.json";
  await page.route("**/geocoding-api.open-meteo.com/**", (r) => { urls.push(r.request().url()); serve("geocode.json")(r); });
  await page.route("**/api.open-meteo.com/v1/forecast**", (r) => { urls.push(r.request().url()); serve(forecast)(r); });
  await page.route("**/air-quality-api.open-meteo.com/**", (r) => { urls.push(r.request().url()); serve(air)(r); });

  const t0 = Date.now();
  await page.goto("http://localhost:8765/");
  await page.waitForSelector("#loading", { state: "hidden", timeout: 120000 });
  console.log("python ready in", Date.now() - t0, "ms");
  await page.waitForFunction(() => document.querySelector("#lines li"), null, { timeout: 30000 });
  const first = await page.textContent("#headline");
  console.log("default brief:", first, "|", await page.textContent("#place"));
  if (!first.includes("Umbrella")) throw new Error("expected umbrella day");
  const nHours = await page.locator("#hours .hr").count();
  if (nHours !== 24) throw new Error("expected 24 hours, got " + nHours);
  await page.screenshot({ path: process.env.SHOT || "preview-desktop.png", fullPage: false });

  // search with suggestions, dirty air, dry day
  forecast = "forecast_dry.json"; air = "air_dirty.json";
  await page.fill("#city", "Durb");
  await page.waitForSelector("#suggest li", { timeout: 20000 });
  const sugg = await page.locator("#suggest li").allTextContents();
  console.log("suggestions:", sugg);
  await page.locator("#suggest li").nth(0).dispatchEvent("mousedown");
  await page.waitForFunction(() => document.querySelector("#headline").textContent.includes("No umbrella"), null, { timeout: 30000 });
  const airTxt = await page.textContent("#tAir");
  console.log("air tile:", airTxt);
  if (!airTxt.includes("Unhealthy for sensitive")) throw new Error("expected bad air");
  const wa = await page.getAttribute("#wa", "href");
  if (!wa.startsWith("https://wa.me/?text=") || !decodeURIComponent(wa).includes("Morning brief")) throw new Error("bad WhatsApp link");
  console.log("url now:", page.url());
  console.log("lines:\n" + (await page.locator("#lines li").allTextContents()).join("\n"));

  // mobile
  const m = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: +(process.env.DSF || 1) });
  const mp = await m.newPage();
  for (const [pat, f] of [["**/geocoding-api.open-meteo.com/**", "geocode.json"], ["**/api.open-meteo.com/v1/forecast**", "forecast_rainy.json"], ["**/air-quality-api.open-meteo.com/**", "air_clean.json"]]) await mp.route(pat, serve(f));
  await mp.goto("http://localhost:8765/?city=Durban");
  await mp.waitForFunction(() => document.querySelector("#lines li"), null, { timeout: 120000 });
  const overflow = await mp.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  console.log("mobile horizontal overflow px:", overflow);
  if (overflow > 0) throw new Error("page scrolls sideways on mobile");
  await mp.screenshot({ path: process.env.MSHOT || "preview-mobile.png", fullPage: true });
  console.log("requests:", urls.length, "OK");
  await browser.close(); server.close();
})().catch((e) => { console.error("FAIL", e); process.exit(1); });
