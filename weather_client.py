"""Client OpenWeatherMap — prévisions météo pour la prédiction Tempo.

Utilise l'endpoint gratuit 5-day/3-hour forecast, agrégé en données journalières.
Si la clé API payante est disponible, utilise le 16-day daily forecast.
"""

import httpx
import logging
from datetime import datetime, date
from config import Config
from database import get_db

logger = logging.getLogger(__name__)


async def fetch_forecast() -> list[dict]:
    """Récupère les prévisions météo (5 jours, gratuit).
    Retourne une liste de dicts journaliers agrégés."""
    if not Config.OPENWEATHER_API_KEY:
        logger.warning("[Météo] Pas de clé API configurée — données simulées")
        return _generate_fallback_forecast()

    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "lat": Config.WEATHER_LAT,
        "lon": Config.WEATHER_LON,
        "appid": Config.OPENWEATHER_API_KEY,
        "units": "metric",
        "lang": "fr",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            forecasts = _aggregate_3h_to_daily(data.get("list", []))
            logger.info(f"[Météo] {len(forecasts)} jours de prévisions récupérés")
            return forecasts
    except Exception as e:
        logger.error(f"[Météo] Erreur fetch forecast : {e}")
        return _generate_fallback_forecast()


async def fetch_forecast_extended() -> list[dict]:
    """Tente le forecast 16 jours (API payante), sinon fallback sur 5 jours."""
    if not Config.OPENWEATHER_API_KEY:
        return _generate_fallback_forecast()

    url = "https://api.openweathermap.org/data/2.5/forecast/daily"
    params = {
        "lat": Config.WEATHER_LAT,
        "lon": Config.WEATHER_LON,
        "appid": Config.OPENWEATHER_API_KEY,
        "units": "metric",
        "cnt": 16,
        "lang": "fr",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            if resp.status_code in (401, 403):
                logger.info("[Météo] API 16 jours non dispo, fallback 5 jours")
                return await fetch_forecast()
            resp.raise_for_status()
            data = resp.json()
            return _parse_daily_forecast(data.get("list", []))
    except Exception:
        return await fetch_forecast()


def _aggregate_3h_to_daily(entries: list[dict]) -> list[dict]:
    """Agrège les données 3h en résumés journaliers."""
    daily: dict[str, list[dict]] = {}
    for entry in entries:
        dt = datetime.fromtimestamp(entry["dt"])
        day_str = dt.date().isoformat()
        daily.setdefault(day_str, []).append(entry)

    result = []
    for day_str, items in sorted(daily.items()):
        temps = [e["main"]["temp"] for e in items]
        temp_mins = [e["main"]["temp_min"] for e in items]
        temp_maxs = [e["main"]["temp_max"] for e in items]
        humidities = [e["main"]["humidity"] for e in items]
        winds = [e["wind"]["speed"] for e in items]
        clouds = [e["clouds"]["all"] for e in items]
        pressures = [e["main"]["pressure"] for e in items]

        result.append({
            "date": day_str,
            "temp_min": round(min(temp_mins), 1),
            "temp_max": round(max(temp_maxs), 1),
            "temp_moy": round(sum(temps) / len(temps), 1),
            "humidity": round(sum(humidities) / len(humidities), 1),
            "wind_speed": round(max(winds), 1),
            "cloud_cover": round(sum(clouds) / len(clouds), 1),
            "pressure": round(sum(pressures) / len(pressures), 1),
            "description": items[len(items) // 2]["weather"][0].get("description", ""),
        })
    return result


def _parse_daily_forecast(entries: list[dict]) -> list[dict]:
    """Parse le format 16-day daily forecast."""
    result = []
    for entry in entries:
        dt = datetime.fromtimestamp(entry["dt"])
        temp = entry.get("temp", {})
        result.append({
            "date": dt.date().isoformat(),
            "temp_min": round(temp.get("min", 0), 1),
            "temp_max": round(temp.get("max", 0), 1),
            "temp_moy": round(temp.get("day", 0), 1),
            "humidity": entry.get("humidity", 0),
            "wind_speed": round(entry.get("speed", 0), 1),
            "cloud_cover": entry.get("clouds", 0),
            "pressure": entry.get("pressure", 1013),
            "description": entry.get("weather", [{}])[0].get("description", ""),
        })
    return result


def _generate_fallback_forecast() -> list[dict]:
    """Génère des données météo simulées si pas de clé API.
    Utilise des moyennes saisonnières pour Paris."""
    from datetime import timedelta
    import random

    today = date.today()
    month = today.month
    # Moyennes mensuelles Paris (temp min / max approx)
    seasonal = {
        1: (-1, 6), 2: (-1, 7), 3: (2, 11), 4: (5, 15), 5: (9, 19),
        9: (11, 21), 10: (7, 16), 11: (3, 10), 12: (0, 7),
        6: (12, 23), 7: (14, 25), 8: (14, 25),
    }
    base_min, base_max = seasonal.get(month, (3, 10))

    result = []
    for i in range(15):
        d = today + timedelta(days=i)
        variation = random.uniform(-3, 3)
        t_min = round(base_min + variation, 1)
        t_max = round(base_max + variation, 1)
        result.append({
            "date": d.isoformat(),
            "temp_min": t_min,
            "temp_max": t_max,
            "temp_moy": round((t_min + t_max) / 2, 1),
            "humidity": round(random.uniform(50, 85), 1),
            "wind_speed": round(random.uniform(5, 25), 1),
            "cloud_cover": round(random.uniform(20, 80), 1),
            "pressure": round(random.uniform(1005, 1035), 1),
            "description": "données simulées",
        })
    return result


def cache_weather(forecasts: list[dict]):
    """Stocke les prévisions dans weather_cache (si la table existe)."""
    if not forecasts:
        return
    conn = get_db()
    now = datetime.now().isoformat()
    try:
        # On crée une table de cache à la volée si utile
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weather_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                temp_min REAL, temp_max REAL, temp_moy REAL,
                pressure REAL, humidity REAL, wind_speed REAL,
                description TEXT, fetched_at TEXT NOT NULL
            )
        """)
        for f in forecasts:
            conn.execute(
                """INSERT INTO weather_cache
                   (date, temp_min, temp_max, temp_moy, pressure, humidity,
                    wind_speed, description, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f["date"], f["temp_min"], f["temp_max"], f["temp_moy"],
                 f.get("pressure", 1013), f.get("humidity", 50),
                 f.get("wind_speed", 10), f.get("description", ""), now),
            )
        conn.commit()
        logger.info(f"[Météo] {len(forecasts)} jours mis en cache")
    finally:
        conn.close()
