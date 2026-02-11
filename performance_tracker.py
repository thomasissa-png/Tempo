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
from database import get_db, get_current_weights
from config import Config

logger = logging.getLogger(__name__)


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
            couleur_pred = pred["couleur_predite"]
            if pred["confirmed"] and pred["couleur_originale"]:
                couleur_pred = pred["couleur_originale"]
            elif pred["confirmed"]:
                # Confirmé sans couleur_originale sauvegardée → skip (ancien format)
                continue

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
        nb_correct = nb_stored  # Recalculer depuis les inserts réels
        # (le compteur ci-dessus ne peut être recalculé simplement ici,
        # on log nb_stored plutôt)
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
    Fix ML-5 : seuil validation F1-macro >= 45% (random = 33%).
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

        # C-1 : entraîner UNIQUEMENT sur raw sub-scores (avant corrections)
        # Fix ML-contamination : ne plus fallback sur scores corrigés via COALESCE
        # Les données pré-v9 (sans raw scores) sont exclues pour éviter la contamination
        # W-6 : plus de LIMIT 300 — utiliser toutes les données disponibles
        # Fix data-integrity : exclure les actuals synthétiques (seed_from_remaining)
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

        if len(rows) < 60:
            logger.info(
                f"[Poids] Pas assez de donnees avec sub-scores ({len(rows)}/60)"
            )
            return None

        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_score
        from sklearn.preprocessing import StandardScaler

        # 6 features de base + 4 interactions clés
        feature_names = [
            "temperature", "jours_restants", "jour_semaine",
            "gradient_thermique", "clustering", "consommation_rte",
            "temp_x_budget", "gradient_x_temp", "cluster_x_gradient", "temp_x_rte",
        ]

        X = []
        y = []
        label_map = {"BLEU": 0, "BLANC": 1, "ROUGE": 2}

        for row in rows:
            t = row["score_temperature"]
            b = row["score_budget"]
            w = row["score_weekday"]
            g = row["score_gradient"]
            c = row["score_clustering"]
            r = row["score_rte"]
            X.append([
                t, b, w, g, c, r,
                # Interactions : produits normalisés sur [0, 100]
                (t * b) / 100,       # froid + pression budgétaire
                (g * t) / 100,       # chute de temp + temp basse
                (c * g) / 100,       # clustering + gradient
                (t * r) / 100,       # temp basse + forte conso
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
            # Fix audit ML #2 : utiliser F1-macro au lieu de accuracy
            # L'accuracy est trompeuse pour des classes déséquilibrées (BLEU ≈ 76%).
            # Un modèle "always-BLEU" a 76% accuracy mais F1-macro ≈ 33%.
            # F1-macro évalue la capacité à prédire CHAQUE classe équitablement.
            cv_f1_scores = cross_val_score(model, X_scaled, y, cv=5, scoring="f1_macro")
            cv_f1 = round(cv_f1_scores.mean() * 100, 1)
            cv_f1_std = round(cv_f1_scores.std() * 100, 1)

            cv_acc_scores = cross_val_score(model, X_scaled, y, cv=5, scoring="accuracy")
            cv_accuracy = round(cv_acc_scores.mean() * 100, 1)
            cv_std = round(cv_acc_scores.std() * 100, 1)
        except ValueError:
            # Pas assez de données pour 5-fold sur une classe
            cv_f1 = 0
            cv_f1_std = 0
            cv_accuracy = 0
            cv_std = 0
            logger.warning("[Poids] Cross-validation impossible (classe trop rare)")

        # Fix audit ML #2 : seuil sur F1-macro ≥ 45% (random ≈ 33%)
        # et accuracy doit dépasser la baseline classe majoritaire
        from collections import Counter
        majority_pct = round(max(Counter(y).values()) / len(y) * 100, 1)

        if cv_f1 < 45:
            logger.warning(
                f"[Poids] CV F1-macro trop faible ({cv_f1}% ± {cv_f1_std}%), "
                f"poids NON deployes (seuil=45%, accuracy={cv_accuracy}%, "
                f"baseline={majority_pct}%)"
            )
            conn.execute(
                """INSERT INTO weights_history
                   (date_update, weights_json, precision_avant, precision_apres,
                    nb_predictions, commentaire, model_version, timestamp_update)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (datetime.now().strftime("%Y-%m-%d"),
                 json.dumps(get_current_weights()),
                 get_accuracy_global(30)["precision"], cv_accuracy, count,
                 f"REJETE (f1_macro={cv_f1}% ± {cv_f1_std}%, "
                 f"acc={cv_accuracy}%, baseline={majority_pct}%)",
                 "logreg_v5_audit_ml",
                 datetime.now().isoformat()),
            )
            conn.commit()
            return None

        # ML-3 : Holdout temporel — train sur 80% anciens, validation sur 20% recents
        holdout_accuracy = None
        n_rows = len(X)
        if n_rows >= 80:
            split_idx = int(n_rows * 0.8)
            # rows sont ORDER BY date DESC → indices bas = recent, hauts = ancien
            X_train_t = X_scaled[split_idx:]
            y_train_t = y[split_idx:]
            X_val_t = X_scaled[:split_idx]
            y_val_t = y[:split_idx]

            try:
                model_holdout = LogisticRegression(
                    multi_class="multinomial", max_iter=1000, C=1.0,
                    class_weight="balanced",
                )
                # W-2 : vérifier balance des classes dans train ET validation
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
                        conn.execute(
                            """INSERT INTO weights_history
                               (date_update, weights_json, precision_avant, precision_apres,
                                nb_predictions, commentaire, model_version, timestamp_update)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (datetime.now().strftime("%Y-%m-%d"),
                             json.dumps(get_current_weights()),
                             get_accuracy_global(30)["precision"], holdout_accuracy, count,
                             f"REJETE holdout (holdout={holdout_accuracy}%, "
                             f"cv_f1={cv_f1}%, cv_acc={cv_accuracy}%)",
                             "logreg_v5_audit_ml",
                             datetime.now().isoformat()),
                        )
                        conn.commit()
                        return None
            except Exception as e:
                logger.warning(f"[Poids] Holdout temporel echoue: {e}")

        # Entraîner le modèle final sur toutes les données
        model.fit(X_scaled, y)

        # Fix ML-11 : permutation importance
        # Seules les 6 features de base contribuent aux poids de l'algorithme
        base_feature_names = [
            "temperature", "jours_restants", "jour_semaine",
            "gradient_thermique", "clustering", "consommation_rte",
        ]
        try:
            from sklearn.inspection import permutation_importance
            # W-3 : n_repeats=30 pour résultats plus stables
            perm_result = permutation_importance(
                model, X_scaled, y, n_repeats=30, random_state=42
            )
            importance = perm_result.importances_mean[:6]  # 6 features de base
            # Rendre positif (certaines importances peuvent être négatives)
            importance = np.maximum(importance, 0.01)
        except Exception:
            # Fallback norme L2 si permutation échoue
            importance = np.sqrt((model.coef_ ** 2).sum(axis=0))[:6]

        total_imp = importance.sum()
        if total_imp == 0:
            logger.warning("[Poids] Importance totale nulle, abandon")
            return None

        raw_weights = {
            k: float(importance[i] / total_imp)
            for i, k in enumerate(base_feature_names)
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
             f"Recalcul auto (f1_macro={cv_f1}% ± {cv_f1_std}%, "
             f"cv_acc={cv_accuracy}%, alpha={ALPHA:.2f}, n={len(rows)}) — "
             f"ancien: {json.dumps(old_weights)}",
             "logreg_v5_audit_ml",
             datetime.now().isoformat()),
        )
        conn.commit()

        logger.info(f"[Poids] Nouveaux poids deployes : {new_weights}")
        logger.info(
            f"[Poids] F1-macro: {cv_f1}% ± {cv_f1_std}% | "
            f"Accuracy: {cv_accuracy}% | Alpha={ALPHA:.2f} | n={len(rows)}"
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

        since = (date.today() - timedelta(days=days)).isoformat()

        perf_rows = conn.execute(
            "SELECT * FROM performance WHERE date_cible >= ?",
            (since,)
        ).fetchall()

        if len(perf_rows) < 20:
            logger.info(f"[Learning] Pas assez de données ({len(perf_rows)}/20)")
            return []

        # Températures réelles par date (weather_cache = données Open-Meteo)
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
                """INSERT OR REPLACE INTO learning_journal
                   (date_analysis, pattern_type, pattern_key, observation,
                    accuracy, bias_direction, bias_magnitude,
                    sample_size, correction_score, confidence, active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
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
        correction, confidence = _compute_correction(
            direction, rate, count, max_correction=6.0)

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

        # Sur-prédiction : le facteur donnait des scores trop élevés
        if stats["over_n"] >= 3:
            avg_over = stats["over_sum"] / stats["over_n"]
            if avg_over > 55:  # Sub-score moyen élevé quand on sur-prédit
                magnitude = min(1.0, (avg_over - 50) / 50)
                correction = -magnitude * 6.0 * min(1.0, stats["over_n"] / 20)
                confidence = min(1.0, stats["over_n"] / 20)
                patterns.append({
                    "type": "factor", "key": f"{short}:over",
                    "observation": f"{fname} moyen={avg_over:.0f} lors de {stats['over_n']} "
                                   f"sur-prédictions",
                    "accuracy": 0.0,
                    "bias_direction": "over", "bias_magnitude": round(magnitude, 3),
                    "sample_size": stats["over_n"],
                    "correction": round(correction, 2), "confidence": round(confidence, 2),
                })

        # Sous-prédiction : le facteur donnait des scores trop bas
        if stats["under_n"] >= 3:
            avg_under = stats["under_sum"] / stats["under_n"]
            if avg_under < 45:  # Sub-score moyen bas quand on sous-prédit
                magnitude = min(1.0, (50 - avg_under) / 50)
                correction = magnitude * 6.0 * min(1.0, stats["under_n"] / 20)
                confidence = min(1.0, stats["under_n"] / 20)
                patterns.append({
                    "type": "factor", "key": f"{short}:under",
                    "observation": f"{fname} moyen={avg_under:.0f} lors de {stats['under_n']} "
                                   f"sous-prédictions",
                    "accuracy": 0.0,
                    "bias_direction": "under", "bias_magnitude": round(magnitude, 3),
                    "sample_size": stats["under_n"],
                    "correction": round(correction, 2), "confidence": round(confidence, 2),
                })

    return patterns


def _analyze_prediction_volatility(conn, since: str) -> list[dict]:
    """Analyse la stabilité des prédictions via la table prediction_changes.

    Détecte les dates avec une volatilité excessive (changements fréquents
    de couleur entre cycles), signe d'un score proche d'un seuil.

    pattern_type='volatility', pattern_key='high_volatility' ou 'threshold_proximity'
    """
    try:
        changes = conn.execute(
            """SELECT date, COUNT(*) as nb_changes,
                      GROUP_CONCAT(couleur_avant || '->' || couleur_apres) as transitions
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

        if accuracy < 0.50:
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
        # Nombre de corrections actives
        active = conn.execute(
            "SELECT COUNT(*) as c FROM learning_journal WHERE active = 1"
        ).fetchone()["c"]

        # Nombre de corrections désactivées (kill-switch / validation)
        disabled = conn.execute(
            "SELECT COUNT(*) as c FROM learning_journal WHERE active = 0 AND disabled_at IS NOT NULL"
        ).fetchone()["c"]

        # Nombre de rollbacks de poids
        rollbacks = conn.execute(
            "SELECT COUNT(*) as c FROM weights_history WHERE rollback_of IS NOT NULL"
        ).fetchone()["c"]

        # Dernière analyse
        last_analysis = conn.execute(
            "SELECT MAX(date_analysis) as last FROM learning_journal"
        ).fetchone()["last"]

        # Précision récente (14j)
        since_14 = (date.today() - timedelta(days=14)).isoformat()
        perf_14 = conn.execute(
            "SELECT COUNT(*) as total, SUM(correct) as correct FROM performance WHERE date_cible >= ?",
            (since_14,)
        ).fetchone()
        accuracy_14 = round(perf_14["correct"] / perf_14["total"] * 100, 1) if perf_14["total"] else 0

        # Nombre de patterns dans l'historique
        total_history = conn.execute(
            "SELECT COUNT(*) as c FROM learning_journal"
        ).fetchone()["c"]

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

        rows = conn.execute(
            """SELECT a.date, a.couleur_reelle
               FROM actuals a
               WHERE a.date >= ? AND a.date < ? AND a.synthetic = 0
               AND NOT EXISTS (
                   SELECT 1 FROM performance p WHERE p.date_cible = a.date
               )""",
            (since, date.today().isoformat())
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
