"""Système d'auto-amélioration — vérification, évaluation et recalcul des poids.

Responsabilités :
  1. Vérification quotidienne (11h30) : compare prédictions vs couleur réelle
  2. Calcul de métriques : précision, recall, F1, matrice de confusion
  3. Recalcul mensuel des poids via régression logistique (scikit-learn)
  4. Journal d'apprentissage : détection de patterns d'erreurs systématiques
  5. Corrections de biais : ajustements appliqués aux prédictions futures
  6. Évaluation rattrapage : évalue rétroactivement les jours manqués
  7. Validation des corrections (A-1) + kill-switch (C-3)
  8. Auto-rollback poids (W-5)

Fix audit ML + Learning System Audit :
  - ML-1/ML-2 : filtre horizon <= 5 dans évaluation et entraînement
  - ML-4 : métriques precision/recall/F1 par classe
  - ML-5 : seuil validation 55% (au lieu de 40%)
  - ML-6 : normalisation StandardScaler des features
  - ML-7/ML-8 : minimum 60 données + cross-validation 5-fold
  - ML-11 : permutation importance au lieu de norme L2
  - ML-12 : ALPHA adaptatif selon quantité de données
  - ML-15 : precision_apres mise à jour indépendamment
  - C-1 : entraînement sur raw sub-scores (avant corrections)
  - D-1 : UNIQUE(date_prediction, date_cible, jours_avance)
  - A-1 : validation impact des corrections
  - A-2 : Wilson interval confidence
  - W-2 : vérification balance des classes dans holdout
  - W-3 : n_repeats=30 pour permutation importance
  - W-5 : auto-rollback si precision chute
  - W-6 : plus de LIMIT 300 (toutes les données)
  - C-3 : kill-switch corrections nocives
  - A-5 : historique versionné des corrections
  - A-6 : garde anti double-exécution
"""

import json
import math
import logging
from datetime import date, datetime, timedelta
from database import get_db, get_current_weights, get_previous_weights
from config import Config

logger = logging.getLogger(__name__)


def _enforce_start_date(since: str) -> str:
    """Clamp 'since' date to not go before PREDICTION_START_DATE.

    Uses Config.PREDICTION_START_DATE if set, otherwise returns since unchanged.
    This allows tests to override or unset the start date.
    """
    start = getattr(Config, 'PREDICTION_START_DATE', None)
    if start:
        return max(since, start)
    return since


def _get_all_version_dates() -> dict[str, str]:
    """Merge code versions (TOOL_UPDATE_DATES) with weight recalculations.

    Returns a sorted dict {date_str: label} combining:
    - Config.TOOL_UPDATE_DATES (manual code changes)
    - Successful weight recalculations from weights_history DB table
      (excludes rejected entries that didn't change active weights)
    """
    versions = dict(getattr(Config, 'TOOL_UPDATE_DATES', {}))

    # Read successful weight recalculations from DB
    start = getattr(Config, 'PREDICTION_START_DATE', None)
    conn = get_db()
    try:
        conditions = ["commentaire NOT LIKE 'REJETE%'"]
        params: list = []
        if start:
            conditions.append("date_update >= ?")
            params.append(start)

        rows = conn.execute(
            f"""SELECT date_update, commentaire, precision_avant,
                       rollback_of
               FROM weights_history
               WHERE {' AND '.join(conditions)}
               ORDER BY id""",
            params,
        ).fetchall()

        for r in rows:
            d = r["date_update"]
            if d in versions:
                # Code version on same date takes precedence
                continue
            if r["rollback_of"]:
                label = "Auto-rollback poids"
            else:
                # Extract F1 from commentaire if available
                comm = r["commentaire"] or ""
                prec = r["precision_avant"] or 0
                label = f"Recalibration poids (préc. {prec:.0f}%)"
            versions[d] = label
    finally:
        conn.close()

    return dict(sorted(versions.items()))



# ================================================================
# UTILITAIRE : profondeur historique disponible
# ================================================================

def get_history_depth_days() -> int:
    """Retourne le nombre de jours d'historique disponible dans performance.

    Fix audit ML #38 : centralise le calcul utilisé par startup, scheduler
    et tâches manuelles pour éviter la duplication et les incohérences.
    Minimum 90 jours (fallback si pas de données).
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT MIN(date_cible) as earliest FROM performance"
        ).fetchone()
        if row and row["earliest"]:
            earliest = date.fromisoformat(row["earliest"])
            return max(90, (date.today() - earliest).days + 1)
        return 90
    finally:
        conn.close()


# ================================================================
# 1. VÉRIFICATION QUOTIDIENNE
# ================================================================

def evaluate_predictions_for_date(target_date: date, couleur_reelle: str):
    """Compare les prédictions faites pour target_date avec la couleur réelle.
    Fix ML-1 : filtre les prédictions à horizon <= 5 jours (pertinentes)."""
    conn = get_db()
    try:
        predictions = conn.execute(
            "SELECT * FROM predictions WHERE date = ?",
            (target_date.isoformat(),)
        ).fetchall()

        if not predictions:
            logger.info(f"[Perf] Aucune prédiction pour {target_date}")
            return

        nb_stored = 0
        for pred in predictions:
            # Ne jamais évaluer les prédictions basées sur données simulées
            if pred["simulated"]:
                continue

            # Déterminer la couleur qui était réellement prédite par l'algo
            # Si confirmé, couleur_predite a été écrasée → utiliser couleur_originale
            # Si couleur_originale est NULL/vide et confirmé → on ne peut pas
            # connaître la prédiction originale → skip (évite de fausser les stats)
            couleur_pred = pred["couleur_predite"]
            if pred["confirmed"] and pred["couleur_originale"]:
                couleur_pred = pred["couleur_originale"]
            elif pred["confirmed"] and not pred["couleur_originale"]:
                continue  # couleur_originale lost — can't evaluate accurately

            # Calculer l'avance en jours
            ts = datetime.fromisoformat(pred["timestamp_prediction"])
            jours_avance = (target_date - ts.date()).days

            # Fix ML-1 : ignorer les prédictions trop anciennes (> 16 jours)
            if jours_avance < 0 or jours_avance > 16:
                continue

            correct = 1 if couleur_pred == couleur_reelle else 0

            # Écart de score : différence entre score prédit et seuil réel
            score_predit = pred["score_risque"]
            seuil_reel = _couleur_to_score(couleur_reelle)
            ecart = abs(score_predit - seuil_reel)

            conn.execute(
                """INSERT OR IGNORE INTO performance
                   (date_prediction, date_cible, jours_avance, correct,
                    couleur_predite, couleur_reelle, score_risque_predit,
                    ecart_score, contexte_meteo, timestamp_evaluation)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts.date().isoformat(), target_date.isoformat(),
                 jours_avance, correct,
                 couleur_pred, couleur_reelle,
                 score_predit, ecart,
                 pred["raison"] or "", datetime.now().isoformat()),
            )
            nb_stored += 1

        conn.commit()
        logger.info(f"[Perf] {target_date}: {nb_stored} prédictions évaluées")

        # Audit DS P2-G : alerter quand on rate un jour ROUGE
        # Un ROUGE raté est très coûteux (0.7562€/kWh). On log un WARNING
        # spécifique pour faciliter le monitoring et le post-mortem.
        if couleur_reelle == "ROUGE":
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM performance
                   WHERE date_cible = ? AND couleur_reelle = 'ROUGE'
                   AND couleur_predite != 'ROUGE'""",
                (target_date.isoformat(),)
            ).fetchone()
            missed_rouge = row["cnt"] if row else 0
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM performance
                   WHERE date_cible = ? AND couleur_reelle = 'ROUGE'""",
                (target_date.isoformat(),)
            ).fetchone()
            total_preds = row["cnt"] if row else 0
            if missed_rouge > 0:
                logger.warning(
                    f"[ROUGE RATÉ] {target_date}: {missed_rouge}/{total_preds} "
                    f"prédictions ont raté le jour ROUGE! "
                    f"Post-mortem nécessaire."
                )

    finally:
        conn.close()


def _couleur_to_score(couleur: str) -> float:
    """Score de reference aligne sur les seuils de l'algorithme."""
    return {
        "ROUGE": (Config.SEUIL_ROUGE + 100) / 2,   # 82.5
        "BLANC": (Config.SEUIL_BLANC + Config.SEUIL_ROUGE) / 2,  # 50.0
        "BLEU": Config.SEUIL_BLANC / 2,             # 17.5
    }.get(couleur, Config.SEUIL_BLANC / 2)


# ================================================================
# 2. MÉTRIQUES DE PERFORMANCE
# ================================================================

def get_accuracy_global(days: int = 30, max_horizon: int | None = None,
                        min_horizon: int | None = None) -> dict:
    """Precision globale sur les N derniers jours.
    max_horizon=1 → J-1 seulement, min_horizon=2 + max_horizon=5 → J+2 à J+5,
    None → tous les horizons."""
    conn = get_db()
    try:
        since = _enforce_start_date((date.today() - timedelta(days=days)).isoformat())
        if min_horizon is not None and max_horizon is not None:
            rows = conn.execute(
                """SELECT correct, COUNT(*) as cnt
                   FROM performance
                   WHERE date_cible >= ? AND jours_avance >= ? AND jours_avance <= ?
                   GROUP BY correct""",
                (since, min_horizon, max_horizon),
            ).fetchall()
        elif max_horizon is not None:
            rows = conn.execute(
                """SELECT correct, COUNT(*) as cnt
                   FROM performance
                   WHERE date_cible >= ? AND jours_avance <= ?
                   GROUP BY correct""",
                (since, max_horizon),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT correct, COUNT(*) as cnt
                   FROM performance WHERE date_cible >= ?
                   GROUP BY correct""",
                (since,),
            ).fetchall()

        total = sum(r["cnt"] for r in rows)
        correct = sum(r["cnt"] for r in rows if r["correct"] == 1)

        # Plage de dates réelles dans cette fenêtre
        date_range = conn.execute(
            """SELECT MIN(date_cible) as first_date, MAX(date_cible) as last_date
               FROM performance WHERE date_cible >= ?""",
            (since,),
        ).fetchone()

        return {
            "total": total,
            "correct": correct,
            "precision": round(correct / total * 100, 1) if total > 0 else 0,
            "periode_jours": days,
            "first_date": date_range["first_date"] if date_range else None,
            "last_date": date_range["last_date"] if date_range else None,
        }
    finally:
        conn.close()


def get_accuracy_combined(days: int = 30) -> dict:
    """Précision globale, J-1 et J-2→J-5 en une seule requête SQL.

    Retourne {global: {...}, j1: {...}, j2_j5: {...}, j6_j15: {...}}
    avec total, correct, precision pour chaque tranche.
    """
    conn = get_db()
    try:
        since = _enforce_start_date((date.today() - timedelta(days=days)).isoformat())
        rows = conn.execute(
            """SELECT
                 jours_avance,
                 SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) as corrects,
                 COUNT(*) as total
               FROM performance
               WHERE date_cible >= ?
               GROUP BY jours_avance""",
            (since,),
        ).fetchall()

        date_range = conn.execute(
            """SELECT MIN(date_cible) as first_date, MAX(date_cible) as last_date
               FROM performance WHERE date_cible >= ?""",
            (since,),
        ).fetchone()

        # Accumulate per bucket
        buckets = {
            "global": {"total": 0, "correct": 0},
            "j1": {"total": 0, "correct": 0},
            "j2_j5": {"total": 0, "correct": 0},
            "j6_j15": {"total": 0, "correct": 0},
        }
        for r in rows:
            h = r["jours_avance"]
            t, c = r["total"], r["corrects"] or 0
            buckets["global"]["total"] += t
            buckets["global"]["correct"] += c
            if h == 1:
                buckets["j1"]["total"] += t
                buckets["j1"]["correct"] += c
            if 2 <= h <= 5:
                buckets["j2_j5"]["total"] += t
                buckets["j2_j5"]["correct"] += c
            if 6 <= h <= 15:
                buckets["j6_j15"]["total"] += t
                buckets["j6_j15"]["correct"] += c

        result = {}
        for key, b in buckets.items():
            result[key] = {
                "total": b["total"],
                "correct": b["correct"],
                "precision": round(b["correct"] / b["total"] * 100, 1)
                if b["total"] > 0 else 0,
                "periode_jours": days,
            }
        # Only global gets date range
        result["global"]["first_date"] = date_range["first_date"] if date_range else None
        result["global"]["last_date"] = date_range["last_date"] if date_range else None
        return result
    finally:
        conn.close()


def get_accuracy_by_horizon(days: int = 60) -> list[dict]:
    """Précision par horizon de prédiction (J-1, J-2, J-3...)."""
    conn = get_db()
    try:
        since = _enforce_start_date((date.today() - timedelta(days=days)).isoformat())
        rows = conn.execute(
            """SELECT jours_avance,
                      COUNT(*) as total,
                      SUM(correct) as corrects
               FROM performance
               WHERE date_cible >= ?
               GROUP BY jours_avance
               ORDER BY jours_avance""",
            (since,)
        ).fetchall()

        return [
            {
                "horizon": f"J-{r['jours_avance']}",
                "jours_avance": r["jours_avance"],
                "total": r["total"],
                "correct": r["corrects"],
                "precision": round(r["corrects"] / r["total"] * 100, 1) if r["total"] > 0 else 0,
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_confusion_matrix(days: int = 60, since_date: str | None = None,
                         end_date: str | None = None,
                         min_horizon: int | None = None,
                         max_horizon: int | None = None,
                         pred_since_date: str | None = None,
                         pred_end_date: str | None = None) -> dict:
    """Matrice de confusion 3×3 (BLEU/BLANC/ROUGE prédit vs réel).

    Args:
        since_date: if provided, overrides the days-based calculation (filters on date_cible).
        end_date: if provided, upper bound on date_cible (exclusive).
        min_horizon: minimum jours_avance (inclusive). A1/A6: filter by horizon.
        max_horizon: maximum jours_avance (inclusive). A1/A6: filter by horizon.
        pred_since_date: filter on date_prediction >= (for version filtering).
        pred_end_date: filter on date_prediction < (for version filtering).
    """
    conn = get_db()
    try:
        since = since_date or _enforce_start_date(
            (date.today() - timedelta(days=days)).isoformat())
        conditions = ["date_cible >= ?"]
        params: list = [since]
        if end_date:
            conditions.append("date_cible < ?")
            params.append(end_date)
        if pred_since_date:
            conditions.append("date_prediction >= ?")
            params.append(pred_since_date)
        if pred_end_date:
            conditions.append("date_prediction < ?")
            params.append(pred_end_date)
        if min_horizon is not None:
            conditions.append("jours_avance >= ?")
            params.append(min_horizon)
        if max_horizon is not None:
            conditions.append("jours_avance <= ?")
            params.append(max_horizon)

        rows = conn.execute(
            f"""SELECT couleur_predite, couleur_reelle, COUNT(*) as cnt
               FROM performance
               WHERE {' AND '.join(conditions)}
               GROUP BY couleur_predite, couleur_reelle""",
            params,
        ).fetchall()

        matrix = {
            c_pred: {c_real: 0 for c_real in ("BLEU", "BLANC", "ROUGE")}
            for c_pred in ("BLEU", "BLANC", "ROUGE")
        }
        for r in rows:
            if r["couleur_predite"] in matrix and r["couleur_reelle"] in matrix[r["couleur_predite"]]:
                matrix[r["couleur_predite"]][r["couleur_reelle"]] = r["cnt"]

        return matrix
    finally:
        conn.close()


def get_precision_recall_f1(days: int = 60, since_date: str | None = None,
                            end_date: str | None = None,
                            min_horizon: int | None = None,
                            max_horizon: int | None = None,
                            pred_since_date: str | None = None,
                            pred_end_date: str | None = None) -> dict:
    """Fix ML-4 : Precision, Recall et F1 par classe sur les N derniers jours."""
    matrix = get_confusion_matrix(days, since_date=since_date,
                                  end_date=end_date,
                                  min_horizon=min_horizon,
                                  max_horizon=max_horizon,
                                  pred_since_date=pred_since_date,
                                  pred_end_date=pred_end_date)
    couleurs = ["BLEU", "BLANC", "ROUGE"]
    metrics = {}

    for couleur in couleurs:
        # TP = matrice[couleur][couleur] (prédit = couleur ET réel = couleur)
        tp = matrix[couleur][couleur]
        # FP = somme des prédits couleur mais réels différents
        fp = sum(matrix[couleur][c] for c in couleurs if c != couleur)
        # FN = somme des réels couleur mais prédits différents
        fn = sum(matrix[c][couleur] for c in couleurs if c != couleur)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        metrics[couleur] = {
            "precision": round(precision * 100, 1),
            "recall": round(recall * 100, 1),
            "f1": round(f1 * 100, 1),
            "support": tp + fn,
        }

    # Weighted F1 (pondéré par le support)
    total_support = sum(m["support"] for m in metrics.values())
    weighted_f1 = 0
    if total_support > 0:
        weighted_f1 = sum(
            m["f1"] * m["support"] / total_support
            for m in metrics.values()
        )

    metrics["weighted_f1"] = round(weighted_f1, 1)
    return metrics


def get_recent_errors(limit: int = 5, days: int | None = None) -> list[dict]:
    """Top N erreurs récentes avec contexte météo, filtré par période."""
    conn = get_db()
    try:
        if days is not None:
            since = _enforce_start_date((date.today() - timedelta(days=days)).isoformat())
            rows = conn.execute(
                """SELECT date_cible, couleur_predite, couleur_reelle,
                          score_risque_predit, ecart_score, contexte_meteo,
                          jours_avance, timestamp_evaluation
                   FROM performance
                   WHERE correct = 0 AND date_cible >= ?
                   ORDER BY date_cible DESC, jours_avance
                   LIMIT ?""",
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT date_cible, couleur_predite, couleur_reelle,
                          score_risque_predit, ecart_score, contexte_meteo,
                          jours_avance, timestamp_evaluation
                   FROM performance
                   WHERE correct = 0
                   ORDER BY date_cible DESC, jours_avance
                   LIMIT ?""",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_rouge_recall_by_horizon(days: int = 90) -> dict:
    """Audit DS P2-H : recall ROUGE par horizon (J-1..J-5).

    Mesure indépendante du recall ROUGE pour chaque horizon de prédiction.
    Permet d'identifier si certains horizons sont particulièrement faibles
    et de prioriser les améliorations (ex: modèles par horizon).
    """
    conn = get_db()
    try:
        since = _enforce_start_date((date.today() - timedelta(days=days)).isoformat())
        result = {}
        for h in range(1, 6):
            row = conn.execute(
                """SELECT
                     SUM(CASE WHEN couleur_predite = 'ROUGE' AND couleur_reelle = 'ROUGE'
                         THEN 1 ELSE 0 END) as tp,
                     SUM(CASE WHEN couleur_reelle = 'ROUGE' THEN 1 ELSE 0 END) as total_rouge,
                     SUM(CASE WHEN couleur_predite = 'ROUGE' AND couleur_reelle != 'ROUGE'
                         THEN 1 ELSE 0 END) as fp
                   FROM performance
                   WHERE date_cible >= ? AND jours_avance = ?""",
                (since, h),
            ).fetchone()
            tp = row["tp"] or 0
            total = row["total_rouge"] or 0
            fp = row["fp"] or 0
            recall = round(tp / total * 100, 1) if total > 0 else None
            precision = round(tp / (tp + fp) * 100, 1) if (tp + fp) > 0 else None
            result[f"J-{h}"] = {
                "recall": recall,
                "precision": precision,
                "rouge_total": total,
                "rouge_caught": tp,
                "false_alarms": fp,
            }
        return result
    finally:
        conn.close()


def get_budget_season() -> dict:
    """Retourne l'état du budget saison pour le dashboard admin."""
    try:
        from tempo_client import (
            get_remaining_days, count_used_days, days_left_in_season,
            get_season_dates,
        )
        remaining = get_remaining_days()
        used = count_used_days()
        d_left = days_left_in_season()
        start, end = get_season_dates()

        # A7: Count future predictions weighted by confidence
        conn = get_db()
        try:
            today = date.today().isoformat()
            rows = conn.execute(
                """SELECT couleur_predite, COUNT(DISTINCT date) as cnt,
                          AVG(CASE WHEN probabilite_rouge > 0 OR probabilite_blanc > 0
                              OR probabilite_bleu > 0
                              THEN CASE
                                  WHEN probabilite_rouge >= probabilite_blanc
                                       AND probabilite_rouge >= probabilite_bleu
                                      THEN probabilite_rouge
                                  WHEN probabilite_blanc >= probabilite_bleu
                                      THEN probabilite_blanc
                                  ELSE probabilite_bleu
                              END
                              ELSE NULL END) as avg_confidence
                   FROM predictions
                   WHERE date > ? AND confirmed = 0
                   GROUP BY couleur_predite""",
                (today,),
            ).fetchall()
            predicted_future = {r["couleur_predite"]: r["cnt"] for r in rows}
            predicted_confidence = {
                r["couleur_predite"]: round(r["avg_confidence"])
                if r["avg_confidence"] else None
                for r in rows
            }
        finally:
            conn.close()

        return {
            "rouge_used": used.get("ROUGE", 0),
            "rouge_remaining": remaining.get("ROUGE", 0),
            "rouge_total": Config.JOURS_ROUGES_TOTAL,
            "rouge_predicted": predicted_future.get("ROUGE", 0),
            "rouge_predicted_confidence": predicted_confidence.get("ROUGE"),
            "blanc_used": used.get("BLANC", 0),
            "blanc_remaining": remaining.get("BLANC", 0),
            "blanc_total": Config.JOURS_BLANCS_TOTAL,
            "blanc_predicted": predicted_future.get("BLANC", 0),
            "blanc_predicted_confidence": predicted_confidence.get("BLANC"),
            "days_left_season": d_left,
            "season_start": start.isoformat(),
            "season_end": end.isoformat(),
        }
    except Exception as e:
        logger.warning(f"[Budget] Erreur calcul budget saison: {e}")
        return {}


def get_accuracy_trend(days: int = 14) -> list[dict]:
    """Précision quotidienne sur les N derniers jours (pour graphique d'évolution)."""
    conn = get_db()
    try:
        since = _enforce_start_date((date.today() - timedelta(days=days)).isoformat())
        rows = conn.execute(
            """SELECT date_cible, COUNT(*) as total, SUM(correct) as corrects
               FROM performance
               WHERE date_cible >= ?
               GROUP BY date_cible
               ORDER BY date_cible""",
            (since,),
        ).fetchall()
        return [
            {
                "date": r["date_cible"],
                "total": r["total"],
                "correct": r["corrects"],
                "precision": round(r["corrects"] / r["total"] * 100, 1)
                if r["total"] > 0 else 0,
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_period_comparison(days: int = 7, pivot_date: str | None = None,
                          min_horizon: int | None = None,
                          max_horizon: int | None = None) -> dict:
    """Compare current period vs previous period.

    A4: If pivot_date is provided, compares predictions MADE after pivot vs
    predictions MADE before pivot (same number of days).
    Uses date_prediction (not date_cible) so version comparison is correct.
    Otherwise falls back to rolling N-day comparison on date_cible.

    min_horizon/max_horizon: optionally restrict to a horizon range (e.g. 2-5).
    """
    conn = get_db()
    try:
        # Build optional horizon filter clause
        horizon_clause = ""
        horizon_params: list = []
        if min_horizon is not None:
            horizon_clause += " AND jours_avance >= ?"
            horizon_params.append(min_horizon)
        if max_horizon is not None:
            horizon_clause += " AND jours_avance <= ?"
            horizon_params.append(max_horizon)

        if pivot_date:
            # A4: Compare predictions MADE after vs before the last tool update
            pivot = date.fromisoformat(pivot_date)
            days_after = max(1, (date.today() - pivot).days)
            days_before = days_after  # same window size for fair comparison
            now_start = _enforce_start_date(pivot_date)
            prev_start = _enforce_start_date(
                (pivot - timedelta(days=days_before)).isoformat())
            prev_end = pivot_date

            current = conn.execute(
                f"""SELECT COUNT(*) as total, SUM(correct) as corrects
                   FROM performance WHERE date_prediction >= ?{horizon_clause}""",
                [now_start] + horizon_params,
            ).fetchone()
            previous = conn.execute(
                f"""SELECT COUNT(*) as total, SUM(correct) as corrects
                   FROM performance WHERE date_prediction >= ? AND date_prediction < ?{horizon_clause}""",
                [prev_start, prev_end] + horizon_params,
            ).fetchone()
        else:
            now_start = (date.today() - timedelta(days=days)).isoformat()
            prev_start = (date.today() - timedelta(days=days * 2)).isoformat()
            prev_end = now_start

            current = conn.execute(
                f"""SELECT COUNT(*) as total, SUM(correct) as corrects
                   FROM performance WHERE date_cible >= ?{horizon_clause}""",
                [now_start] + horizon_params,
            ).fetchone()
            previous = conn.execute(
                f"""SELECT COUNT(*) as total, SUM(correct) as corrects
                   FROM performance WHERE date_cible >= ? AND date_cible < ?{horizon_clause}""",
                [prev_start, prev_end] + horizon_params,
            ).fetchone()

        c_total = current["total"] or 0
        c_correct = current["corrects"] or 0
        p_total = previous["total"] or 0
        p_correct = previous["corrects"] or 0

        c_pct = round(c_correct / c_total * 100, 1) if c_total > 0 else None
        p_pct = round(p_correct / p_total * 100, 1) if p_total > 0 else None

        delta = None
        if c_pct is not None and p_pct is not None:
            delta = round(c_pct - p_pct, 1)

        return {
            "current": {"precision": c_pct, "total": c_total},
            "previous": {"precision": p_pct, "total": p_total},
            "delta": delta,
            "pivot_date": pivot_date,
            "label": f"depuis {pivot_date}" if pivot_date else f"{days}j glissants",
        }
    finally:
        conn.close()


def get_diagnostic(days: int = 30, since_date: str | None = None,
                   end_date: str | None = None,
                   min_horizon: int | None = None,
                   max_horizon: int | None = None,
                   pred_since_date: str | None = None,
                   pred_end_date: str | None = None) -> dict:
    """Diagnostic synthétique : identifie les causes principales d'erreur.

    Retourne un verdict global + les top problèmes + recommandations.
    Args:
        since_date: if provided, overrides the days-based calculation.
        end_date: upper bound on date_cible (exclusive). A5: per-version.
        min_horizon/max_horizon: A1/A6: restrict to specific horizon range.
        pred_since_date/pred_end_date: filter on date_prediction (for version filtering).
    """
    cm = get_confusion_matrix(days, since_date=since_date, end_date=end_date,
                              min_horizon=min_horizon, max_horizon=max_horizon,
                              pred_since_date=pred_since_date, pred_end_date=pred_end_date)
    prf = get_precision_recall_f1(days, since_date=since_date, end_date=end_date,
                                  min_horizon=min_horizon, max_horizon=max_horizon,
                                  pred_since_date=pred_since_date, pred_end_date=pred_end_date)
    # A3/B6: Compute accuracy directly from confusion matrix (consistent scope)
    couleurs = ["BLEU", "BLANC", "ROUGE"]
    total_all = sum(cm.get(p, {}).get(a, 0) for p in couleurs for a in couleurs)
    correct_all = sum(cm.get(c, {}).get(c, 0) for c in couleurs)
    g = {"precision": round(correct_all / total_all * 100, 1) if total_all > 0 else 0,
         "total": total_all}

    # Calculer les confusions dominantes
    # Seuil adaptatif : count >= 2 normalement, mais count >= 1 quand peu de données
    # (< 10 évaluations) pour ne pas masquer les erreurs d'une version récente
    min_count = 1 if total_all < 10 else 2
    problems = []
    total_errors = 0
    for predicted in ("BLEU", "BLANC", "ROUGE"):
        for actual in ("BLEU", "BLANC", "ROUGE"):
            if predicted != actual:
                count = cm.get(predicted, {}).get(actual, 0)
                total_errors += count
                if count >= min_count:
                    problems.append({
                        "predicted": predicted,
                        "actual": actual,
                        "count": count,
                    })

    problems.sort(key=lambda x: x["count"], reverse=True)

    # Déterminer le biais dominant
    over_pred = sum(p["count"] for p in problems
                    if _color_rank(p["predicted"]) > _color_rank(p["actual"]))
    under_pred = sum(p["count"] for p in problems
                     if _color_rank(p["predicted"]) < _color_rank(p["actual"]))

    if total_errors == 0:
        bias = "aucun"
    elif over_pred > under_pred * 1.5:
        bias = "sur-prediction"
    elif under_pred > over_pred * 1.5:
        bias = "sous-prediction"
    else:
        bias = "mixte"

    # Verdict
    precision = g.get("precision", 0)
    rouge_prf = prf.get("ROUGE", {})
    rouge_support = rouge_prf.get("support", 0)
    rouge_recall = rouge_prf.get("recall", 0)
    # If no actual ROUGE days exist, recall is not meaningful (not a failure)
    has_rouge_days = rouge_support > 0

    if not has_rouge_days:
        # No ROUGE days to detect — judge only on precision
        if precision >= 75:
            verdict = "bon"
        elif precision >= 55:
            verdict = "moyen"
        else:
            verdict = "insuffisant"
    elif precision >= 80 and rouge_recall >= 60:
        verdict = "bon"
    elif precision >= 65 or rouge_recall >= 40:
        verdict = "moyen"
    else:
        verdict = "insuffisant"

    # Recommandations (C7: accents corrects)
    recs = []
    top_confusions = problems[:3]
    for p in top_confusions:
        if p["predicted"] == "BLANC" and p["actual"] == "BLEU":
            recs.append(
                f"{p['count']}x BLANC prédit au lieu de BLEU — le seuil BLANC "
                f"est probablement trop bas, ou le score budget pousse trop."
            )
        elif p["predicted"] == "ROUGE" and p["actual"] in ("BLEU", "BLANC"):
            recs.append(
                f"{p['count']}x fausse alarme ROUGE (réel={p['actual']}) — "
                f"le seuil ROUGE est trop sensible ou la température est "
                f"surestimée."
            )
        elif p["actual"] == "ROUGE" and p["predicted"] != "ROUGE":
            recs.append(
                f"{p['count']}x ROUGE manqué (prédit {p['predicted']}) — "
                f"critique pour les abonnés. Vérifier le recall ROUGE."
            )
        else:
            recs.append(
                f"{p['count']}x {p['predicted']} prédit au lieu de "
                f"{p['actual']}."
            )

    if bias == "sur-prediction":
        recs.append(
            "Tendance globale : sur-prédiction de sévérité. "
            "L'algo prédit trop de jours ROUGE/BLANC."
        )

    # C4: Build actionable summary sentence
    if not has_rouge_days:
        # No ROUGE days in period — cannot judge ROUGE detection
        if total_errors == 0:
            summary = (
                f"L'outil fonctionne bien : {precision}% de précision. "
                f"Aucun jour ROUGE dans la période — détection ROUGE non évaluable."
            )
        else:
            summary = (
                f"Précision : {precision}%. "
                f"Aucun jour ROUGE dans la période — détection ROUGE non évaluable."
            )
    elif verdict == "bon":
        summary = (
            f"L'outil fonctionne bien : {precision}% de précision globale "
            f"et {rouge_recall}% de détection ROUGE."
        )
    elif rouge_recall < 40 and precision >= 65:
        summary = (
            f"Précision correcte ({precision}%) mais détection ROUGE insuffisante "
            f"({rouge_recall}%). Priorité : abaisser le seuil ROUGE pour capter "
            f"plus de jours rouges, quitte à augmenter les fausses alertes."
        )
    elif precision < 65 and rouge_recall >= 40:
        summary = (
            f"Détection ROUGE acceptable ({rouge_recall}%) mais précision globale "
            f"faible ({precision}%). Trop de fausses alertes BLANC ou ROUGE. "
            f"Priorité : remonter les seuils pour réduire les faux positifs."
        )
    else:
        summary = (
            f"Performance insuffisante : {precision}% de précision, "
            f"{rouge_recall}% de détection ROUGE. "
            f"Revoir la calibration des seuils et la qualité des données météo."
        )

    # Add top problem as actionable focus
    action = None
    if top_confusions:
        p = top_confusions[0]
        if p["actual"] == "ROUGE" and p["predicted"] != "ROUGE":
            action = (
                f"Action prioritaire : {p['count']} jour(s) ROUGE manqué(s) — "
                f"chaque ROUGE raté coûte 0.76€/kWh aux abonnés."
            )
        elif p["predicted"] == "ROUGE" and p["actual"] != "ROUGE":
            action = (
                f"Point d'attention : {p['count']} fausse(s) alerte(s) ROUGE "
                f"— crédibilité en jeu."
            )
        elif p["predicted"] == "BLANC" and p["actual"] == "BLEU":
            action = (
                f"Point d'attention : {p['count']}x BLANC prédit au lieu de BLEU "
                f"— le seuil BLANC est peut-être trop bas."
            )

    return {
        "verdict": verdict,
        "precision": precision,
        "rouge_recall": rouge_recall if has_rouge_days else None,
        "has_rouge_days": has_rouge_days,
        "bias": bias,
        "over_predictions": over_pred,
        "under_predictions": under_pred,
        "total_errors": total_errors,
        "top_confusions": top_confusions[:5],
        "recommendations": recs,
        "summary": summary,
        "action": action,
    }


def get_color_recall_by_horizon(color: str, days: int = 90,
                                max_horizon: int = 10,
                                since_date: str | None = None,
                                end_date: str | None = None,
                                pred_since_date: str | None = None,
                                pred_end_date: str | None = None) -> dict:
    """Recall/precision for a specific color by horizon J-1..J-N.

    B2: Single GROUP BY query instead of N individual queries.
    A3: Extended from J-5 to J-10 for consistency with recap table.
    A5: end_date support for per-version filtering.
    pred_since_date/pred_end_date: filter on date_prediction (for version filtering).
    """
    conn = get_db()
    try:
        since = since_date or _enforce_start_date(
            (date.today() - timedelta(days=days)).isoformat())
        conditions = ["date_cible >= ?", "jours_avance >= 1", "jours_avance <= ?"]
        params: list = [since, max_horizon]
        if end_date:
            conditions.insert(1, "date_cible < ?")
            params.insert(1, end_date)
        if pred_since_date:
            conditions.append("date_prediction >= ?")
            params.append(pred_since_date)
        if pred_end_date:
            conditions.append("date_prediction < ?")
            params.append(pred_end_date)

        rows = conn.execute(
            f"""SELECT jours_avance,
                     SUM(CASE WHEN couleur_predite = ? AND couleur_reelle = ?
                         THEN 1 ELSE 0 END) as tp,
                     SUM(CASE WHEN couleur_reelle = ? THEN 1 ELSE 0 END) as total_actual,
                     SUM(CASE WHEN couleur_predite = ? AND couleur_reelle != ?
                         THEN 1 ELSE 0 END) as fp
                   FROM performance
                   WHERE {' AND '.join(conditions)}
                   GROUP BY jours_avance
                   ORDER BY jours_avance""",
            [color, color, color, color, color] + params,
        ).fetchall()

        # Build result dict with all horizons (empty ones get None)
        horizon_data = {}
        for r in rows:
            h = r["jours_avance"]
            horizon_data[h] = r

        result = {}
        for h in range(1, max_horizon + 1):
            r = horizon_data.get(h)
            if r:
                tp = r["tp"] or 0
                total = r["total_actual"] or 0
                fp = r["fp"] or 0
            else:
                tp, total, fp = 0, 0, 0
            recall = round(tp / total * 100, 1) if total > 0 else None
            precision = round(tp / (tp + fp) * 100, 1) if (tp + fp) > 0 else None
            result[f"J-{h}"] = {
                "recall": recall,
                "precision": precision,
                "total_actual": total,
                "caught": tp,
                "false_alarms": fp,
            }
        return result
    finally:
        conn.close()


def get_monthly_performance(season: str = "2025-2026") -> list[dict]:
    """Accuracy per horizon (J-1 to J-15) per month."""
    conn = get_db()
    try:
        season_start, season_end = parse_season(season)
        start_date = _enforce_start_date(season_start.isoformat())
        end_date = min(season_end.isoformat(), date.today().isoformat())

        rows = conn.execute(
            """SELECT
                   SUBSTR(date_cible, 1, 7) as month_key,
                   jours_avance,
                   COUNT(*) as total,
                   SUM(correct) as corrects
               FROM performance
               WHERE date_cible >= ? AND date_cible <= ?
               GROUP BY month_key, jours_avance
               ORDER BY month_key, jours_avance""",
            (start_date, end_date),
        ).fetchall()

        month_names = {
            "01": "Janvier", "02": "Février", "03": "Mars", "04": "Avril",
            "05": "Mai", "06": "Juin", "07": "Juillet", "08": "Août",
            "09": "Septembre", "10": "Octobre", "11": "Novembre", "12": "Décembre",
        }

        # Build per-month per-horizon accuracy
        months_data: dict[str, dict] = {}
        months_totals: dict[str, dict] = {}
        for r in rows:
            mk = r["month_key"]
            if mk not in months_data:
                months_data[mk] = {}
                months_totals[mk] = {"total": 0, "correct": 0}
            h = r["jours_avance"]
            total = r["total"]
            corrects = r["corrects"] or 0
            if 1 <= h <= 15:
                months_data[mk][h] = {
                    "total": total,
                    "precision": round(corrects / total * 100, 1) if total > 0 else None,
                }
            months_totals[mk]["total"] += total
            months_totals[mk]["correct"] += corrects

        result = []
        for mk in sorted(months_data.keys()):
            mm = mk.split("-")[1]
            mt = months_totals[mk]
            entry = {
                "month_key": mk,
                "month_label": month_names.get(mm, mm),
                "total": mt["total"],
                "accuracy": round(mt["correct"] / mt["total"] * 100, 1) if mt["total"] > 0 else 0,
                "horizons": {},
            }
            for h in range(1, 16):
                if h in months_data[mk]:
                    entry["horizons"][f"J-{h}"] = months_data[mk][h]
            result.append(entry)

        return result
    finally:
        conn.close()


def get_version_performance() -> list[dict]:
    """Accuracy per horizon (J-1 to J-15) per tool version.

    Uses _get_all_version_dates() to segment performance data by version
    (code changes + weight recalculations).
    Each version's data includes predictions MADE during that version's period
    (filters on date_prediction, not date_cible) — so only predictions actually
    produced with that version's code are attributed to it.
    """
    tool_dates = _get_all_version_dates()
    if not tool_dates:
        return []

    sorted_dates = sorted(tool_dates.keys())
    conn = get_db()
    try:
        result = []
        for i, vdate in enumerate(sorted_dates):
            start = _enforce_start_date(vdate)
            end = sorted_dates[i + 1] if i + 1 < len(sorted_dates) else (
                date.today() + timedelta(days=1)).isoformat()
            label = tool_dates[vdate]

            rows = conn.execute(
                """SELECT
                       jours_avance,
                       COUNT(*) as total,
                       SUM(correct) as corrects
                   FROM performance
                   WHERE date_prediction >= ? AND date_prediction < ?
                   GROUP BY jours_avance
                   ORDER BY jours_avance""",
                (start, end),
            ).fetchall()

            if not rows:
                continue

            total_all = sum(r["total"] for r in rows)
            correct_all = sum((r["corrects"] or 0) for r in rows)

            horizons_data: dict[int, dict] = {}
            for r in rows:
                h = r["jours_avance"]
                t = r["total"]
                c = r["corrects"] or 0
                if 1 <= h <= 15:
                    horizons_data[h] = {
                        "total": t,
                        "precision": round(c / t * 100, 1) if t > 0 else None,
                    }

            # D8: Number of calendar days with data for this version
            start_d = date.fromisoformat(start)
            end_d = date.fromisoformat(end) if end != (date.today() + timedelta(days=1)).isoformat() else date.today()
            days_count = max(1, (end_d - start_d).days)

            # D12: Count distinct dates with evaluations (coverage)
            distinct_dates_row = conn.execute(
                """SELECT COUNT(DISTINCT date_cible) as cnt
                   FROM performance WHERE date_prediction >= ? AND date_prediction < ?""",
                (start, end),
            ).fetchone()
            dates_with_data = distinct_dates_row["cnt"] if distinct_dates_row else 0

            entry = {
                "version_date": vdate,
                "version_label": label,
                "total": total_all,
                "accuracy": round(correct_all / total_all * 100, 1) if total_all > 0 else 0,
                "days_count": days_count,
                "dates_with_data": dates_with_data,
                "horizons": {},
            }
            for h in range(1, 16):
                if h in horizons_data:
                    entry["horizons"][f"J-{h}"] = horizons_data[h]
            result.append(entry)

        # Latest version first
        result.reverse()
        return result
    finally:
        conn.close()


def parse_season(season: str) -> tuple[date, date]:
    """Parse '2025-2026' into (date(2025,9,1), date(2026,8,31))."""
    parts = season.split("-")
    start_year = int(parts[0])
    return date(start_year, 9, 1), date(start_year + 1, 8, 31)


def get_available_seasons() -> list[str]:
    """Return seasons that have non-simulated prediction data.

    Respects PREDICTION_START_DATE: ignores predictions before this date
    to avoid listing seasons with no real prediction data.
    """
    start = getattr(Config, 'PREDICTION_START_DATE', None)
    conn = get_db()
    try:
        if start:
            rows = conn.execute(
                "SELECT DISTINCT date FROM predictions WHERE simulated = 0 AND date >= ? ORDER BY date",
                (start,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT date FROM predictions WHERE simulated = 0 ORDER BY date"
            ).fetchall()
        seasons = set()
        for r in rows:
            d = date.fromisoformat(r["date"])
            if d.month >= 9:
                seasons.add(f"{d.year}-{d.year + 1}")
            else:
                seasons.add(f"{d.year - 1}-{d.year}")
        return sorted(seasons, reverse=True)
    finally:
        conn.close()


def _build_error_diagnostic(predicted: str, actual: str,
                            score: float | None, raison: str | None) -> str:
    """Build a concise human-readable diagnostic for a wrong prediction."""
    if predicted == actual:
        return ""
    parts = []
    # Error type
    if actual == "ROUGE" and predicted != "ROUGE":
        parts.append(f"ROUGE manqué (prédit {predicted})")
    elif predicted == "ROUGE" and actual != "ROUGE":
        parts.append(f"Fausse alerte ROUGE (réel {actual})")
    elif predicted == "BLANC" and actual == "BLEU":
        parts.append("Sur-estimation BLANC→BLEU")
    elif predicted == "BLEU" and actual == "BLANC":
        parts.append("Sous-estimation BLEU→BLANC")
    else:
        parts.append(f"{predicted}→{actual}")
    if score is not None:
        parts.append(f"score {score}")
    # Extract key info from raison (first 2 items)
    if raison:
        items = [r.strip() for r in raison.split("·") if r.strip()]
        if items:
            parts.append(items[0][:50])
    return " — ".join(parts)


def get_daily_recap(season: str = "2025-2026") -> list[dict]:
    """Récapitulatif jour par jour avec 15 horizons de prévision.

    Horizons en format J-N (J-1 = veille, J-15 = 15 jours avant).
    Inclut l'évolution de la météo prévue à chaque horizon,
    le premier horizon correct, et un diagnostic pour les erreurs.
    """
    conn = get_db()
    try:
        season_start, season_end = parse_season(season)
        today = date.today()
        end_date = min(season_end, today + timedelta(days=15))

        since = _enforce_start_date(season_start.isoformat())
        until = end_date.isoformat()

        # 1) All non-simulated predictions in season (C8: include probabilities)
        pred_rows = conn.execute(
            """SELECT date, horizon, couleur_predite, couleur_originale,
                      score_risque, confirmed, raison,
                      probabilite_bleu, probabilite_blanc, probabilite_rouge
               FROM predictions
               WHERE date >= ? AND date <= ? AND simulated = 0
               ORDER BY date, timestamp_prediction""",
            (since, until),
        ).fetchall()

        # 2) Actuals
        actual_rows = conn.execute(
            "SELECT date, couleur_reelle FROM actuals WHERE date >= ? AND date <= ? AND synthetic = 0",
            (since, until),
        ).fetchall()
        actuals_map = {r["date"]: r["couleur_reelle"] for r in actual_rows}

        # 3) Weather forecast evolution from weather_forecast_log
        weather_log = conn.execute(
            """SELECT target_date, horizon_days, temp_moy
               FROM weather_forecast_log
               WHERE target_date >= ? AND target_date <= ?
               ORDER BY target_date, horizon_days""",
            (since, until),
        ).fetchall()
        # {target_date: {horizon_days: temp_moy}}
        weather_evo = {}
        for r in weather_log:
            td = r["target_date"]
            if td not in weather_evo:
                weather_evo[td] = {}
            weather_evo[td][r["horizon_days"]] = r["temp_moy"]

        # 4) Observed weather (J-0 from weather_cache, latest per date)
        weather_obs = conn.execute(
            """SELECT date, temp_moy, humidity, wind_speed
               FROM weather_cache WHERE date >= ? AND date <= ?
               ORDER BY fetched_at DESC""",
            (since, until),
        ).fetchall()
        weather_obs_map = {}
        for r in weather_obs:
            if r["date"] not in weather_obs_map:
                weather_obs_map[r["date"]] = {
                    "temp_moy": r["temp_moy"],
                    "humidity": r["humidity"],
                    "wind_speed": r["wind_speed"],
                }

        # 5) Build per-date predictions structure
        dates_data = {}
        dates_raison = {}  # raison from closest available prediction
        for r in pred_rows:
            dt = r["date"]
            if dt not in dates_data:
                dates_data[dt] = {}

            # Determine the originally predicted color:
            # - couleur_originale set (truthy) → use it (saved before EDF confirmation)
            # - couleur_originale empty/NULL → fall back to couleur_predite
            #   (for confirmed rows this is the EDF color, which is acceptable
            #    since we still want to show the dot — better than hiding it)
            couleur = r["couleur_originale"] if r["couleur_originale"] else r["couleur_predite"]
            horizon = r["horizon"]  # DB format: J-1, J-2, ... J-15

            actual = actuals_map.get(dt)
            correct = None
            if actual:
                correct = couleur == actual

            score = None
            if r["score_risque"] and r["score_risque"] > 0:
                score = round(r["score_risque"], 1)

            # Get weather forecast temp for this horizon from weather_forecast_log
            temp_prevue = None
            if horizon and horizon.startswith("J-"):
                try:
                    h_num = int(horizon[2:])
                    if dt in weather_evo:
                        temp_prevue = weather_evo[dt].get(h_num)
                        if temp_prevue is not None:
                            temp_prevue = round(temp_prevue, 1)
                except ValueError:
                    pass

            # C8: include max probability as confidence indicator
            prob_values = [
                r["probabilite_bleu"] or 0,
                r["probabilite_blanc"] or 0,
                r["probabilite_rouge"] or 0,
            ]
            confidence = round(max(prob_values)) if any(p > 0 for p in prob_values) else None

            dates_data[dt][horizon] = {
                "couleur": couleur,
                "score": score,
                "correct": correct,
                "temp_prevue": temp_prevue,
                "confidence": confidence,
            }

            # Keep raison from J-1 (or lowest horizon) for diagnostic
            if r["raison"] and (dt not in dates_raison or horizon == "J-1"):
                dates_raison[dt] = (r["raison"], couleur, score)

        # 6) Assemble — only dates with predictions (skip backfill-only dates)
        # Use all versions (code + weight recalibrations) for tool_update labels
        all_versions = _get_all_version_dates()
        tool_updates = all_versions
        version_dates_sorted = sorted(all_versions.keys()) if all_versions else []

        result = []
        for dt in sorted(dates_data.keys(), reverse=True):
            preds = dates_data[dt]
            actual = actuals_map.get(dt)

            # Consecutive correct horizons from J-1 backwards (anticipation)
            consec_correct = None
            if actual:
                count = 0
                for n in range(1, 16):
                    key = f"J-{n}"
                    if key in preds and preds[key].get("correct") is True:
                        count += 1
                    else:
                        break
                consec_correct = count if count > 0 else None

            # A4: Diagnostic for J-1 to J-5 errors
            # Only consider horizons whose prediction was made under the
            # version active at confirmation time (= version active on dt).
            # Predictions from before that version are irrelevant.
            diagnostic = None
            # D5: Temperature deviation info for error context
            temp_deviation = None
            # Find version active on confirmation date
            confirm_version = None
            for vd in version_dates_sorted:
                if vd <= dt:
                    confirm_version = vd
                else:
                    break
            if actual:
                # Check J-1 first (most important), then J-2→J-5
                for h in range(1, 6):
                    # Skip horizons where prediction was made before the
                    # version that was active at confirmation
                    if confirm_version:
                        pred_date = (date.fromisoformat(dt) - timedelta(days=h)).isoformat()
                        if pred_date < confirm_version:
                            continue
                    jh = preds.get(f"J-{h}")
                    if jh and jh.get("correct") is False:
                        raison_text = dates_raison.get(dt, (None,))[0]
                        diag = _build_error_diagnostic(
                            jh["couleur"], actual, jh.get("score"), raison_text
                        )
                        # D5: Add temperature deviation context
                        obs_w = weather_obs_map.get(dt)
                        if obs_w and obs_w.get("temp_moy") is not None and jh.get("temp_prevue") is not None:
                            delta_t = round(obs_w["temp_moy"] - jh["temp_prevue"], 1)
                            sign = "+" if delta_t > 0 else ""
                            diag += f" · ΔT={sign}{delta_t}°"
                            temp_deviation = delta_t
                        if h == 1:
                            diagnostic = diag
                        else:
                            diagnostic = f"J-{h}: {diag}"
                        break  # Show first error (closest horizon)
                # A8: If J-1 correct but J-2→J-5 had errors, show as WARNING
                j1 = preds.get("J-1")
                j1_valid = True
                if confirm_version:
                    j1_pred = (date.fromisoformat(dt) - timedelta(days=1)).isoformat()
                    j1_valid = j1_pred >= confirm_version
                if j1 and j1.get("correct") is True and diagnostic is None and j1_valid:
                    wrong_horizons = []
                    for h in range(2, 6):
                        if confirm_version:
                            pred_dt = (date.fromisoformat(dt) - timedelta(days=h)).isoformat()
                            if pred_dt < confirm_version:
                                continue
                        if preds.get(f"J-{h}") and preds[f"J-{h}"].get("correct") is False:
                            wrong_horizons.append(f"J-{h}")
                    if wrong_horizons:
                        diagnostic = f"⚠ Rattrapé J-1 (erreur {', '.join(wrong_horizons)})"

            # A11: consec_correct — for past days without actual yet, mark as "pending"
            actual_status = "confirmed" if actual else (
                "pending" if dt <= today.isoformat() else "future")

            result.append({
                "date": dt,
                "actual": actual,
                "actual_status": actual_status,
                "predictions": preds,
                "weather_observed": weather_obs_map.get(dt),
                "consec_correct": consec_correct,
                "diagnostic": diagnostic,
                "temp_deviation": temp_deviation,
                "tool_update": tool_updates.get(dt),
            })

        return result

    finally:
        conn.close()


def get_weather_reliability(days: int = 90) -> dict:
    """D6: Measure weather forecast accuracy by horizon.

    Compares forecast temperature at each horizon vs J-0 observation.
    Returns avg absolute error and bias per horizon.
    """
    conn = get_db()
    try:
        since = _enforce_start_date((date.today() - timedelta(days=days)).isoformat())
        rows = conn.execute(
            """SELECT wf.horizon_days,
                      AVG(ABS(wf.temp_moy - wc.temp_moy)) as avg_abs_error,
                      AVG(wf.temp_moy - wc.temp_moy) as avg_bias,
                      COUNT(*) as cnt
               FROM weather_forecast_log wf
               JOIN (SELECT wc1.date, wc1.temp_moy FROM weather_cache wc1
                     WHERE wc1.temp_moy IS NOT NULL
                       AND wc1.fetched_at = (SELECT MAX(wc2.fetched_at)
                                              FROM weather_cache wc2
                                              WHERE wc2.date = wc1.date)) wc
                 ON wf.target_date = wc.date
               WHERE wf.target_date >= ?
                 AND wf.temp_moy IS NOT NULL
                 AND wf.horizon_days BETWEEN 1 AND 15
               GROUP BY wf.horizon_days
               ORDER BY wf.horizon_days""",
            (since,),
        ).fetchall()

        result = {}
        for r in rows:
            h = r["horizon_days"]
            result[f"J-{h}"] = {
                "avg_error": round(r["avg_abs_error"], 1) if r["avg_abs_error"] else None,
                "avg_bias": round(r["avg_bias"], 1) if r["avg_bias"] else None,
                "samples": r["cnt"],
            }
        return result
    except Exception as e:
        logger.warning(f"[WeatherReliability] Error: {e}")
        return {}
    finally:
        conn.close()


def get_rouge_postmortem(season: str = "2025-2026") -> list[dict]:
    """D10: Detailed analysis of each ROUGE day in the season.

    For each actual ROUGE day: temperature, predictions at each horizon,
    which horizons caught it, which version was running.
    """
    conn = get_db()
    try:
        season_start, season_end = parse_season(season)
        since = _enforce_start_date(season_start.isoformat())
        until = min(season_end, date.today()).isoformat()

        # Get all ROUGE actuals
        rouge_days = conn.execute(
            """SELECT date, couleur_reelle FROM actuals
               WHERE date >= ? AND date <= ? AND couleur_reelle = 'ROUGE'
                 AND synthetic = 0
               ORDER BY date""",
            (since, until),
        ).fetchall()

        if not rouge_days:
            return []

        tool_dates = sorted(getattr(Config, 'TOOL_UPDATE_DATES', {}).keys())
        tool_labels = getattr(Config, 'TOOL_UPDATE_DATES', {})

        result = []
        for rd in rouge_days:
            dt = rd["date"]

            # Get predictions for this date
            preds = conn.execute(
                """SELECT horizon, couleur_predite, couleur_originale,
                          score_risque, confirmed,
                          probabilite_rouge, probabilite_blanc, probabilite_bleu
                   FROM predictions
                   WHERE date = ? AND simulated = 0
                   ORDER BY timestamp_prediction""",
                (dt,),
            ).fetchall()

            # Build per-horizon info
            horizons = {}
            for p in preds:
                couleur = p["couleur_originale"] if p["couleur_originale"] else p["couleur_predite"]
                hz = p["horizon"]
                horizons[hz] = {
                    "couleur": couleur,
                    "correct": couleur == "ROUGE",
                    "score": round(p["score_risque"], 1) if p["score_risque"] else None,
                    "prob_rouge": p["probabilite_rouge"],
                }

            # Caught at which horizons?
            caught_at = [hz for hz, v in horizons.items() if v["correct"]]
            missed_at = [hz for hz, v in horizons.items() if not v["correct"]]

            # Observed weather
            weather = conn.execute(
                """SELECT temp_moy, humidity, wind_speed
                   FROM weather_cache WHERE date = ?
                   ORDER BY fetched_at DESC LIMIT 1""",
                (dt,),
            ).fetchone()

            # Which version was running?
            version = None
            for td in reversed(tool_dates):
                if dt >= td:
                    version = f"{td} — {tool_labels.get(td, '')}"
                    break

            result.append({
                "date": dt,
                "temp_observed": round(weather["temp_moy"], 1) if weather and weather["temp_moy"] else None,
                "humidity": round(weather["humidity"]) if weather and weather["humidity"] else None,
                "horizons": horizons,
                "caught_at": caught_at,
                "missed_at": missed_at,
                "caught_j2_j5": sum(1 for hz in caught_at if hz in ("J-2", "J-3", "J-4", "J-5")),
                "total_j2_j5": sum(1 for hz in ("J-2", "J-3", "J-4", "J-5") if hz in horizons),
                "version": version,
            })

        return result
    except Exception as e:
        logger.warning(f"[RougePostmortem] Error: {e}")
        return []
    finally:
        conn.close()


def get_data_coverage(season: str = "2025-2026") -> dict:
    """D12: Data coverage indicator.

    For each horizon J-1 to J-15, count how many days have predictions
    and how many days have been evaluated.
    """
    conn = get_db()
    try:
        season_start, season_end = parse_season(season)
        since = _enforce_start_date(season_start.isoformat())
        today_str = date.today().isoformat()
        until = min(season_end.isoformat(), today_str)

        # Total evaluable days (days with actuals)
        total_days_row = conn.execute(
            """SELECT COUNT(DISTINCT date) as cnt FROM actuals
               WHERE date >= ? AND date <= ? AND synthetic = 0""",
            (since, until),
        ).fetchone()
        total_days = total_days_row["cnt"] if total_days_row else 0

        # Predictions coverage by horizon
        pred_rows = conn.execute(
            """SELECT horizon, COUNT(DISTINCT date) as cnt
               FROM predictions
               WHERE date >= ? AND date <= ? AND simulated = 0
               GROUP BY horizon""",
            (since, until),
        ).fetchall()
        pred_coverage = {r["horizon"]: r["cnt"] for r in pred_rows}

        # Performance coverage by horizon (evaluated predictions)
        perf_rows = conn.execute(
            """SELECT jours_avance, COUNT(DISTINCT date_cible) as cnt
               FROM performance
               WHERE date_cible >= ? AND date_cible <= ?
               GROUP BY jours_avance""",
            (since, until),
        ).fetchall()
        perf_coverage = {r["jours_avance"]: r["cnt"] for r in perf_rows}

        horizons = {}
        for h in range(1, 16):
            key = f"J-{h}"
            preds = pred_coverage.get(key, 0)
            evals = perf_coverage.get(h, 0)
            horizons[key] = {
                "predictions": preds,
                "evaluations": evals,
                "coverage_pct": round(evals / total_days * 100) if total_days > 0 else 0,
            }

        return {
            "total_days": total_days,
            "horizons": horizons,
        }
    except Exception as e:
        logger.warning(f"[DataCoverage] Error: {e}")
        return {"total_days": 0, "horizons": {}}
    finally:
        conn.close()


# B7: Simple TTL cache for performance summary
_perf_summary_cache: dict = {"data": None, "season": None, "ts": 0}
_CACHE_TTL_SECONDS = 300  # 5 minutes


def invalidate_perf_summary_cache():
    """Invalide le cache performance (appelé après évaluation/confirmation)."""
    _perf_summary_cache["data"] = None
    _perf_summary_cache["ts"] = 0


def get_performance_summary(season: str = "2025-2026") -> dict:
    """Résumé complet des performances pour le dashboard admin.

    Args:
        season: saison au format "YYYY-YYYY" (ex: "2025-2026").
    """
    import time as _time

    # B7: Check cache
    now_ts = _time.time()
    if (_perf_summary_cache["data"] is not None
            and _perf_summary_cache["season"] == season
            and (now_ts - _perf_summary_cache["ts"]) < _CACHE_TTL_SECONDS):
        return _perf_summary_cache["data"]

    season_start, _ = parse_season(season)
    d = max(1, min((date.today() - season_start).days, 365))

    # All versions: code changes + weight recalculations
    all_versions = _get_all_version_dates()
    tool_dates = sorted(all_versions.keys())
    last_update = tool_dates[-1] if tool_dates else None
    last_update_label = all_versions.get(last_update, "") if last_update else ""
    days_since_update = max(1, (date.today() - date.fromisoformat(last_update)).days) if last_update else d

    # D2: Single combined query instead of 3 separate get_accuracy_global calls
    acc = get_accuracy_combined(d)

    # Build tool_versions list for frontend version filter
    tool_versions = []
    for td in tool_dates:
        tool_versions.append({"date": td, "label": all_versions.get(td, td)})

    # A5: Pre-compute per-version data — filter by date_prediction (not date_cible)
    # so only predictions actually made WITH that version's code are attributed to it
    per_version_data = {}
    for i, td in enumerate(tool_dates):
        v_pred_end = tool_dates[i + 1] if i + 1 < len(tool_dates) else None
        v_days = max(1, (date.today() - date.fromisoformat(td)).days)
        per_version_data[td] = {
            # A1/A6: CM and diagnostic scoped to J-2→J-5 (consistent with main data)
            "confusion_matrix": get_confusion_matrix(
                v_days, pred_since_date=td, pred_end_date=v_pred_end,
                min_horizon=2, max_horizon=5),
            "diagnostic": get_diagnostic(
                v_days, pred_since_date=td, pred_end_date=v_pred_end,
                min_horizon=2, max_horizon=5),
            "precision_recall_f1": get_precision_recall_f1(
                v_days, pred_since_date=td, pred_end_date=v_pred_end,
                min_horizon=2, max_horizon=5),
            # Recall tables need all horizons (J-1 through J-10)
            "rouge_recall_by_horizon": get_color_recall_by_horizon(
                "ROUGE", v_days, pred_since_date=td, pred_end_date=v_pred_end),
            "blanc_recall_by_horizon": get_color_recall_by_horizon(
                "BLANC", v_days, pred_since_date=td, pred_end_date=v_pred_end),
            "bleu_recall_by_horizon": get_color_recall_by_horizon(
                "BLEU", v_days, pred_since_date=td, pred_end_date=v_pred_end),
        }

    # A4: Period comparison anchored on last tool update, scoped to J-2→J-5
    period_comp = get_period_comparison(7, pivot_date=last_update,
                                        min_horizon=2, max_horizon=5)

    result = {
        "season": season,
        "available_seasons": get_available_seasons(),
        "days": d,
        "global": acc["global"],
        "accuracy_j1": acc["j1"],
        "accuracy_j2_j5": acc["j2_j5"],
        "accuracy_j6_j15": acc["j6_j15"],
        "by_horizon": get_accuracy_by_horizon(d),
        # A1/A6: Global confusion matrix restricted to J-2→J-5 (our value zone)
        "confusion_matrix": get_confusion_matrix(d, min_horizon=2, max_horizon=5),
        # Also provide all-horizons matrix for reference
        "confusion_matrix_all": get_confusion_matrix(d),
        "precision_recall_f1": get_precision_recall_f1(d, min_horizon=2, max_horizon=5),
        "precision_recall_f1_all": get_precision_recall_f1(d),
        "rouge_recall_by_horizon": get_color_recall_by_horizon("ROUGE", d),
        "blanc_recall_by_horizon": get_color_recall_by_horizon("BLANC", d),
        "bleu_recall_by_horizon": get_color_recall_by_horizon("BLEU", d),
        "monthly_performance": get_monthly_performance(season),
        "version_performance": get_version_performance(),
        "current_weights": get_current_weights(),
        "previous_weights": get_previous_weights(),
        # Diagnostic scoped to latest version (pred_since_date=last_update, J-2→J-5)
        # Uses pred_since_date to only include predictions MADE with the latest version
        "diagnostic": get_diagnostic(
            days_since_update, pred_since_date=last_update,
            min_horizon=2, max_horizon=5),
        "period_comparison": period_comp,
        "budget_season": get_budget_season(),
        "tool_versions": tool_versions,
        "per_version_data": per_version_data,
        "last_tool_update": last_update,
        "last_tool_update_label": last_update_label,
        "daily_recap": get_daily_recap(season),
        # D6: Weather forecast reliability by horizon
        "weather_reliability": get_weather_reliability(d),
    }

    # B7: Store in cache
    _perf_summary_cache["data"] = result
    _perf_summary_cache["season"] = season
    _perf_summary_cache["ts"] = now_ts

    return result


# ================================================================
# 3. RECALCUL AUTOMATIQUE DES POIDS (mensuel)
# ================================================================

def recalculate_weights():
    """Recalcule les poids de l'algorithme via regression logistique.

    Fix ML-2 : filtre sur jours_avance <= 5 pour entraîner sur horizons fiables.
    Fix ML-5 : seuil validation F1-macro >= 45% (random = 33%).
    Fix ML-6 : normalisation StandardScaler des features.
    Fix ML-7 : minimum 60 données.
    Fix ML-8 : cross-validation 5-fold.
    Fix ML-11 : permutation importance.
    Fix ML-12 : ALPHA adaptatif.

    Fix audit DB : transaction split en 3 phases pour ne pas bloquer
    les writers pendant le traitement ML (2-5s de scikit-learn).
    """
    # === Phase 1 : lectures DB (transaction courte) ===
    conn = get_db()
    try:
        # Fix ML-15 : toujours mettre à jour precision_apres, même sans recalcul
        _update_previous_precision_apres(conn)
        conn.commit()

        # Verifier qu'on a assez de donnees evaluees
        row = conn.execute(
            "SELECT COUNT(*) as c FROM performance WHERE jours_avance <= 5"
        ).fetchone()
        count = row["c"] if row else 0

        # Fix ML-7 : minimum 60 données (au lieu de 30)
        if count < 60:
            logger.info(f"[Poids] Pas assez de donnees evaluees ({count}/60)")
            return None

        # C-1 : entraîner UNIQUEMENT sur raw sub-scores (avant corrections)
        # W-6 : plus de LIMIT 300 — utiliser toutes les données disponibles
        rows = conn.execute(
            """SELECT
                      p.score_temperature_raw as score_temperature,
                      p.score_budget_raw      as score_budget,
                      p.score_weekday_raw     as score_weekday,
                      p.score_gradient_raw    as score_gradient,
                      p.score_clustering_raw  as score_clustering,
                      p.score_rte_raw         as score_rte,
                      a.couleur_reelle
               FROM predictions p
               JOIN actuals a ON p.date = a.date
               WHERE p.horizon IN ('J-1','J-2','J-3','J-4','J-5','J0')
                 AND a.synthetic = 0
                 AND p.simulated = 0
                 AND (p.score_temperature_raw + p.score_budget_raw + p.score_weekday_raw
                      + p.score_gradient_raw + p.score_clustering_raw + p.score_rte_raw) > 0
               ORDER BY p.date DESC"""
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 60:
        logger.info(
            f"[Poids] Pas assez de donnees avec sub-scores ({len(rows)}/60)"
        )
        return None

    # === Phase 2 : traitement ML (pas de connexion DB) ===
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        logger.error("[Poids] scikit-learn non disponible, recalcul impossible")
        return None

    try:
        X = []
        y = []

        for row in rows:
            t = row["score_temperature"]
            b = row["score_budget"]
            w = row["score_weekday"]
            g = row["score_gradient"]
            c = row["score_clustering"]
            r = row["score_rte"]
            X.append([
                t, b, w, g, c, r,
                (t * b) / 100,
                (g * t) / 100,
                (c * g) / 100,
                (t * r) / 100,
            ])
            y.append(row["couleur_reelle"] or "BLEU")

        X = np.array(X)
        y = np.array(y)

        # Au moins 2 classes presentes
        unique_classes = set(y)
        if len(unique_classes) < 2:
            logger.info("[Poids] Pas assez de diversite dans les labels")
            return None

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Audit DS : cost-sensitive training — rater un ROUGE est beaucoup
        # plus coûteux (0.7562€/kWh) qu'une fausse alarme. Le poids ROUGE=25
        # (au lieu de ~2.5 avec "balanced") force le modèle à prioriser le
        # recall ROUGE, quitte à avoir plus de fausses alertes.
        model = LogisticRegression(
            multi_class="multinomial", max_iter=1000, C=1.0,
            class_weight={"BLEU": 1, "BLANC": 3, "ROUGE": 25},
        )

        try:
            cv_f1_scores = cross_val_score(model, X_scaled, y, cv=5, scoring="f1_macro")
            cv_f1 = round(cv_f1_scores.mean() * 100, 1)
            cv_f1_std = round(cv_f1_scores.std() * 100, 1)

            cv_acc_scores = cross_val_score(model, X_scaled, y, cv=5, scoring="accuracy")
            cv_accuracy = round(cv_acc_scores.mean() * 100, 1)
            cv_std = round(cv_acc_scores.std() * 100, 1)

            # Audit DS P1-D : mesurer le recall ROUGE en CV pour monitoring
            # F-beta=2 (recall pèse 4x plus que precision) pour la classe ROUGE
            from sklearn.metrics import make_scorer, fbeta_score, recall_score
            rouge_recall_scorer = make_scorer(
                recall_score, labels=["ROUGE"], average=None, zero_division=0)
            cv_rouge_recall = cross_val_score(
                model, X_scaled, y, cv=5, scoring=rouge_recall_scorer)
            cv_rouge_recall_mean = round(cv_rouge_recall.mean() * 100, 1)
            logger.info(f"[Poids] CV ROUGE recall: {cv_rouge_recall_mean}%")
        except ValueError:
            cv_f1 = 0
            cv_f1_std = 0
            cv_accuracy = 0
            cv_std = 0
            cv_rouge_recall_mean = 0
            logger.warning("[Poids] Cross-validation impossible (classe trop rare)")

        from collections import Counter
        majority_pct = round(max(Counter(y).values()) / len(y) * 100, 1)

        if cv_f1 < 45:
            logger.warning(
                f"[Poids] CV F1-macro trop faible ({cv_f1}% ± {cv_f1_std}%), "
                f"poids NON deployes (seuil=45%, accuracy={cv_accuracy}%, "
                f"baseline={majority_pct}%)"
            )
            _store_weights_entry(
                get_current_weights(),
                get_accuracy_global(30)["precision"], cv_accuracy, count,
                f"REJETE (f1_macro={cv_f1}% ± {cv_f1_std}%, "
                f"acc={cv_accuracy}%, baseline={majority_pct}%)",
            )
            return None

        # Audit DS P2-F : garde recall ROUGE — rejeter les poids si le
        # recall ROUGE en CV tombe sous 30% (minimum acceptable)
        if cv_rouge_recall_mean > 0 and cv_rouge_recall_mean < 30:
            logger.warning(
                f"[Poids] CV ROUGE recall trop faible ({cv_rouge_recall_mean}%), "
                f"poids NON deployes (seuil=30%)"
            )
            _store_weights_entry(
                get_current_weights(),
                get_accuracy_global(30)["precision"], cv_accuracy, count,
                f"REJETE rouge_recall ({cv_rouge_recall_mean}% < 30%, "
                f"f1_macro={cv_f1}%, acc={cv_accuracy}%)",
            )
            return None

        # ML-3 : Holdout temporel
        # Données triées ORDER BY date DESC : index 0 = plus récent.
        # On entraîne sur les 80% les plus anciens (fin du tableau) et
        # on valide sur les 20% les plus récents (début du tableau).
        # Fix audit DS fev 2026 : les proportions étaient inversées
        # (20% train, 80% test) — corrigé en split_idx = 0.2.
        holdout_accuracy = None
        n_rows = len(X)
        if n_rows >= 80:
            split_idx = int(n_rows * 0.2)
            X_train_t = X_scaled[split_idx:]
            y_train_t = y[split_idx:]
            X_val_t = X_scaled[:split_idx]
            y_val_t = y[:split_idx]

            try:
                model_holdout = LogisticRegression(
                    multi_class="multinomial", max_iter=1000, C=1.0,
                    class_weight={"BLEU": 1, "BLANC": 3, "ROUGE": 25},
                )
                train_classes = set(y_train_t)
                val_classes = set(y_val_t)
                if len(train_classes) >= 2 and len(val_classes) >= 2:
                    model_holdout.fit(X_train_t, y_train_t)
                    holdout_accuracy = round(model_holdout.score(X_val_t, y_val_t) * 100, 1)
                    if holdout_accuracy < 65:
                        logger.warning(
                            f"[Poids] Holdout temporel accuracy trop faible "
                            f"({holdout_accuracy}%), poids NON deployes"
                        )
                        _store_weights_entry(
                            get_current_weights(),
                            get_accuracy_global(30)["precision"], holdout_accuracy, count,
                            f"REJETE holdout (holdout={holdout_accuracy}%, "
                            f"cv_f1={cv_f1}%, cv_acc={cv_accuracy}%)",
                        )
                        return None
            except Exception as e:
                logger.warning(f"[Poids] Holdout temporel echoue: {e}")

        # Entraîner le modèle final sur toutes les données
        model.fit(X_scaled, y)

        # Fix ML-11 : permutation importance
        base_feature_names = [
            "temperature", "jours_restants", "jour_semaine",
            "gradient_thermique", "clustering", "consommation_rte",
        ]
        try:
            from sklearn.inspection import permutation_importance
            perm_result = permutation_importance(
                model, X_scaled, y, n_repeats=30, random_state=42
            )
            importance = perm_result.importances_mean[:6]
            importance = np.maximum(importance, 0.01)
        except Exception:
            importance = np.sqrt((model.coef_ ** 2).sum(axis=0))[:6]

        total_imp = importance.sum()
        if total_imp == 0:
            logger.warning("[Poids] Importance totale nulle, abandon")
            return None

        raw_weights = {
            k: float(importance[i] / total_imp)
            for i, k in enumerate(base_feature_names)
        }

        WEIGHT_MIN = 0.05
        WEIGHT_MAX = 0.50
        bounded = {
            k: max(WEIGHT_MIN, min(WEIGHT_MAX, v))
            for k, v in raw_weights.items()
        }
        total_bounded = sum(bounded.values())
        bounded = {k: v / total_bounded for k, v in bounded.items()}

        old_weights = get_current_weights()
        ALPHA = min(0.6, max(0.2, len(rows) / 500))
        smoothed = {}
        for key in bounded:
            old_val = old_weights.get(key, bounded[key])
            smoothed[key] = ALPHA * bounded[key] + (1 - ALPHA) * old_val

        total_smooth = sum(smoothed.values())
        new_weights = {
            k: round(v / total_smooth, 4) for k, v in smoothed.items()
        }

    except Exception as e:
        logger.error(f"[Poids] Erreur recalcul : {e}")
        return None

    # === Phase 3 : monitoring recall ROUGE (Audit DS P2-F) ===
    # Mesurer le recall ROUGE actuel pour inclure dans l'historique des poids
    prf = get_precision_recall_f1(90)
    rouge_recall_actual = prf.get("ROUGE", {}).get("recall", 0)
    rouge_f1_actual = prf.get("ROUGE", {}).get("f1", 0)

    # === Phase 4 : ecriture DB (transaction courte) ===
    precision_avant = get_accuracy_global(30)["precision"]
    _store_weights_entry(
        new_weights, precision_avant, 0, count,
        f"Recalcul auto (f1_macro={cv_f1}% ± {cv_f1_std}%, "
        f"cv_acc={cv_accuracy}%, cv_rouge_recall={cv_rouge_recall_mean}%, "
        f"actual_rouge_recall={rouge_recall_actual}%, "
        f"actual_rouge_f1={rouge_f1_actual}%, "
        f"alpha={ALPHA:.2f}, n={len(rows)}) — "
        f"ancien: {json.dumps(old_weights)}",
    )

    logger.info(f"[Poids] Nouveaux poids deployes : {new_weights}")
    logger.info(
        f"[Poids] F1-macro: {cv_f1}% ± {cv_f1_std}% | "
        f"Accuracy: {cv_accuracy}% | ROUGE recall(CV): {cv_rouge_recall_mean}% | "
        f"ROUGE recall(actual): {rouge_recall_actual}% | "
        f"Alpha={ALPHA:.2f} | n={len(rows)}"
    )
    return new_weights


def _store_weights_entry(weights: dict, precision_avant: float,
                         precision_apres: float, nb_predictions: int,
                         commentaire: str) -> None:
    """Stocke une entree dans weights_history (transaction courte)."""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO weights_history
               (date_update, weights_json, precision_avant, precision_apres,
                nb_predictions, commentaire, model_version, timestamp_update)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now().strftime("%Y-%m-%d"),
             json.dumps(weights),
             precision_avant, precision_apres, nb_predictions,
             commentaire,
             "logreg_v5_audit_ml",
             datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _update_previous_precision_apres(conn):
    """Met à jour precision_apres de la dernière entrée weights_history.

    W-5 : Auto-rollback si la précision a chuté de plus de 5 points
    par rapport à precision_avant (signe de poids dégradés).
    """
    try:
        last_entry = conn.execute(
            """SELECT id, precision_avant, precision_apres, rollback_of
               FROM weights_history ORDER BY id DESC LIMIT 1"""
        ).fetchone()

        if not last_entry or last_entry["precision_apres"] != 0:
            return

        current_precision = get_accuracy_global(30)["precision"]
        conn.execute(
            "UPDATE weights_history SET precision_apres = ? WHERE id = ?",
            (current_precision, last_entry["id"]),
        )
        logger.info(
            f"[Poids] precision_apres mise a jour pour id={last_entry['id']}: "
            f"{current_precision}%"
        )

        # W-5: Auto-rollback if precision dropped by more than 5 percentage points
        precision_avant = last_entry["precision_avant"]
        rollback_of = last_entry["rollback_of"]
        if (precision_avant > 0
                and current_precision < precision_avant - 5
                and rollback_of is None):  # Don't rollback a rollback

            prev = conn.execute(
                "SELECT id, weights_json FROM weights_history WHERE id < ? ORDER BY id DESC LIMIT 1",
                (last_entry["id"],)
            ).fetchone()

            if prev:
                conn.execute(
                    """INSERT INTO weights_history
                       (date_update, weights_json, precision_avant, precision_apres,
                        nb_predictions, commentaire, model_version,
                        timestamp_update, rollback_of)
                       VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?)""",
                    (datetime.now().strftime("%Y-%m-%d"),
                     prev["weights_json"],
                     current_precision,
                     f"AUTO-ROLLBACK (precision {precision_avant:.1f}% → "
                     f"{current_precision:.1f}%, delta="
                     f"{current_precision - precision_avant:.1f}%)",
                     "rollback_v9",
                     datetime.now().isoformat(),
                     last_entry["id"]),
                )
                logger.warning(
                    f"[Poids] AUTO-ROLLBACK: precision {precision_avant:.1f}% → "
                    f"{current_precision:.1f}%, retour aux poids id={prev['id']}"
                )

    except Exception as e:
        logger.error(f"[Poids] Erreur mise a jour precision_apres: {e}")



# ================================================================
# 4. EXPORT CSV MENSUEL
# ================================================================

def export_monthly_csv(month: int, year: int) -> str:
    """Exporte les performances d'un mois en format CSV."""
    import csv
    import io

    conn = get_db()
    try:
        start = _enforce_start_date(f"{year}-{month:02d}-01")
        if month == 12:
            end = f"{year + 1}-01-01"
        else:
            end = f"{year}-{month + 1:02d}-01"

        rows = conn.execute(
            """SELECT date_prediction, date_cible, jours_avance, correct,
                      couleur_predite, couleur_reelle, score_risque_predit,
                      ecart_score, contexte_meteo
               FROM performance
               WHERE date_cible >= ? AND date_cible < ?
               ORDER BY date_cible""",
            (start, end),
        ).fetchall()

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
        writer.writerow([
            "date_prediction", "date_cible", "jours_avance", "correct",
            "couleur_predite", "couleur_reelle", "score_risque",
            "ecart_score", "contexte_meteo",
        ])
        for r in rows:
            writer.writerow([
                r["date_prediction"], r["date_cible"], r["jours_avance"],
                r["correct"], r["couleur_predite"], r["couleur_reelle"],
                r["score_risque_predit"], r["ecart_score"],
                r["contexte_meteo"],
            ])
        return output.getvalue()
    finally:
        conn.close()


# ================================================================
# 5. JOURNAL D'APPRENTISSAGE — Analyse des patterns d'erreurs
# ================================================================

MOIS_MAP = {
    9: "sept", 10: "oct", 11: "nov", 12: "dec",
    1: "jan", 2: "fev", 3: "mars", 4: "avr", 5: "mai",
}


def _color_rank(couleur: str) -> int:
    """Rang ordinal d'une couleur pour comparer la direction de l'erreur."""
    return {"BLEU": 0, "BLANC": 1, "ROUGE": 2}.get(couleur, 0)


def _compute_bias(group: list[dict]) -> tuple[float, float, float, str, float]:
    """Calcule le biais directionnel pour un groupe de prédictions.

    Returns: (accuracy, over_rate, under_rate, direction, magnitude)
    - over = on prédit trop haut (ex: ROUGE prédit, BLEU réel)
    - under = on prédit trop bas (ex: BLEU prédit, ROUGE réel)
    """
    total = len(group)
    if total == 0:
        return (0, 0, 0, "balanced", 0)

    correct = sum(1 for r in group if r["correct"] == 1)
    over = sum(1 for r in group
               if _color_rank(r["couleur_predite"]) > _color_rank(r["couleur_reelle"]))
    under = sum(1 for r in group
                if _color_rank(r["couleur_predite"]) < _color_rank(r["couleur_reelle"]))

    accuracy = correct / total
    over_rate = over / total
    under_rate = under / total

    if over_rate > under_rate + 0.1:
        direction = "over"
    elif under_rate > over_rate + 0.1:
        direction = "under"
    else:
        direction = "balanced"

    magnitude = abs(over_rate - under_rate)
    return (accuracy, over_rate, under_rate, direction, magnitude)


def _wilson_lower_bound(p: float, n: int, z: float = 1.96) -> float:
    """Wilson score interval lower bound (95% CI).
    A-2 : estimation conservative de la proportion réelle."""
    if n <= 0 or p <= 0:
        return 0.0
    denominator = 1 + z ** 2 / n
    center = p + z ** 2 / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))
    return max(0.0, (center - spread) / denominator)


def _compute_correction(bias_direction: str, bias_magnitude: float,
                        sample_size: int, max_correction: float = 8.0
                        ) -> tuple[float, float]:
    """Calcule la correction de score et la confiance.

    Returns: (correction_score, confidence)
    - correction négative = on prédit trop haut → réduire le score
    - correction positive = on prédit trop bas → augmenter le score

    A-2 : Wilson interval pour estimation conservative du biais.
    Remplace min(1, n/30) par sqrt(n/30) + Wilson lower bound.
    """
    if sample_size <= 0:
        return (0.0, 0.0)

    # A-2: Wilson lower bound on magnitude for conservative estimate
    conservative_magnitude = _wilson_lower_bound(bias_magnitude, sample_size)

    # Confidence from sample size (sqrt scaling = less aggressive than linear)
    confidence = min(1.0, math.sqrt(sample_size / 30))

    if bias_direction == "over":
        correction = -conservative_magnitude * max_correction * confidence
    elif bias_direction == "under":
        correction = conservative_magnitude * max_correction * confidence
    else:
        correction = 0.0

    return (round(correction, 2), round(confidence, 2))


def analyze_error_patterns(days: int = 90, force: bool = False) -> list[dict]:
    """Analyse les patterns d'erreurs systématiques et stocke les corrections.

    Dimensions analysées :
      1. Par horizon (J-1 à J-15) — dégradation naturelle avec la distance
      2. Par confusion de couleur ciblée (contexte-aware)
      3. Par tranche de température (zone critique 0-5°C)
      4. Par mois de la saison (profil saisonnier)
      5. Par jour de semaine (semaine vs weekend)
      6. Par facteur de scoring (attribution d'erreur aux sub-scores)
      7. Volatilité des prédictions (stabilité inter-cycles)

    Les corrections sont stockées dans learning_journal et appliquées
    automatiquement par le prédicteur lors des prochaines prédictions.

    A-5 : les anciennes corrections sont préservées (historique versionné).
    A-6 : garde anti double-exécution (skip si déjà analysé aujourd'hui).
    """
    conn = get_db()
    try:
        # A-6 : éviter double exécution le même jour
        if not force:
            last_analysis = conn.execute(
                "SELECT MAX(date_analysis) as last FROM learning_journal"
            ).fetchone()
            if last_analysis and last_analysis["last"] == date.today().isoformat():
                logger.info("[Learning] Déjà analysé aujourd'hui, skip (force=False)")
                return []

        since = _enforce_start_date((date.today() - timedelta(days=days)).isoformat())

        perf_rows = conn.execute(
            "SELECT * FROM performance WHERE date_cible >= ?",
            (since,)
        ).fetchall()

        if len(perf_rows) < 20:
            logger.info(f"[Learning] Pas assez de données ({len(perf_rows)}/20)")
            return []

        # Températures réelles par date (weather_cache = données météo archivées)
        # Fix ML-circular: on utilise les vraies températures, pas les prévues
        temp_rows = conn.execute(
            """SELECT date, temp_min
               FROM weather_cache
               WHERE date >= ? AND temp_min IS NOT NULL
               GROUP BY date
               ORDER BY fetched_at DESC""",
            (since,)
        ).fetchall()
        temp_map = {r["date"]: r["temp_min"] for r in temp_rows}

        # Sub-scores par date pour l'attribution par facteur
        sub_score_rows = conn.execute(
            """SELECT date, horizon,
                      score_temperature, score_budget, score_weekday,
                      score_gradient, score_clustering, score_rte,
                      couleur_predite, score_risque
               FROM predictions
               WHERE date >= ?
                 AND (score_temperature + score_budget + score_weekday
                      + score_gradient + score_clustering + score_rte) > 0""",
            (since,)
        ).fetchall()
        sub_score_map = {}
        for r in sub_score_rows:
            sub_score_map[r["date"]] = {
                "score_temperature": r["score_temperature"],
                "score_budget": r["score_budget"],
                "score_weekday": r["score_weekday"],
                "score_gradient": r["score_gradient"],
                "score_clustering": r["score_clustering"],
                "score_rte": r["score_rte"],
            }

        # Convertir en dicts et enrichir avec la température et sub-scores
        rows = []
        for r in perf_rows:
            d = {k: r[k] for k in r.keys()}
            d["_temp_min"] = temp_map.get(d["date_cible"])
            d["_sub_scores"] = sub_score_map.get(d["date_cible"])
            rows.append(d)

        all_patterns = []
        all_patterns.extend(_analyze_by_horizon(rows))
        all_patterns.extend(_analyze_color_confusion(rows))
        all_patterns.extend(_analyze_by_temp_range(rows))
        all_patterns.extend(_analyze_by_month(rows))
        all_patterns.extend(_analyze_by_weekday(rows))
        all_patterns.extend(_analyze_factor_contributions(rows))
        all_patterns.extend(_analyze_prediction_volatility(conn, since))

        # A-5 : Désactiver les anciennes corrections du même type
        # (elles restent en DB pour l'historique, mais active=0)
        today_iso = date.today().isoformat()
        now = datetime.now().isoformat()
        conn.execute(
            """UPDATE learning_journal SET active = 0
               WHERE active = 1 AND date_analysis < ?""",
            (today_iso,)
        )

        # Stocker les nouvelles corrections versionnées par date
        stored = 0
        for p in all_patterns:
            conn.execute(
                """INSERT INTO learning_journal
                   (date_analysis, pattern_type, pattern_key, observation,
                    accuracy, bias_direction, bias_magnitude,
                    sample_size, correction_score, confidence, active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(pattern_type, pattern_key, date_analysis) DO UPDATE SET
                       observation = excluded.observation,
                       accuracy = excluded.accuracy,
                       bias_direction = excluded.bias_direction,
                       bias_magnitude = excluded.bias_magnitude,
                       sample_size = excluded.sample_size,
                       correction_score = excluded.correction_score,
                       confidence = excluded.confidence,
                       active = excluded.active,
                       created_at = excluded.created_at""",
                (today_iso, p["type"], p["key"],
                 p["observation"], p["accuracy"], p["bias_direction"],
                 p["bias_magnitude"], p["sample_size"],
                 p["correction"], p["confidence"], now),
            )
            stored += 1

        conn.commit()
        logger.info(f"[Learning] {stored} patterns analysés et stockés "
                    f"(sur {len(rows)} évaluations, {days}j)")
        return all_patterns

    except Exception as e:
        logger.error(f"[Learning] Erreur analyse patterns: {e}")
        return []
    finally:
        conn.close()


def _analyze_by_horizon(rows: list[dict]) -> list[dict]:
    """Biais par horizon de prédiction (J-0 à J-15)."""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        groups[r["jours_avance"]].append(r)

    patterns = []
    for horizon, group in sorted(groups.items()):
        if len(group) < 5:
            continue

        accuracy, over_rate, under_rate, direction, magnitude = _compute_bias(group)
        correction, confidence = _compute_correction(direction, magnitude, len(group))

        key = f"J-{horizon}"
        obs = (f"Précision {accuracy*100:.0f}% · "
               f"{'sur' if direction == 'over' else 'sous' if direction == 'under' else 'pas de '}"
               f"-prédiction {magnitude*100:.0f}% · n={len(group)}")

        patterns.append({
            "type": "horizon", "key": key, "observation": obs,
            "accuracy": round(accuracy, 3),
            "bias_direction": direction, "bias_magnitude": round(magnitude, 3),
            "sample_size": len(group),
            "correction": correction, "confidence": confidence,
        })
    return patterns


def _analyze_color_confusion(rows: list[dict]) -> list[dict]:
    """Confusions systématiques entre couleurs (ex: ROUGE prédit → BLEU réel)."""
    from collections import defaultdict
    confusions = defaultdict(int)
    totals_predicted = defaultdict(int)

    for r in rows:
        totals_predicted[r["couleur_predite"]] += 1
        if r["couleur_predite"] != r["couleur_reelle"]:
            key = f"{r['couleur_predite']}->{r['couleur_reelle']}"
            confusions[key] += 1

    patterns = []
    for confusion, count in confusions.items():
        predicted, actual = confusion.split("->")
        total_pred = totals_predicted.get(predicted, 1)

        if count < 3:
            continue

        rate = count / total_pred
        direction = "over" if _color_rank(predicted) > _color_rank(actual) else "under"
        # Audit DS : corrections asymétriques — rater un ROUGE coûte bien
        # plus cher (0.7562€/kWh) qu'une fausse alarme ROUGE.
        # ROUGE raté (under, actual=ROUGE) → max_correction=10
        # Fausse alarme ROUGE (over, predicted=ROUGE) → max_correction=4
        # Autres confusions → max_correction=6 (défaut)
        if actual == "ROUGE" and direction == "under":
            mc = 10.0  # rater un ROUGE = très coûteux
        elif predicted == "ROUGE" and direction == "over":
            mc = 4.0   # fausse alarme ROUGE = coût modéré
        else:
            mc = 6.0
        correction, confidence = _compute_correction(
            direction, rate, count, max_correction=mc)

        patterns.append({
            "type": "color_confusion", "key": confusion,
            "observation": f"{count} cas ({rate*100:.0f}% des {predicted} prédits) "
                          f"→ réellement {actual}",
            "accuracy": round(1 - rate, 3),
            "bias_direction": direction, "bias_magnitude": round(rate, 3),
            "sample_size": count,
            "correction": correction, "confidence": confidence,
        })
    return patterns


def _analyze_by_temp_range(rows: list[dict]) -> list[dict]:
    """Biais par tranche de température prévisionnelle."""
    from collections import defaultdict

    def _temp_bucket(temp):
        if temp is None:
            return None
        if temp < -2:
            return "<-2C"
        if temp < 0:
            return "-2_0C"
        if temp < 2:
            return "0_2C"
        if temp < 5:
            return "2_5C"
        if temp < 8:
            return "5_8C"
        if temp < 12:
            return "8_12C"
        return ">12C"

    groups = defaultdict(list)
    for r in rows:
        bucket = _temp_bucket(r.get("_temp_min"))
        if bucket:
            groups[bucket].append(r)

    order = ["<-2C", "-2_0C", "0_2C", "2_5C", "5_8C", "8_12C", ">12C"]
    patterns = []
    for range_key in order:
        group = groups.get(range_key, [])
        if len(group) < 5:
            continue

        accuracy, over_rate, under_rate, direction, magnitude = _compute_bias(group)
        correction, confidence = _compute_correction(direction, magnitude, len(group))

        obs = (f"Précision {accuracy*100:.0f}% · "
               f"{'sur' if direction == 'over' else 'sous' if direction == 'under' else 'pas de '}"
               f"-prédiction {magnitude*100:.0f}% · n={len(group)}")

        patterns.append({
            "type": "temp_range", "key": range_key, "observation": obs,
            "accuracy": round(accuracy, 3),
            "bias_direction": direction, "bias_magnitude": round(magnitude, 3),
            "sample_size": len(group),
            "correction": correction, "confidence": confidence,
        })
    return patterns


def _analyze_by_month(rows: list[dict]) -> list[dict]:
    """Biais par mois de la saison Tempo."""
    from collections import defaultdict
    groups = defaultdict(list)

    for r in rows:
        try:
            month = int(r["date_cible"].split("-")[1])
            month_key = MOIS_MAP.get(month, str(month))
            groups[month_key].append(r)
        except (ValueError, IndexError):
            continue

    patterns = []
    for month_key, group in groups.items():
        if len(group) < 5:
            continue

        accuracy, over_rate, under_rate, direction, magnitude = _compute_bias(group)
        correction, confidence = _compute_correction(direction, magnitude, len(group))

        obs = (f"Précision {accuracy*100:.0f}% · "
               f"{'sur' if direction == 'over' else 'sous' if direction == 'under' else 'pas de '}"
               f"-prédiction {magnitude*100:.0f}% · n={len(group)}")

        patterns.append({
            "type": "month", "key": month_key, "observation": obs,
            "accuracy": round(accuracy, 3),
            "bias_direction": direction, "bias_magnitude": round(magnitude, 3),
            "sample_size": len(group),
            "correction": correction, "confidence": confidence,
        })
    return patterns


def _analyze_by_weekday(rows: list[dict]) -> list[dict]:
    """Biais semaine vs weekend."""
    groups = {"semaine": [], "weekend": []}

    for r in rows:
        try:
            d = date.fromisoformat(r["date_cible"])
            key = "weekend" if d.weekday() >= 5 else "semaine"
            groups[key].append(r)
        except (ValueError, AttributeError):
            continue

    patterns = []
    for day_type, group in groups.items():
        if len(group) < 5:
            continue

        accuracy, over_rate, under_rate, direction, magnitude = _compute_bias(group)
        correction, confidence = _compute_correction(direction, magnitude, len(group))

        obs = (f"Précision {accuracy*100:.0f}% · "
               f"{'sur' if direction == 'over' else 'sous' if direction == 'under' else 'pas de '}"
               f"-prédiction {magnitude*100:.0f}% · n={len(group)}")

        patterns.append({
            "type": "weekday", "key": day_type, "observation": obs,
            "accuracy": round(accuracy, 3),
            "bias_direction": direction, "bias_magnitude": round(magnitude, 3),
            "sample_size": len(group),
            "correction": correction, "confidence": confidence,
        })
    return patterns


def _analyze_factor_contributions(rows: list[dict]) -> list[dict]:
    """Attribution d'erreur aux sub-scores individuels.

    Pour chaque prédiction incorrecte, identifie quel(s) sub-score(s) ont le
    plus contribué à l'erreur. Produit des corrections par facteur qui seront
    appliquées directement aux sub-scores (avant la somme pondérée).

    pattern_type='factor', pattern_key='temperature:over' ou 'budget:under', etc.
    """
    factor_names = [
        "score_temperature", "score_budget", "score_weekday",
        "score_gradient", "score_clustering", "score_rte",
    ]
    factor_short = {
        "score_temperature": "temperature",
        "score_budget": "budget",
        "score_weekday": "weekday",
        "score_gradient": "gradient",
        "score_clustering": "clustering",
        "score_rte": "rte",
    }

    # Accumuler les écarts par facteur et direction
    from collections import defaultdict
    factor_errors = defaultdict(lambda: {"over_sum": 0.0, "under_sum": 0.0,
                                          "over_n": 0, "under_n": 0, "total": 0})

    for r in rows:
        sub_scores = r.get("_sub_scores")
        if not sub_scores:
            continue
        if r["correct"] == 1:
            continue  # On ne s'intéresse qu'aux erreurs

        predicted_rank = _color_rank(r["couleur_predite"])
        actual_rank = _color_rank(r["couleur_reelle"])
        is_over = predicted_rank > actual_rank  # On a prédit trop haut

        for fname in factor_names:
            val = sub_scores.get(fname, 50)
            entry = factor_errors[fname]
            entry["total"] += 1
            if is_over:
                # Ce sub-score a contribué à sur-prédire si sa valeur est haute
                entry["over_sum"] += val
                entry["over_n"] += 1
            else:
                # Ce sub-score a contribué à sous-prédire si sa valeur est basse
                entry["under_sum"] += val
                entry["under_n"] += 1

    patterns = []
    for fname, stats in factor_errors.items():
        if stats["total"] < 5:
            continue
        short = factor_short[fname]

        # Fix P1-5 audit : ne pas empiler over+under pour le meme facteur
        # On prend la direction dominante (celle avec le plus d'echantillons)
        over_correction = None
        under_correction = None

        # Sur-prédiction : le facteur donnait des scores trop élevés
        if stats["over_n"] >= 3:
            avg_over = stats["over_sum"] / stats["over_n"]
            if avg_over > 55:
                magnitude = min(1.0, (avg_over - 50) / 50)
                correction = -magnitude * 6.0 * min(1.0, stats["over_n"] / 20)
                confidence = min(1.0, stats["over_n"] / 20)
                over_correction = {
                    "type": "factor", "key": f"{short}:over",
                    "observation": f"{fname} moyen={avg_over:.0f} lors de {stats['over_n']} "
                                   f"sur-prédictions",
                    "accuracy": 0.0,
                    "bias_direction": "over", "bias_magnitude": round(magnitude, 3),
                    "sample_size": stats["over_n"],
                    "correction": round(correction, 2), "confidence": round(confidence, 2),
                }

        # Sous-prédiction : le facteur donnait des scores trop bas
        if stats["under_n"] >= 3:
            avg_under = stats["under_sum"] / stats["under_n"]
            if avg_under < 45:
                magnitude = min(1.0, (50 - avg_under) / 50)
                correction = magnitude * 6.0 * min(1.0, stats["under_n"] / 20)
                confidence = min(1.0, stats["under_n"] / 20)
                under_correction = {
                    "type": "factor", "key": f"{short}:under",
                    "observation": f"{fname} moyen={avg_under:.0f} lors de {stats['under_n']} "
                                   f"sous-prédictions",
                    "accuracy": 0.0,
                    "bias_direction": "under", "bias_magnitude": round(magnitude, 3),
                    "sample_size": stats["under_n"],
                    "correction": round(correction, 2), "confidence": round(confidence, 2),
                }

        # Prendre uniquement la direction dominante
        if over_correction and under_correction:
            if stats["over_n"] >= stats["under_n"]:
                patterns.append(over_correction)
            else:
                patterns.append(under_correction)
        elif over_correction:
            patterns.append(over_correction)
        elif under_correction:
            patterns.append(under_correction)

    return patterns


def _analyze_prediction_volatility(conn, since: str) -> list[dict]:
    """Analyse la stabilité des prédictions via la table prediction_changes.

    Détecte les dates avec une volatilité excessive (changements fréquents
    de couleur entre cycles), signe d'un score proche d'un seuil.

    pattern_type='volatility', pattern_key='high_volatility' ou 'threshold_proximity'
    """
    try:
        changes = conn.execute(
            """SELECT date, COUNT(*) as nb_changes
               FROM prediction_changes
               WHERE date >= ?
               GROUP BY date
               HAVING COUNT(*) >= 2
               ORDER BY COUNT(*) DESC""",
            (since,)
        ).fetchall()
    except Exception:
        return []

    if not changes:
        return []

    # Build transitions per date in Python (avoids GROUP_CONCAT which is SQLite-only)
    transitions_map = {}
    try:
        for ch in changes:
            ch_date = ch["date"]
            trans_rows = conn.execute(
                "SELECT couleur_avant, couleur_apres FROM prediction_changes WHERE date = ?",
                (ch_date,)
            ).fetchall()
            transitions_map[ch_date] = ",".join(
                f"{t['couleur_avant']}->{t['couleur_apres']}" for t in trans_rows
            )
    except Exception:
        pass

    total_volatile_dates = len(changes)
    avg_changes = sum(r["nb_changes"] for r in changes) / total_volatile_dates

    patterns = []
    if total_volatile_dates >= 3:
        confidence = min(1.0, total_volatile_dates / 15)
        patterns.append({
            "type": "volatility", "key": "high_volatility",
            "observation": f"{total_volatile_dates} dates avec changements multiples "
                          f"(moy={avg_changes:.1f} changes/date). "
                          f"Scores proches des seuils.",
            "accuracy": 0.0,
            "bias_direction": "balanced",
            "bias_magnitude": round(min(1.0, avg_changes / 5), 3),
            "sample_size": total_volatile_dates,
            "correction": 0.0,  # Informatif, pas de correction directe
            "confidence": round(confidence, 2),
        })

    return patterns


# ================================================================
# 6. CORRECTIONS ACTIVES (lues par le prédicteur)
# ================================================================

def get_active_learnings() -> dict:
    """Retourne les corrections actives avec decay temporel (ML-5).

    Format retourné :
    {
        "horizon":   {"J-3": -2.5, "J-5": -4.0},
        "temp_range": {"2_5C": -3.0},
        "month":     {"jan": 2.0},
        "weekday":   {"weekend": -1.5},
    }

    ML-5 : les corrections perdent du poids avec le temps (demi-vie ~45 jours).
    Seules les corrections avec confidence >= 0.3 et |correction| > 0.5
    sont retournées (les autres sont du bruit statistique).
    """
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT pattern_type, pattern_key, correction_score, date_analysis
               FROM learning_journal
               WHERE active = 1
                 AND confidence >= 0.3
                 AND ABS(correction_score) > 0.5"""
        ).fetchall()

        corrections = {}
        today = date.today()
        for r in rows:
            ptype = r["pattern_type"]
            if ptype not in corrections:
                corrections[ptype] = {}

            # ML-5 : Temporal decay (demi-vie ~45 jours)
            raw_correction = r["correction_score"]
            try:
                analysis_date = date.fromisoformat(r["date_analysis"])
                age_days = max(0, (today - analysis_date).days)
                decay = math.exp(-age_days * 0.693 / 45)  # ln(2)/45
            except (ValueError, TypeError):
                decay = 0.5  # Fallback si date invalide

            decayed_correction = raw_correction * decay
            # Ignorer les corrections devenues negligeables apres decay
            if abs(decayed_correction) > 0.3:
                corrections[ptype][r["pattern_key"]] = decayed_correction

        return corrections
    except Exception:
        # Table peut ne pas exister si migration pas encore faite
        return {}
    finally:
        conn.close()


def get_learning_summary() -> list[dict]:
    """Résumé du journal d'apprentissage pour le dashboard admin."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT pattern_type, pattern_key, observation,
                      accuracy, bias_direction, bias_magnitude,
                      sample_size, correction_score, confidence,
                      date_analysis
               FROM learning_journal
               WHERE active = 1
               ORDER BY ABS(correction_score) DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ================================================================
# 7. VALIDATION DES CORRECTIONS (A-1) + KILL-SWITCH (C-3)
# ================================================================

def validate_correction_impact() -> dict | None:
    """A-1 : Compare la précision avec vs sans corrections.

    Utilise les raw sub-scores pour estimer ce qu'auraient été les prédictions
    sans corrections, et compare avec la précision réelle.
    Si les corrections dégradent de plus de 3%, désactive toutes les corrections.

    Returns: dict avec acc_with, acc_without, action ou None si pas assez de données.
    """
    conn = get_db()
    try:
        since = (date.today() - timedelta(days=30)).isoformat()

        rows = conn.execute(
            """SELECT p.date, p.couleur_predite, p.couleur_originale,
                      p.score_risque,
                      p.score_temperature_raw, p.score_budget_raw,
                      p.score_weekday_raw, p.score_gradient_raw,
                      p.score_clustering_raw, p.score_rte_raw,
                      a.couleur_reelle
               FROM predictions p
               JOIN actuals a ON p.date = a.date
               WHERE p.date >= ? AND a.synthetic = 0
                 AND p.simulated = 0
                 AND p.horizon IN ('J-1','J-2','J-3')
                 AND p.score_temperature_raw > 0""",
            (since,)
        ).fetchall()

        if len(rows) < 15:
            return None  # Pas assez de données

        weights = get_current_weights()
        w = [weights.get(k, 0.15) for k in [
            "temperature", "jours_restants", "jour_semaine",
            "gradient_thermique", "clustering", "consommation_rte"
        ]]

        correct_with = 0
        correct_without = 0
        total = len(rows)

        for r in rows:
            actual = r["couleur_reelle"]

            # Avec corrections (couleur effectivement prédite par l'algo)
            pred_with = r["couleur_originale"] or r["couleur_predite"]
            if pred_with == actual:
                correct_with += 1

            # Sans corrections : recompute depuis raw sub-scores
            raw_scores = [
                r["score_temperature_raw"], r["score_budget_raw"],
                r["score_weekday_raw"], r["score_gradient_raw"],
                r["score_clustering_raw"], r["score_rte_raw"],
            ]
            raw_composite = sum(s * wt for s, wt in zip(raw_scores, w))

            if raw_composite >= Config.SEUIL_ROUGE:
                pred_without = "ROUGE"
            elif raw_composite >= Config.SEUIL_BLANC:
                pred_without = "BLANC"
            else:
                pred_without = "BLEU"

            if pred_without == actual:
                correct_without += 1

        acc_with = correct_with / total * 100
        acc_without = correct_without / total * 100

        logger.info(
            f"[Learning] Correction validation: with={acc_with:.1f}%, "
            f"without={acc_without:.1f}%, n={total}"
        )

        # Si corrections dégradent de plus de 3%, désactiver
        if acc_with < acc_without - 3:
            conn.execute(
                "UPDATE learning_journal SET active = 0, disabled_at = ? WHERE active = 1",
                (datetime.now().isoformat(),)
            )
            conn.commit()
            logger.warning(
                f"[Learning] CORRECTIONS DISABLED: with={acc_with:.1f}% vs "
                f"without={acc_without:.1f}%, delta={acc_with - acc_without:.1f}%"
            )
            return {"action": "disabled", "acc_with": round(acc_with, 1),
                    "acc_without": round(acc_without, 1), "n": total}

        return {"action": "ok", "acc_with": round(acc_with, 1),
                "acc_without": round(acc_without, 1), "n": total}

    except Exception as e:
        logger.error(f"[Learning] Correction validation error: {e}")
        return None
    finally:
        conn.close()


def killswitch_harmful_corrections() -> list[dict]:
    """C-3 : Kill-switch — désactive les corrections individuelles nocives.

    Vérifie la précision récente (14 jours). Si elle est inférieure à 50%,
    désactive les 3 corrections les plus fortes (probables responsables).
    """
    conn = get_db()
    disabled = []
    try:
        since = (date.today() - timedelta(days=14)).isoformat()
        perf = conn.execute(
            """SELECT COUNT(*) as total, SUM(correct) as correct
               FROM performance WHERE date_cible >= ?""",
            (since,)
        ).fetchone()

        if not perf or perf["total"] < 10:
            return disabled

        accuracy = perf["correct"] / perf["total"]

        # Fix P2-7 audit : seuil releve de 50% a 55% (kill-switch plus reactif)
        if accuracy < 0.55:
            strongest = conn.execute(
                """SELECT id, pattern_type, pattern_key, correction_score
                   FROM learning_journal
                   WHERE active = 1 AND ABS(correction_score) > 2.0
                   ORDER BY ABS(correction_score) DESC LIMIT 3"""
            ).fetchall()

            for c in strongest:
                conn.execute(
                    "UPDATE learning_journal SET active = 0, disabled_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), c["id"])
                )
                entry = {
                    "pattern": f"{c['pattern_type']}:{c['pattern_key']}",
                    "correction": c["correction_score"],
                    "reason": f"accuracy={accuracy*100:.0f}%"
                }
                disabled.append(entry)
                logger.warning(
                    f"[Learning] KILL-SWITCH: {entry['pattern']} "
                    f"(score={c['correction_score']}) désactivée ({entry['reason']})"
                )

            conn.commit()

        return disabled
    except Exception as e:
        logger.error(f"[Learning] Kill-switch error: {e}")
        return disabled
    finally:
        conn.close()


def get_learning_health() -> dict:
    """Métriques de santé du système d'apprentissage pour monitoring."""
    conn = get_db()
    try:
        # Fix audit DB : requete consolidee pour learning_journal
        lj_stats = conn.execute(
            """SELECT
                   SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) as active,
                   SUM(CASE WHEN active = 0 AND disabled_at IS NOT NULL THEN 1 ELSE 0 END) as disabled,
                   COUNT(*) as total_history,
                   MAX(date_analysis) as last_analysis
               FROM learning_journal"""
        ).fetchone()
        active = lj_stats["active"] or 0
        disabled = lj_stats["disabled"] or 0
        total_history = lj_stats["total_history"]
        last_analysis = lj_stats["last_analysis"]

        # Nombre de rollbacks de poids
        row = conn.execute(
            "SELECT COUNT(*) as c FROM weights_history WHERE rollback_of IS NOT NULL"
        ).fetchone()
        rollbacks = row["c"] if row else 0

        # Précision récente (14j)
        since_14 = _enforce_start_date((date.today() - timedelta(days=14)).isoformat())
        perf_14 = conn.execute(
            "SELECT COUNT(*) as total, SUM(correct) as correct FROM performance WHERE date_cible >= ?",
            (since_14,)
        ).fetchone()
        accuracy_14 = round(perf_14["correct"] / perf_14["total"] * 100, 1) if perf_14["total"] else 0

        # Correction validation
        validation = validate_correction_impact()

        return {
            "active_corrections": active,
            "disabled_corrections": disabled,
            "weight_rollbacks": rollbacks,
            "last_analysis_date": last_analysis,
            "accuracy_14d": accuracy_14,
            "total_history_entries": total_history,
            "correction_validation": validation,
        }
    except Exception as e:
        logger.error(f"[Learning] Health check error: {e}")
        return {"error": str(e)}
    finally:
        conn.close()


# ================================================================
# 8. ÉVALUATION RATTRAPAGE (jours manqués)
# ================================================================

def evaluate_missed_days(lookback: int = 7) -> int:
    """Évalue rétroactivement les prédictions pour les jours non encore évalués.

    Pour chaque jour des N derniers jours ayant un actual confirmé (non synthétique)
    mais aucune entrée dans performance, lance evaluate_predictions_for_date().

    Retourne le nombre de jours rattrapés.
    """
    conn = get_db()
    try:
        since = (date.today() - timedelta(days=lookback)).isoformat()
        # Include tomorrow: if EDF confirmed tomorrow's color, evaluate predictions
        # for it too (our J-2→J-5 predictions can already be measured)
        upper_bound = (date.today() + timedelta(days=2)).isoformat()

        rows = conn.execute(
            """SELECT a.date, a.couleur_reelle
               FROM actuals a
               WHERE a.date >= ? AND a.date < ? AND a.synthetic = 0
               AND NOT EXISTS (
                   SELECT 1 FROM performance p WHERE p.date_cible = a.date
               )""",
            (since, upper_bound)
        ).fetchall()

        evaluated = 0
        for r in rows:
            try:
                target = date.fromisoformat(r["date"])
                evaluate_predictions_for_date(target, r["couleur_reelle"])
                evaluated += 1
            except Exception as e:
                logger.error(f"[Learning] Erreur rattrapage {r['date']}: {e}")

        if evaluated:
            logger.info(f"[Learning] {evaluated} jour(s) manqué(s) évalué(s) "
                       f"en rattrapage (lookback={lookback}j)")
        return evaluated
    finally:
        conn.close()
