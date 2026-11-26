"""Writes made-up but realistic Open-Meteo answers to tests/fixtures/ (used by the tests and the browser test).

Run:  python tests/make_fixtures.py
"""
import json
import math
from pathlib import Path

OUT = Path(__file__).parent / "fixtures"
DAYS = ["2026-11-26", "2026-11-27"]
TIMES = [f"{d}T{h:02d}:00" for d in DAYS for h in range(24)]


def weather(rainy: bool) -> dict:
    temp, app, prob, mm, code, wind, gust, uv = [], [], [], [], [], [], [], []
    for n, t in enumerate(TIMES):
        h = n % 24
        day2 = n >= 24
        base = 17 + 7 * math.sin((h - 9) / 24 * 2 * math.pi) + (1.5 if day2 else 0)
        temp.append(round(base, 1)); app.append(round(base + 1.2, 1))
        if rainy and not day2 and 14 <= h <= 17:
            p = [55, 75, 70, 45][h - 14]; prob.append(p); mm.append([0.8, 2.6, 1.9, 0.4][h - 14]); code.append(63 if p > 60 else 61)
        elif rainy and not day2 and h in (12, 13, 18):
            prob.append(30); mm.append(0.0); code.append(3)
        else:
            prob.append(5 if not day2 else 10); mm.append(0.0); code.append(1 if 6 <= h <= 18 else 0)
        wind.append(12.0); gust.append(28.0 if not rainy else 46.0)
        uv.append(round(max(0, 9 * math.sin((h - 6) / 13 * math.pi)), 1) if 6 <= h <= 19 else 0.0)
    return {
        "latitude": -29.875, "longitude": 31.0, "timezone": "Africa/Johannesburg", "timezone_abbreviation": "GMT+2",
        "utc_offset_seconds": 7200, "elevation": 8.0,
        "current": {"time": "2026-11-26T06:45", "interval": 900, "temperature_2m": 15.8, "apparent_temperature": 15.1,
                    "weather_code": 1, "wind_speed_10m": 9.4},
        "hourly": {"time": TIMES, "temperature_2m": temp, "apparent_temperature": app, "precipitation_probability": prob,
                   "precipitation": mm, "weather_code": code, "wind_speed_10m": wind, "wind_gusts_10m": gust, "uv_index": uv},
        "daily": {"time": DAYS, "weather_code": [63 if rainy else 1, 2],
                  "temperature_2m_max": [round(max(temp[:24]), 1), round(max(temp[24:]), 1)],
                  "temperature_2m_min": [round(min(temp[:24]), 1), round(min(temp[24:]), 1)],
                  "precipitation_probability_max": [max(prob[:24]), max(prob[24:])],
                  "precipitation_sum": [round(sum(mm[:24]), 1), 0.0], "uv_index_max": [9.0, 8.1],
                  "wind_gusts_10m_max": [max(gust[:24]), 28.0],
                  "sunrise": ["2026-11-26T04:52", "2026-11-27T04:52"], "sunset": ["2026-11-26T18:41", "2026-11-27T18:42"]},
    }


def air(dirty: bool) -> dict:
    pm25, pm10, o3, no2, us = [], [], [], [], []
    for n, t in enumerate(TIMES):
        h = n % 24
        rush = 1.0 + 0.8 * math.exp(-((h - 7.5) ** 2) / 3) + 0.9 * math.exp(-((h - 18) ** 2) / 4)
        f = 4.5 if dirty else 1.0
        pm25.append(round(5.5 * rush * f, 1)); pm10.append(round(12 * rush * f, 1))
        o3.append(round(40 + 45 * max(0, math.sin((h - 8) / 12 * math.pi)), 1)); no2.append(round(14 * rush * f, 1))
        us.append(None)
    return {"latitude": -29.9, "longitude": 31.0, "timezone": "Africa/Johannesburg", "utc_offset_seconds": 7200,
            "hourly": {"time": TIMES, "pm2_5": pm25, "pm10": pm10, "ozone": o3, "nitrogen_dioxide": no2, "us_aqi": us}}


GEO = {"results": [
    {"id": 1007311, "name": "Durban", "latitude": -29.8579, "longitude": 31.0292, "country_code": "ZA",
     "timezone": "Africa/Johannesburg", "population": 3120282, "country": "South Africa", "admin1": "KwaZulu-Natal"},
    {"id": 4692400, "name": "Durban", "latitude": 43.4, "longitude": 1.4, "country_code": "FR",
     "timezone": "Europe/Paris", "population": 1200, "country": "France", "admin1": "Occitanie"},
]}

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, data in {"geocode.json": GEO, "forecast_rainy.json": weather(True), "forecast_dry.json": weather(False),
                       "air_clean.json": air(False), "air_dirty.json": air(True)}.items():
        (OUT / name).write_text(json.dumps(data, indent=1))
    print("written to", OUT)
