"""Client Meteo France + fallback Open-Meteo — meteo nationale ponderee sur 9 villes.

Source principale : Meteo France via la librairie meteole :
  - AROME : haute resolution (1.3 km), previsions jusqu'a 51h (J a J+2)
  - ARPEGE : modele global (10 km Europe), previsions jusqu'a 114h (J+2 a J+5)
  - Vigilance : alertes departementales (grand froid, neige-verglas, etc.)

Fallback automatique : Open-Meteo Forecast API (gratuit, sans cle) :
  - Active quand Meteo France est indisponible (circuit breaker, cle absente, erreur)
  - Memes variables horaires (temperature, humidite, pression, vent)
  - Memes 9 villes ponderees, meme format de sortie
  - Source marquee "open-meteo" pour attenuation de confiance dans le scoring

Calcule une moyenne ponderee par population/parc chauffage electrique
a partir des previsions de 9 villes representatives de la France.
"""

import httpx
import logging
import asyncio
import math
import re
import threading
import time
from datetime import datetime, date, timedelta
from config import Config

logger = logging.getLogger(__name__)


# ================================================================
# CIRCUIT BREAKER — protection contre les cascades d'echecs API
# ================================================================

class _CircuitBreaker:
    """Circuit breaker simple pour les appels API externes.

    Etats : CLOSED (normal) → OPEN (echecs repetes) → HALF_OPEN (test).
    Apres `threshold` echecs consecutifs, le circuit s'ouvre pendant
    `reset_timeout` secondes. Un seul appel passe en HALF_OPEN pour
    tester si le service est de retour.
    """

    def __init__(self, threshold: int = 5, reset_timeout: int = 120):
        self._threshold = threshold
        self._reset_timeout = reset_timeout
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        import time
        if time.monotonic() - self._opened_at >= self._reset_timeout:
            # Passage en HALF_OPEN : on laisse passer un appel test
            return False
        return True

    def record_success(self):
        self._failures = 0
        self._opened_at = None

    def record_failure(self):
        self._failures += 1
        if self._failures >= self._threshold:
            import time
            self._opened_at = time.monotonic()
            logger.warning(
                f"[CircuitBreaker] OPEN apres {self._failures} echecs "
                f"(cooldown {self._reset_timeout}s)"
            )


_meteo_breaker = _CircuitBreaker(threshold=5, reset_timeout=120)
_vigilance_breaker = _CircuitBreaker(threshold=3, reset_timeout=60)


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

# Open-Meteo Forecast API — fallback gratuit sans cle
_OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_OPENMETEO_HOURLY = "temperature_2m,relative_humidity_2m,pressure_msl,wind_speed_10m"
_openmeteo_breaker = _CircuitBreaker(threshold=5, reset_timeout=300)


# ================================================================
# FETCH MULTI-VILLES (Meteo France AROME + ARPEGE via meteole)
# ================================================================

async def fetch_forecast() -> list[dict]:
    """Previsions jusqu'a 15 jours, moyenne ponderee sur 9 villes.

    Strategie :
      1. Meteo France (AROME + ARPEGE) pour J+0 a J+5 — source principale
      2. Open-Meteo en extension pour J+6 a J+15 (au-dela de la portee MF)
      3. Open-Meteo en fallback complet si Meteo France echoue

    Ne genere PAS de donnees simulees : mieux vaut ne pas predire
    que de predire sur du bruit.
    """
    # --- Tentative 1 : Meteo France (J+0 a J+5) ---
    mf_result = await _fetch_meteofrance()

    if mf_result:
        # Etendre avec Open-Meteo pour les jours au-dela de MF (J+6 a J+15)
        extended = await _extend_with_openmeteo(mf_result)
        return extended

    # --- Fallback : Open-Meteo complet (J+0 a J+15) ---
    logger.warning("[Meteo] Meteo France indisponible — fallback complet Open-Meteo")
    result = await _fetch_openmeteo_fallback()

    if result:
        logger.info(f"[Meteo] Fallback Open-Meteo OK — {len(result)} jours recuperes")
        return result

    logger.error(
        "[Meteo] ALERTE: Aucune source meteo disponible "
        "(Meteo France ET Open-Meteo en echec) — pas de predictions ce cycle. "
        "Verifier la connectivite reseau et les cles API."
    )
    return []


async def _extend_with_openmeteo(mf_days: list[dict]) -> list[dict]:
    """Etend les previsions Meteo France avec Open-Meteo pour J+6 a J+15.

    Meteo France couvre J+0 a J+5. Open-Meteo couvre jusqu'a J+16.
    On garde MF en priorite et on ajoute les jours Open-Meteo manquants.
    Les jours Open-Meteo sont marques source='open-meteo' pour attenuation.
    """
    mf_dates = {d["date"] for d in mf_days}

    try:
        om_days = await _fetch_openmeteo_fallback()
    except Exception as e:
        logger.debug(f"[Meteo] Extension Open-Meteo echouee: {e}")
        return mf_days

    if not om_days:
        return mf_days

    # Ajouter uniquement les jours Open-Meteo absents de MF
    extended = list(mf_days)
    for day in om_days:
        if day["date"] not in mf_dates:
            extended.append(day)

    extended.sort(key=lambda d: d["date"])

    if len(extended) > len(mf_days):
        logger.info(
            f"[Meteo] Extension Open-Meteo : {len(mf_days)} jours MF "
            f"+ {len(extended) - len(mf_days)} jours Open-Meteo "
            f"= {len(extended)} jours total"
        )

    return extended


def _get_meteofrance_auth(model: str = "default") -> dict[str, str]:
    """Determine le mode d'authentification Meteo France pour un modele donne.

    Sur le portail Meteo France, chaque API (AROME, ARPEGE, Vigilance) necessite
    sa propre application et sa propre cle API (duree 0, pas d'OAuth).
    On supporte :
      - Cle par modele : METEOFRANCE_AROME_KEY, METEOFRANCE_ARPEGE_KEY, METEOFRANCE_VIGILANCE_KEY
      - Cle globale : METEOFRANCE_API_KEY (fallback si cle specifique absente)
      - Application ID OAuth2 : METEOFRANCE_APPLICATION_ID (dernier recours, legacy)

    Retourne un dict de kwargs a passer a AromeForecast/ArpegeForecast/Vigilance.
    """
    # 1. Cle specifique au modele (API key simple, pas OAuth)
    model_keys = {
        "arome": Config.METEOFRANCE_AROME_KEY,
        "arpege": Config.METEOFRANCE_ARPEGE_KEY,
        "vigilance": Config.METEOFRANCE_VIGILANCE_KEY,
    }
    specific_key = model_keys.get(model, "")
    if specific_key:
        logger.debug(f"[Meteo] Auth {model} : cle API specifique")
        return {"api_key": specific_key}

    # 2. Cle globale (fallback, aussi API key)
    if Config.METEOFRANCE_API_KEY:
        logger.debug(f"[Meteo] Auth {model} : cle globale METEOFRANCE_API_KEY")
        return {"api_key": Config.METEOFRANCE_API_KEY}

    # 3. Application ID OAuth2 (dernier recours, legacy)
    if Config.METEOFRANCE_APPLICATION_ID:
        logger.debug(f"[Meteo] Auth {model} : application_id OAuth2 (legacy)")
        return {"application_id": Config.METEOFRANCE_APPLICATION_ID}

    return {}


async def _fetch_meteofrance() -> list[dict]:
    """Fetch via Meteo France AROME + ARPEGE. Retourne [] si echec."""
    arome_auth = _get_meteofrance_auth("arome")
    arpege_auth = _get_meteofrance_auth("arpege")
    if not arome_auth and not arpege_auth:
        logger.warning("[Meteo] Meteo France non configure "
                       "(aucune cle AROME/ARPEGE/globale)")
        return []

    if _meteo_breaker.is_open:
        logger.warning("[Meteo] Circuit breaker Meteo France OPEN — skip")
        return []

    try:
        city_forecasts = await asyncio.wait_for(
            asyncio.to_thread(_fetch_all_cities_sync, arome_auth, arpege_auth),
            timeout=120,
        )
    except Exception as e:
        _meteo_breaker.record_failure()
        logger.warning(f"[Meteo] Erreur Meteo France (fallback Open-Meteo): {e}")
        return []

    if not city_forecasts:
        _meteo_breaker.record_failure()
        return []

    _meteo_breaker.record_success()
    result = _merge_city_forecasts(city_forecasts)

    for day in result:
        day["forecast_quality"] = "api"
        if "source" not in day:
            day["source"] = "arome"

    return result


async def fetch_forecast_extended() -> list[dict]:
    """Alias de fetch_forecast — MF (J+0-5) + Open-Meteo (J+6-15) = ~15 jours."""
    return await fetch_forecast()


# ================================================================
# FETCH SYNCHRONE (thread pool) — meteole
# ================================================================

def _fetch_all_cities_sync(arome_auth: dict[str, str],
                           arpege_auth: dict[str, str]) -> dict[str, list[dict]]:
    """Fetch previsions pour chaque ville via meteole (synchrone).

    Cree les clients AROME et ARPEGE avec leurs cles respectives.
    Sur le portail Meteo France, chaque API a sa propre application/cle.
    """
    try:
        from meteole import AromeForecast, ArpegeForecast
    except ImportError:
        logger.error(
            "[Meteo] meteole non installe. "
            "Installer avec : pip install meteole"
        )
        return {}

    arome = None
    arpege = None
    if arome_auth:
        try:
            arome = AromeForecast(**arome_auth)
        except Exception as e:
            logger.error(f"[Meteo] Erreur init AROME: {e}")
    if arpege_auth:
        try:
            arpege = ArpegeForecast(**arpege_auth)
        except Exception as e:
            logger.error(f"[Meteo] Erreur init ARPEGE: {e}")

    if not arome and not arpege:
        logger.error("[Meteo] Ni AROME ni ARPEGE n'ont pu etre initialises")
        return {}

    city_forecasts = {}
    city_errors = []
    for city in Config.WEATHER_CITIES:
        try:
            data = _fetch_city_sync(city, arome, arpege)
            if data:
                city_forecasts[city["name"]] = data
            else:
                logger.warning(f"[Meteo] Pas de donnees pour {city['name']}")
                city_errors.append(city["name"])
        except Exception as e:
            logger.warning(f"[Meteo] Erreur {city['name']}: {e}")
            city_errors.append(city["name"])

    if not city_forecasts:
        # Toutes les villes ont échoué — erreur structurelle probable
        # (ex: mise à jour meteole cassée, API Météo France en panne).
        # Forcer l'ouverture du circuit breaker pour basculer immédiatement
        # sur Open-Meteo au lieu d'attendre 5 échecs successifs.
        logger.error(
            f"[Meteo] AUCUNE ville n'a repondu via meteole "
            f"({len(city_errors)}/{len(Config.WEATHER_CITIES)} echecs) — "
            f"circuit breaker force OPEN, fallback Open-Meteo"
        )
        _meteo_breaker._failures = _meteo_breaker._threshold
        _meteo_breaker.record_failure()
        return {}

    # Fix P2-10 audit : seuil minimum de 5 villes pour une moyenne fiable
    if len(city_forecasts) < 5:
        logger.warning(
            f"[Meteo] Seulement {len(city_forecasts)} ville(s) sur "
            f"{len(Config.WEATHER_CITIES)} — moyenne peu representative "
            f"(minimum recommande: 5)"
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
        logger.warning(f"[Meteo] {model_name}/{param_key} ({lat},{lon}): {e}")


def _extract_values_from_df(df, param_key: str, model_name: str) -> dict[int, float]:
    """Extrait une serie temporelle d'un DataFrame meteole.

    Retourne {step_index: value} ou step_index est l'heure de prevision.

    Le format du DataFrame peut varier — on essaie plusieurs strategies
    et on loggue la structure pour diagnostic.

    Fix meteole v0.2.5 : le DataFrame peut contenir des colonnes Timestamp
    (datetime) en plus des colonnes numeriques. On filtre avec
    select_dtypes(include='number') pour eviter float(Timestamp) qui echoue.
    """
    import pandas as pd

    if df is None or (hasattr(df, 'empty') and df.empty):
        return {}

    values = {}

    def _safe_float(val) -> float | None:
        """Convertit une valeur en float de manière sûre.

        Résiste aux Timedelta, Timestamp, NaT et autres types non-numériques
        que meteole peut injecter selon sa version.
        """
        if val is None:
            return None
        if not pd.notna(val):
            return None
        # Rejeter explicitement les types temporels (Timedelta, Timestamp, NaT)
        if isinstance(val, (pd.Timedelta, pd.Timestamp)):
            return None
        try:
            f = float(val)
            if math.isnan(f) or math.isinf(f):
                return None
            return f
        except (ValueError, TypeError):
            return None

    try:
        # Strategie 1 : DataFrame avec index temporel et colonnes spatiales
        # Typique de meteole : lignes = timesteps, colonnes = grid points
        if hasattr(df, 'shape') and len(df.shape) == 2:
            # Fix meteole v0.2.5+ : filtrer les colonnes non-numeriques
            # (Timestamp, datetime, str, timedelta) pour eviter float() qui echoue.
            # Exclure explicitement timedelta64 — pandas le considere "numeric"
            # mais float(Timedelta) leve TypeError.
            numeric_df = df.select_dtypes(include="number", exclude=["timedelta", "timedelta64"])
            # Exclure les colonnes de coordonnées (latitude, longitude) que
            # meteole inclut parfois dans le DataFrame — sinon elles sont
            # moyennées avec les valeurs météo et corrompent les résultats.
            coord_cols = [c for c in numeric_df.columns
                          if any(k in str(c).lower() for k in ("lat", "lon", "altitude"))]
            if coord_cols:
                numeric_df = numeric_df.drop(columns=coord_cols, errors="ignore")
            if numeric_df.empty:
                logger.debug(
                    f"[Meteo] Aucune colonne numerique pour {model_name}/{param_key}. "
                    f"Colonnes: {list(df.columns)[:5]}, dtypes: {list(df.dtypes)[:5]}"
                )
                return {}

            nrows, ncols = numeric_df.shape

            if ncols == 1:
                # Une seule colonne (point unique) — ideal pour notre cas
                for i in range(nrows):
                    f = _safe_float(numeric_df.iloc[i, 0])
                    if f is not None:
                        values[i] = f
            elif ncols > 1:
                # Plusieurs colonnes — prendre la moyenne (petit voisinage)
                for i in range(nrows):
                    row_values = [f for v in numeric_df.iloc[i]
                                  if (f := _safe_float(v)) is not None]
                    if row_values:
                        values[i] = sum(row_values) / len(row_values)

        # Strategie 2 : Series pandas
        elif hasattr(df, 'values') and not hasattr(df, 'shape'):
            for i, val in enumerate(df.values):
                f = _safe_float(val)
                if f is not None:
                    values[i] = f

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
                            arpege_results: dict[int, dict],
                            ref_time: datetime | None = None,
                            day_tz=None) -> list[dict]:
    """Fusionne les donnees horaires AROME et ARPEGE en previsions journalieres.

    Strategie : AROME prioritaire (meilleure resolution), ARPEGE en complement
    pour les heures non couvertes par AROME.

    Les step_index sont des indices horaires depuis le run du modele.
    On les convertit en dates avec l'heure UTC courante comme reference.
    `ref_time` (UTC aware) et `day_tz` (ex. Europe/Paris) sont optionnels :
    utilises par l'archive Meteo France (heure de run reelle, jours de Paris
    comme Open-Meteo) ; par defaut, comportement historique inchange.
    """
    # Fix P0-1 audit : timezone-aware UTC au lieu de utcnow() deprecie
    from zoneinfo import ZoneInfo
    now = ref_time or datetime.now(tz=ZoneInfo("UTC")).replace(minute=0, second=0, microsecond=0)

    # Convertir les step_index en timestamps et grouper par date
    # AROME : pas horaire, indices 0 a ~51
    # ARPEGE : pas horaire/3h, indices 0 a ~114
    daily_data = {}

    def _add_entries(results: dict, source: str):
        for step_idx, params in results.items():
            valid_time = now + timedelta(hours=step_idx)
            if day_tz is not None:
                valid_time = valid_time.astimezone(day_tz)
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

        # Fix P2-9 audit : detection d'unites plus robuste avec seuils explicites
        # Temperature : Kelvin (200-350) vs Celsius (-50 a +60)
        median_t = sorted(temps)[len(temps) // 2]
        is_kelvin = median_t > 100  # Median plus robuste que any()
        k_offset = 273.15 if is_kelvin else 0
        if is_kelvin:
            logger.debug(f"[Meteo] {day_str}: temperature en Kelvin (median={median_t:.0f}K)")

        temp_min = round(min(temps) - k_offset, 1)
        temp_max = round(max(temps) - k_offset, 1)
        temp_moy = round(sum(temps) / len(temps) - k_offset, 1)

        # Validation plausibilite temperature
        if temp_moy < -40 or temp_moy > 50:
            logger.warning(f"[Meteo] {day_str}: temp_moy={temp_moy}°C hors plage [-40, 50], jour ignoré")
            continue

        humidity = round(sum(humidities) / len(humidities), 1) if humidities else 50.0

        # Vent : max des rafales
        # m/s (0-100 typique) vs km/h (0-300+)
        if winds:
            max_wind = max(winds)
            # Si max < 80, probablement m/s → convertir en km/h
            # Si max >= 80, probablement deja en km/h
            if max_wind < 80:
                wind_speed = round(max_wind * 3.6, 1)
            else:
                wind_speed = round(max_wind, 1)
                logger.debug(f"[Meteo] {day_str}: vent deja en km/h ({max_wind:.0f})")
        else:
            wind_speed = 10.0

        # Pression : Pa (>50000) vs hPa (800-1100 typique)
        if pressures:
            avg_p = sum(pressures) / len(pressures)
            if avg_p > 50000:
                pressure = round(avg_p / 100, 1)
            else:
                pressure = round(avg_p, 1)
            # Validation plausibilite
            if pressure < 850 or pressure > 1100:
                logger.warning(f"[Meteo] {day_str}: pression={pressure} hPa hors plage")
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
# FALLBACK OPEN-METEO (gratuit, sans cle API)
# ================================================================

async def _fetch_openmeteo_fallback() -> list[dict]:
    """Fallback : previsions via Open-Meteo Forecast API (gratuit, sans cle).

    Memes 9 villes ponderees, memes variables horaires, meme format de sortie.
    Source marquee "open-meteo" pour attenuation de confiance dans le scoring.
    """
    if _openmeteo_breaker.is_open:
        logger.debug("[Meteo] Circuit breaker Open-Meteo OPEN — skip")
        return []

    city_forecasts = {}

    for city in Config.WEATHER_CITIES:
        try:
            daily = await _fetch_openmeteo_city(city)
            if daily:
                city_forecasts[city["name"]] = daily
        except Exception as e:
            logger.debug(f"[Meteo] Open-Meteo {city['name']}: {e}")

        await asyncio.sleep(0.2)  # Rate limit politesse

    if not city_forecasts:
        _openmeteo_breaker.record_failure()
        logger.warning("[Meteo] Fallback Open-Meteo : aucune ville n'a repondu")
        return []

    _openmeteo_breaker.record_success()
    logger.info(
        f"[Meteo] Open-Meteo fallback: {len(city_forecasts)}/"
        f"{len(Config.WEATHER_CITIES)} villes OK"
    )

    # Fusionner avec le meme merge que Meteo France
    result = _merge_city_forecasts(city_forecasts)

    for day in result:
        day["source"] = "open-meteo"
        day["forecast_quality"] = "api"

    return result


async def _fetch_openmeteo_city(city: dict) -> list[dict] | None:
    """Fetch previsions horaires Open-Meteo pour une ville, agrege en daily."""
    params = {
        "latitude": city["lat"],
        "longitude": city["lon"],
        "hourly": _OPENMETEO_HOURLY,
        "timezone": "Europe/Paris",
        "forecast_days": 16,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(_OPENMETEO_FORECAST_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    humidities = hourly.get("relative_humidity_2m", [])
    pressures = hourly.get("pressure_msl", [])
    winds = hourly.get("wind_speed_10m", [])

    if not times or not temps:
        return None

    # Grouper par jour
    from collections import defaultdict
    days = defaultdict(lambda: {"temps": [], "humids": [], "pressures": [], "winds": []})

    for i, ts in enumerate(times):
        day_str = ts[:10]
        if i < len(temps) and temps[i] is not None:
            days[day_str]["temps"].append(temps[i])
        if i < len(humidities) and humidities[i] is not None:
            days[day_str]["humids"].append(humidities[i])
        if i < len(pressures) and pressures[i] is not None:
            days[day_str]["pressures"].append(pressures[i])
        if i < len(winds) and winds[i] is not None:
            days[day_str]["winds"].append(winds[i])

    result = []
    for day_str in sorted(days.keys()):
        d = days[day_str]
        if not d["temps"]:
            continue

        temp_min = round(min(d["temps"]), 1)
        temp_max = round(max(d["temps"]), 1)
        temp_moy = round(sum(d["temps"]) / len(d["temps"]), 1)
        humidity = round(sum(d["humids"]) / len(d["humids"]), 1) if d["humids"] else 50.0
        wind_speed = round(max(d["winds"]), 1) if d["winds"] else 10.0  # deja en km/h
        pressure = round(sum(d["pressures"]) / len(d["pressures"]), 1) if d["pressures"] else None

        result.append({
            "date": day_str,
            "temp_min": temp_min,
            "temp_max": temp_max,
            "temp_moy": temp_moy,
            "humidity": humidity,
            "wind_speed": wind_speed,
            "pressure": pressure,
            "description": _infer_description(humidity, wind_speed, temp_moy),
            "source": "open-meteo",
        })

    return result


# ================================================================
# VIGILANCE METEO FRANCE (API REST directe, pas besoin de meteole)
# ================================================================

async def fetch_vigilance(api_key: str | None = None) -> dict:
    """Recupere les alertes de vigilance Meteo France.

    Utilise meteole.Vigilance avec la meme authentification que AROME/ARPEGE.
    Supporte api_key (permanent) et application_id (OAuth2).

    Retourne un dict avec :
      - grand_froid: bool (vigilance grand froid orange ou rouge active)
      - neige_verglas: bool (vigilance neige-verglas orange ou rouge active)
      - max_level: int (0=vert, 1=jaune, 2=orange, 3=rouge)
      - details: list[dict] (detail par departement)
    """
    # Utilise le meme mecanisme d'auth que AROME/ARPEGE
    if api_key:
        auth_kwargs = {"api_key": api_key}
    else:
        auth_kwargs = _get_meteofrance_auth("vigilance")
    if not auth_kwargs:
        return _empty_vigilance()

    # Fix audit DB : circuit breaker vigilance
    if _vigilance_breaker.is_open:
        logger.debug("[Meteo] Circuit breaker vigilance OPEN — skip")
        return _empty_vigilance()

    try:
        data = await asyncio.wait_for(
            asyncio.to_thread(_fetch_vigilance_sync, auth_kwargs),
            timeout=30,
        )
        _vigilance_breaker.record_success()
        return _parse_vigilance(data)
    except Exception as e:
        _vigilance_breaker.record_failure()
        logger.warning(f"[Meteo] Vigilance indisponible: {e}")
        return _empty_vigilance()


def _fetch_vigilance_sync(auth_kwargs: dict[str, str]) -> dict:
    """Fetch vigilance via meteole (synchrone, lance dans thread pool)."""
    try:
        from meteole import Vigilance
        vig = Vigilance(**auth_kwargs)
        return vig.get_map()
    except ImportError:
        # Fallback : appel direct si meteole trop ancien (pas de classe Vigilance)
        import requests
        # En mode api_key, on peut utiliser le header directement
        key = auth_kwargs.get("api_key") or auth_kwargs.get("application_id", "")
        headers = {"apikey": key}
        resp = requests.get(_VIGILANCE_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()


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
        # Fix vigilance : meteole peut retourner des formats variables
        # selon la version. On protege chaque niveau de nesting avec
        # isinstance(x, dict) pour eviter AttributeError sur .get()
        if not isinstance(data, dict):
            logger.debug(f"[Meteo] Vigilance: data n'est pas un dict ({type(data)})")
            return result

        product = data.get("product", data)
        if not isinstance(product, dict):
            logger.debug(f"[Meteo] Vigilance: product n'est pas un dict ({type(product)})")
            return result

        periods = product.get("periods", [])
        if not periods and isinstance(product, dict) and "text_bloc_item" in product:
            periods = [product]

        for period in periods:
            if not isinstance(period, dict):
                continue
            timelaps = period.get("timelaps", [])
            for entry in timelaps:
                if not isinstance(entry, dict):
                    continue
                phenomenon_id = entry.get("phenomenon_id", 0)
                timelaps_items = entry.get("timelaps_items", [])

                for item in timelaps_items:
                    if not isinstance(item, dict):
                        continue
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
    except (KeyError, TypeError, AttributeError) as e:
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


# ================================================================
# ARCHIVE METEO FRANCE (comparaison uniquement, PAS utilisee au scoring)
# ================================================================
# Constat 2026-10-01 : `_fetch_indicator` n'envoie pas `forecast_horizons`
# a meteole, qui ne renvoie que l'echeance 0 : Open-Meteo fait tout le
# scoring depuis fevrier. Decision fondateur : archiver AROME + ARPEGE a
# cote d'Open-Meteo (table weather_forecast_mf_log, v26), comparer aux
# temperatures observees (tools/replay/compare_weather_sources.py), et
# seulement ensuite (rejeu obligatoire) decider de les utiliser.
# `fetch_forecast()` et le chemin de scoring ne sont PAS modifies.

_MF_API_ROOT = "https://public-api.meteofrance.fr/public/"
_MF_MAX_REQ_PER_MIN = 45          # limite Meteo France 50/min, marge de 10 %
_MF_REQUEST_TIMEOUT_S = 30        # un GRIB fait quelques centaines de Ko
_MF_ARCHIVE_DEADLINE_S = 15 * 60  # garde-fou global : le thread finit toujours
_MF_ARCHIVE_STEP_H = 3            # 8 echeances par jour (moyenne exacte d'un cycle diurne)
_MF_ARCHIVE_MAX_DAYS = {"arome": 2, "arpege": 4}   # J+0..J+2 / J+0..J+4
_MF_AROME_PRECISION = 0.025       # grille 2,5 km : 6x moins de points que 0,01
_MF_ARCHIVE_INDICATORS = (        # (cle, indicateur WCS, hauteurs)
    ("temperature", _INDICATORS["temperature"], [2]),
    ("humidity", _INDICATORS["humidity"], [2]),
    ("wind_gust", _INDICATORS["wind_gust"], [10]),
    ("pressure", _INDICATORS["pressure"], None),
)
_MF_OPTIONAL_FIELDS = (("humidity", "humidity"), ("wind_gust", "wind_speed"),
                       ("pressure", "pressure"))
_mf_fallback_logged: set[str] = set()


class MFAuthError(Exception):
    """HTTP 401/403 Meteo France : cle refusee ou non abonnee a cette API."""


class _RateLimiter:
    """Espace les requetes (toutes cles et modeles confondus)."""

    def __init__(self, per_minute: int, clock=time.monotonic, sleep=time.sleep):
        self.interval = 60.0 / per_minute
        self._clock, self._sleep = clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._next:
                self._sleep(self._next - now)
                now = self._next
            self._next = now + self.interval


_mf_limiter = _RateLimiter(_MF_MAX_REQ_PER_MIN)


class _MFArchiveClient:
    """Client HTTP minimal pour meteole (meme interface que MeteoFranceClient.get).

    Ecarts voulus avec le client meteole : 401/403 levent tout de suite (meteole
    reessaie 5 fois avec 150 s d'attente), rythme global <= 45 requetes/min,
    timeout explicite, 429 respecte Retry-After (plafonne a 60 s).
    """

    def __init__(self, api_key: str, limiter: _RateLimiter | None = None,
                 deadline: float | None = None):
        import requests
        self._requests = requests
        self._session = requests.Session()
        self._session.headers.update({"apikey": api_key})
        self._limiter = limiter or _mf_limiter
        self._deadline = deadline
        self.request_count = 0

    def get(self, path: str, *, params: dict | None = None, max_retries: int = 3):
        last = "?"
        for attempt in range(1, max_retries + 1):
            if self._deadline is not None and time.monotonic() > self._deadline:
                raise TimeoutError("archive Meteo France : delai global depasse")
            self._limiter.wait()
            self.request_count += 1
            try:
                resp = self._session.get(_MF_API_ROOT + path, params=params,
                                         timeout=_MF_REQUEST_TIMEOUT_S)
            except self._requests.exceptions.RequestException as e:
                last = f"reseau : {e}"
                time.sleep(5 * attempt)
                continue
            code = resp.status_code
            if code in (200, 202, 204):
                return resp
            if code in (401, 403):
                raise MFAuthError(f"HTTP {code} : {resp.text[:200]}")
            if code == 400:
                raise ValueError(f"HTTP 400 : {resp.text[:300]}")
            if code == 404:
                raise LookupError(f"HTTP 404 : {resp.text[:200]}")
            last = f"HTTP {code}"
            if code == 429:
                try:
                    wait = float(resp.headers.get("Retry-After", 60))
                except ValueError:
                    wait = 60.0
                time.sleep(min(max(wait, 1.0), 60.0))
            else:
                time.sleep(5 * attempt)
        raise RuntimeError(f"Meteo France : echec apres {max_retries} essais ({last})")


def _mf_archive_build_model(model_name: str, auth: dict, deadline: float):
    """Instancie AromeForecast / ArpegeForecast avec le client d'archive."""
    from meteole import AromeForecast, ArpegeForecast
    key = auth.get("api_key")
    if not key:
        raise MFAuthError("aucune cle API (application_id OAuth non gere par l'archive)")
    client = _MFArchiveClient(key, deadline=deadline)
    if model_name == "arome":
        return AromeForecast(client=client, precision=_MF_AROME_PRECISION)
    return ArpegeForecast(client=client)


def _mf_archive_auths(model_name: str) -> list[tuple[str, dict]]:
    """Cles a essayer dans l'ordre : cle du modele, puis (AROME) cle ARPEGE.

    Verifie le 2026-10-01 : la cle ARPEGE (meme application) ouvre AROME.
    """
    auths = []
    primary = _get_meteofrance_auth(model_name)
    if primary.get("api_key"):
        auths.append((model_name, primary))
    if model_name == "arome":
        fallback = _get_meteofrance_auth("arpege")
        if fallback.get("api_key") and fallback != primary:
            auths.append(("arpege", fallback))
    return auths


def _mf_parse_run(coverage_id: str) -> datetime:
    """Heure du run (UTC) contenue dans le coverage_id meteole."""
    from datetime import timezone
    m = re.search(r"___(\d{4})-(\d{2})-(\d{2})T(\d{2})\.(\d{2})\.(\d{2})Z", coverage_id)
    if not m:
        raise ValueError(f"run introuvable dans {coverage_id!r}")
    return datetime(*(int(g) for g in m.groups()), tzinfo=timezone.utc)


def _mf_day_instants(day: date, tz) -> list[datetime]:
    """Instants UTC multiples de 3 h compris dans la journee `day` (heure de Paris)."""
    from datetime import timezone
    start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(timezone.utc)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(timezone.utc)
    t = start + timedelta(hours=(-start.hour) % _MF_ARCHIVE_STEP_H)
    out = []
    while t < end:
        out.append(t)
        t += timedelta(hours=_MF_ARCHIVE_STEP_H)
    return out


def _mf_select_horizons(run_dt: datetime, available: list, days: list[date], tz,
                        require_complete: bool) -> tuple[list[timedelta], list[date]]:
    """Choisit les echeances dans la liste DISPONIBLE du run.

    require_complete=True : un jour n'est retenu que si toutes ses echeances
    3 h sont disponibles (une moyenne sur une demi-journee biaiserait la
    comparaison). Retourne (echeances triees, jours retenus).
    """
    avail = set(available)
    chosen: set[timedelta] = set()
    kept = []
    for d in days:
        wanted = [t - run_dt for t in _mf_day_instants(d, tz)]
        ok = [h for h in wanted if h in avail]
        if (require_complete and wanted and len(ok) == len(wanted)) or (not require_complete and ok):
            chosen.update(ok)
            kept.append(d)
    return sorted(chosen), kept


def _mf_pick_run(model, indicator: str, days: list[date], tz, n_runs: int = 4):
    """Choisit le run parmi les `n_runs` plus recents : jour complet le plus
    lointain d'abord, puis le plus recent. Le dernier run est publie
    progressivement (AROME 06Z : 28 echeances a 10h30 UTC) et seuls les runs
    ARPEGE de 12Z vont jusqu'a 114 h (J+4 complet) ; les autres s'arretent a 102 h.

    Retourne (coverage_id, run UTC, echeances disponibles, jours complets).
    """
    try:
        cap = model.capabilities
        runs = sorted((str(r) for r in cap[cap["indicator"] == indicator]["run"].unique()),
                      reverse=True)[:n_runs] or [None]
    except Exception:
        runs = [None]  # capacites illisibles : dernier run seulement
    best = None
    for run in runs:
        try:
            cid = (model._get_coverage_id(indicator, run=run) if run
                   else model._get_coverage_id(indicator))
            run_dt = _mf_parse_run(cid)
            available = model.get_coverage_description(cid)["forecast_horizons"]
        except MFAuthError:
            raise
        except Exception as e:
            logger.debug(f"[Météo France archive] run {run} ignore : {e}")
            continue
        _, complete = _mf_select_horizons(run_dt, available, days, tz, True)
        score = (max(complete) if complete else date.min, run_dt)
        if best is None or score > best[0]:
            best = (score, cid, run_dt, available, complete)
    if best is None:
        raise RuntimeError(f"aucun run lisible pour {indicator}")
    return best[1:]


def _mf_city_bbox(cities: list[dict], pad: float = 0.3) -> tuple[tuple, tuple]:
    lats = [c["lat"] for c in cities]
    lons = [c["lon"] for c in cities]
    return ((round(min(lats) - pad, 2), round(max(lats) + pad, 2)),
            (round(min(lons) - pad, 2), round(max(lons) + pad, 2)))


def _mf_points_from_df(df, run_dt: datetime, cities: list[dict]) -> dict[str, dict[datetime, float]]:
    """Valeurs au point de grille le plus proche de chaque ville.

    DataFrame meteole : latitude, longitude, run, forecast_horizon, <valeur>.
    Retourne {ville: {instant_valide_UTC: valeur}}.
    """
    import pandas as pd
    if df is None or getattr(df, "empty", True):
        return {}
    known = {"latitude", "longitude", "run", "forecast_horizon", "ensemble_number",
             "valid_time", "time", "step"}
    value_cols = [c for c in df.columns if c not in known]
    if not value_cols or "forecast_horizon" not in df.columns:
        return {}
    vcol = value_cols[0]
    lats = sorted(set(float(v) for v in df["latitude"].unique()))
    lons = sorted(set(float(v) for v in df["longitude"].unique()))
    out: dict[str, dict[datetime, float]] = {}
    for city in cities:
        la = min(lats, key=lambda v: abs(v - city["lat"]))
        lo = min(lons, key=lambda v: abs(v - city["lon"]))
        if abs(la - city["lat"]) > 0.5 or abs(lo - city["lon"]) > 0.5:
            continue  # point hors de l'emprise recue : pas de valeur plutot qu'une fausse
        sub = df[(df["latitude"] == la) & (df["longitude"] == lo)]
        series = {}
        for fh, val in zip(sub["forecast_horizon"], sub[vcol]):
            if val is None or not pd.notna(val) or isinstance(val, (pd.Timedelta, pd.Timestamp)):
                continue
            try:
                f = float(val)
            except (TypeError, ValueError):
                continue
            if math.isnan(f) or math.isinf(f):
                continue
            series[run_dt + pd.Timedelta(fh).to_pytimedelta()] = f
        if series:
            out[city["name"]] = series
    return out


def _mf_fetch_model(model, model_name: str, today: date, cities: list[dict],
                    bbox: tuple, tz) -> dict:
    """Recupere les 4 indicateurs d'un modele pour les 9 villes (une emprise
    commune par echeance : 1 requete couvre toutes les villes).

    Requetes : 1 capacites, 1 description par run candidat (temperature,
    4 max), puis par indicateur et par jour retenu 1 description + 8 GRIB.
    """
    days = [today + timedelta(days=i) for i in range(_MF_ARCHIVE_MAX_DAYS[model_name] + 1)]
    series: dict[str, dict[str, dict]] = {c["name"]: {} for c in cities}
    kept_days: list[date] = []
    params_ok: list[str] = []
    run_iso = None
    run_str = None
    for key, indicator, heights in _MF_ARCHIVE_INDICATORS:
        try:
            if key == "temperature":
                cid, run_dt, available, sel_days = _mf_pick_run(model, indicator, days, tz)
                kept_days, run_iso = sel_days, run_dt.isoformat()
                run_str = cid.split("___", 1)[1][:20]
                if not kept_days:
                    logger.warning(f"[Météo France archive] {model_name} : aucun jour complet "
                                   f"dans les runs récents ({len(available)} échéances "
                                   f"au run {run_iso})")
                    break
            else:
                try:  # même run que la température (cohérence), sinon le dernier
                    cid = model._get_coverage_id(indicator, run=run_str)
                except Exception:
                    cid = model._get_coverage_id(indicator)
                run_dt = _mf_parse_run(cid)
                available = model.get_coverage_description(cid)["forecast_horizons"]
                _, sel_days = _mf_select_horizons(run_dt, available, kept_days, tz, False)
            got = False
            for d in sel_days:  # un appel par jour : DataFrame borné en mémoire
                horizons, _ = _mf_select_horizons(run_dt, available, [d], tz, False)
                df = model.get_coverage(coverage_id=cid, lat=bbox[0], long=bbox[1],
                                        heights=heights, forecast_horizons=horizons)
                for city, pts in _mf_points_from_df(df, run_dt, cities).items():
                    series[city].setdefault(key, {}).update(pts)
                    got = True
                del df
            if got:
                params_ok.append(key)
        except (MFAuthError, TimeoutError):
            raise
        except Exception as e:
            if key == "temperature":
                raise RuntimeError(f"{model_name}/temperature : {e}") from e
            logger.warning(f"[Météo France archive] {model_name}/{key} indisponible : {e}")
    return {"run": run_iso, "days": kept_days, "series": series, "params": params_ok}


def _mf_has_values(city_series: dict, key: str, day_str: str, tz) -> bool:
    return any(t.astimezone(tz).strftime("%Y-%m-%d") == day_str
               for t in city_series.get(key, {}))


def _mf_national_rows(per_model: dict[str, dict], cities: list[dict], today: date,
                      tz, fetched_at: str) -> list[dict]:
    """Agrege par jour (`_merge_models_to_daily`) puis moyenne ponderee des
    villes (`_merge_city_forecasts`) pour arome, arpege et merged.

    Humidite / vent / pression : None si l'indicateur manque (jamais les
    valeurs par defaut 50 % / 10 km/h du scoring, qui seraient inventees).
    """
    from datetime import timezone
    all_times = [t for m in per_model.values() for s in m["series"].values()
                 for pts in s.values() for t in pts]
    if not all_times:
        return []
    ref = min(all_times).astimezone(timezone.utc)
    weights = {c["name"]: c["weight"] for c in cities}

    def hourly(city: str, model: str) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for key, pts in per_model.get(model, {}).get("series", {}).get(city, {}).items():
            for t, v in pts.items():
                out.setdefault(int((t - ref).total_seconds() // 3600), {})[key] = v
        return out

    combos = {"arome": ("arome",), "arpege": ("arpege",), "merged": ("arome", "arpege")}
    rows = []
    for label, models in combos.items():
        models = tuple(m for m in models if m in per_model)
        if not models:
            continue
        allowed = {d.isoformat() for m in models for d in per_model[m]["days"]}
        city_days: dict[str, list[dict]] = {}
        for c in cities:
            aro = hourly(c["name"], "arome") if "arome" in models else {}
            arp = hourly(c["name"], "arpege") if "arpege" in models else {}
            daily = [d for d in _merge_models_to_daily(aro, arp, ref_time=ref, day_tz=tz)
                     if d["date"] in allowed]
            for d in daily:
                src = per_model[d["source"]]["series"].get(c["name"], {})
                for key, field in _MF_OPTIONAL_FIELDS:
                    if not _mf_has_values(src, key, d["date"], tz):
                        d[field] = None
            if daily:
                city_days[c["name"]] = daily
        if not city_days:
            continue
        runs = ";".join(f"{m}={per_model[m]['run']}" for m in models)
        # None retires avant la moyenne existante (elle supposerait 50 % / 10 km/h),
        # humidite/vent/pression recalcules ci-dessous sur les villes renseignees
        clean = {city: [{k: v for k, v in r.items() if v is not None} for r in lst]
                 for city, lst in city_days.items()}
        for nat in _merge_city_forecasts(clean):
            day_rows = {city: next(r for r in lst if r["date"] == nat["date"])
                        for city, lst in city_days.items()
                        if any(r["date"] == nat["date"] for r in lst)}
            row = {"target_date": nat["date"], "forecast_date": today.isoformat(),
                   "horizon_days": (date.fromisoformat(nat["date"]) - today).days,
                   "model": label, "temp_min": nat["temp_min"], "temp_max": nat["temp_max"],
                   "temp_moy": nat["temp_moy"], "n_villes": len(day_rows), "run": runs,
                   "fetched_at": fetched_at}
            for _, field in _MF_OPTIONAL_FIELDS:  # moyenne ponderee sur les villes renseignees
                vals = [(r[field], weights.get(city, 0.1)) for city, r in day_rows.items()
                        if r.get(field) is not None]
                wt = sum(w for _, w in vals)
                row[field] = round(sum(v * w for v, w in vals) / wt, 1) if wt > 0 else None
            rows.append(row)
    return rows


def fetch_meteofrance_archive(today: date | None = None) -> dict:
    """Prevision Meteo France AROME (J+0..J+2) et ARPEGE (J+0..J+4), 9 villes,
    pour ARCHIVE et comparaison (weather_forecast_mf_log). Synchrone, a lancer
    dans un thread. Ne leve jamais : les erreurs sont dans le rapport.

    Retourne {"forecast_date", "rows": [...], "models": {modele: {ok, key,
    run, days, cities, error}}, "requests": n}.
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Paris")
    now = datetime.now(tz)
    today = today or now.date()
    cities = Config.WEATHER_CITIES
    bbox = _mf_city_bbox(cities)
    deadline = time.monotonic() + _MF_ARCHIVE_DEADLINE_S
    report = {"forecast_date": today.isoformat(), "rows": [], "models": {}, "requests": 0}
    per_model: dict[str, dict] = {}
    for model_name in ("arome", "arpege"):
        info = {"ok": False, "key": None, "run": None, "days": 0, "cities": 0, "error": None}
        report["models"][model_name] = info
        auths = _mf_archive_auths(model_name)
        if not auths:
            info["error"] = "aucune clé Météo France configurée"
            continue
        for i, (key_label, auth) in enumerate(auths):
            model = None
            try:
                model = _mf_archive_build_model(model_name, auth, deadline)
                data = _mf_fetch_model(model, model_name, today, cities, bbox, tz)
                per_model[model_name] = data
                info.update(ok=bool(data["days"]), key=key_label, run=data["run"], error=None,
                            days=len(data["days"]),
                            cities=sum(1 for s in data["series"].values() if s.get("temperature")))
                break
            except MFAuthError as e:
                info["error"] = f"clé {key_label} refusée ({str(e)[:120]})"
                if i + 1 < len(auths) and model_name not in _mf_fallback_logged:
                    _mf_fallback_logged.add(model_name)
                    logger.warning(f"[Météo France archive] clé {key_label} refusée pour "
                                   f"{model_name.upper()} : nouvel essai avec la clé "
                                   f"{auths[i + 1][0].upper()}")
            except Exception as e:
                info["error"] = str(e)[:200]
                logger.warning(f"[Météo France archive] {model_name} en échec : {e}")
                break
            finally:
                client = getattr(model, "_client", None)
                report["requests"] += getattr(client, "request_count", 0) or 0
    per_model = {m: d for m, d in per_model.items() if d["days"]}
    if per_model:
        report["rows"] = _mf_national_rows(per_model, cities, today, tz, now.isoformat())
    return report
