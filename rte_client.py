"""Client RTE eco2mix — prevision de consommation et disponibilite nucleaire.

API RTE (data.rte-france.com) avec OAuth2 Bearer token.
Fournit le signal de consommation nationale, facteur cle pour la decision Tempo.
Fallback gracieux si pas de credentials configures.

Corrections audit :
  - Fix #3 : type D-1 forecast au lieu de REALISED
  - Fix #12 : capacite nucleaire configurable
  - Fix #13 : fetches paralleles asyncio.gather
"""

import httpx
import logging
import asyncio
import base64
from datetime import date, timedelta
from config import Config

logger = logging.getLogger(__name__)

# Token cache en memoire
_token_cache = {"token": None, "expires": 0}
_token_lock = asyncio.Lock()


# ================================================================
# AUTHENTIFICATION OAuth2
# ================================================================

async def _get_token() -> str | None:
    """Obtient un token OAuth2 RTE (cache en memoire).

    Fix audit v6 : asyncio.Lock pour eviter les race conditions
    (deux appels simultanes pourraient rafraichir le token en double).
    """
    import time
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires"]:
        return _token_cache["token"]

    async with _token_lock:
        # Re-verifier apres acquisition du lock
        now = time.time()
        if _token_cache["token"] and now < _token_cache["expires"]:
            return _token_cache["token"]

        if not Config.RTE_CLIENT_ID or not Config.RTE_CLIENT_SECRET:
            return None

        credentials = f"{Config.RTE_CLIENT_ID}:{Config.RTE_CLIENT_SECRET}"
        b64 = base64.b64encode(credentials.encode()).decode()

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
                _token_cache["token"] = data["access_token"]
                _token_cache["expires"] = now + 6600
                logger.info("[RTE] Token OAuth2 obtenu")
                return _token_cache["token"]
        except Exception as e:
            logger.warning(f"[RTE] Erreur authentification: {e}")
            return None


# ================================================================
# PREVISION DE CONSOMMATION
# ================================================================

async def fetch_consumption_forecast() -> dict | None:
    """Recupere la prevision de consommation J+1 depuis RTE.

    Fix #3 : utilise type D-1 (prevision) au lieu de REALISED (passe).
    """
    token = await _get_token()
    if not token:
        return None

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    after = (date.today() + timedelta(days=2)).isoformat()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{Config.RTE_API_BASE}/open_api/consumption/v1/short_term",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "type": "D-1",
                    "start_date": f"{tomorrow}T00:00:00+01:00",
                    "end_date": f"{after}T00:00:00+01:00",
                },
            )
            if resp.status_code in (401, 403):
                _token_cache["token"] = None
                logger.warning("[RTE] Token expire, retry au prochain appel")
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
                "date": tomorrow,
                "peak_mw": round(max(values)),
                "mean_mw": round(sum(values) / len(values)),
            }
    except Exception as e:
        logger.warning(f"[RTE] Erreur consommation: {e}")
        return None


async def fetch_nuclear_availability() -> dict | None:
    """Recupere la disponibilite du parc nucleaire.

    Fix #12 : capacite totale configurable via Config.
    """
    token = await _get_token()
    if not token:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            today_str = date.today().isoformat()
            tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
            resp = await client.get(
                f"{Config.RTE_API_BASE}/open_api/generation_forecast/v2/forecasts",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "production_type": "NUCLEAR",
                    "start_date": f"{today_str}T00:00:00+01:00",
                    "end_date": f"{tomorrow_str}T00:00:00+01:00",
                },
            )
            if resp.status_code in (401, 403):
                _token_cache["token"] = None
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

            total_capacity = Config.RTE_NUCLEAR_CAPACITY_MW
            if total_capacity <= 0:
                return None
            available = round(max(values))
            return {
                "available_mw": available,
                "total_mw": total_capacity,
                "availability_pct": round(available / total_capacity * 100, 1),
            }
    except Exception as e:
        logger.warning(f"[RTE] Erreur nucleaire: {e}")
        return None


# ================================================================
# SCORE CONSOMMATION (pour le predictor)
# ================================================================

async def get_consumption_score() -> dict:
    """Calcule un score de risque base sur la consommation prevue.

    Fix #13 : fetches paralleles avec asyncio.gather.
    """
    # Fix #13 : lancer les deux fetches en parallele
    conso, nuke = await asyncio.gather(
        fetch_consumption_forecast(),
        fetch_nuclear_availability(),
        return_exceptions=True,
    )

    # Gerer les exceptions retournees par gather
    if isinstance(conso, Exception):
        logger.warning(f"[RTE] Erreur consommation dans gather: {conso}")
        conso = None
    if isinstance(nuke, Exception):
        logger.warning(f"[RTE] Erreur nucleaire dans gather: {nuke}")
        nuke = None

    if not conso and not nuke:
        return {"score": 50, "peak_mw": None, "nuke_pct": None, "available": False}

    score = 0

    # Score consommation prevue
    if conso:
        peak = conso["peak_mw"]
        if peak >= Config.RTE_CONSO_SEUIL_CRITIQUE:
            score += 80
        elif peak >= Config.RTE_CONSO_SEUIL_HAUT:
            score += 60
        elif peak >= Config.RTE_CONSO_SEUIL_MOYEN:
            score += 35
        else:
            score += 10

    # Ajustement disponibilite nucleaire
    nuke_pct = None
    if nuke:
        nuke_pct = nuke["availability_pct"]
        if nuke_pct < 60:
            score += 20  # Beaucoup de reacteurs en maintenance
        elif nuke_pct < 70:
            score += 10
        elif nuke_pct > 85:
            score -= 10  # Parc en forme, risque reduit

    return {
        "score": max(0, min(100, score)),
        "peak_mw": conso["peak_mw"] if conso else None,
        "nuke_pct": nuke_pct,
        "available": conso is not None or nuke is not None,
    }
