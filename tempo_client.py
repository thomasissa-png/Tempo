"""Client pour l'API officielle Tempo EDF (api-couleur-tempo.fr).

Endpoints utilisés :
  GET /jourTempo/today     → couleur du jour
  GET /jourTempo/tomorrow  → couleur de demain (dispo après 11h)
  GET /jourTempo/{date}    → couleur d'une date passée
  GET /joursTempo          → décompte jours restants par couleur
"""

import httpx
import logging
from datetime import date, datetime
from config import Config
from database import get_db

logger = logging.getLogger(__name__)

CODE_TO_COULEUR = {1: "BLEU", 2: "BLANC", 3: "ROUGE"}


# === Appels API ===

async def fetch_tempo_today() -> dict | None:
    """Récupère la couleur Tempo du jour."""
    return await _fetch(f"{Config.TEMPO_API_BASE}/jourTempo/today")


async def fetch_tempo_tomorrow() -> dict | None:
    """Récupère la couleur de demain (disponible après 11h J-1)."""
    return await _fetch(f"{Config.TEMPO_API_BASE}/jourTempo/tomorrow")


async def fetch_tempo_date(target_date: date) -> dict | None:
    """Récupère la couleur d'une date spécifique."""
    return await _fetch(f"{Config.TEMPO_API_BASE}/jourTempo/{target_date.isoformat()}")


async def fetch_remaining_days() -> dict | None:
    """Récupère le nombre de jours restants par couleur pour la saison."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{Config.TEMPO_API_BASE}/joursTempo")
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"[Tempo] Jours restants saison : {data}")
            return data
    except Exception as e:
        logger.error(f"[Tempo] Erreur fetch jours restants : {e}")
        return None


async def _fetch(url: str) -> dict | None:
    """Appel générique à l'API Tempo avec parsing."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return _parse_response(data)
    except Exception as e:
        logger.error(f"[Tempo] Erreur fetch {url} : {e}")
        return None


def _parse_response(data: dict) -> dict | None:
    """Convertit la réponse API en format interne.
    API renvoie : {"codeJour": 1|2|3, "periode": "...", "dateJour": "2024-01-15"}
    """
    if not data:
        return None
    code = data.get("codeJour")
    if code not in CODE_TO_COULEUR:
        logger.warning(f"[Tempo] Code jour inconnu : {code}")
        return None
    return {
        "date": data.get("dateJour", ""),
        "couleur": CODE_TO_COULEUR[code],
        "code": code,
    }


# === Stockage en base ===

def store_actual(date_str: str, couleur: str):
    """Enregistre une couleur réelle confirmée dans la table actuals."""
    conn = get_db()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO actuals (date, couleur_reelle, timestamp_confirmation)
               VALUES (?, ?, ?)""",
            (date_str, couleur, datetime.now().isoformat()),
        )
        conn.commit()
        logger.info(f"[Tempo] Couleur réelle enregistrée : {date_str} = {couleur}")
    finally:
        conn.close()


# === Calcul saison ===

def get_season_dates() -> tuple[date, date]:
    """Retourne (début, fin) de la saison Tempo en cours.
    Saison = 1er septembre → 31 mai."""
    today = date.today()
    if today.month >= 9:
        return date(today.year, 9, 1), date(today.year + 1, 5, 31)
    else:
        return date(today.year - 1, 9, 1), date(today.year, 5, 31)


def count_used_days() -> dict:
    """Compte les jours utilisés de chaque couleur cette saison."""
    start, end = get_season_dates()
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT couleur_reelle, COUNT(*) as cnt
               FROM actuals
               WHERE date >= ? AND date <= ?
               GROUP BY couleur_reelle""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        counts = {"BLEU": 0, "BLANC": 0, "ROUGE": 0}
        for row in rows:
            counts[row["couleur_reelle"]] = row["cnt"]
        return counts
    finally:
        conn.close()


def get_remaining_days() -> dict:
    """Calcule les jours restants de chaque couleur pour la saison."""
    used = count_used_days()
    return {
        "ROUGE": max(0, Config.JOURS_ROUGES_TOTAL - used["ROUGE"]),
        "BLANC": max(0, Config.JOURS_BLANCS_TOTAL - used["BLANC"]),
        "BLEU": max(0, Config.JOURS_BLEUS_TOTAL - used["BLEU"]),
    }


def days_left_in_season() -> int:
    """Nombre de jours restants jusqu'à la fin de la saison."""
    _, end = get_season_dates()
    delta = (end - date.today()).days
    return max(0, delta)


def is_in_season(target_date: date) -> bool:
    """Vérifie si une date est dans la période Tempo (sept-mai)."""
    return target_date.month >= 9 or target_date.month <= 5
