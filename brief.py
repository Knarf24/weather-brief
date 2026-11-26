"""Daily Weather & Air Brief: the brain of the app.

Pick a city and this module
  1. finds it (Open-Meteo geocoding, free, no key),
  2. fetches today's hourly forecast and air-quality forecast (Open-Meteo),
  3. works out the Air Quality Index itself from the pollutant levels (US EPA breakpoints),
  4. turns it all into a short morning brief: umbrella or not, when it rains, what to wear,
     how clean the air is and the best time to be outside.

It runs in the browser with Pyodide (web page) and on a normal computer or GitHub Actions
(send_brief.py). All network access goes through one `get_json(url)` coroutine that the
caller passes in, so the same code works everywhere and can be tested without the internet.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Awaitable, Callable
from urllib.parse import urlencode

GetJson = Callable[[str], Awaitable[dict]]

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

HOURLY_WEATHER = ["temperature_2m", "apparent_temperature", "precipitation_probability", "precipitation",
                  "weather_code", "wind_speed_10m", "wind_gusts_10m", "uv_index"]
DAILY_WEATHER = ["weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max",
                 "precipitation_sum", "uv_index_max", "wind_gusts_10m_max", "sunrise", "sunset"]
HOURLY_AIR = ["pm2_5", "pm10", "ozone", "nitrogen_dioxide", "us_aqi"]

RAIN_LIKELY = 40      # % chance from which we say "take an umbrella"
RAIN_POSSIBLE = 25    # % chance shown as "might drizzle"
DAY_START, DAY_END = 6, 21  # the hours people are out and about (06:00 to 21:59)

# ---------------------------------------------------------------- weather codes (WMO)

WEATHER = {
    0: ("Clear sky", "☀️"), 1: ("Mostly clear", "🌤️"), 2: ("Partly cloudy", "⛅"), 3: ("Overcast", "☁️"),
    45: ("Fog", "🌫️"), 48: ("Freezing fog", "🌫️"),
    51: ("Light drizzle", "🌦️"), 53: ("Drizzle", "🌦️"), 55: ("Heavy drizzle", "🌧️"),
    56: ("Freezing drizzle", "🌧️"), 57: ("Freezing drizzle", "🌧️"),
    61: ("Light rain", "🌦️"), 63: ("Rain", "🌧️"), 65: ("Heavy rain", "🌧️"),
    66: ("Freezing rain", "🌧️"), 67: ("Freezing rain", "🌧️"),
    71: ("Light snow", "🌨️"), 73: ("Snow", "🌨️"), 75: ("Heavy snow", "❄️"), 77: ("Snow grains", "🌨️"),
    80: ("Light showers", "🌦️"), 81: ("Showers", "🌧️"), 82: ("Violent showers", "⛈️"),
    85: ("Snow showers", "🌨️"), 86: ("Heavy snow showers", "❄️"),
    95: ("Thunderstorms", "⛈️"), 96: ("Thunderstorms with hail", "⛈️"), 99: ("Thunderstorms with hail", "⛈️"),
}


def _severity(code) -> int:
    """How much a weather code matters for a short summary: storms > snow > rain > fog > cloud > clear."""
    c = int(code)
    if c >= 95: return 90
    if c in (71, 73, 75, 77, 85, 86): return 80
    if c >= 51: return 50 + c % 10
    if c in (45, 48): return 40
    return c


def describe(code) -> tuple[str, str]:
    return WEATHER.get(int(code) if code is not None else -1, ("Unknown", "🌡️"))


# ---------------------------------------------------------------- Air Quality Index (US EPA)

# (concentration low, concentration high, index low, index high)
BREAKPOINTS = {
    # PM2.5, µg/m³ (EPA 2024 update)
    "pm2_5": [(0.0, 9.0, 0, 50), (9.1, 35.4, 51, 100), (35.5, 55.4, 101, 150), (55.5, 125.4, 151, 200),
              (125.5, 225.4, 201, 300), (225.5, 325.4, 301, 500)],
    # PM10, µg/m³
    "pm10": [(0, 54, 0, 50), (55, 154, 51, 100), (155, 254, 101, 150), (255, 354, 151, 200),
             (355, 424, 201, 300), (425, 604, 301, 500)],
    # Ozone, ppb (8-hour)
    "ozone": [(0, 54, 0, 50), (55, 70, 51, 100), (71, 85, 101, 150), (86, 105, 151, 200), (106, 200, 201, 300)],
    # Nitrogen dioxide, ppb (1-hour)
    "nitrogen_dioxide": [(0, 53, 0, 50), (54, 100, 51, 100), (101, 360, 101, 150), (361, 649, 151, 200),
                         (650, 1249, 201, 300), (1250, 2049, 301, 500)],
}
# Open-Meteo gives gases in µg/m³; EPA breakpoints are in ppb (at 25 °C).
UG_PER_PPB = {"ozone": 1.96, "nitrogen_dioxide": 1.88}
# Particles are rounded to 1 decimal (PM2.5) or whole numbers, gases to whole ppb, as EPA does.
ROUND = {"pm2_5": 1, "pm10": 0, "ozone": 0, "nitrogen_dioxide": 0}
POLLUTANT_NAMES = {"pm2_5": "fine dust (PM2.5)", "pm10": "dust (PM10)", "ozone": "ozone",
                   "nitrogen_dioxide": "traffic fumes (NO₂)"}

AQI_LEVELS = [
    (50, "Good", "🟢", "Great air. Enjoy being outside."),
    (100, "Moderate", "🟡", "Fine for most people. Very sensitive people may want to take it easier outside."),
    (150, "Unhealthy for sensitive groups", "🟠",
     "People with asthma, heart or lung problems, children and older adults should cut down long or hard exercise outside."),
    (200, "Unhealthy", "🔴", "Everyone should cut down hard exercise outside. Sensitive groups should stay indoors."),
    (300, "Very unhealthy", "🟣", "Avoid exercise outside. Keep windows closed and wear a mask if you must go out."),
    (10_000, "Hazardous", "🟤", "Stay indoors with windows closed. Health warning for everyone."),
]


def sub_index(pollutant: str, value) -> int | None:
    """AQI for one pollutant (0 to 500), or None if there is no reading."""
    if value is None:
        return None
    conc = float(value) / UG_PER_PPB.get(pollutant, 1.0)
    conc = round(conc, ROUND[pollutant])
    if ROUND[pollutant] == 0:
        conc = int(conc)
    table = BREAKPOINTS[pollutant]
    if conc < 0:
        return 0
    for c_lo, c_hi, i_lo, i_hi in table:
        if conc <= c_hi:
            # The gaps between ranges (e.g. 9.0 to 9.1) belong to the upper range.
            c_lo = min(c_lo, conc)
            return round((i_hi - i_lo) / (c_hi - c_lo) * (conc - c_lo) + i_lo) if c_hi > c_lo else i_lo
    return 500 if pollutant != "ozone" else 300


def aqi(readings: dict) -> tuple[int | None, str | None]:
    """Overall AQI = the worst pollutant's index. Returns (index, pollutant key)."""
    best: tuple[int | None, str | None] = (None, None)
    for key in BREAKPOINTS:
        idx = sub_index(key, readings.get(key))
        if idx is not None and (best[0] is None or idx > best[0]):
            best = (idx, key)
    return best


def aqi_level(index: int | None) -> dict:
    if index is None:
        return {"name": "No data", "icon": "⚪", "advice": "No air-quality reading for this place right now."}
    for top, name, icon, advice in AQI_LEVELS:
        if index <= top:
            return {"name": name, "icon": icon, "advice": advice}
    return {"name": "Hazardous", "icon": "🟤", "advice": AQI_LEVELS[-1][3]}


# ---------------------------------------------------------------- fetching

def place_label(place: dict) -> str:
    parts = [place.get("name")]
    if place.get("admin1") and place.get("admin1") != place.get("name"):
        parts.append(place["admin1"])
    if place.get("country"):
        parts.append(place["country"])
    return ", ".join(p for p in parts if p)


async def find_places(name: str, get_json: GetJson, count: int = 6) -> list[dict]:
    """Places matching a typed name, biggest first."""
    name = name.strip()
    if len(name) < 2:
        return []
    data = await get_json(GEOCODE_URL + "?" + urlencode({"name": name, "count": count, "language": "en", "format": "json"}))
    places = []
    for r in data.get("results") or []:
        places.append({
            "name": r.get("name"), "admin1": r.get("admin1"), "country": r.get("country"),
            "country_code": r.get("country_code"), "latitude": r["latitude"], "longitude": r["longitude"],
            "timezone": r.get("timezone"), "population": r.get("population"),
        })
    for p in places:
        p["label"] = place_label(p)
    return places


def forecast_url(lat: float, lon: float) -> str:
    return FORECAST_URL + "?" + urlencode({
        "latitude": round(lat, 4), "longitude": round(lon, 4), "timezone": "auto", "forecast_days": 2,
        "hourly": ",".join(HOURLY_WEATHER), "daily": ",".join(DAILY_WEATHER),
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "wind_speed_unit": "kmh",
    })


def air_url(lat: float, lon: float) -> str:
    return AIR_URL + "?" + urlencode({
        "latitude": round(lat, 4), "longitude": round(lon, 4), "timezone": "auto", "forecast_days": 2,
        "hourly": ",".join(HOURLY_AIR),
    })


async def fetch(place: dict, get_json: GetJson) -> tuple[dict, dict | None]:
    weather = await get_json(forecast_url(place["latitude"], place["longitude"]))
    try:
        air = await get_json(air_url(place["latitude"], place["longitude"]))
    except Exception:  # the brief still works without air data
        air = None
    return weather, air


# ---------------------------------------------------------------- building the brief

def _hours_for(day: str, times: list[str]) -> list[int]:
    return [i for i, t in enumerate(times) if t.startswith(day)]


def _hour(t: str) -> int:
    return int(t[11:13])


def _ranges(hours: list[int]) -> list[tuple[int, int]]:
    """[7, 8, 9, 14, 15] -> [(7, 9), (14, 15)]"""
    out: list[tuple[int, int]] = []
    for h in hours:
        if out and h == out[-1][1] + 1:
            out[-1] = (out[-1][0], h)
        else:
            out.append((h, h))
    return out


def _fmt_range(r: tuple[int, int]) -> str:
    return f"{r[0]:02d}:00–{(r[1] + 1) % 24:02d}:00"


def clothing(t_min: float, t_max: float, rain: bool) -> str:
    if t_min < 5:
        wear = "a warm coat, it's cold"
    elif t_min < 12:
        wear = "a warm jacket for the morning"
    elif t_min < 17:
        wear = "a light jacket or jersey"
    elif t_max >= 30:
        wear = "light, cool clothes and a hat"
    else:
        wear = "something light"
    if rain:
        wear += ", plus a raincoat or umbrella"
    return wear[0].upper() + wear[1:] + "."


def _val(arr, i):
    return arr[i] if arr and i < len(arr) else None


def make_brief(place: dict, weather: dict, air: dict | None, now: str | None = None) -> dict:
    """Turn Open-Meteo answers into the morning brief.

    `now` is the local time at the place ("2026-11-26T07:15"); by default the forecast's current time.
    The brief covers the rest of today (from the current hour), or the whole day if it's early.
    """
    h = weather["hourly"]
    d = weather["daily"]
    times: list[str] = h["time"]
    now = now or (weather.get("current") or {}).get("time") or times[0]
    today = now[:10]
    now_hour = _hour(now) if len(now) >= 13 else 0
    day_idx = d["time"].index(today) if today in d["time"] else 0
    today = d["time"][day_idx]

    idx_today = _hours_for(today, times)
    start = min(max(now_hour, DAY_START), 23)  # before 06:00, brief the coming day
    end = 23 if now_hour >= 17 else DAY_END     # an evening brief covers the night ahead
    # the part of the day still ahead, during waking hours
    ahead = [i for i in idx_today if start <= _hour(times[i]) <= end] or idx_today[-3:]

    # --- rain
    probs = [(i, h["precipitation_probability"][i] or 0) for i in ahead]
    peak_i, peak_p = max(probs, key=lambda x: x[1]) if probs else (None, 0)
    rain_mm = sum((h["precipitation"][i] or 0) for i in ahead)
    wet_hours = [_hour(times[i]) for i, p in probs if p >= RAIN_LIKELY]
    maybe_hours = [_hour(times[i]) for i, p in probs if p >= RAIN_POSSIBLE]
    umbrella = peak_p >= RAIN_LIKELY or rain_mm >= 1.0
    if umbrella:
        windows = _ranges(wet_hours) or _ranges(maybe_hours)
        when = ", ".join(_fmt_range(r) for r in windows[:3]) if windows else "later today"
        rain_line = f"Take an umbrella. Rain is most likely {when} (up to {peak_p}% chance"
        rain_line += f", about {rain_mm:.0f} mm)." if rain_mm >= 1 else ")."
    elif peak_p >= RAIN_POSSIBLE:
        rain_line = (f"Probably dry. A small chance of a shower around {_fmt_range((_hour(times[peak_i]),) * 2)} "
                     f"({peak_p}%), so a light raincoat is enough.")
    else:
        rain_line = f"No umbrella needed. It should stay dry (rain chance at most {peak_p}%)."

    # --- temperature and sky
    temps = [h["temperature_2m"][i] for i in ahead if h["temperature_2m"][i] is not None]
    t_min = d["temperature_2m_min"][day_idx]
    t_max = d["temperature_2m_max"][day_idx]
    sky, icon = describe(d["weather_code"][day_idx])
    cur = weather.get("current") or {}
    now_line = None
    if cur.get("temperature_2m") is not None and (cur.get("time") or "")[:13] == now[:13]:
        c_sky, c_icon = describe(cur.get("weather_code"))
        now_line = f"Now {cur['temperature_2m']:.0f}°C and {c_sky.lower()}"
        if cur.get("apparent_temperature") is not None and abs(cur["apparent_temperature"] - cur["temperature_2m"]) >= 2:
            now_line += f" (feels like {cur['apparent_temperature']:.0f}°C)"
        now_line += "."
    temp_line = f"{sky}, {t_min:.0f}°C to {t_max:.0f}°C."
    wear_line = clothing(t_min if temps else t_min, t_max, umbrella)

    extras = []
    uv = d.get("uv_index_max", [None])[day_idx] if d.get("uv_index_max") else None
    if uv is not None and uv >= 8:
        extras.append(f"Very strong sun (UV {uv:.0f}): sunscreen, hat and shade around midday.")
    elif uv is not None and uv >= 6:
        extras.append(f"Strong sun (UV {uv:.0f}): wear sunscreen.")
    gust = d.get("wind_gusts_10m_max", [None])[day_idx] if d.get("wind_gusts_10m_max") else None
    if gust is not None and gust >= 60:
        extras.append(f"Very windy, gusts up to {gust:.0f} km/h. Secure loose things outside.")
    elif gust is not None and gust >= 40:
        extras.append(f"Windy, gusts up to {gust:.0f} km/h.")
    if t_max >= 32:
        extras.append("Hot day: drink plenty of water.")

    # --- air quality, worked out from the pollutants
    hourly_aqi: dict[str, dict] = {}
    if air and air.get("hourly"):
        ah = air["hourly"]
        for i, t in enumerate(ah["time"]):
            readings = {k: _val(ah.get(k), i) for k in BREAKPOINTS}
            idx, main = aqi(readings)
            hourly_aqi[t] = {"aqi": idx, "main": main, "api_aqi": _val(ah.get("us_aqi"), i), **readings}
    air_ahead = [(times[i], hourly_aqi.get(times[i], {}).get("aqi")) for i in ahead]
    air_ahead = [(t, a) for t, a in air_ahead if a is not None]
    if air_ahead:
        worst_t, worst = max(air_ahead, key=lambda x: x[1])
        level = aqi_level(worst)
        main_pol = hourly_aqi[worst_t]["main"]
        air_line = f"{level['icon']} Air quality: {level['name']} (AQI up to {worst}"
        air_line += f", mainly {POLLUTANT_NAMES[main_pol]})." if worst > 50 else ")."
        air_advice = level["advice"]
    else:
        worst, level, main_pol = None, aqi_level(None), None
        air_line = "⚪ Air quality: no reading for this place."
        air_advice = ""

    # --- best time to be outside: dry, cleanest air, not the hottest hours on a hot day
    scored = []
    for i in ahead:
        t = times[i]
        p = h["precipitation_probability"][i] or 0
        a = hourly_aqi.get(t, {}).get("aqi")
        temp = h["temperature_2m"][i]
        score = p * 2 + (a if a is not None else 50)
        if temp is not None and temp >= 30:
            score += (temp - 29) * 15
        if temp is not None and temp <= 5:
            score += (6 - temp) * 10
        scored.append((score, _hour(t), p, a))
    best_line = None
    if scored:
        best_score = min(s[0] for s in scored)
        good = sorted(hr for s, hr, p, a in scored if s <= best_score + 10 and p < RAIN_LIKELY)
        if good:
            r = max(_ranges(good), key=lambda x: x[1] - x[0])
            if r[1] - r[0] >= 9:
                best_line = "Any time is fine to be outside today."
            else:
                best_line = f"Best time to be outside: {_fmt_range(r)}."
        else:
            best_line = "No really good time to be outside today; keep outdoor plans short."

    # --- tomorrow in one line
    tomorrow_line = None
    if len(d["time"]) > day_idx + 1:
        j = day_idx + 1
        t_sky, t_icon = describe(d["weather_code"][j])
        tp = d["precipitation_probability_max"][j] or 0
        tomorrow_line = (f"Tomorrow: {t_icon} {t_sky.lower()}, {d['temperature_2m_min'][j]:.0f}–"
                         f"{d['temperature_2m_max'][j]:.0f}°C, rain chance {tp}%.")
        tom_air = [v["aqi"] for t, v in hourly_aqi.items() if t.startswith(d["time"][j]) and v["aqi"] is not None]
        if tom_air:
            tomorrow_line = tomorrow_line[:-1] + f", air {aqi_level(max(tom_air))['name'].lower()}."

    # --- hour by hour (for the page's chart)
    hours = []
    for i in idx_today:
        t = times[i]
        a = hourly_aqi.get(t, {})
        hours.append({
            "time": t[11:16], "temp": h["temperature_2m"][i], "rain": h["precipitation_probability"][i],
            "mm": h["precipitation"][i], "icon": describe(h["weather_code"][i])[1], "aqi": a.get("aqi"),
            "past": _hour(t) < now_hour,
        })

    date_label = datetime.strptime(today, "%Y-%m-%d").strftime("%A %d %B").replace(" 0", " ")
    headline = ("☔ Umbrella day" if umbrella else "🌂 No umbrella needed")
    rows = [("🕖", now_line), (icon, temp_line), ("☔" if umbrella else "🌂", rain_line), ("👕", wear_line),
            *(("⚠️", e) for e in extras), ("", air_line), ("💡", air_advice), ("🚶", best_line), ("📅", tomorrow_line)]
    text = f"☀️ Morning brief for {place_label(place)}\n{date_label}\n\n" + "\n".join(
        f"{ic} {line}".strip() for ic, line in rows if line)
    text += "\n\nData: Open-Meteo.com"

    # --- the short version for a phone message: three lines
    day_short = datetime.strptime(today, "%Y-%m-%d").strftime("%a %d %b").replace(" 0", " ")
    if umbrella:
        windows = _ranges(wet_hours) or _ranges(maybe_hours)
        rain_short = "☔ Take an umbrella" + (f" ({_fmt_range(windows[0])})" if windows else "")
    else:
        rain_short = "🌂 No umbrella needed"
    air_short = f"{level['icon']} Air: {level['name'].lower()}" if worst is not None else "⚪ Air: no reading"
    name = place.get("name") or place_label(place)
    if now_hour >= 12 and temps:
        # afternoon / evening: only the hours still ahead, not the whole day again
        part = "tonight" if now_hour >= 17 else "this afternoon"
        codes = [h["weather_code"][i] for i in ahead if h["weather_code"][i] is not None]
        p_sky, p_icon = describe(max(codes, key=_severity)) if codes else (sky, icon)
        lo, hi = min(temps), max(temps)
        t_part = f"{lo:.0f}°C" if round(lo) == round(hi) else f"{lo:.0f}–{hi:.0f}°C"
        first = f"{p_icon} {name}, {part}: {t_part}, {p_sky.lower()}"
    else:
        first = f"{icon} {name}, {day_short}: {t_min:.0f}–{t_max:.0f}°C, {sky.lower()}"
    short_text = f"{first}\n{rain_short}\n{air_short}"

    return {
        "place": place_label(place), "date": today, "date_label": date_label, "now": now,
        "headline": headline, "umbrella": umbrella, "icon": icon, "sky": sky,
        "now_line": now_line, "temp_line": temp_line, "rain_line": rain_line, "wear_line": wear_line,
        "extras": extras, "air_line": air_line, "air_advice": air_advice, "aqi": worst,
        "aqi_level": level["name"], "aqi_icon": level["icon"], "main_pollutant": main_pol and POLLUTANT_NAMES[main_pol],
        "best_line": best_line, "tomorrow_line": tomorrow_line, "rain_chance": peak_p, "rain_mm": round(rain_mm, 1),
        "t_min": t_min, "t_max": t_max, "uv": uv, "hours": hours, "text": text, "short_text": short_text,
        "timezone": weather.get("timezone"),
    }


async def brief_for(place: dict, get_json: GetJson, now: str | None = None) -> dict:
    weather, air = await fetch(place, get_json)
    return make_brief(place, weather, air, now)


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


async def find_city(query: str, get_json: GetJson) -> dict | None:
    """The best match for "Town" or "Town, Region[, Country]" (e.g. "Salisbury, North Carolina" or "Salisbury, NC")."""
    town, *hints = [part.strip() for part in query.split(",") if part.strip()]
    places = await find_places(town, get_json, count=20 if hints else 1)
    if not places:
        return None
    hints = [US_STATES.get(h.upper(), h).lower() for h in hints]
    for p in places:
        where = [(p.get("admin1") or "").lower(), (p.get("country") or "").lower(), (p.get("country_code") or "").lower()]
        if all(any(w and (w == h or w.startswith(h)) for w in where) for h in hints):
            return p
    return places[0]


async def brief_for_city(name: str, get_json: GetJson, now: str | None = None) -> dict:
    place = await find_city(name, get_json)
    if not place:
        raise LookupError(f"Couldn't find a place called {name!r}.")
    return await brief_for(place, get_json, now)


# ---------------------------------------------------------------- browser entry points (Pyodide)

async def _pyfetch_json(url: str) -> dict:
    from pyodide.http import pyfetch  # only exists inside Pyodide

    resp = await pyfetch(url)
    if not resp.ok:
        raise RuntimeError(f"{resp.status} from {url.split('?')[0]}")
    return await resp.json()


async def places_json(name: str) -> str:
    """Suggestions for the search box; "Town, Region" puts the matching town first."""
    if "," not in name:
        return json.dumps(await find_places(name, _pyfetch_json))
    best = await find_city(name, _pyfetch_json)
    town = name.split(",")[0].strip()
    others = [p for p in await find_places(town, _pyfetch_json) if best is None or p["label"] != best["label"]]
    return json.dumps(([best] if best else []) + others)


async def brief_json(place_json: str) -> str:
    place = json.loads(place_json)
    place.setdefault("label", place_label(place))
    return json.dumps(await brief_for(place, _pyfetch_json))
