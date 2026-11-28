// Page code: city search, showing the brief, sharing. The brief itself is made in Python (brief.py).
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let placesJson = null, briefJson = null;
let current = null;      // the place shown now
let lastText = "";
let suggestions = [], active = -1, searchTimer = null, searchSeq = 0;

const DEFAULT_CHIPS = [
  { name: "Durban", admin1: "KwaZulu-Natal", country: "South Africa", latitude: -29.8579, longitude: 31.0292 },
  { name: "Johannesburg", admin1: "Gauteng", country: "South Africa", latitude: -26.2023, longitude: 28.0436 },
  { name: "Cape Town", admin1: "Western Cape", country: "South Africa", latitude: -33.9258, longitude: 18.4232 },
  { name: "Nairobi", admin1: "Nairobi Area", country: "Kenya", latitude: -1.2833, longitude: 36.8167 },
  { name: "London", admin1: "England", country: "United Kingdom", latitude: 51.5085, longitude: -0.1257 },
  { name: "Delhi", admin1: "Delhi", country: "India", latitude: 28.6519, longitude: 77.2315 },
];

const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
};

const label = (p) => p.label || [p.name, p.admin1 !== p.name ? p.admin1 : null, p.country].filter(Boolean).join(", ");

// ---------- start-up ----------
// Python starts in the background while the page is already usable. Anything that needs Python waits for it.
let pyReady = null;
async function startPython() {
  const [py, code] = await Promise.all([
    loadPyodideFast(),
    fetch("brief.py").then((r) => { if (!r.ok) throw new Error("brief.py missing"); return r.text(); }),
  ]);
  py.FS.writeFile("/home/pyodide/brief.py", code);
  py.runPython("import brief");
  const mod = py.pyimport("brief");
  placesJson = mod.places_json;
  briefJson = mod.brief_json;
}
const whenPython = (fn) => async (...args) => { await pyReady; return fn(...args); };
const places = whenPython((q) => placesJson(q));
const makeBrief = whenPython((p) => briefJson(p));

// Keeps Python saved on this device so the next visit starts without downloading it (see sw.js).
function keepPythonSaved() {
  if ("serviceWorker" in navigator && isSecureContext) navigator.serviceWorker.register("sw.js").catch(() => {});
}

// ---------- recent cities ----------
function renderChips() {
  const recent = store.get("wb-recent", []);
  const seen = new Set();
  const list = [...recent, ...DEFAULT_CHIPS].filter((p) => {
    const k = `${p.latitude.toFixed(2)},${p.longitude.toFixed(2)}`;
    if (seen.has(k)) return false; seen.add(k); return true;
  }).slice(0, 8);
  $("chips").innerHTML = list.map((p, i) => `<button class="chip" type="button" data-i="${i}" title="${esc(label(p))}">${esc(p.name)}</button>`).join("");
  $("chips").querySelectorAll(".chip").forEach((b) => { b.onclick = () => showBrief(list[+b.dataset.i]); });
}

function remember(place) {
  if (place.mine) return;
  const keep = { name: place.name, admin1: place.admin1, country: place.country, latitude: place.latitude, longitude: place.longitude };
  const recent = store.get("wb-recent", []).filter((p) => label(p) !== label(keep));
  store.set("wb-recent", [keep, ...recent].slice(0, 5));
  store.set("wb-last", keep);
}

// ---------- search with suggestions ----------
function closeSuggest() { $("suggest").hidden = true; active = -1; }

function renderSuggest() {
  const ul = $("suggest");
  if (!suggestions.length) { closeSuggest(); return; }
  ul.innerHTML = suggestions.map((p, i) =>
    `<li role="option" data-i="${i}" class="${i === active ? "on" : ""}"><b>${esc(p.name)}</b> <span class="small">${esc([p.admin1, p.country].filter(Boolean).join(", "))}</span></li>`).join("");
  ul.hidden = false;
  ul.querySelectorAll("li").forEach((li) => { li.onmousedown = (e) => { e.preventDefault(); pick(+li.dataset.i); }; });
}

async function search(q) {
  const seq = ++searchSeq;
  try {
    const res = JSON.parse(await places(q));
    if (seq !== searchSeq) return [];
    suggestions = res; active = -1; renderSuggest();
    return res;
  } catch (e) {
    console.error(e);
    if (seq === searchSeq) { suggestions = []; closeSuggest(); }
    throw e;
  }
}

function pick(i) {
  const p = suggestions[i];
  if (!p) return;
  $("city").value = p.name;
  closeSuggest();
  showBrief(p);
}

async function submitSearch() {
  const q = $("city").value.trim();
  if (!q) return;
  if (active >= 0) { pick(active); return; }
  clearTimeout(searchTimer);
  try {
    const res = suggestions.length && suggestions[0].name.toLowerCase().startsWith(q.toLowerCase()) ? suggestions : await search(q);
    if (!res.length) { showError(`Couldn't find a place called “${q}”. Check the spelling, or try a bigger town nearby.`); return; }
    suggestions = res; pick(0);
  } catch (e) { showError("Couldn't search for places: " + e.message + ". Check your internet connection."); }
}

// ---------- the brief ----------
function showError(msg) {
  $("error").hidden = false;
  $("error").textContent = msg;
}

function aqiColour(a) {
  if (a == null) return "var(--line)";
  if (a <= 50) return "#2ea043";
  if (a <= 100) return "#d4a72c";
  if (a <= 150) return "#e16f24";
  if (a <= 200) return "#cf222e";
  if (a <= 300) return "#8250df";
  return "#7d2a1e";
}

async function showBrief(place) {
  current = place;
  $("error").hidden = true;
  $("app").hidden = false;
  $("app").classList.add("busy");
  $("place").textContent = label(place);
  $("headline").textContent = "Getting the forecast…";
  try {
    const b = JSON.parse(await makeBrief(JSON.stringify(place)));
    render(b, place);
    remember(place);
    renderChips();
    const url = new URL(location.href);
    url.searchParams.delete("lat"); url.searchParams.delete("lon"); url.searchParams.delete("city");
    if (!place.mine) url.searchParams.set("city", place.name);
    history.replaceState(null, "", url);
  } catch (e) {
    console.error(e);
    $("app").hidden = true;
    showError("Couldn't get the forecast: " + e.message + ". Check your internet connection and try again.");
  } finally {
    $("app").classList.remove("busy");
  }
}

function render(b, place) {
  $("place").textContent = place.mine ? "📍 My location" : b.place;
  $("date").textContent = `${b.date_label}${b.timezone ? " · local time " + b.now.slice(11, 16) : ""}`;
  $("bigIcon").textContent = b.icon;
  $("headline").textContent = b.headline;
  $("headline").className = "headline " + (b.umbrella ? "wet" : "dry");
  $("tRain").textContent = `${b.rain_chance}%`;
  $("tTemp").textContent = `${Math.round(b.t_min)}–${Math.round(b.t_max)}°C`;
  $("tAir").textContent = b.aqi == null ? "No data" : `${b.aqi_icon} ${b.aqi} ${b.aqi_level}`;
  $("aqiTile").style.borderColor = aqiColour(b.aqi);

  const rows = [
    ["🕖", b.now_line], [b.icon, b.temp_line], [b.umbrella ? "☔" : "🌂", b.rain_line], ["👕", b.wear_line],
    ...b.extras.map((e) => ["⚠️", e]), [b.aqi_icon, b.air_line.replace(/^\S+\s/, "") + (b.air_advice ? " " + b.air_advice : "")],
    ["🚶", b.best_line], ["📅", b.tomorrow_line && b.tomorrow_line.replace(/^Tomorrow: /, "Tomorrow: ")],
  ].filter(([, t]) => t);
  $("lines").innerHTML = rows.map(([i, t]) => `<li><span class="ic">${esc(i)}</span><span>${esc(t)}</span></li>`).join("");

  const maxMm = Math.max(1, ...b.hours.map((h) => h.mm || 0));
  $("hours").innerHTML = b.hours.map((h) => `
    <div class="hr${h.past ? " past" : ""}" title="${esc(h.time)}: ${h.rain ?? 0}% rain${h.mm ? `, ${h.mm} mm` : ""}${h.aqi != null ? `, AQI ${h.aqi}` : ""}">
      <div class="t">${esc(h.time.slice(0, 2))}</div>
      <div class="i">${esc(h.icon)}</div>
      <div class="deg">${h.temp == null ? "" : Math.round(h.temp) + "°"}</div>
      <div class="bar"><div style="height:${Math.max(2, h.rain || 0)}%"></div></div>
      <div class="pct">${h.rain ?? 0}%</div>
      <div class="dot" style="background:${aqiColour(h.aqi)}">${h.aqi ?? "–"}</div>
    </div>`).join("");
  const firstAhead = [...$("hours").children].findIndex((el) => !el.classList.contains("past"));
  if (firstAhead > 1) $("hours").scrollLeft = $("hours").children[firstAhead - 1].offsetLeft - $("hours").offsetLeft;

  lastText = b.text;
  const pageUrl = place.mine ? location.origin + location.pathname : `${location.origin}${location.pathname}?city=${encodeURIComponent(place.name)}`;
  $("wa").href = "https://wa.me/?text=" + encodeURIComponent(b.text + "\n" + pageUrl);
  $("tg").href = `https://t.me/share/url?url=${encodeURIComponent(pageUrl)}&text=${encodeURIComponent(b.text)}`;
  $("nativeShare").hidden = !navigator.share;
}

// ---------- wiring ----------
function wire() {
  $("searchForm").onsubmit = (e) => { e.preventDefault(); submitSearch(); };
  $("city").oninput = () => {
    clearTimeout(searchTimer);
    const q = $("city").value.trim();
    if (q.length < 2) { suggestions = []; closeSuggest(); return; }
    searchTimer = setTimeout(() => search(q).catch(() => {}), 300);
  };
  $("city").onkeydown = (e) => {
    if ($("suggest").hidden) return;
    if (e.key === "ArrowDown") { active = Math.min(active + 1, suggestions.length - 1); renderSuggest(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(active - 1, -1); renderSuggest(); e.preventDefault(); }
    else if (e.key === "Escape") closeSuggest();
  };
  $("city").onblur = () => setTimeout(closeSuggest, 150);
  $("locate").onclick = () => {
    if (!navigator.geolocation) { showError("This browser can't share your location. Type your city instead."); return; }
    $("locate").disabled = true;
    navigator.geolocation.getCurrentPosition(
      (pos) => { $("locate").disabled = false; showBrief({ name: "My location", mine: true, latitude: pos.coords.latitude, longitude: pos.coords.longitude }); },
      () => { $("locate").disabled = false; showError("Location permission was refused. Type your city instead."); },
      { timeout: 15000, maximumAge: 600000 });
  };
  $("copy").onclick = async () => {
    try { await navigator.clipboard.writeText(lastText); $("copy").textContent = "✅ Copied"; }
    catch (e) { $("copy").textContent = "Couldn't copy"; }
    setTimeout(() => { $("copy").textContent = "📋 Copy"; }, 1800);
  };
  $("nativeShare").onclick = () => navigator.share?.({ title: "Morning brief", text: lastText }).catch(() => {});
  $("refresh").onclick = () => current && showBrief(current);
}

(async () => {
  wire();
  renderChips();
  pyReady = startPython();
  document.documentElement.dataset.ui = "ready";
  const params = new URLSearchParams(location.search);
  const city = params.get("city");
  if (params.get("lat") && params.get("lon")) {
    showBrief({ name: params.get("name") || "Chosen place", latitude: +params.get("lat"), longitude: +params.get("lon") });
  } else if (city) {
    $("city").value = city;
    submitSearch();
  } else {
    const last = store.get("wb-last", null);
    showBrief(last || DEFAULT_CHIPS[0]);
  }
  try {
    await pyReady;
    $("loading").hidden = true;
    document.documentElement.dataset.py = "ready";
  } catch (e) {
    console.error(e);
    $("loading").classList.add("error");
    $("loadText").textContent = "Couldn't start Python: " + e.message + ". Please reload the page.";
    document.querySelector(".spinner")?.remove();
  }
  keepPythonSaved();
})();
