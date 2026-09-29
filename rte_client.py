"""Client RTE eco2mix — prevision de consommation nationale.

API RTE (data.rte-france.com) avec OAuth2 Bearer token.
Fournit le signal de consommation nationale, facteur cle pour la decision Tempo.
Fallback gracieux si pas de credentials configures.

Corrections audit :
  - Fix #3 : type D-1 forecast au lieu de REALISED
  - Fix #13 : fetches paralleles asyncio.gather
  - Fix API fev 2026 : suppression appel Generation Forecast.
    L'API v3 n'accepte que WIND_ONSHORE, WIND_OFFSHORE, SOLAR,
    AGGREGATED_CPC, MDSE — aucun type ne fournit la disponibilite
    nucleaire ni la production agregee France. Le scoring fonctionne
    correctement avec la consommation seule.
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


async def fetch_nuclear_availability() -> None:
    """DESACTIVEE — la disponibilite nucleaire n'est pas disponible via l'API
    Generation Forecast RTE (v3).

    Types valides en entree v3 : WIND_ONSHORE, WIND_OFFSHORE, SOLAR,
    AGGREGATED_CPC, MDSE. Aucun ne fournit de donnee nucleaire.
    Il faudrait l'API Actual Generation ou Generation Installed Capacities
    (non implementee, et non necessaire : le scoring fonctionne bien
    avec la consommation seule).
    """
    return None


# ================================================================
# SCORE CONSOMMATION (pour le predictor)
# ================================================================

async def get_consumption_score() -> dict:
    """Calcule un score de risque base sur la prevision de consommation.

    Fix API fev 2026 : le scoring est base uniquement sur la consommation.
    La disponibilite nucleaire n'est pas disponible via l'API Generation
    Forecast v3 (aucun type valide ne fournit cette donnee).
    """
    try:
        conso = await fetch_consumption_forecast()
    except Exception as e:
        logger.warning(f"[RTE] Erreur consommation: {e}")
        conso = None

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

    return {
        "score": max(0, min(100, score)),
        "peak_mw": conso["peak_mw"],
        "mean_mw": conso["mean_mw"],
        "nuke_pct": None,
        "nuke_mw": None,
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


# ================================================================
# ARCHIVE DES PREVISIONS RTE (migration v25, table rte_forecast_log)
# ================================================================
# Observabilite / archive uniquement : aucune de ces valeurs n'alimente le
# scoring. Horizons fournis par l'API (doc RTE, Consumption v1.2 et
# Generation Forecast v3) :
#   - consumption/v1/short_term : type D-1 (J+1) et D-2 (J+2)
#   - consumption/v1/weekly_forecasts : J+3 a J+9 (pas d'emission le week-end)
#   - generation_forecast/v3/forecasts : types D-1, D-2, D-3 (J+1 a J+3)
#     pour WIND_ONSHORE, WIND_OFFSHORE, SOLAR
# Chaque serie ne garde que les points de SON jour (D-k -> jour J+k) : on
# archive ce qui etait disponible le jour de l'archivage, sans substituer une
# emission plus ancienne.

ARCHIVE_TIMEOUT_S = 10
ARCHIVE_MAX_HORIZON = 9          # J+9 = dernier jour des weekly_forecasts
ARCHIVE_GEN_TYPES = (1, 2, 3)    # D-1, D-2, D-3
ARCHIVE_COVERAGE_MIN = 0.9       # 90 % de la fenetre couverte pour une moyenne
_TEMPO_DAY_START_HOUR = 6

_GEN_COMPONENTS = {
    "WIND_ONSHORE": "wind_onshore_mw",
    "WIND_OFFSHORE": "wind_offshore_mw",
    "SOLAR": "solar_mw",
}
_SOURCE_LABELS = {
    "conso_mw": "conso",
    "wind_onshore_mw": "eol_terre",
    "wind_offshore_mw": "eol_mer",
    "solar_mw": "solaire",
}


def _parse_rte_dt(value) -> datetime | None:
    """ISO 8601 RTE ('2026-01-15T06:00:00+01:00') -> datetime UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_PARIS_TZ)
    return dt.astimezone(timezone.utc)


def _paris_bound(d: date, hour: int) -> datetime:
    """Instant d a `hour` h heure de Paris, en UTC (gere les jours DST)."""
    return datetime(d.year, d.month, d.day, hour, tzinfo=_PARIS_TZ).astimezone(timezone.utc)


def _collect_points(forecasts: list, kind: str, keep_day: date | None,
                    min_day: date | None, points: dict) -> None:
    """Ajoute les pas de temps d'une reponse RTE dans `points` (cle = debut UTC).

    Filtre sur le jour de Paris du pas : `keep_day` (series D-k) ou
    `>= min_day` (weekly). Les emissions les plus recentes (updated_date)
    ecrasent les plus anciennes.
    """
    def _upd(f):
        return str(f.get("updated_date") or "")

    for fc in sorted((f for f in forecasts or [] if isinstance(f, dict)), key=_upd):
        maj = (fc.get("updated_date") or "")[:10]
        for val in fc.get("values") or []:
            v = val.get("value")
            start = _parse_rte_dt(val.get("start_date"))
            end = _parse_rte_dt(val.get("end_date"))
            if v is None or start is None or end is None or end <= start:
                continue
            day = start.astimezone(_PARIS_TZ).date()
            if keep_day is not None and day != keep_day:
                continue
            if min_day is not None and day < min_day:
                continue
            label = f"{kind}(maj {maj})" if kind == "hebdo" and maj else kind
            points[start] = (end, float(v), label)


def _window_mean(points: dict, w_start: datetime, w_end: datetime):
    """Moyenne ponderee par la duree sur [w_start, w_end[ -> (moyenne, kinds).

    None si moins de ARCHIVE_COVERAGE_MIN de la fenetre est couverte.
    """
    covered = 0.0
    acc = 0.0
    kinds: set[str] = set()
    for start, (end, v, kind) in points.items():
        lo, hi = max(start, w_start), min(end, w_end)
        if hi > lo:
            sec = (hi - lo).total_seconds()
            covered += sec
            acc += v * sec
            kinds.add(kind)
    total = (w_end - w_start).total_seconds()
    if total <= 0 or covered < ARCHIVE_COVERAGE_MIN * total:
        return None, kinds
    return acc / covered, kinds


def build_forecast_log_rows(series: dict[str, dict], today: date,
                            fetched_at: str) -> list[dict]:
    """Agrege les points horaires en lignes rte_forecast_log (J+1..J+9).

    `series` : {"conso_mw": points, "wind_onshore_mw": points, ...}.
    Une seule methode par ligne (toutes les colonnes sont comparables) :
    journee Tempo 6 h-6 h si chaque composante disponible la couvre, sinon
    moyenne calendaire 0 h-24 h ; la methode est ecrite dans `source`.
    net_conso_mw = conso - eolien terrestre - eolien en mer - solaire,
    uniquement si les 4 composantes existent.
    """
    rows = []
    for h in range(1, ARCHIVE_MAX_HORIZON + 1):
        target = today + timedelta(days=h)
        nxt = target + timedelta(days=1)
        windows = {
            "6h-6h": (_paris_bound(target, _TEMPO_DAY_START_HOUR),
                      _paris_bound(nxt, _TEMPO_DAY_START_HOUR)),
            "calendaire": (_paris_bound(target, 0), _paris_bound(nxt, 0)),
        }
        means = {m: {col: _window_mean(series.get(col) or {}, *w)
                     for col in _SOURCE_LABELS}
                 for m, w in windows.items()}
        # 6h-6h seulement si aucune composante n'est perdue par rapport au calendaire
        tempo_ok = all(means["6h-6h"][c][0] is not None
                       for c in _SOURCE_LABELS if means["calendaire"][c][0] is not None)
        method = "6h-6h" if tempo_ok else "calendaire"
        chosen = means[method]
        if all(v is None for v, _ in chosen.values()):
            continue
        row = {"target_date": target.isoformat(), "forecast_date": today.isoformat(),
               "horizon_days": h, "fetched_at": fetched_at}
        parts = []
        for col, label in _SOURCE_LABELS.items():
            value, kinds = chosen[col]
            row[col] = round(value, 1) if value is not None else None
            if value is not None:
                parts.append(f"{label}={'+'.join(sorted(kinds))}")
        comps = [row[c] for c in _SOURCE_LABELS]
        row["net_conso_mw"] = (
            round(comps[0] - comps[1] - comps[2] - comps[3], 1)
            if all(c is not None for c in comps) else None
        )
        row["source"] = f"rte/{method};" + ";".join(parts)
        rows.append(row)
    return rows


async def _archive_get(client, api: str, path: str, params: dict) -> dict | None:
    """GET RTE pour l'archive : jamais d'exception, warning en cas d'echec."""
    token = await _get_token(api)
    if not token:
        return None
    try:
        resp = await client.get(f"{Config.RTE_API_BASE}{path}",
                                headers={"Authorization": f"Bearer {token}"},
                                params=params)
        if resp.status_code in (401, 403):
            (_token_conso if api == "consumption" else _token_generation)["token"] = None
            logger.warning(f"[RTE Archive] {path} {params.get('type', '')}: HTTP {resp.status_code}")
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"[RTE Archive] {path} {params.get('production_type', '')} "
                       f"{params.get('type', '')}: {e}")
        return None


def _window_params(d_from: date, d_to: date) -> dict:
    return {"start_date": f"{d_from.isoformat()}T00:00:00{_paris_offset_str(d_from)}",
            "end_date": f"{d_to.isoformat()}T00:00:00{_paris_offset_str(d_to)}"}


async def fetch_forecasts_for_archive(today: date | None = None) -> list[dict]:
    """Recupere les previsions RTE disponibles aujourd'hui pour l'archive.

    Retourne les lignes rte_forecast_log (liste vide si pas de credentials
    ou si toutes les requetes echouent). Ne leve jamais d'exception.
    """
    try:
        if today is None:
            today = datetime.now(_PARIS_TZ).date()
        if not _get_credentials("consumption") and not _get_credentials("generation"):
            return []
        d = lambda k: today + timedelta(days=k)  # noqa: E731
        calls = [("conso_mw", "short_term", 1, "consumption", "/open_api/consumption/v1/short_term",
                  {"type": "D-1", **_window_params(d(1), d(2))}),
                 ("conso_mw", "short_term", 2, "consumption", "/open_api/consumption/v1/short_term",
                  {"type": "D-2", **_window_params(d(2), d(3))}),
                 ("conso_mw", "weekly_forecasts", None, "consumption",
                  "/open_api/consumption/v1/weekly_forecasts",
                  _window_params(d(3), d(ARCHIVE_MAX_HORIZON + 1)))]
        for ptype, col in _GEN_COMPONENTS.items():
            for k in ARCHIVE_GEN_TYPES:
                calls.append((col, "forecasts", k, "generation",
                              "/open_api/generation_forecast/v3/forecasts",
                              {"production_type": ptype, "type": f"D-{k}",
                               **_window_params(d(k), d(k + 1))}))
        async with httpx.AsyncClient(timeout=ARCHIVE_TIMEOUT_S) as client:
            payloads = await asyncio.gather(
                *(_archive_get(client, api, path, params)
                  for _, _, _, api, path, params in calls),
                return_exceptions=True,
            )
        series: dict[str, dict] = {}
        for (col, key, k, _, _, _), payload in zip(calls, payloads):
            if not isinstance(payload, dict):
                continue
            pts = series.setdefault(col, {})
            if k is None:
                _collect_points(payload.get(key), "hebdo", None, d(3), pts)
            else:
                _collect_points(payload.get(key), f"D-{k}", d(k), None, pts)
        return build_forecast_log_rows(series, today, datetime.now(_PARIS_TZ).isoformat())
    except Exception as e:
        logger.warning(f"[RTE Archive] Echec collecte previsions: {e}")
        return []
