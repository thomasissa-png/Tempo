"""Client OpenWeatherMap v3 — meteo nationale ponderee sur 9 villes.

Calcule une moyenne ponderee par population/parc chauffage electrique
a partir des previsions de 9 villes representatives de la France.

Fix meteo #1 : extrapolation climatologique J+6 a J+15 quand API gratuite (5j).
Fix meteo #2 : poids reequilibres (Marseille -, Lille/Strasbourg +, +Clermont).
Fix meteo #3 : variations min/max independantes dans le fallback.
Fix meteo #4 : mois de la date cible dans le fallback (pas mois courant).
"""

import httpx
import logging
import asyncio
import random
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from config import Config
from database import get_db

logger = logging.getLogger(__name__)


# ================================================================
# FETCH MULTI-VILLES
# ================================================================

async def fetch_forecast() -> list[dict]:
    """Previsions 5 jours reelles + extrapolation climatologique J+6 a J+15.

    Fix meteo #1 : etend les 5 jours API a 15 jours en regressant
    progressivement vers les normales saisonnieres. Chaque jour porte
    un champ 'forecast_quality' : 'api' ou 'climatology'.
    """
    if not Config.OPENWEATHER_API_KEY:
        logger.warning("[Meteo] Pas de cle API — donnees simulees")
        return _generate_fallback_forecast()

    city_forecasts = await _fetch_all_cities_5day()
    if not city_forecasts:
        return _generate_fallback_forecast()

    api_days = _merge_city_forecasts(city_forecasts)

    # Marquer les jours API comme fiables
    for day in api_days:
        day["forecast_quality"] = "api"

    # Etendre a 15 jours si on n'a que 5-6 jours (API gratuite)
    if len(api_days) < 12:
        extended = _extrapolate_to_15_days(api_days)
        logger.info(f"[Meteo] {len(api_days)} jours API + "
                    f"{len(extended) - len(api_days)} jours extrapoles")
        return extended

    return api_days


async def fetch_forecast_extended() -> list[dict]:
    """Tente le forecast 16 jours (API payante), sinon fallback 5 jours + extrapolation."""
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
                logger.info("[Meteo] API 16 jours non dispo, fallback 5 jours + extrapolation")
                return await fetch_forecast()
            resp.raise_for_status()
            # API 16 jours dispo : fetcher toutes les villes en 16 jours
            city_forecasts = await _fetch_all_cities_16day()
            if city_forecasts:
                result = _merge_city_forecasts(city_forecasts)
                for day in result:
                    day["forecast_quality"] = "api"
                return result
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
# EXTRAPOLATION CLIMATOLOGIQUE (Fix meteo #1)
# ================================================================

# Normales saisonnieres nationales ponderees (min, max) par mois
# Basees sur Meteo France 1991-2020, ponderees par les poids des villes
_SEASONAL_NORMALS = {
    1: (-1, 6),   2: (-1, 7),   3: (2, 11),  4: (5, 15),
    5: (9, 19),   6: (12, 23),  7: (14, 26),  8: (14, 25),
    9: (11, 21),  10: (7, 16),  11: (3, 10),  12: (0, 7),
}


def _extrapolate_to_15_days(api_days: list[dict]) -> list[dict]:
    """Etend les jours API (5-6) a 15 jours par regression climatologique.

    Strategie : les derniers jours API servent de point de depart.
    On regresse lineairement vers les normales saisonnieres du mois cible
    avec un facteur de blend croissant (0% au jour 6 -> ~80% au jour 15).
    Un bruit deterministique simule la variabilite naturelle.
    """
    if not api_days:
        return _generate_fallback_forecast()

    last_api = api_days[-1]
    last_date = date.fromisoformat(last_api["date"])
    last_temp_min = last_api["temp_min"]
    last_temp_max = last_api["temp_max"]
    last_humidity = last_api.get("humidity", 65)
    last_wind = last_api.get("wind_speed", 12)
    last_pressure = last_api.get("pressure", 1015)

    # Tendance des 2-3 derniers jours API (est-ce que ca se refroidit ?)
    trend_min = 0.0
    trend_max = 0.0
    if len(api_days) >= 3:
        trend_min = (api_days[-1]["temp_min"] - api_days[-3]["temp_min"]) / 2
        trend_max = (api_days[-1]["temp_max"] - api_days[-3]["temp_max"]) / 2
    elif len(api_days) >= 2:
        trend_min = api_days[-1]["temp_min"] - api_days[-2]["temp_min"]
        trend_max = api_days[-1]["temp_max"] - api_days[-2]["temp_max"]

    # Limiter la tendance pour ne pas diverger
    trend_min = max(-2.0, min(2.0, trend_min))
    trend_max = max(-2.0, min(2.0, trend_max))

    result = list(api_days)
    today = date.today()
    target_days = 15

    for i in range(len(api_days), target_days):
        d = today + timedelta(days=i)
        month = d.month
        norm_min, norm_max = _SEASONAL_NORMALS.get(month, (3, 10))

        # Blend progressif : 0% climatologie au debut, ~80% a J+15
        # Sur 10 jours extrapoles (J+6 a J+15), blend va de ~10% a ~80%
        days_beyond_api = i - len(api_days) + 1
        blend = min(0.8, days_beyond_api * 0.08)  # 0.08 par jour

        # Temperature avec tendance amortie + regression vers normale
        damped_trend_min = trend_min * max(0, 1.0 - days_beyond_api * 0.15)
        damped_trend_max = trend_max * max(0, 1.0 - days_beyond_api * 0.15)

        ext_min = (1 - blend) * (last_temp_min + damped_trend_min * days_beyond_api) + blend * norm_min
        ext_max = (1 - blend) * (last_temp_max + damped_trend_max * days_beyond_api) + blend * norm_max

        # Bruit deterministique pour variabilite
        rng = random.Random(today.toordinal() + i)
        noise_min = rng.uniform(-1.5, 1.5)
        noise_max = rng.uniform(-1.5, 1.5)

        t_min = round(ext_min + noise_min, 1)
        t_max = round(ext_max + noise_max, 1)
        # Garantir min < max
        if t_min >= t_max:
            t_max = t_min + 2.0

        t_moy = round((t_min + t_max) / 2, 1)

        # Meteorologie extrapolee avec regression vers les normales
        ext_humidity = round((1 - blend) * last_humidity + blend * 70 + rng.uniform(-5, 5), 1)
        ext_wind = round((1 - blend) * last_wind + blend * 12 + rng.uniform(-3, 3), 1)
        ext_pressure = round((1 - blend) * last_pressure + blend * 1015 + rng.uniform(-5, 5), 1)

        result.append({
            "date": d.isoformat(),
            "temp_min": t_min,
            "temp_max": t_max,
            "temp_moy": t_moy,
            "humidity": max(20, min(100, ext_humidity)),
            "wind_speed": max(0, ext_wind),
            "pressure": max(980, min(1050, ext_pressure)),
            "description": f"extrapolation climatologique (J+{i})",
            "forecast_quality": "climatology",
        })

    return result


# ================================================================
# FALLBACK (sans cle API)
# ================================================================

def _generate_fallback_forecast() -> list[dict]:
    """Donnees meteo simulees — moyennes saisonnieres nationales ponderees.

    Fix #25 : utilise un Random local pour ne pas polluer l'etat global.
    Fix meteo #3 : variations min/max independantes (ecart variable).
    Fix meteo #4 : utilise le mois de la date cible (pas mois courant).
    """
    today = date.today()

    result = []
    for i in range(15):
        d = today + timedelta(days=i)
        # Fix meteo #4 : mois de la date cible, pas du jour courant
        month = d.month
        base_min, base_max = _SEASONAL_NORMALS.get(month, (3, 10))

        # Fix #25 : instance locale au lieu de random.seed global
        rng = random.Random(today.toordinal() + i)
        # Fix meteo #3 : variations separees pour min et max
        var_min = rng.uniform(-4, 4)
        var_max = rng.uniform(-4, 4)
        t_min = round(base_min + var_min, 1)
        t_max = round(base_max + var_max, 1)
        # Garantir min < max
        if t_min >= t_max:
            t_max = t_min + 2.0
        result.append({
            "date": d.isoformat(),
            "temp_min": t_min,
            "temp_max": t_max,
            "temp_moy": round((t_min + t_max) / 2, 1),
            "humidity": round(rng.uniform(50, 85), 1),
            "wind_speed": round(rng.uniform(5, 25), 1),
            "pressure": round(rng.uniform(1005, 1035), 1),
            "description": "donnees simulees",
            "forecast_quality": "simulated",
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
