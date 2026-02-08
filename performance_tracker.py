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
    """Score de reference aligne sur les seuils de l'algorithme.

    Fix #10 : milieu de chaque zone definie par SEUIL_ROUGE et SEUIL_BLANC.
    """
    return {
        "ROUGE": (Config.SEUIL_ROUGE + 100) / 2,   # 82.5
        "BLANC": (Config.SEUIL_BLANC + Config.SEUIL_ROUGE) / 2,  # 50.0
        "BLEU": Config.SEUIL_BLANC / 2,             # 17.5
    }.get(couleur, Config.SEUIL_BLANC / 2)


# ================================================================
# 2. MÉTRIQUES DE PERFORMANCE
# ================================================================

def get_accuracy_global(days: int = 30, max_horizon: int | None = None) -> dict:
    """Precision globale sur les N derniers jours.

    Fix #6 audit v4 : filtre optionnel par horizon max (jours_avance).
    max_horizon=1 → J-1 seulement, None → tous les horizons.
    """
    conn = get_db()
    try:
        since = (date.today() - timedelta(days=days)).isoformat()
        if max_horizon is not None:
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
    """Recalcule les poids de l'algorithme via regression logistique
    sur l'historique des predictions evaluees.

    Corrections audit apprentissage :
      Fix #1/#2 : utilise les sub-scores stockes (memes features qu'inference)
      Fix #4 : bornes [0.05, 0.50] + lissage EMA avec anciens poids
      Fix #5 : met a jour precision_apres de l'entree precedente
      Fix #6 : class_weight='balanced' pour contrer desequilibre BLEU
      Fix #8 : LIMIT 500 pour couvrir une saison complete
      Fix #11 : split train/test 80/20 avec stratification
      Fix #12 : entraine sur tous les horizons (sub-scores deja corrects)
    """
    conn = get_db()
    try:
        # Verifier qu'on a assez de donnees evaluees
        count = conn.execute(
            "SELECT COUNT(*) as c FROM performance WHERE jours_avance <= 5"
        ).fetchone()["c"]

        if count < 30:
            logger.info(f"[Poids] Pas assez de donnees evaluees ({count}/30)")
            return None

        # Fix #5 : mettre a jour precision_apres de l'entree precedente
        _update_previous_precision_apres(conn)

        # Fix #1/#2 : utiliser les sub-scores stockes dans predictions
        # Fix #8 : LIMIT 500 pour couvrir ~une saison
        # Fix #12 : tous les horizons (pas juste J-1/J-2/J-3)
        rows = conn.execute(
            """SELECT p.score_temperature, p.score_budget, p.score_weekday,
                      p.score_gradient, p.score_clustering, p.score_rte,
                      a.couleur_reelle
               FROM predictions p
               JOIN actuals a ON p.date = a.date
               WHERE (p.score_temperature + p.score_budget + p.score_weekday
                      + p.score_gradient + p.score_clustering + p.score_rte) > 0
               ORDER BY p.date DESC
               LIMIT 500"""
        ).fetchall()

        if len(rows) < 30:
            logger.info(
                f"[Poids] Pas assez de donnees avec sub-scores ({len(rows)}/30), "
                "en attente d'accumulation de nouvelles predictions"
            )
            return None

        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import train_test_split

        X = []
        y = []
        label_map = {"BLEU": 0, "BLANC": 1, "ROUGE": 2}

        for row in rows:
            X.append([
                row["score_temperature"],
                row["score_budget"],
                row["score_weekday"],
                row["score_gradient"],
                row["score_clustering"],
                row["score_rte"],
            ])
            y.append(label_map.get(row["couleur_reelle"], 0))

        X = np.array(X)
        y = np.array(y)

        # Au moins 2 classes presentes
        unique_classes = set(y)
        if len(unique_classes) < 2:
            logger.info("[Poids] Pas assez de diversite dans les labels")
            return None

        # Fix #11 : split train/test 80/20 avec stratification
        try:
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42, stratify=y
            )
        except ValueError:
            # Stratification impossible si une classe a < 2 exemples
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42
            )

        # Fix #6 : class_weight='balanced' pour contrer le desequilibre
        model = LogisticRegression(
            multi_class="multinomial", max_iter=1000, C=1.0,
            class_weight="balanced",
        )
        model.fit(X_train, y_train)

        # Fix #11 : evaluer sur le test set
        test_accuracy = round(model.score(X_test, y_test) * 100, 1)

        # Extraire l'importance relative (norme L2 des coefficients)
        importance = np.sqrt((model.coef_ ** 2).sum(axis=0))
        total_imp = importance.sum()
        if total_imp == 0:
            logger.warning("[Poids] Importance totale nulle, abandon")
            return None

        raw_weights = {
            "temperature": float(importance[0] / total_imp),
            "jours_restants": float(importance[1] / total_imp),
            "jour_semaine": float(importance[2] / total_imp),
            "gradient_thermique": float(importance[3] / total_imp),
            "clustering": float(importance[4] / total_imp),
            "consommation_rte": float(importance[5] / total_imp),
        }

        # Fix #4 : bornes [0.05, 0.50] — aucun facteur desactive ni dominant
        WEIGHT_MIN = 0.05
        WEIGHT_MAX = 0.50
        bounded = {
            k: max(WEIGHT_MIN, min(WEIGHT_MAX, v))
            for k, v in raw_weights.items()
        }
        total_bounded = sum(bounded.values())
        bounded = {k: v / total_bounded for k, v in bounded.items()}

        # Fix #4 : lissage EMA (alpha=0.5) avec les anciens poids
        old_weights = get_current_weights()
        ALPHA = 0.5
        smoothed = {}
        for key in bounded:
            old_val = old_weights.get(key, bounded[key])
            smoothed[key] = ALPHA * bounded[key] + (1 - ALPHA) * old_val

        # Renormaliser apres lissage
        total_smooth = sum(smoothed.values())
        new_weights = {
            k: round(v / total_smooth, 4) for k, v in smoothed.items()
        }

        precision_avant = get_accuracy_global(30)["precision"]

        # Fix #4 : validation — deployer seulement si test accuracy > 40%
        if test_accuracy < 40:
            logger.warning(
                f"[Poids] Test accuracy trop faible ({test_accuracy}%), "
                "poids NON deployes"
            )
            conn.execute(
                """INSERT INTO weights_history
                   (date_update, weights_json, precision_avant, precision_apres,
                    nb_predictions, commentaire, timestamp_update)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now().strftime("%Y-%m-%d"),
                 json.dumps(new_weights),
                 precision_avant, test_accuracy, count,
                 f"REJETE (test_acc={test_accuracy}%) — ancien: "
                 f"{json.dumps(old_weights)}",
                 datetime.now().isoformat()),
            )
            conn.commit()
            return None

        # Fix #2 audit v4 : precision_apres = 0 (placeholder), sera mise a jour
        # au prochain recalcul par _update_previous_precision_apres.
        # test_accuracy est stockee dans le commentaire pour reference.
        conn.execute(
            """INSERT INTO weights_history
               (date_update, weights_json, precision_avant, precision_apres,
                nb_predictions, commentaire, timestamp_update)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now().strftime("%Y-%m-%d"),
             json.dumps(new_weights),
             precision_avant, 0, count,
             f"Recalcul auto (test_acc={test_accuracy}%) — "
             f"ancien: {json.dumps(old_weights)}",
             datetime.now().isoformat()),
        )
        conn.commit()

        logger.info(f"[Poids] Nouveaux poids deployes : {new_weights}")
        logger.info(
            f"[Poids] Anciens : {old_weights} | "
            f"Test accuracy : {test_accuracy}%"
        )
        return new_weights

    except ImportError:
        logger.error("[Poids] scikit-learn non disponible, recalcul impossible")
        return None
    except Exception as e:
        logger.error(f"[Poids] Erreur recalcul : {e}")
        return None
    finally:
        conn.close()


def _update_previous_precision_apres(conn):
    """Fix #5 : met a jour precision_apres de la derniere entree weights_history.

    Appelee au debut de chaque recalcul mensuel pour enregistrer
    la precision obtenue avec les poids du mois precedent.
    """
    last_entry = conn.execute(
        "SELECT id, precision_apres FROM weights_history ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if last_entry and last_entry["precision_apres"] == 0:
        current_precision = get_accuracy_global(30)["precision"]
        conn.execute(
            "UPDATE weights_history SET precision_apres = ? WHERE id = ?",
            (current_precision, last_entry["id"]),
        )
        logger.info(
            f"[Poids] precision_apres mise a jour pour id={last_entry['id']}: "
            f"{current_precision}%"
        )



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
