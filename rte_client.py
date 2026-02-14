"""Client RTE eco2mix — prevision de consommation et production agregee.

API RTE (data.rte-france.com) avec OAuth2 Bearer token.
Fournit le signal de consommation nationale, facteur cle pour la decision Tempo.
Fallback gracieux si pas de credentials configures.

Corrections audit :
  - Fix #3 : type D-1 forecast au lieu de REALISED
  - Fix #12 : capacite nucleaire configurable
  - Fix #13 : fetches paralleles asyncio.gather
  - Fix API fev 2026 : migration Generation Forecast v2→v3,
    NUCLEAR→AGGREGATED_FRANCE (NUCLEAR n'est pas un type valide).
    La disponibilite nucleaire n'est pas disponible via cette API.
"""

import httpx
import logging
import asyncio
import base64
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from config import Config

logger = logging.getLogger(__name__)

_PARIS_TZ = ZoneInfo("Europe/Paris")


def _paris_offset_str(d: date) -> str:
    """Retourne l'offset timezone Paris pour une date donnée ('+01:00' ou '+02:00').
    Gère automatiquement le changement heure d'été/hiver (BUG-06 QA)."""
    dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=_PARIS_TZ)
    offset = dt.utcoffset()
    total_seconds = int(offset.total_seconds())
    hours, remainder = divmod(abs(total_seconds), 3600)
    minutes = remainder // 60
    sign = "+" if total_seconds >= 0 else "-"
    return f"{sign}{hours:02d}:{minutes:02d}"


# Token caches separes par API (chaque API RTE a sa propre application/credentials)
_token_conso = {"token": None, "expires": 0}
_token_generation = {"token": None, "expires": 0}
_lock_conso = asyncio.Lock()
_lock_generation = asyncio.Lock()


# ================================================================
# AUTHENTIFICATION OAuth2
# ================================================================

def _get_credentials(api: str) -> tuple[str, str] | None:
    """Retourne (client_id, client_secret) pour une API RTE donnee.

    Chaque API RTE necessite sa propre application sur le portail.
    Fallback sur RTE_CLIENT_ID/SECRET si cle specifique absente.
    """
    if api == "consumption":
        cid = Config.RTE_CONSO_CLIENT_ID or Config.RTE_CLIENT_ID
        sec = Config.RTE_CONSO_CLIENT_SECRET or Config.RTE_CLIENT_SECRET
    elif api == "generation":
        cid = Config.RTE_GENERATION_CLIENT_ID or Config.RTE_CLIENT_ID
        sec = Config.RTE_GENERATION_CLIENT_SECRET or Config.RTE_CLIENT_SECRET
    else:
        cid = Config.RTE_CLIENT_ID
        sec = Config.RTE_CLIENT_SECRET

    if not cid or not sec:
        return None
    return (cid, sec)


async def _get_token(api: str = "consumption") -> str | None:
    """Obtient un token OAuth2 RTE pour une API donnee (cache en memoire).

    Chaque API a son propre cache de token car les credentials sont differentes.
    Fix audit v6 : asyncio.Lock pour eviter les race conditions.
    """
    import time

    cache = _token_conso if api == "consumption" else _token_generation
    lock = _lock_conso if api == "consumption" else _lock_generation

    now = time.time()
    if cache["token"] and now < cache["expires"]:
        return cache["token"]

    async with lock:
        now = time.time()
        if cache["token"] and now < cache["expires"]:
            return cache["token"]

        creds = _get_credentials(api)
        if not creds:
            return None

        b64 = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{Config.RTE_API_BASE}/token/oauth/",
                    headers={
                        "Authorization": f"Basic {b64}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    data="grant_type=client_credentials",
                )
                resp.raise_for_status()
                data = resp.json()
                cache["token"] = data["access_token"]
                cache["expires"] = now + 6600
                logger.info(f"[RTE] Token OAuth2 obtenu ({api})")
                return cache["token"]
        except Exception as e:
            logger.warning(f"[RTE] Erreur authentification ({api}): {e}")
            return None


# ================================================================
# PREVISION DE CONSOMMATION
# ================================================================

async def fetch_consumption_forecast() -> dict | None:
    """Recupere la prevision de consommation J+1 depuis RTE.

    Fix #3 : utilise type D-1 (prevision) au lieu de REALISED (passe).
    """
    token = await _get_token("consumption")
    if not token:
        return None

    tomorrow = date.today() + timedelta(days=1)
    after = date.today() + timedelta(days=2)
    tz_offset = _paris_offset_str(tomorrow)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{Config.RTE_API_BASE}/open_api/consumption/v1/short_term",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "type": "D-1",
                    "start_date": f"{tomorrow.isoformat()}T00:00:00{tz_offset}",
                    "end_date": f"{after.isoformat()}T00:00:00{tz_offset}",
                },
            )
            if resp.status_code in (401, 403):
                _token_conso["token"] = None
                logger.warning("[RTE] Token conso expire, retry au prochain appel")
                return None
            resp.raise_for_status()
            data = resp.json()

            values = []
            for forecast in data.get("short_term", []):
                for val in forecast.get("values", []):
                    if val.get("value") is not None:
                        values.append(val["value"])

            if not values:
                return None

            return {
                "date": tomorrow.isoformat(),
                "peak_mw": round(max(values)),
                "mean_mw": round(sum(values) / len(values)),
            }
    except Exception as e:
        logger.warning(f"[RTE] Erreur consommation: {e}")
        return None


async def fetch_generation_forecast() -> dict | None:
    """Recupere la prevision de production agregee France (MW).

    Fix API fev 2026 : migration v2→v3, NUCLEAR→AGGREGATED_FRANCE.
    NUCLEAR n'est pas un type valide dans l'API Generation Forecast RTE.
    La disponibilite nucleaire necessiterait l'API Actual Generation ou
    Generation Installed Capacities (non implementee).

    AGGREGATED_FRANCE fournit la production totale previsionnelle France.
    Ce signal est correle a la consommation (production suit la demande)
    et n'apporte pas d'information independante pour le scoring Tempo.
    Il est retourne pour observabilite uniquement.
    """
    token = await _get_token("generation")
    if not token:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            today_d = date.today()
            tomorrow_d = today_d + timedelta(days=1)
            tz_offset = _paris_offset_str(today_d)
            resp = await client.get(
                f"{Config.RTE_API_BASE}/open_api/generation_forecast/v3/forecasts",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "production_type": "AGGREGATED_FRANCE",
                    "start_date": f"{today_d.isoformat()}T00:00:00{tz_offset}",
                    "end_date": f"{tomorrow_d.isoformat()}T00:00:00{tz_offset}",
                },
            )
            if resp.status_code in (401, 403):
                _token_generation["token"] = None
                return None
            resp.raise_for_status()
            data = resp.json()

            values = []
            for forecast in data.get("forecasts", []):
                for val in forecast.get("values", []):
                    if val.get("value") is not None:
                        values.append(val["value"])

            if not values:
                return None

            peak = round(max(values))
            mean = round(sum(values) / len(values))
            return {
                "gen_peak_mw": peak,
                "gen_mean_mw": mean,
            }
    except Exception as e:
        logger.warning(f"[RTE] Erreur generation forecast: {e}")
        return None


# Alias backward-compatible (utilise dans les imports existants)
fetch_nuclear_availability = fetch_generation_forecast


# ================================================================
# SCORE CONSOMMATION (pour le predictor)
# ================================================================

async def get_consumption_score() -> dict:
    """Calcule un score de risque base sur la consommation prevue.

    Fix #13 : fetches paralleles avec asyncio.gather.
    Fix API fev 2026 : la disponibilite nucleaire n'est plus disponible
    (NUCLEAR n'est pas un type valide dans l'API Generation Forecast).
    Le score est base uniquement sur la prevision de consommation.
    La production agregee (AGGREGATED_FRANCE) est recuperee pour
    observabilite mais n'ajuste plus le score (signal correle a la conso).
    """
    # Fix #13 : lancer les deux fetches en parallele
    conso, gen = await asyncio.gather(
        fetch_consumption_forecast(),
        fetch_generation_forecast(),
        return_exceptions=True,
    )

    # Gerer les exceptions retournees par gather
    if isinstance(conso, Exception):
        logger.warning(f"[RTE] Erreur consommation dans gather: {conso}")
        conso = None
    if isinstance(gen, Exception):
        logger.warning(f"[RTE] Erreur generation dans gather: {gen}")
        gen = None

    if not conso:
        return {"score": 50, "peak_mw": None, "nuke_pct": None,
                "nuke_mw": None, "available": False}

    score = 0

    # Score consommation prevue
    peak = conso["peak_mw"]
    if peak >= Config.RTE_CONSO_SEUIL_CRITIQUE:
        score += 80
    elif peak >= Config.RTE_CONSO_SEUIL_HAUT:
        score += 60
    elif peak >= Config.RTE_CONSO_SEUIL_MOYEN:
        score += 35
    else:
        score += 10

    # Production agregee France (observabilite uniquement)
    gen_peak = gen.get("gen_peak_mw") if gen else None
    if gen_peak:
        logger.debug(f"[RTE] Production agregee France: peak={gen_peak} MW")

    return {
        "score": max(0, min(100, score)),
        "peak_mw": conso["peak_mw"],
        "mean_mw": conso["mean_mw"],
        "nuke_pct": None,   # Plus disponible (API v3 ne fournit pas NUCLEAR)
        "nuke_mw": None,    # Plus disponible
        "gen_peak_mw": gen_peak,
        "available": True,
    }


async def fetch_realised_consumption(target: date | None = None) -> dict | None:
    """Recupere la consommation realisee de la veille depuis l'API RTE.

    Stocke le resultat dans rte_daily pour alimenter les features ML lag.
    Appelee quotidiennement par le scheduler.
    """
    token = await _get_token("consumption")
    if not token:
        return None

    if target is None:
        target = date.today() - timedelta(days=1)
    next_day = target + timedelta(days=1)
    tz_offset = _paris_offset_str(target)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{Config.RTE_API_BASE}/open_api/consumption/v1/short_term",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "type": "REALISED",
                    "start_date": f"{target.isoformat()}T00:00:00{tz_offset}",
                    "end_date": f"{next_day.isoformat()}T00:00:00{tz_offset}",
                },
            )
            if resp.status_code in (401, 403):
                _token_conso["token"] = None
                return None
            resp.raise_for_status()
            data = resp.json()

            values = []
            for forecast in data.get("short_term", []):
                for val in forecast.get("values", []):
                    if val.get("value") is not None:
                        values.append(val["value"])

            if not values:
                return None

            return {
                "date": target.isoformat(),
                "conso_peak_mw": round(max(values)),
                "conso_mean_mw": round(sum(values) / len(values)),
            }
    except Exception as e:
        logger.warning(f"[RTE] Erreur consommation realisee: {e}")
        return None
