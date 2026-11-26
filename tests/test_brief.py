"""Tests for brief.py and send_brief.py, using saved made-up Open-Meteo answers (no internet needed).

    python -m pytest -q
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brief  # noqa: E402
import send_brief  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


PLACE = load("geocode.json")["results"][0]


def fake_get_json(forecast="forecast_rainy.json", air="air_clean.json", seen=None):
    async def get_json(url):
        if seen is not None:
            seen.append(url)
        if "geocoding" in url:
            return load("geocode.json")
        if "air-quality" in url:
            if air is None:
                raise RuntimeError("air down")
            return load(air)
        return load(forecast)
    return get_json


# ---------------------------------------------------------------- AQI maths

@pytest.mark.parametrize("pollutant,value,expected", [
    ("pm2_5", 0, 0), ("pm2_5", 9.0, 50), ("pm2_5", 9.05, 51), ("pm2_5", 35.4, 100), ("pm2_5", 55.4, 150),
    ("pm2_5", 150.0, 225), ("pm10", 54, 50), ("pm10", 154, 100),
    ("ozone", 54 * 1.96, 50), ("nitrogen_dioxide", 100 * 1.88, 100),
])
def test_sub_index_matches_epa_breakpoints(pollutant, value, expected):
    assert brief.sub_index(pollutant, value) == expected


def test_overall_aqi_is_worst_pollutant():
    idx, main = brief.aqi({"pm2_5": 40.0, "pm10": 20, "ozone": 60, "nitrogen_dioxide": 10})
    assert main == "pm2_5" and 101 <= idx <= 150
    assert brief.aqi({}) == (None, None)


def test_aqi_levels():
    assert brief.aqi_level(42)["name"] == "Good"
    assert brief.aqi_level(120)["name"] == "Unhealthy for sensitive groups"
    assert brief.aqi_level(450)["name"] == "Hazardous"
    assert brief.aqi_level(None)["name"] == "No data"


# ---------------------------------------------------------------- the brief

def test_rainy_day_says_umbrella_with_time_window():
    b = brief.make_brief(PLACE, load("forecast_rainy.json"), load("air_clean.json"))
    assert b["umbrella"] is True
    assert "Take an umbrella" in b["rain_line"] and "14:00–18:00" in b["rain_line"]
    assert b["rain_chance"] == 75
    assert "raincoat or umbrella" in b["wear_line"]
    assert b["place"] == "Durban, KwaZulu-Natal, South Africa"
    assert b["text"].startswith("☀️ Morning brief for Durban")
    assert "Open-Meteo" in b["text"]


def test_dry_day_no_umbrella_and_bad_air_warning():
    b = brief.make_brief(PLACE, load("forecast_dry.json"), load("air_dirty.json"))
    assert b["umbrella"] is False and "No umbrella needed" in b["rain_line"]
    assert b["aqi_level"] == "Unhealthy for sensitive groups"
    assert "fine dust" in b["air_line"] and "asthma" in b["air_advice"]


def test_later_in_day_only_counts_hours_ahead():
    b = brief.make_brief(PLACE, load("forecast_rainy.json"), load("air_clean.json"), now="2026-11-26T19:10")
    assert b["umbrella"] is False  # the rain was 14:00 to 18:00
    assert b["now_line"] is None   # the "current" reading is from the morning
    assert sum(h["past"] for h in b["hours"]) == 19


def test_best_time_avoids_rain():
    b = brief.make_brief(PLACE, load("forecast_rainy.json"), load("air_clean.json"))
    start = int(b["best_line"].split(": ")[1][:2])
    assert not 14 <= start <= 17


def test_extras_and_tomorrow():
    b = brief.make_brief(PLACE, load("forecast_rainy.json"), load("air_clean.json"))
    assert any("UV 9" in e for e in b["extras"]) and any("gusts" in e for e in b["extras"])
    assert b["tomorrow_line"].startswith("Tomorrow:") and "air" in b["tomorrow_line"]
    assert len(b["hours"]) == 24


def test_works_without_air_data():
    b = asyncio.run(brief.brief_for(PLACE, fake_get_json(air=None)))
    assert b["aqi"] is None and "no reading" in b["air_line"]


def test_brief_for_city_uses_the_first_match_and_right_urls():
    seen = []
    b = asyncio.run(brief.brief_for_city("Durban", fake_get_json(seen=seen)))
    assert b["place"].startswith("Durban, KwaZulu-Natal")
    assert "name=Durban" in seen[0]
    assert "timezone=auto" in seen[1] and "precipitation_probability" in seen[1]
    assert "pm2_5" in seen[2]


def test_find_places_labels_and_short_names():
    places = asyncio.run(brief.find_places("Durban", fake_get_json()))
    assert [p["label"] for p in places] == ["Durban, KwaZulu-Natal, South Africa", "Durban, Occitanie, France"]
    assert asyncio.run(brief.find_places("D", fake_get_json())) == []


def test_unknown_city():
    async def nothing(url):
        return {}
    with pytest.raises(LookupError):
        asyncio.run(brief.brief_for_city("Nowhereville", nothing))


# ---------------------------------------------------------------- sending

def test_send_brief_prints_and_skips_without_keys(monkeypatch, capsys):
    monkeypatch.setattr(send_brief, "get_json", fake_get_json())
    for k in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "CALLMEBOT_PHONE", "CALLMEBOT_APIKEY"]:
        monkeypatch.delenv(k, raising=False)
    assert send_brief.main(["Durban", "--send"]) == 0
    out = capsys.readouterr().out
    assert "Durban, Thu 26 Nov" in out and "Nothing sent" in out


def test_send_brief_sends_to_both(monkeypatch, capsys):
    monkeypatch.setattr(send_brief, "get_json", fake_get_json())
    calls = []
    monkeypatch.setattr(send_brief, "send_telegram", lambda *a: calls.append(("tg", a)))
    monkeypatch.setattr(send_brief, "send_whatsapp_callmebot", lambda *a: calls.append(("wa", a)))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("CALLMEBOT_PHONE", "+27000"); monkeypatch.setenv("CALLMEBOT_APIKEY", "k")
    assert send_brief.main(["Durban", "--send"]) == 0
    assert [c[0] for c in calls] == ["tg", "wa"]
    assert calls[0][1][2].count("\n") == 2  # short message by default
    assert "Sent to Telegram and WhatsApp" in capsys.readouterr().out


def test_night_run_briefs_the_coming_day():
    b = brief.make_brief(PLACE, load("forecast_rainy.json"), load("air_clean.json"), now="2026-11-26T02:14")
    assert b["umbrella"] is True and "14:00–18:00" in b["rain_line"]
    assert "02:00" not in (b["best_line"] or "")


def test_all_day_good_says_any_time():
    w = load("forecast_dry.json")
    w["hourly"]["temperature_2m"] = [20.0] * len(w["hourly"]["time"])
    air = load("air_clean.json")
    for k in ("pm2_5", "pm10", "nitrogen_dioxide"):
        air["hourly"][k] = [1.0] * len(air["hourly"]["time"])
    air["hourly"]["ozone"] = [20.0] * len(air["hourly"]["time"])
    b = brief.make_brief(PLACE, w, air, now="2026-11-26T05:00")
    assert b["best_line"] == "Any time is fine to be outside today."


def test_short_message_is_three_lines():
    b = brief.make_brief(PLACE, load("forecast_rainy.json"), load("air_clean.json"))
    lines = b["short_text"].split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("🌧️ Durban, Thu 26 Nov: 10–24°C")
    assert lines[1] == "☔ Take an umbrella (14:00–18:00)"
    assert lines[2] == "🟡 Air: moderate"
