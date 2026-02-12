"""Client Meteo France — meteo nationale ponderee sur 9 villes.

Utilise les API publiques Meteo France via la librairie meteole :
  - AROME : haute resolution (1.3 km), previsions jusqu'a 51h (J a J+2)
  - ARPEGE : modele global (10 km Europe), previsions jusqu'a 114h (J+2 a J+5)
  - Vigilance : alertes departementales (grand froid, neige-verglas, etc.)

Calcule une moyenne ponderee par population/parc chauffage electrique
a partir des previsions de 9 villes representatives de la France.

Ameliorations par rapport a Open-Meteo :
  - Humidite relative REELLE (plus d'estimation depuis codes WMO)
  - Pression atmospherique de surface disponible
  - Resolution 20x superieure pour J-1/J-2 (AROME 1.3 km vs ~25 km)
  - Donnees directes du producteur (pas de proxy intermediaire)
  - Signal vigilance grand froid pour bonus scoring
"""

import httpx
import logging
import asyncio
import math
from datetime import datetime, date, timedelta
from config import Config

logger = logging.getLogger(__name__)

# Indicateurs Meteo France (noms WCS officiels)
_INDICATORS = {
    "temperature": "TEMPERATURE__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
    "humidity": "RELATIVE_HUMIDITY__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
    "wind_gust": "WIND_SPEED_GUST__SPECIFIC_HEIGHT_LEVEL_ABOVE_GROUND",
    "pressure": "PRESSURE__GROUND_OR_WATER_SURFACE",
}

# Endpoint Vigilance (API REST simple, JSON)
_MF_API_BASE = "https://public-api.meteofrance.fr/public"
_VIGILANCE_URL = f"{_MF_API_BASE}/DPVigilance/v1/cartevigilance/encours"


# ================================================================
# FETCH MULTI-VILLES (Meteo France AROME + ARPEGE via meteole)
# ================================================================

async def fetch_forecast() -> list[dict]:
    """Previsions jusqu'a 5 jours, moyenne ponderee sur 9 villes via Meteo France.

    Strategie en cascade :
      - AROME (1.3 km) pour J a J+2 (~51h) — meilleure precision
      - ARPEGE (10 km) pour J+2 a J+5 (~114h) — portee etendue

    Retourne une liste vide si Meteo France est injoignable.
    On ne genere PAS de donnees simulees : mieux vaut ne pas predire
    que de predire sur du bruit.
    """
    api_key = Config.METEOFRANCE_API_KEY
    if not api_key:
        logger.error("[Meteo] METEOFRANCE_API_KEY non configuree — pas de previsions")
        return []

    try:
        # meteole est synchrone (requests) — on le lance dans un thread pool
        city_forecasts = await asyncio.to_thread(_fetch_all_cities_sync, api_key)
    except Exception as e:
        logger.error(f"[Meteo] Erreur globale fetch Meteo France: {e}")
        return []

    if not city_forecasts:
        logger.warning("[Meteo] Meteo France indisponible — pas de predictions ce cycle")
        return []

    result = _merge_city_forecasts(city_forecasts)

    for day in result:
        day["forecast_quality"] = "api"

    return result


async def fetch_forecast_extended() -> list[dict]:
    """Alias de fetch_forecast — AROME+ARPEGE fournissent ~5 jours nativement."""
    return await fetch_forecast()


# ================================================================
# FETCH SYNCHRONE (thread pool) — meteole
# ================================================================

def _fetch_all_cities_sync(api_key: str) -> dict[str, list[dict]]:
    """Fetch previsions pour chaque ville via meteole (synchrone).

    Cree les clients AROME et ARPEGE une seule fois, puis itere sur les villes.
    """
    try:
        from meteole import AromeForecast, ArpegeForecast
    except ImportError:
        logger.error(
            "[Meteo] meteole non installe. "
            "Installer avec : pip install meteole"
        )
        return {}

    try:
        arome = AromeForecast(application_id=api_key)
        arpege = ArpegeForecast(application_id=api_key)
    except Exception as e:
        logger.error(f"[Meteo] Erreur initialisation clients meteole: {e}")
        return {}

    city_forecasts = {}
    for city in Config.WEATHER_CITIES:
        try:
            data = _fetch_city_sync(city, arome, arpege)
            if data:
                city_forecasts[city["name"]] = data
            else:
                logger.warning(f"[Meteo] Pas de donnees pour {city['name']}")
        except Exception as e:
            logger.warning(f"[Meteo] Erreur {city['name']}: {e}")

    if not city_forecasts:
        logger.error("[Meteo] Aucune ville n'a repondu")
        return {}

    if len(city_forecasts) < 3:
        logger.warning(
            f"[Meteo] Seulement {len(city_forecasts)} ville(s) sur "
            f"{len(Config.WEATHER_CITIES)} — moyenne peu representative"
        )

    logger.info(
        f"[Meteo] {len(city_forecasts)}/{len(Config.WEATHER_CITIES)} villes OK "
        f"({len(next(iter(city_forecasts.values())))} jours)"
    )
    return city_forecasts


def _fetch_city_sync(city: dict, arome, arpege) -> list[dict] | None:
    """Fetch previsions horaires pour une ville via AROME puis ARPEGE.

    AROME couvre J a J+2 (51h) en haute resolution.
    ARPEGE couvre J a J+5 (114h) en resolution standard.
    On utilise AROME en priorite, ARPEGE en complement.
    """
    lat, lon = city["lat"], city["lon"]

    # --- AROME (haute resolution, ~51h) ---
    arome_hourly = _fetch_model_params(arome, "arome", lat, lon)

    # --- ARPEGE (global, ~114h) ---
    arpege_hourly = _fetch_model_params(arpege, "arpege", lat, lon)

    if not arome_hourly and not arpege_hourly:
        return None

    # Fusionner en previsions journalieres
    return _merge_models_to_daily(arome_hourly, arpege_hourly)


def _fetch_model_params(client, model_name: str,
                         lat: float, lon: float) -> dict[str, dict]:
    """Fetch tous les parametres pour un modele et un point.

    Retourne {step_hours: {temperature: val, humidity: val, ...}}
    """
    results = {}

    # Temperature a 2m
    _fetch_indicator(client, model_name, "temperature",
                     _INDICATORS["temperature"], lat, lon,
                     heights=[2], results=results)

    # Humidite relative a 2m
    _fetch_indicator(client, model_name, "humidity",
                     _INDICATORS["humidity"], lat, lon,
                     heights=[2], results=results)

    # Rafales de vent a 10m
    _fetch_indicator(client, model_name, "wind_gust",
                     _INDICATORS["wind_gust"], lat, lon,
                     heights=[10], results=results)

    # Pression de surface (pas de parametre height)
    _fetch_indicator(client, model_name, "pressure",
                     _INDICATORS["pressure"], lat, lon,
                     heights=None, results=results)

    return results


def _fetch_indicator(client, model_name: str, param_key: str,
                      indicator: str, lat: float, lon: float,
                      heights: list[int] | None,
                      results: dict) -> None:
    """Fetch un indicateur via meteole et injecte les valeurs dans results.

    results[step_index][param_key] = value
    """
    try:
        kwargs = {
            "indicator": indicator,
            "lat": lat,
            "long": lon,
        }
        if heights is not None:
            kwargs["heights"] = heights

        df = client.get_coverage(**kwargs)

        if df is None:
            return

        # Extraire les valeurs du DataFrame
        # meteole retourne un pandas DataFrame — le format exact varie
        # selon l'indicateur et le modele. On essaie plusieurs strategies.
        series = _extract_values_from_df(df, param_key, model_name)

        for step_idx, value in series.items():
            if step_idx not in results:
                results[step_idx] = {}
            results[step_idx][param_key] = value

    except Exception as e:
        logger.debug(f"[Meteo] {model_name}/{param_key} ({lat},{lon}): {e}")


def _extract_values_from_df(df, param_key: str, model_name: str) -> dict[int, float]:
    """Extrait une serie temporelle d'un DataFrame meteole.

    Retourne {step_index: value} ou step_index est l'heure de prevision.

    Le format du DataFrame peut varier — on essaie plusieurs strategies
    et on loggue la structure pour diagnostic.
    """
    import pandas as pd

    if df is None or (hasattr(df, 'empty') and df.empty):
        return {}

    values = {}

    try:
        # Strategie 1 : DataFrame avec index temporel et colonnes spatiales
        # Typique de meteole : lignes = timesteps, colonnes = grid points
        if hasattr(df, 'shape') and len(df.shape) == 2:
            nrows, ncols = df.shape

            if ncols == 1:
                # Une seule colonne (point unique) — ideal pour notre cas
                for i in range(nrows):
                    val = df.iloc[i, 0]
                    if pd.notna(val):
                        values[i] = float(val)
            elif ncols > 1:
                # Plusieurs colonnes — prendre la premiere (point le plus proche)
                # ou la moyenne si c'est un petit voisinage
                for i in range(nrows):
                    row_values = [float(v) for v in df.iloc[i] if pd.notna(v)]
                    if row_values:
                        values[i] = sum(row_values) / len(row_values)

        # Strategie 2 : Series pandas
        elif hasattr(df, 'values') and not hasattr(df, 'shape'):
            for i, val in enumerate(df.values):
                if pd.notna(val):
                    values[i] = float(val)

    except Exception as e:
        logger.warning(
            f"[Meteo] Erreur extraction {model_name}/{param_key}: {e}. "
            f"DataFrame type={type(df)}, shape={getattr(df, 'shape', '?')}, "
            f"columns={list(getattr(df, 'columns', []))[:5]}"
        )

    if not values:
        logger.debug(
            f"[Meteo] DataFrame vide pour {model_name}/{param_key}. "
            f"Type={type(df)}, shape={getattr(df, 'shape', '?')}, "
            f"head={str(df)[:200] if df is not None else 'None'}"
        )

    return values


# ================================================================
# AGGREGATION HORAIRE -> JOURNALIERE
# ================================================================

def _merge_models_to_daily(arome_results: dict[int, dict],
                            arpege_results: dict[int, dict]) -> list[dict]:
    """Fusionne les donnees horaires AROME et ARPEGE en previsions journalieres.

    Strategie : AROME prioritaire (meilleure resolution), ARPEGE en complement
    pour les heures non couvertes par AROME.

    Les step_index sont des indices horaires depuis le run du modele.
    On les convertit en dates avec l'heure UTC courante comme reference.
    """
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)

    # Convertir les step_index en timestamps et grouper par date
    # AROME : pas horaire, indices 0 a ~51
    # ARPEGE : pas horaire/3h, indices 0 a ~114
    daily_data = {}

    def _add_entries(results: dict, source: str):
        for step_idx, params in results.items():
            valid_time = now + timedelta(hours=step_idx)
            day_str = valid_time.strftime("%Y-%m-%d")

            if day_str not in daily_data:
                daily_data[day_str] = {"entries": [], "source": source}

            # AROME prioritaire : ne pas ecraser si on a deja de l'AROME
            if daily_data[day_str]["source"] == "arome" and source == "arpege":
                continue

            daily_data[day_str]["entries"].append({
                "hour": valid_time.hour,
                **params,
            })
            # Mettre a jour la source si on injecte AROME dans un jour ARPEGE
            if source == "arome":
                daily_data[day_str]["source"] = "arome"

    _add_entries(arome_results, "arome")
    _add_entries(arpege_results, "arpege")

    # Agreger en statistiques journalieres
    result = []
    for day_str in sorted(daily_data.keys()):
        entries = daily_data[day_str]["entries"]
        source = daily_data[day_str]["source"]

        if not entries:
            continue

        temps = [e["temperature"] for e in entries if "temperature" in e]
        humidities = [e["humidity"] for e in entries if "humidity" in e]
        winds = [e["wind_gust"] for e in entries if "wind_gust" in e]
        pressures = [e["pressure"] for e in entries if "pressure" in e]

        if not temps:
            continue

        # Conversion des unites Meteo France :
        # - Temperature : Kelvin -> Celsius (si > 100, c'est du Kelvin)
        # - Pression : Pa -> hPa (si > 10000, c'est du Pa)
        # - Vent rafales : m/s -> km/h
        is_kelvin = any(t > 100 for t in temps)
        k_offset = 273.15 if is_kelvin else 0

        temp_min = round(min(temps) - k_offset, 1)
        temp_max = round(max(temps) - k_offset, 1)
        temp_moy = round(sum(temps) / len(temps) - k_offset, 1)

        humidity = round(sum(humidities) / len(humidities), 1) if humidities else 50.0

        # Vent : max des rafales, conversion m/s -> km/h
        if winds:
            max_wind_ms = max(winds)
            wind_speed = round(max_wind_ms * 3.6, 1) if max_wind_ms < 200 else round(max_wind_ms, 1)
        else:
            wind_speed = 10.0

        # Pression : moyenne, conversion Pa -> hPa si necessaire
        if pressures:
            avg_p = sum(pressures) / len(pressures)
            pressure = round(avg_p / 100, 1) if avg_p > 10000 else round(avg_p, 1)
        else:
            pressure = None

        description = _infer_description(humidity, wind_speed, temp_moy)

        result.append({
            "date": day_str,
            "temp_min": temp_min,
            "temp_max": temp_max,
            "temp_moy": temp_moy,
            "humidity": humidity,
            "wind_speed": wind_speed,
            "pressure": pressure,
            "description": description,
            "source": source,
        })

    return result


def _infer_description(humidity: float, wind_speed: float, temp_moy: float) -> str:
    """Infere une description meteo depuis les parametres numeriques."""
    if humidity >= 90 and temp_moy <= 3:
        return "Brouillard givrant possible"
    if humidity >= 85:
        if temp_moy <= 0:
            return "Neige probable"
        return "Pluie probable"
    if humidity >= 75:
        return "Couvert, risque de pluie"
    if wind_speed >= 50:
        return "Vent fort"
    if humidity <= 50:
        return "Ciel degagé"
    return "Partiellement nuageux"


# ================================================================
# VIGILANCE METEO FRANCE (API REST directe, pas besoin de meteole)
# ================================================================

async def fetch_vigilance(api_key: str | None = None) -> dict:
    """Recupere les alertes de vigilance Meteo France.

    Retourne un dict avec :
      - grand_froid: bool (vigilance grand froid orange ou rouge active)
      - neige_verglas: bool (vigilance neige-verglas orange ou rouge active)
      - max_level: int (0=vert, 1=jaune, 2=orange, 3=rouge)
      - details: list[dict] (detail par departement)
    """
    key = api_key or Config.METEOFRANCE_API_KEY
    if not key:
        return _empty_vigilance()

    headers = {"apikey": key}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(_VIGILANCE_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return _parse_vigilance(data)
    except Exception as e:
        logger.warning(f"[Meteo] Vigilance indisponible: {e}")
        return _empty_vigilance()


def _empty_vigilance() -> dict:
    """Retour par defaut quand la vigilance n'est pas disponible."""
    return {"grand_froid": False, "neige_verglas": False, "max_level": 0, "details": []}


def _parse_vigilance(data: dict) -> dict:
    """Parse la reponse de l'API Vigilance Meteo France.

    Detecte les alertes grand froid et neige-verglas au niveau national.
    IDs des phenomenes Meteo France :
      1 = Vent violent, 2 = Pluie-inondation, 3 = Orages,
      4 = Crues, 5 = Neige-verglas, 6 = Canicule,
      7 = Grand froid, 8 = Avalanches, 9 = Vagues-submersion
    """
    result = _empty_vigilance()

    try:
        product = data.get("product", data)
        periods = product.get("periods", [])
        if not periods and "text_bloc_item" in product:
            periods = [product]

        for period in periods:
            timelaps = period.get("timelaps", [])
            for entry in timelaps:
                phenomenon_id = entry.get("phenomenon_id", 0)
                timelaps_items = entry.get("timelaps_items", [])

                for item in timelaps_items:
                    level = item.get("color_id", 0)
                    result["max_level"] = max(result["max_level"], level)

                    # Grand froid (phenomene 7) — orange (3) ou rouge (4)
                    if phenomenon_id == 7 and level >= 3:
                        result["grand_froid"] = True

                    # Neige-verglas (phenomene 5) — orange (3) ou rouge (4)
                    if phenomenon_id == 5 and level >= 3:
                        result["neige_verglas"] = True

                    result["details"].append({
                        "phenomenon_id": phenomenon_id,
                        "level": level,
                        "begin": item.get("begin_time", ""),
                        "end": item.get("end_time", ""),
                    })
    except (KeyError, TypeError) as e:
        logger.debug(f"[Meteo] Erreur parsing vigilance: {e}")

    return result


# ================================================================
# MERGE : moyenne ponderee des villes
# ================================================================

def _merge_city_forecasts(city_forecasts: dict[str, list[dict]]) -> list[dict]:
    """Fusionne les previsions de toutes les villes en moyennes ponderees nationales."""
    weight_map = {c["name"]: c["weight"] for c in Config.WEATHER_CITIES}

    all_dates = set()
    for forecasts in city_forecasts.values():
        for f in forecasts:
            all_dates.add(f["date"])

    result = []
    for day_str in sorted(all_dates):
        temp_min_w = 0.0
        temp_max_w = 0.0
        temp_moy_w = 0.0
        humidity_w = 0.0
        wind_w = 0.0
        pressure_w = 0.0
        pressure_wt = 0.0
        total_weight = 0.0

        city_details = {}
        for city_name, forecasts in city_forecasts.items():
            day_data = next((f for f in forecasts if f["date"] == day_str), None)
            if not day_data:
                continue
            w = weight_map.get(city_name, 0.1)
            total_weight += w
            temp_min_w += day_data["temp_min"] * w
            temp_max_w += day_data["temp_max"] * w
            temp_moy_w += day_data["temp_moy"] * w
            humidity_w += day_data.get("humidity", 50) * w
            wind_w += day_data.get("wind_speed", 10) * w
            if day_data.get("pressure") is not None:
                pressure_w += day_data["pressure"] * w
                pressure_wt += w
            city_details[city_name] = {
                "temp_min": day_data["temp_min"],
                "temp_max": day_data["temp_max"],
            }

        if total_weight == 0:
            continue

        avg_pressure = round(pressure_w / pressure_wt, 1) if pressure_wt > 0 else None

        result.append({
            "date": day_str,
            "temp_min": round(temp_min_w / total_weight, 1),
            "temp_max": round(temp_max_w / total_weight, 1),
            "temp_moy": round(temp_moy_w / total_weight, 1),
            "humidity": round(humidity_w / total_weight, 1),
            "wind_speed": round(wind_w / total_weight, 1),
            "pressure": avg_pressure,
            "description": "moyenne nationale ponderee",
            "city_details": city_details,
        })

    return result


# Note: weather_cache table existe en DB mais n'est plus alimentee.
# Les temperatures sont stockees directement dans la table predictions
# (temp_min_prevue, temp_max_prevue) par store_prediction().
# La table weather_cache sera purgee naturellement par purge_old_data().
