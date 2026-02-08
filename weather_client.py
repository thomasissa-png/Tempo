"""Client OpenWeatherMap v2 — meteo nationale ponderee sur 8 villes.

Calcule une moyenne ponderee par population/parc chauffage electrique
a partir des previsions de 8 villes representatives de la France.
"""

import httpx
import logging
import asyncio
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from config import Config
from database import get_db

logger = logging.getLogger(__name__)


# ================================================================
# FETCH MULTI-VILLES
# ================================================================

async def fetch_forecast() -> list[dict]:
    """Previsions 5 jours, moyenne ponderee sur 8 villes."""
    if not Config.OPENWEATHER_API_KEY:
        logger.warning("[Meteo] Pas de cle API — donnees simulees")
        return _generate_fallback_forecast()

    city_forecasts = await _fetch_all_cities_5day()
    if not city_forecasts:
        return _generate_fallback_forecast()

    return _merge_city_forecasts(city_forecasts)


async def fetch_forecast_extended() -> list[dict]:
    """Tente le forecast 16 jours (API payante), sinon fallback 5 jours multi-villes."""
    if not Config.OPENWEATHER_API_KEY:
        return _generate_fallback_forecast()

    # Essai API 16 jours sur Paris pour tester la disponibilite
    url = "https://api.openweathermap.org/data/2.5/forecast/daily"
    params = {
        "lat": Config.WEATHER_LAT, "lon": Config.WEATHER_LON,
        "appid": Config.OPENWEATHER_API_KEY, "units": "metric", "cnt": 16,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            if resp.status_code in (401, 403):
                logger.info("[Meteo] API 16 jours non dispo, fallback 5 jours multi-villes")
                return await fetch_forecast()
            resp.raise_for_status()
            # API 16 jours dispo : fetcher toutes les villes en 16 jours
            city_forecasts = await _fetch_all_cities_16day()
            if city_forecasts:
                return _merge_city_forecasts(city_forecasts)
            return await fetch_forecast()
    except Exception:
        return await fetch_forecast()


async def _fetch_all_cities_5day() -> dict[str, list[dict]]:
    """Fetch previsions 5 jours pour chaque ville en parallele."""
    tasks = []
    for city in Config.WEATHER_CITIES:
        tasks.append(_fetch_city_5day(city))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    city_forecasts = {}
    for city, result in zip(Config.WEATHER_CITIES, results):
        if isinstance(result, Exception):
            logger.warning(f"[Meteo] Erreur {city['name']}: {result}")
            continue
        if result:
            city_forecasts[city["name"]] = result

    if not city_forecasts:
        logger.error("[Meteo] Aucune ville n'a repondu")
        return {}

    logger.info(f"[Meteo] {len(city_forecasts)}/{len(Config.WEATHER_CITIES)} villes OK")
    return city_forecasts


async def _fetch_city_5day(city: dict) -> list[dict] | None:
    """Fetch previsions 5 jours pour une ville."""
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "lat": city["lat"], "lon": city["lon"],
        "appid": Config.OPENWEATHER_API_KEY,
        "units": "metric", "lang": "fr",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return _aggregate_3h_to_daily(data.get("list", []))


async def _fetch_all_cities_16day() -> dict[str, list[dict]]:
    """Fetch previsions 16 jours pour chaque ville en parallele."""
    tasks = []
    for city in Config.WEATHER_CITIES:
        tasks.append(_fetch_city_16day(city))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    city_forecasts = {}
    for city, result in zip(Config.WEATHER_CITIES, results):
        if isinstance(result, Exception):
            continue
        if result:
            city_forecasts[city["name"]] = result

    return city_forecasts


async def _fetch_city_16day(city: dict) -> list[dict] | None:
    """Fetch previsions 16 jours pour une ville."""
    url = "https://api.openweathermap.org/data/2.5/forecast/daily"
    params = {
        "lat": city["lat"], "lon": city["lon"],
        "appid": Config.OPENWEATHER_API_KEY,
        "units": "metric", "cnt": 16, "lang": "fr",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return _parse_daily_forecast(data.get("list", []))


# ================================================================
# MERGE : moyenne ponderee des villes
# ================================================================

def _merge_city_forecasts(city_forecasts: dict[str, list[dict]]) -> list[dict]:
    """Fusionne les previsions de toutes les villes en moyennes ponderees nationales."""
    # Construire un index poids par ville
    weight_map = {c["name"]: c["weight"] for c in Config.WEATHER_CITIES}

    # Trouver toutes les dates disponibles
    all_dates = set()
    for forecasts in city_forecasts.values():
        for f in forecasts:
            all_dates.add(f["date"])

    result = []
    for day_str in sorted(all_dates):
        temp_min_weighted = 0.0
        temp_max_weighted = 0.0
        temp_moy_weighted = 0.0
        humidity_weighted = 0.0
        wind_weighted = 0.0
        pressure_weighted = 0.0
        total_weight = 0.0

        city_details = {}
        for city_name, forecasts in city_forecasts.items():
            day_data = next((f for f in forecasts if f["date"] == day_str), None)
            if not day_data:
                continue
            w = weight_map.get(city_name, 0.1)
            total_weight += w
            temp_min_weighted += day_data["temp_min"] * w
            temp_max_weighted += day_data["temp_max"] * w
            temp_moy_weighted += day_data["temp_moy"] * w
            humidity_weighted += day_data.get("humidity", 50) * w
            wind_weighted += day_data.get("wind_speed", 10) * w
            pressure_weighted += day_data.get("pressure", 1013) * w
            city_details[city_name] = {
                "temp_min": day_data["temp_min"],
                "temp_max": day_data["temp_max"],
            }

        if total_weight == 0:
            continue

        result.append({
            "date": day_str,
            "temp_min": round(temp_min_weighted / total_weight, 1),
            "temp_max": round(temp_max_weighted / total_weight, 1),
            "temp_moy": round(temp_moy_weighted / total_weight, 1),
            "humidity": round(humidity_weighted / total_weight, 1),
            "wind_speed": round(wind_weighted / total_weight, 1),
            "pressure": round(pressure_weighted / total_weight, 1),
            "description": "moyenne nationale ponderee",
            "city_details": city_details,
        })

    return result


# ================================================================
# PARSING DES FORMATS API
# ================================================================

_CET = ZoneInfo("Europe/Paris")


def _aggregate_3h_to_daily(entries: list[dict]) -> list[dict]:
    """Agrege les donnees 3h en resumes journaliers.
    Fix #7 audit v4 : groupement en heure francaise (CET/CEST) au lieu de UTC."""
    daily: dict[str, list[dict]] = {}
    for entry in entries:
        dt = datetime.fromtimestamp(entry["dt"], tz=_CET)
        day_str = dt.date().isoformat()
        daily.setdefault(day_str, []).append(entry)

    result = []
    for day_str, items in sorted(daily.items()):
        temps = [e["main"]["temp"] for e in items]
        temp_mins = [e["main"]["temp_min"] for e in items]
        temp_maxs = [e["main"]["temp_max"] for e in items]
        humidities = [e["main"]["humidity"] for e in items]
        winds = [e["wind"]["speed"] for e in items]
        pressures = [e["main"]["pressure"] for e in items]

        result.append({
            "date": day_str,
            "temp_min": round(min(temp_mins), 1),
            "temp_max": round(max(temp_maxs), 1),
            "temp_moy": round(sum(temps) / len(temps), 1),
            "humidity": round(sum(humidities) / len(humidities), 1),
            "wind_speed": round(max(winds), 1),
            "pressure": round(sum(pressures) / len(pressures), 1),
            "description": items[len(items) // 2]["weather"][0].get("description", ""),
        })
    return result


def _parse_daily_forecast(entries: list[dict]) -> list[dict]:
    """Parse le format 16-day daily forecast."""
    result = []
    for entry in entries:
        dt = datetime.fromtimestamp(entry["dt"], tz=_CET)
        temp = entry.get("temp", {})
        result.append({
            "date": dt.date().isoformat(),
            "temp_min": round(temp.get("min", 0), 1),
            "temp_max": round(temp.get("max", 0), 1),
            "temp_moy": round(temp.get("day", 0), 1),
            "humidity": entry.get("humidity", 0),
            "wind_speed": round(entry.get("speed", 0), 1),
            "pressure": entry.get("pressure", 1013),
            "description": entry.get("weather", [{}])[0].get("description", ""),
        })
    return result


# ================================================================
# FALLBACK (sans cle API)
# ================================================================

def _generate_fallback_forecast() -> list[dict]:
    """Donnees meteo simulees — moyennes saisonnieres nationales ponderees.
    Fix #25 : utilise un Random local pour ne pas polluer l'etat global."""
    import random

    today = date.today()
    month = today.month
    # Moyennes nationales ponderees (pas juste Paris)
    seasonal = {
        1: (-2, 5), 2: (-1, 6), 3: (2, 10), 4: (5, 14), 5: (9, 18),
        6: (12, 22), 7: (14, 25), 8: (14, 24),
        9: (11, 20), 10: (7, 15), 11: (3, 9), 12: (0, 6),
    }
    base_min, base_max = seasonal.get(month, (3, 10))

    result = []
    for i in range(15):
        d = today + timedelta(days=i)
        # Fix #25 : instance locale au lieu de random.seed global
        rng = random.Random(today.toordinal() + i)
        variation = rng.uniform(-4, 4)
        t_min = round(base_min + variation, 1)
        t_max = round(base_max + variation, 1)
        result.append({
            "date": d.isoformat(),
            "temp_min": t_min,
            "temp_max": t_max,
            "temp_moy": round((t_min + t_max) / 2, 1),
            "humidity": round(rng.uniform(50, 85), 1),
            "wind_speed": round(rng.uniform(5, 25), 1),
            "pressure": round(rng.uniform(1005, 1035), 1),
            "description": "donnees simulees",
        })
    return result


# ================================================================
# CACHE
# ================================================================

def cache_weather(forecasts: list[dict]):
    """Stocke les previsions dans weather_cache.

    Fix #9 : INSERT OR REPLACE avec UNIQUE(date) evite les doublons.
    """
    if not forecasts:
        return
    conn = get_db()
    now = datetime.now().isoformat()
    try:
        for f in forecasts:
            conn.execute(
                """INSERT OR REPLACE INTO weather_cache
                   (date, temp_min, temp_max, temp_moy, pressure, humidity,
                    wind_speed, description, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f["date"], f["temp_min"], f["temp_max"], f["temp_moy"],
                 f.get("pressure", 1013), f.get("humidity", 50),
                 f.get("wind_speed", 10), f.get("description", ""), now),
            )
        conn.commit()
        logger.info(f"[Meteo] {len(forecasts)} jours mis en cache")
    finally:
        conn.close()
