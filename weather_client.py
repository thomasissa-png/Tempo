"""Client Open-Meteo — meteo nationale ponderee sur 9 villes.

Utilise l'API gratuite Open-Meteo (https://open-meteo.com/) qui fournit
des previsions journalieres jusqu'a 16 jours sans cle API.

Calcule une moyenne ponderee par population/parc chauffage electrique
a partir des previsions de 9 villes representatives de la France.
"""

import httpx
import logging
import asyncio
import random
from datetime import datetime, date, timedelta
from config import Config
from database import get_db

logger = logging.getLogger(__name__)

# API Open-Meteo : endpoint et variables journalieres demandees
_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_DAILY_VARS = ",".join([
    "temperature_2m_max",
    "temperature_2m_min",
    "weather_code",
    "wind_speed_10m_max",
    "precipitation_sum",
])

# Descriptions WMO weather codes en francais
_WMO_DESCRIPTIONS = {
    0: "Ciel degagé",
    1: "Principalement degagé",
    2: "Partiellement nuageux",
    3: "Couvert",
    45: "Brouillard",
    48: "Brouillard givrant",
    51: "Bruine legere",
    53: "Bruine moderee",
    55: "Bruine dense",
    56: "Bruine verglacante legere",
    57: "Bruine verglacante dense",
    61: "Pluie legere",
    63: "Pluie moderee",
    65: "Pluie forte",
    66: "Pluie verglacante legere",
    67: "Pluie verglacante forte",
    71: "Neige legere",
    73: "Neige moderee",
    75: "Neige forte",
    77: "Grains de neige",
    80: "Averses legeres",
    81: "Averses moderees",
    82: "Averses violentes",
    85: "Averses de neige legeres",
    86: "Averses de neige fortes",
    95: "Orage",
    96: "Orage avec grele legere",
    99: "Orage avec grele forte",
}


# ================================================================
# FETCH MULTI-VILLES (Open-Meteo, 16 jours gratuits)
# ================================================================

async def fetch_forecast() -> list[dict]:
    """Previsions 16 jours, moyenne ponderee sur 9 villes via Open-Meteo."""
    city_forecasts = await _fetch_all_cities()
    if not city_forecasts:
        logger.warning("[Meteo] Open-Meteo indisponible — donnees simulees")
        return _generate_fallback_forecast()

    result = _merge_city_forecasts(city_forecasts)
    # Toutes les donnees Open-Meteo sont des previsions modele fiables
    for day in result:
        day["forecast_quality"] = "api"

    return result


async def fetch_forecast_extended() -> list[dict]:
    """Alias de fetch_forecast — Open-Meteo fournit 16 jours nativement."""
    return await fetch_forecast()


async def _fetch_all_cities() -> dict[str, list[dict]]:
    """Fetch previsions 16 jours pour chaque ville en parallele via Open-Meteo."""
    tasks = [_fetch_city_openmeteo(city) for city in Config.WEATHER_CITIES]
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

    # Fix P-5 : au moins 3 villes pour une moyenne nationale fiable
    if len(city_forecasts) < 3:
        logger.warning(
            f"[Meteo] Seulement {len(city_forecasts)} ville(s) sur "
            f"{len(Config.WEATHER_CITIES)} — moyenne peu representative"
        )

    logger.info(f"[Meteo] {len(city_forecasts)}/{len(Config.WEATHER_CITIES)} villes OK "
                f"({len(next(iter(city_forecasts.values())))} jours)")
    return city_forecasts


async def _fetch_city_openmeteo(city: dict) -> list[dict] | None:
    """Fetch previsions journalieres pour une ville via Open-Meteo."""
    params = {
        "latitude": city["lat"],
        "longitude": city["lon"],
        "daily": _DAILY_VARS,
        "timezone": "Europe/Paris",
        "forecast_days": 16,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(_OPEN_METEO_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
        return _parse_openmeteo_daily(data)


def _parse_openmeteo_daily(data: dict) -> list[dict]:
    """Parse la reponse Open-Meteo (format arrays paralleles) en liste de dicts.

    Format Open-Meteo :
    {
      "daily": {
        "time": ["2026-02-08", "2026-02-09", ...],
        "temperature_2m_max": [8.5, 7.2, ...],
        "temperature_2m_min": [2.1, 1.3, ...],
        ...
      }
    }
    """
    daily = data.get("daily", {})
    times = daily.get("time", [])
    temp_maxs = daily.get("temperature_2m_max", [])
    temp_mins = daily.get("temperature_2m_min", [])
    weather_codes = daily.get("weather_code", [])
    wind_maxs = daily.get("wind_speed_10m_max", [])
    precip_sums = daily.get("precipitation_sum", [])

    result = []
    for i, day_str in enumerate(times):
        t_min = temp_mins[i] if i < len(temp_mins) and temp_mins[i] is not None else 5.0
        t_max = temp_maxs[i] if i < len(temp_maxs) and temp_maxs[i] is not None else 10.0
        t_moy = round((t_min + t_max) / 2, 1)

        wmo = weather_codes[i] if i < len(weather_codes) and weather_codes[i] is not None else 0
        wind = wind_maxs[i] if i < len(wind_maxs) and wind_maxs[i] is not None else 10.0
        precip = precip_sums[i] if i < len(precip_sums) and precip_sums[i] is not None else 0.0

        # Estimation humidite depuis meteo code et precipitations
        humidity = _estimate_humidity(wmo, precip, t_moy)

        result.append({
            "date": day_str,
            "temp_min": round(t_min, 1),
            "temp_max": round(t_max, 1),
            "temp_moy": t_moy,
            "humidity": humidity,  # ESTIMEE depuis WMO code (non fournie par Open-Meteo daily)
            "wind_speed": round(wind, 1),
            "pressure": None,  # NON DISPONIBLE dans Open-Meteo daily — non utilise dans scoring
            "description": _WMO_DESCRIPTIONS.get(wmo, f"Code WMO {wmo}"),
            "precipitation": round(precip, 1),
            "weather_code": wmo,
        })
    return result


def _estimate_humidity(wmo_code: int, precipitation: float, temp_moy: float) -> float:
    """Estime l'humidite relative depuis le code meteo et les precipitations.
    Open-Meteo daily ne fournit pas directement la moyenne d'humidite."""
    # Base selon type de temps
    if wmo_code >= 61:  # Pluie, neige, orage
        base = 85
    elif wmo_code >= 45:  # Brouillard, bruine
        base = 90
    elif wmo_code >= 3:  # Couvert
        base = 75
    elif wmo_code >= 1:  # Partiellement nuageux
        base = 60
    else:  # Ciel degagé
        base = 50

    # Ajustement par precipitations
    if precipitation > 10:
        base = min(95, base + 10)
    elif precipitation > 2:
        base = min(90, base + 5)

    # L'air froid est souvent plus humide en relatif
    if temp_moy < 0:
        base = min(95, base + 5)

    return float(base)


# ================================================================
# MERGE : moyenne ponderee des villes
# ================================================================

def _merge_city_forecasts(city_forecasts: dict[str, list[dict]]) -> list[dict]:
    """Fusionne les previsions de toutes les villes en moyennes ponderees nationales."""
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
            "humidity": round(humidity_weighted / total_weight, 1),  # Estimee
            "wind_speed": round(wind_weighted / total_weight, 1),
            "pressure": None,  # Non disponible via Open-Meteo daily
            "description": "moyenne nationale ponderee",
            "city_details": city_details,
        })

    return result


# ================================================================
# FALLBACK (si Open-Meteo indisponible)
# ================================================================

# Normales saisonnieres nationales ponderees (min, max) par mois
_SEASONAL_NORMALS = {
    1: (-1, 6),   2: (-1, 7),   3: (2, 11),  4: (5, 15),
    5: (9, 19),   6: (12, 23),  7: (14, 26),  8: (14, 25),
    9: (11, 21),  10: (7, 16),  11: (3, 10),  12: (0, 7),
}


def _generate_fallback_forecast() -> list[dict]:
    """Donnees meteo simulees — moyennes saisonnieres nationales ponderees.

    Utilise uniquement quand Open-Meteo est inaccessible.
    Variations min/max independantes, mois de la date cible.
    """
    today = date.today()

    result = []
    for i in range(16):
        d = today + timedelta(days=i)
        month = d.month
        base_min, base_max = _SEASONAL_NORMALS.get(month, (3, 10))

        rng = random.Random(today.toordinal() + i)
        var_min = rng.uniform(-4, 4)
        var_max = rng.uniform(-4, 4)
        t_min = round(base_min + var_min, 1)
        t_max = round(base_max + var_max, 1)
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
                 f.get("pressure"), f.get("humidity", 50),
                 f.get("wind_speed", 10), f.get("description", ""), now),
            )
        conn.commit()
        logger.info(f"[Meteo] {len(forecasts)} jours mis en cache")
    finally:
        conn.close()
