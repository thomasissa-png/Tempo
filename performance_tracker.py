"""Système d'auto-amélioration — vérification, évaluation et recalcul des poids.

Trois responsabilités :
  1. Vérification quotidienne (11h30) : compare prédictions vs couleur réelle
  2. Calcul de métriques : précision globale, par horizon, matrice de confusion
  3. Recalcul mensuel des poids via régression logistique (scikit-learn)
"""

import json
import logging
from datetime import date, datetime, timedelta
from database import get_db, get_current_weights
from predictor import is_french_holiday
from config import Config

logger = logging.getLogger(__name__)


# ================================================================
# 1. VÉRIFICATION QUOTIDIENNE
# ================================================================

def evaluate_predictions_for_date(target_date: date, couleur_reelle: str):
    """Compare toutes les prédictions faites pour target_date avec la couleur réelle.
    Enregistre les résultats dans la table performance."""
    conn = get_db()
    try:
        predictions = conn.execute(
            "SELECT * FROM predictions WHERE date = ?",
            (target_date.isoformat(),)
        ).fetchall()

        if not predictions:
            logger.info(f"[Perf] Aucune prédiction pour {target_date}")
            return

        for pred in predictions:
            # Calculer l'avance en jours
            ts = datetime.fromisoformat(pred["timestamp_prediction"])
            jours_avance = (target_date - ts.date()).days
            correct = 1 if pred["couleur_predite"] == couleur_reelle else 0

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
                 pred["couleur_predite"], couleur_reelle,
                 score_predit, ecart,
                 pred["raison"] or "", datetime.now().isoformat()),
            )

        conn.commit()
        nb = len(predictions)
        nb_correct = sum(1 for p in predictions if p["couleur_predite"] == couleur_reelle)
        logger.info(f"[Perf] {target_date}: {nb_correct}/{nb} prédictions correctes")

    finally:
        conn.close()


def _couleur_to_score(couleur: str) -> float:
    """Score de référence pour chaque couleur (milieu de la fourchette)."""
    return {"ROUGE": 85, "BLANC": 55, "BLEU": 20}.get(couleur, 20)


# ================================================================
# 2. MÉTRIQUES DE PERFORMANCE
# ================================================================

def get_accuracy_global(days: int = 30) -> dict:
    """Précision globale sur les N derniers jours."""
    conn = get_db()
    try:
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """SELECT correct, COUNT(*) as cnt
               FROM performance WHERE date_cible >= ?
               GROUP BY correct""",
            (since,)
        ).fetchall()

        total = sum(r["cnt"] for r in rows)
        correct = sum(r["cnt"] for r in rows if r["correct"] == 1)

        return {
            "total": total,
            "correct": correct,
            "precision": round(correct / total * 100, 1) if total > 0 else 0,
            "periode_jours": days,
        }
    finally:
        conn.close()


def get_accuracy_by_horizon(days: int = 60) -> list[dict]:
    """Précision par horizon de prédiction (J-1, J-2, J-3...)."""
    conn = get_db()
    try:
        since = (date.today() - timedelta(days=days)).isoformat()
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


def get_confusion_matrix(days: int = 60) -> dict:
    """Matrice de confusion 3×3 (BLEU/BLANC/ROUGE prédit vs réel)."""
    conn = get_db()
    try:
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """SELECT couleur_predite, couleur_reelle, COUNT(*) as cnt
               FROM performance
               WHERE date_cible >= ?
               GROUP BY couleur_predite, couleur_reelle""",
            (since,)
        ).fetchall()

        matrix = {
            c_pred: {c_real: 0 for c_real in ("BLEU", "BLANC", "ROUGE")}
            for c_pred in ("BLEU", "BLANC", "ROUGE")
        }
        for r in rows:
            matrix[r["couleur_predite"]][r["couleur_reelle"]] = r["cnt"]

        return matrix
    finally:
        conn.close()


def get_recent_errors(limit: int = 5) -> list[dict]:
    """Top N erreurs récentes avec contexte météo."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT date_cible, couleur_predite, couleur_reelle,
                      score_risque_predit, ecart_score, contexte_meteo,
                      jours_avance, timestamp_evaluation
               FROM performance
               WHERE correct = 0
               ORDER BY timestamp_evaluation DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_performance_summary() -> dict:
    """Résumé complet des performances pour le dashboard admin."""
    return {
        "global_30j": get_accuracy_global(30),
        "global_90j": get_accuracy_global(90),
        "by_horizon": get_accuracy_by_horizon(90),
        "confusion_matrix": get_confusion_matrix(90),
        "recent_errors": get_recent_errors(10),
        "current_weights": get_current_weights(),
    }


# ================================================================
# 3. RECALCUL AUTOMATIQUE DES POIDS (mensuel)
# ================================================================

def recalculate_weights():
    """Recalcule les poids de l'algorithme via régression logistique
    sur l'historique des prédictions évaluées.

    Exécuté le 1er de chaque mois si >30 prédictions évaluées.
    """
    conn = get_db()
    try:
        # Vérifier qu'on a assez de données
        count = conn.execute(
            "SELECT COUNT(*) as c FROM performance WHERE jours_avance <= 3"
        ).fetchone()["c"]

        if count < 30:
            logger.info(f"[Poids] Pas assez de données ({count}/30), report du recalcul")
            return None

        # Récupérer les données d'entraînement
        rows = conn.execute(
            """SELECT p.score_risque, p.temp_min_prevue, p.temp_max_prevue,
                      p.pression_prevue, p.date, p.jours_rouges_restants,
                      a.couleur_reelle
               FROM predictions p
               JOIN actuals a ON p.date = a.date
               WHERE p.horizon IN ('J-1', 'J-2', 'J-3')
               ORDER BY p.date DESC
               LIMIT 200"""
        ).fetchall()

        if len(rows) < 30:
            logger.info("[Poids] Pas assez de jointures prédiction-réalité")
            return None

        # Préparer les features
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from datetime import date as date_type

        X = []
        y = []
        for row in rows:
            d = date_type.fromisoformat(row["date"])
            temp_min = row["temp_min_prevue"] or 5
            temp_max = row["temp_max_prevue"] or 10
            temp_moy = (temp_min + temp_max) / 2

            rouge_restants = row["jours_rouges_restants"] or 0
            # Simple normalization: 22 jours = 0, 0 jours = 100
            budget_feature = max(0, min(100, (22 - rouge_restants) / 22 * 100))

            is_weekday = 1 if d.weekday() <= 4 else 0
            is_holiday = 1 if is_french_holiday(d) else 0
            jour_semaine_feature = is_weekday * 70 + is_holiday * 30

            X.append([
                _score_temp_feature(temp_moy),     # feature température
                budget_feature,                     # feature jours_restants
                jour_semaine_feature,               # feature jour_semaine
                30,                                 # feature gradient_thermique (placeholder)
                20,                                 # feature clustering (placeholder)
                50,                                 # feature consommation_rte (placeholder)
            ])

            # Label : 0=BLEU, 1=BLANC, 2=ROUGE
            label_map = {"BLEU": 0, "BLANC": 1, "ROUGE": 2}
            y.append(label_map.get(row["couleur_reelle"], 0))

        X = np.array(X)
        y = np.array(y)

        # S'assurer qu'on a au moins 2 classes
        if len(set(y)) < 2:
            logger.info("[Poids] Pas assez de diversité dans les labels")
            return None

        # Régression logistique multinomiale
        model = LogisticRegression(
            multi_class="multinomial", max_iter=1000, C=1.0
        )
        model.fit(X, y)

        # Extraire l'importance relative des features
        # On utilise la norme L2 des coefficients par feature
        importance = np.sqrt((model.coef_ ** 2).sum(axis=0))
        total_imp = importance.sum()
        new_weights = {
            "temperature": round(float(importance[0] / total_imp), 4),
            "jours_restants": round(float(importance[1] / total_imp), 4),
            "jour_semaine": round(float(importance[2] / total_imp), 4),
            "gradient_thermique": round(float(importance[3] / total_imp), 4),
            "clustering": round(float(importance[4] / total_imp), 4),
            "consommation_rte": round(float(importance[5] / total_imp), 4),
        }

        # Calculer la précision avant/après
        old_weights = get_current_weights()
        precision_avant = get_accuracy_global(30)["precision"]

        # Sauvegarder
        conn.execute(
            """INSERT INTO weights_history
               (date_update, weights_json, precision_avant, precision_apres,
                nb_predictions, commentaire, timestamp_update)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().strftime("%Y-%m-%d"),
                json.dumps(new_weights),
                precision_avant,
                0,  # precision_apres sera calculée le mois suivant
                count,
                f"Recalcul auto - ancien: {json.dumps(old_weights)}",
                datetime.now().isoformat(),
            ),
        )
        conn.commit()

        logger.info(f"[Poids] Nouveaux poids calculés : {new_weights}")
        logger.info(f"[Poids] Anciens : {old_weights}")
        return new_weights

    except ImportError:
        logger.error("[Poids] scikit-learn non disponible, recalcul impossible")
        return None
    except Exception as e:
        logger.error(f"[Poids] Erreur recalcul : {e}")
        return None
    finally:
        conn.close()


def _score_temp_feature(temp_moy: float) -> float:
    """Feature température normalisée pour le ML (basée sur temp_moy)."""
    if temp_moy < -2:
        return 90
    if temp_moy < 0:
        return 75
    if temp_moy < 2:
        return 60
    if temp_moy < 5:
        return 40
    if temp_moy < 8:
        return 20
    return 5



# ================================================================
# 4. EXPORT CSV MENSUEL
# ================================================================

def export_monthly_csv(month: int, year: int) -> str:
    """Exporte les performances d'un mois en format CSV.
    Retourne le contenu CSV comme string."""
    conn = get_db()
    try:
        start = f"{year}-{month:02d}-01"
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

        lines = ["date_prediction,date_cible,jours_avance,correct,couleur_predite,"
                  "couleur_reelle,score_risque,ecart_score,contexte_meteo"]
        for r in rows:
            lines.append(
                f"{r['date_prediction']},{r['date_cible']},{r['jours_avance']},"
                f"{r['correct']},{r['couleur_predite']},{r['couleur_reelle']},"
                f"{r['score_risque_predit']},{r['ecart_score']},"
                f"\"{r['contexte_meteo']}\""
            )
        return "\n".join(lines)
    finally:
        conn.close()
