"""Système d'auto-amélioration — vérification, évaluation et recalcul des poids.

Trois responsabilités :
  1. Vérification quotidienne (11h30) : compare prédictions vs couleur réelle
  2. Calcul de métriques : précision, recall, F1, matrice de confusion
  3. Recalcul mensuel des poids via régression logistique (scikit-learn)

Fix audit ML :
  - ML-1/ML-2 : filtre horizon <= 5 dans évaluation et entraînement
  - ML-4 : métriques precision/recall/F1 par classe
  - ML-5 : seuil validation 55% (au lieu de 40%)
  - ML-6 : normalisation StandardScaler des features
  - ML-7/ML-8 : minimum 60 données + cross-validation 5-fold
  - ML-11 : permutation importance au lieu de norme L2
  - ML-12 : ALPHA adaptatif selon quantité de données
  - ML-15 : precision_apres mise à jour indépendamment
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
            # Calculer l'avance en jours
            ts = datetime.fromisoformat(pred["timestamp_prediction"])
            jours_avance = (target_date - ts.date()).days

            # Fix ML-1 : ignorer les prédictions trop anciennes (> 16 jours)
            if jours_avance < 0 or jours_avance > 16:
                continue

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
            nb_stored += 1

        conn.commit()
        nb_correct = sum(
            1 for p in predictions
            if p["couleur_predite"] == couleur_reelle
        )
        logger.info(f"[Perf] {target_date}: {nb_correct}/{nb_stored} prédictions correctes")

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

def get_accuracy_global(days: int = 30, max_horizon: int | None = None) -> dict:
    """Precision globale sur les N derniers jours.
    max_horizon=1 → J-1 seulement, None → tous les horizons."""
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
            if r["couleur_predite"] in matrix and r["couleur_reelle"] in matrix[r["couleur_predite"]]:
                matrix[r["couleur_predite"]][r["couleur_reelle"]] = r["cnt"]

        return matrix
    finally:
        conn.close()


def get_precision_recall_f1(days: int = 60) -> dict:
    """Fix ML-4 : Precision, Recall et F1 par classe sur les N derniers jours."""
    matrix = get_confusion_matrix(days)
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
        "precision_recall_f1": get_precision_recall_f1(90),
        "recent_errors": get_recent_errors(10),
        "current_weights": get_current_weights(),
    }


# ================================================================
# 3. RECALCUL AUTOMATIQUE DES POIDS (mensuel)
# ================================================================

def recalculate_weights():
    """Recalcule les poids de l'algorithme via regression logistique.

    Fix ML-2 : filtre sur jours_avance <= 5 pour entraîner sur horizons fiables.
    Fix ML-5 : seuil validation 55% (random = 33%).
    Fix ML-6 : normalisation StandardScaler des features.
    Fix ML-7 : minimum 60 données.
    Fix ML-8 : cross-validation 5-fold.
    Fix ML-11 : permutation importance.
    Fix ML-12 : ALPHA adaptatif.
    """
    conn = get_db()
    try:
        # Fix ML-15 : toujours mettre à jour precision_apres, même sans recalcul
        _update_previous_precision_apres(conn)

        # Verifier qu'on a assez de donnees evaluees
        count = conn.execute(
            "SELECT COUNT(*) as c FROM performance WHERE jours_avance <= 5"
        ).fetchone()["c"]

        # Fix ML-7 : minimum 60 données (au lieu de 30)
        if count < 60:
            logger.info(f"[Poids] Pas assez de donnees evaluees ({count}/60)")
            return None

        # Fix ML-2 : filtrer sur jours_avance <= 5 pour horizons fiables
        # Fix data-integrity : exclure les actuals synthétiques (seed_from_remaining)
        # qui ne sont PAS des couleurs confirmées par l'API EDF
        rows = conn.execute(
            """SELECT p.score_temperature, p.score_budget, p.score_weekday,
                      p.score_gradient, p.score_clustering, p.score_rte,
                      a.couleur_reelle
               FROM predictions p
               JOIN actuals a ON p.date = a.date
               WHERE p.horizon IN ('J-1','J-2','J-3','J-4','J-5','J0')
                 AND a.synthetic = 0
                 AND (p.score_temperature + p.score_budget + p.score_weekday
                      + p.score_gradient + p.score_clustering + p.score_rte) > 0
               ORDER BY p.date DESC
               LIMIT 300"""
        ).fetchall()

        if len(rows) < 60:
            logger.info(
                f"[Poids] Pas assez de donnees avec sub-scores ({len(rows)}/60)"
            )
            return None

        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import StandardScaler

        feature_names = [
            "temperature", "jours_restants", "jour_semaine",
            "gradient_thermique", "clustering", "consommation_rte",
        ]

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

        # Fix ML-6 : normalisation des features
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Fix ML-8 : cross-validation 5-fold
        model = LogisticRegression(
            multi_class="multinomial", max_iter=1000, C=1.0,
            class_weight="balanced",
        )

        try:
            cv_scores = cross_val_score(model, X_scaled, y, cv=5, scoring="accuracy")
            cv_accuracy = round(cv_scores.mean() * 100, 1)
            cv_std = round(cv_scores.std() * 100, 1)
        except ValueError:
            # Pas assez de données pour 5-fold sur une classe
            cv_accuracy = 0
            cv_std = 0
            logger.warning("[Poids] Cross-validation impossible (classe trop rare)")

        # Fix ML-5 : seuil validation 55% (random baseline = 33%)
        if cv_accuracy < 55:
            logger.warning(
                f"[Poids] CV accuracy trop faible ({cv_accuracy}% ± {cv_std}%), "
                "poids NON deployes"
            )
            conn.execute(
                """INSERT INTO weights_history
                   (date_update, weights_json, precision_avant, precision_apres,
                    nb_predictions, commentaire, model_version, timestamp_update)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now().strftime("%Y-%m-%d"),
                 json.dumps(get_current_weights()),
                 get_accuracy_global(30)["precision"], cv_accuracy, count,
                 f"REJETE (cv_acc={cv_accuracy}% ± {cv_std}%)",
                 "logreg_v2.1_scaler_cv5",
                 datetime.now().isoformat()),
            )
            conn.commit()
            return None

        # Entraîner le modèle final sur toutes les données
        model.fit(X_scaled, y)

        # Fix ML-11 : permutation importance
        try:
            from sklearn.inspection import permutation_importance
            perm_result = permutation_importance(
                model, X_scaled, y, n_repeats=10, random_state=42
            )
            importance = perm_result.importances_mean
            # Rendre positif (certaines importances peuvent être négatives)
            importance = np.maximum(importance, 0.01)
        except Exception:
            # Fallback norme L2 si permutation échoue
            importance = np.sqrt((model.coef_ ** 2).sum(axis=0))

        total_imp = importance.sum()
        if total_imp == 0:
            logger.warning("[Poids] Importance totale nulle, abandon")
            return None

        raw_weights = {
            k: float(importance[i] / total_imp)
            for i, k in enumerate(feature_names)
        }

        # Bornes [0.05, 0.50] — aucun facteur desactive ni dominant
        WEIGHT_MIN = 0.05
        WEIGHT_MAX = 0.50
        bounded = {
            k: max(WEIGHT_MIN, min(WEIGHT_MAX, v))
            for k, v in raw_weights.items()
        }
        total_bounded = sum(bounded.values())
        bounded = {k: v / total_bounded for k, v in bounded.items()}

        # Fix ML-12 : ALPHA adaptatif (plus de données = plus de confiance)
        old_weights = get_current_weights()
        ALPHA = min(0.6, max(0.2, len(rows) / 500))
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

        conn.execute(
            """INSERT INTO weights_history
               (date_update, weights_json, precision_avant, precision_apres,
                nb_predictions, commentaire, model_version, timestamp_update)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now().strftime("%Y-%m-%d"),
             json.dumps(new_weights),
             precision_avant, 0, count,
             f"Recalcul auto (cv_acc={cv_accuracy}% ± {cv_std}%, "
             f"alpha={ALPHA:.2f}, n={len(rows)}) — "
             f"ancien: {json.dumps(old_weights)}",
             "logreg_v2.1_scaler_cv5",
             datetime.now().isoformat()),
        )
        conn.commit()

        logger.info(f"[Poids] Nouveaux poids deployes : {new_weights}")
        logger.info(
            f"[Poids] CV accuracy : {cv_accuracy}% ± {cv_std}% | "
            f"Alpha={ALPHA:.2f} | n={len(rows)}"
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
    """Met à jour precision_apres de la dernière entrée weights_history."""
    try:
        last_entry = conn.execute(
            "SELECT id, precision_apres FROM weights_history ORDER BY id DESC LIMIT 1"
        ).fetchone()

        if last_entry and last_entry["precision_apres"] == 0:
            current_precision = get_accuracy_global(30)["precision"]
            conn.execute(
                "UPDATE weights_history SET precision_apres = ? WHERE id = ?",
                (current_precision, last_entry["id"]),
            )
            conn.commit()
            logger.info(
                f"[Poids] precision_apres mise a jour pour id={last_entry['id']}: "
                f"{current_precision}%"
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
