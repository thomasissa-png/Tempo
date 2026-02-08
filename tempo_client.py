"""Client pour l'API officielle Tempo EDF (api-couleur-tempo.fr).

Endpoints utilisés :
  GET /jourTempo/today     → couleur du jour
  GET /jourTempo/tomorrow  → couleur de demain (dispo après 11h)
  GET /jourTempo/{date}    → couleur d'une date passée
  GET /joursTempo          → décompte jours restants par couleur
"""

import asyncio
import httpx
import logging
from datetime import date, datetime, timedelta
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

def store_actual(date_str: str, couleur: str, overwrite: bool = True):
    """Enregistre une couleur réelle confirmée dans la table actuals.

    Args:
        overwrite: Si True (défaut), utilise INSERT OR REPLACE (données officielles).
                   Si False, utilise INSERT OR IGNORE (backfill/seed — ne jamais
                   écraser une donnée existante).
    """
    conn = get_db()
    try:
        mode = "INSERT OR REPLACE" if overwrite else "INSERT OR IGNORE"
        conn.execute(
            f"""{mode} INTO actuals (date, couleur_reelle, timestamp_confirmation)
               VALUES (?, ?, ?)""",
            (date_str, couleur, datetime.now().isoformat()),
        )
        conn.commit()
        logger.info(f"[Tempo] Couleur réelle enregistrée : {date_str} = {couleur}")
    finally:
        conn.close()


# === Décompte officiel EDF ===

async def fetch_edf_remaining() -> dict | None:
    """Récupère les compteurs officiels EDF via /joursTempo.
    Retourne {"ROUGE": n, "BLANC": n, "BLEU": n} ou None."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{Config.TEMPO_API_BASE}/joursTempo")
            resp.raise_for_status()
            data = resp.json()
            return {
                "ROUGE": data.get("PARAM_NB_J_ROUGE", 0),
                "BLANC": data.get("PARAM_NB_J_BLANC", 0),
                "BLEU": data.get("PARAM_NB_J_BLEU", 0),
            }
    except Exception as e:
        logger.error(f"[Tempo] Erreur fetch /joursTempo : {e}")
        return None


# === Backfill saison ===

def _count_actuals_in_season() -> int:
    """Compte le nombre total d'entrées actuals pour la saison en cours."""
    start, end = get_season_dates()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM actuals WHERE date >= ? AND date <= ?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


def seed_from_remaining(remaining_rouge: int, remaining_blanc: int):
    """Injecte des actuals synthétiques pour atteindre les bons compteurs.

    Utilisé quand l'API par date n'est pas accessible. Crée des entrées
    aux premières dates de la saison pour que count_used_days() reflète
    les vrais compteurs EDF.
    """
    start, _ = get_season_dates()
    used_rouge = Config.JOURS_ROUGES_TOTAL - remaining_rouge
    used_blanc = Config.JOURS_BLANCS_TOTAL - remaining_blanc

    if used_rouge < 0 or used_blanc < 0:
        logger.error(f"[Seed] Valeurs invalides: remaining_rouge={remaining_rouge}, "
                     f"remaining_blanc={remaining_blanc}")
        return

    # Compter combien on a déjà en DB
    current_used = count_used_days()
    need_rouge = max(0, used_rouge - current_used["ROUGE"])
    need_blanc = max(0, used_blanc - current_used["BLANC"])

    if need_rouge == 0 and need_blanc == 0:
        logger.info("[Seed] Compteurs déjà corrects, rien à injecter")
        return

    # Trouver des dates libres au début de la saison pour les entrées synthétiques
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT date FROM actuals WHERE date >= ?",
            (start.isoformat(),)
        ).fetchall()
        existing_dates = {row["date"] for row in existing}
    finally:
        conn.close()

    current = start
    injected = {"ROUGE": 0, "BLANC": 0, "BLEU": 0}
    # D'abord injecter les rouges, puis les blancs, le reste en bleu
    colors_to_inject = (["ROUGE"] * need_rouge) + (["BLANC"] * need_blanc)

    yesterday = date.today() - timedelta(days=1)
    idx = 0
    while current <= yesterday and idx < len(colors_to_inject):
        if current.isoformat() not in existing_dates:
            couleur = colors_to_inject[idx]
            store_actual(current.isoformat(), couleur, overwrite=False)
            injected[couleur] += 1
            existing_dates.add(current.isoformat())
            idx += 1
        current += timedelta(days=1)

    # Remplir le reste des jours manquants en BLEU
    while current <= yesterday:
        if current.isoformat() not in existing_dates:
            store_actual(current.isoformat(), "BLEU", overwrite=False)
            injected["BLEU"] += 1
        current += timedelta(days=1)

    logger.info(f"[Seed] Injecté: {injected['ROUGE']}R, {injected['BLANC']}B, "
                f"{injected['BLEU']}BL synthétiques")

    # Vérification
    new_remaining = get_remaining_days()
    logger.info(f"[Seed] Nouveaux compteurs: R={new_remaining['ROUGE']}, "
                f"B={new_remaining['BLANC']}, BL={new_remaining['BLEU']}")


async def backfill_season_actuals():
    """Rempli la table actuals avec les couleurs passées de la saison en cours.

    Appelle l'API EDF /jourTempo/{date} pour chaque jour manquant entre
    le début de saison et hier. Exécuté au démarrage de l'application.
    Si l'API par date échoue, essaie le endpoint /joursTempo pour obtenir
    les compteurs globaux et injecte des actuals synthétiques.
    """
    start, _ = get_season_dates()
    yesterday = date.today() - timedelta(days=1)

    # Charger les dates déjà présentes en DB
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT date FROM actuals WHERE date >= ? AND date <= ?",
            (start.isoformat(), yesterday.isoformat()),
        ).fetchall()
        existing_dates = {row["date"] for row in rows}
    finally:
        conn.close()

    # Calculer les dates manquantes
    missing_dates = []
    current = start
    while current <= yesterday:
        if current.isoformat() not in existing_dates:
            missing_dates.append(current)
        current += timedelta(days=1)

    if not missing_dates:
        logger.info(f"[Backfill] Aucun jour manquant ({len(existing_dates)} actuals en DB)")
        return

    logger.info(f"[Backfill] {len(missing_dates)} jours manquants à récupérer "
                f"(du {missing_dates[0]} au {missing_dates[-1]})")

    # Récupérer par lots pour ne pas surcharger l'API
    fetched = 0
    errors = 0
    for i, target_date in enumerate(missing_dates):
        data = await fetch_tempo_date(target_date)
        if data and data.get("couleur"):
            store_actual(data["date"], data["couleur"], overwrite=False)
            fetched += 1
        else:
            errors += 1

        # Pause toutes les 10 requêtes pour ne pas surcharger l'API
        if (i + 1) % 10 == 0:
            await asyncio.sleep(0.5)

        # Si les 5 premières requêtes échouent toutes, l'API n'est pas accessible
        if i == 4 and errors == 5:
            logger.warning("[Backfill] API EDF inaccessible (5 erreurs consécutives)")
            break

    # Si trop d'erreurs, essayer le fallback via /joursTempo
    if errors > fetched and errors > 3:
        logger.info("[Backfill] Fallback: tentative via /joursTempo pour compteurs globaux")
        edf_remaining = await fetch_edf_remaining()
        if edf_remaining and edf_remaining.get("ROUGE") is not None:
            seed_from_remaining(edf_remaining["ROUGE"], edf_remaining["BLANC"])
            return
        else:
            # Dernier recours : variables d'environnement
            import os
            env_rouge = os.getenv("TEMPO_REMAINING_ROUGE")
            env_blanc = os.getenv("TEMPO_REMAINING_BLANC")
            if env_rouge and env_blanc:
                logger.info(f"[Backfill] Fallback env: ROUGE={env_rouge}, BLANC={env_blanc}")
                seed_from_remaining(int(env_rouge), int(env_blanc))
                return
            logger.warning(
                "[Backfill] Impossible de récupérer les compteurs EDF. "
                "Définissez TEMPO_REMAINING_ROUGE et TEMPO_REMAINING_BLANC "
                "dans .env pour initialiser manuellement.")
            return

    # Vérifier la cohérence avec les compteurs officiels EDF
    edf_remaining = await fetch_edf_remaining()
    if edf_remaining:
        our_remaining = get_remaining_days()
        logger.info(f"[Backfill] Terminé: {fetched} jours récupérés, {errors} erreurs")
        logger.info(f"[Backfill] Nos compteurs   : R={our_remaining['ROUGE']}, "
                    f"B={our_remaining['BLANC']}, BL={our_remaining['BLEU']}")
        logger.info(f"[Backfill] Compteurs EDF   : R={edf_remaining['ROUGE']}, "
                    f"B={edf_remaining['BLANC']}, BL={edf_remaining['BLEU']}")

        # Si écart, corriger via seed
        for couleur in ["ROUGE", "BLANC"]:
            delta = abs(our_remaining[couleur] - edf_remaining[couleur])
            if delta > 0:
                logger.warning(
                    f"[Backfill] ECART {couleur}: nous={our_remaining[couleur]}, "
                    f"EDF={edf_remaining[couleur]} (delta={delta})")
    else:
        logger.info(f"[Backfill] Terminé: {fetched} jours récupérés, {errors} erreurs")


# === Calcul saison ===

def get_season_dates() -> tuple[date, date]:
    """Retourne (début, fin) de la saison Tempo en cours.
    Saison = 1er septembre → 31 mai."""
    today = date.today()
    if today.month >= 6:
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


def get_blue_days_total() -> int:
    """Calcule le nombre total de jours bleus pour la saison en cours.
    = nombre total de jours dans la saison - rouges (22) - blancs (43).
    Varie selon les annees bissextiles (208 ou 209)."""
    start, end = get_season_dates()
    total_days = (end - start).days + 1
    return total_days - Config.JOURS_ROUGES_TOTAL - Config.JOURS_BLANCS_TOTAL


def get_remaining_days() -> dict:
    """Calcule les jours restants de chaque couleur pour la saison."""
    used = count_used_days()
    return {
        "ROUGE": max(0, Config.JOURS_ROUGES_TOTAL - used["ROUGE"]),
        "BLANC": max(0, Config.JOURS_BLANCS_TOTAL - used["BLANC"]),
        "BLEU": max(0, get_blue_days_total() - used["BLEU"]),
    }


def days_left_in_season() -> int:
    """Nombre de jours restants jusqu'à la fin de la saison."""
    _, end = get_season_dates()
    delta = (end - date.today()).days
    return max(0, delta)


def is_in_season(target_date: date) -> bool:
    """Vérifie si une date est dans la période Tempo (sept-mai)."""
    return target_date.month >= 9 or target_date.month <= 5
