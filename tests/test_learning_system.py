"""Tests pour les améliorations du système d'apprentissage (audit learning system).

Couvre :
  - C-1 : Raw sub-scores stockés avant corrections
  - D-1 : Performance UNIQUE multi-horizon
  - A-1 : Validation impact des corrections
  - A-2 : Wilson interval confidence
  - W-5 : Auto-rollback poids
  - C-3 : Kill-switch corrections nocives
  - A-5 : Historique versionné des corrections
  - A-6 : Garde anti double-exécution
  - W-6 : Plus de LIMIT 300
  - Monitoring : Métriques de santé
"""

import os
import sys
import math
import json
import pytest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ================================================================
# C-1 : Raw sub-scores stockés avant corrections
# ================================================================

class TestRawSubScores:
    def test_predict_day_returns_raw_scores(self):
        """predict_day retourne les raw sub-scores avant corrections."""
        from predictor import predict_day
        target = date(2026, 1, 15)
        weather = {"temp_min": 0, "temp_max": 5, "temp_moy": 2, "wind_speed": 15,
                    "pressure": 1010, "humidity": 80, "description": "couvert",
                    "forecast_quality": "api"}
        remaining = {"ROUGE": 10, "BLANC": 20, "BLEU": 100}
        result = predict_day(target, weather=weather, remaining=remaining)

        # Doit contenir les 6 raw sub-scores
        assert "score_temperature_raw" in result
        assert "score_budget_raw" in result
        assert "score_weekday_raw" in result
        assert "score_gradient_raw" in result
        assert "score_clustering_raw" in result
        assert "score_rte_raw" in result

    def test_raw_scores_equal_when_no_corrections(self):
        """Sans corrections, raw == corrected."""
        from predictor import predict_day
        target = date(2026, 1, 15)
        weather = {"temp_min": 0, "temp_max": 5, "temp_moy": 2, "wind_speed": 10,
                    "pressure": 1010, "humidity": 80, "description": "couvert",
                    "forecast_quality": "api"}
        remaining = {"ROUGE": 10, "BLANC": 20, "BLEU": 100}
        # Sans learnings, les raw et corrected doivent être identiques
        result = predict_day(target, weather=weather, remaining=remaining,
                            _learnings=None)

        assert result["score_temperature_raw"] == result["score_temperature"]
        assert result["score_budget_raw"] == result["score_budget"]
        assert result["score_rte_raw"] == result["score_rte"]

    def test_raw_scores_differ_with_corrections(self):
        """Avec corrections de facteur, raw != corrected."""
        from predictor import predict_day
        target = date(2026, 1, 15)
        weather = {"temp_min": 0, "temp_max": 5, "temp_moy": 2, "wind_speed": 10,
                    "pressure": 1010, "humidity": 80, "description": "couvert",
                    "forecast_quality": "api"}
        remaining = {"ROUGE": 10, "BLANC": 20, "BLEU": 100}
        # Learnings avec correction de facteur temperature
        learnings = {"factor": {"temperature:over": -5.0}}
        result = predict_day(target, weather=weather, remaining=remaining,
                            _learnings=learnings)

        # Raw doit être plus élevé que corrected (correction négative)
        assert result["score_temperature_raw"] > result["score_temperature"]
        assert result["score_temperature_raw"] - result["score_temperature"] == pytest.approx(5.0, abs=0.5)


# ================================================================
# D-1 : Performance UNIQUE multi-horizon
# ================================================================

class TestPerformanceMultiHorizon:
    def test_multi_horizon_stored(self):
        """Deux évaluations du même jour avec des horizons différents sont stockées."""
        from database import get_db, init_db
        init_db()
        conn = get_db()
        try:
            # Nettoyer
            conn.execute("DELETE FROM performance WHERE date_cible = '2026-01-15'")
            conn.commit()

            # Insérer deux horizons pour le même jour
            conn.execute(
                """INSERT OR IGNORE INTO performance
                   (date_prediction, date_cible, jours_avance, correct,
                    couleur_predite, couleur_reelle, score_risque_predit,
                    ecart_score, contexte_meteo, timestamp_evaluation)
                   VALUES ('2026-01-14', '2026-01-15', 1, 1, 'ROUGE', 'ROUGE',
                           75, 5, '', ?)""",
                (datetime.now().isoformat(),)
            )
            conn.execute(
                """INSERT OR IGNORE INTO performance
                   (date_prediction, date_cible, jours_avance, correct,
                    couleur_predite, couleur_reelle, score_risque_predit,
                    ecart_score, contexte_meteo, timestamp_evaluation)
                   VALUES ('2026-01-14', '2026-01-15', 3, 0, 'BLANC', 'ROUGE',
                           55, 20, '', ?)""",
                (datetime.now().isoformat(),)
            )
            conn.commit()

            # Vérifier que les deux sont stockées
            rows = conn.execute(
                "SELECT * FROM performance WHERE date_cible = '2026-01-15'"
            ).fetchall()
            assert len(rows) >= 2, f"Expected 2 rows, got {len(rows)}"

            horizons = {r["jours_avance"] for r in rows}
            assert 1 in horizons
            assert 3 in horizons
        finally:
            conn.execute("DELETE FROM performance WHERE date_cible = '2026-01-15'")
            conn.commit()
            conn.close()


# ================================================================
# A-2 : Wilson interval confidence
# ================================================================

class TestWilsonConfidence:
    def test_wilson_lower_bound_small_sample(self):
        """Avec petit échantillon, Wilson est conservatif."""
        from performance_tracker import _wilson_lower_bound
        # p=0.5, n=5 → lower bound bien en dessous de 0.5
        lb = _wilson_lower_bound(0.5, 5)
        assert 0.1 < lb < 0.5, f"Wilson lower bound with n=5: {lb}"

    def test_wilson_lower_bound_large_sample(self):
        """Avec grand échantillon, Wilson est proche de p."""
        from performance_tracker import _wilson_lower_bound
        lb = _wilson_lower_bound(0.5, 1000)
        assert lb > 0.45, f"Wilson lower bound with n=1000: {lb}"

    def test_wilson_zero(self):
        """Wilson avec p=0 retourne 0."""
        from performance_tracker import _wilson_lower_bound
        assert _wilson_lower_bound(0, 100) == 0.0

    def test_correction_more_conservative(self):
        """Avec Wilson, les corrections sont plus faibles pour petits échantillons."""
        from performance_tracker import _compute_correction
        # Petit échantillon : correction doit être atténuée
        corr_small, conf_small = _compute_correction("over", 0.5, 5)
        corr_large, conf_large = _compute_correction("over", 0.5, 100)
        assert abs(corr_small) < abs(corr_large), \
            f"Small sample correction ({corr_small}) should be weaker than large ({corr_large})"


# ================================================================
# A-5 : Historique versionné des corrections
# ================================================================

class TestCorrectionHistory:
    def test_learning_journal_versioned(self):
        """Les corrections sont versionnées par date_analysis."""
        from database import get_db, init_db
        init_db()
        conn = get_db()
        try:
            # Nettoyer
            conn.execute("DELETE FROM learning_journal WHERE pattern_type = 'test_version'")
            conn.commit()

            # Insérer deux corrections pour le même pattern à des dates différentes
            conn.execute(
                """INSERT OR REPLACE INTO learning_journal
                   (date_analysis, pattern_type, pattern_key, observation,
                    accuracy, bias_direction, bias_magnitude,
                    sample_size, correction_score, confidence, active, created_at)
                   VALUES ('2026-01-01', 'test_version', 'key1', 'old', 0.7, 'over', 0.3,
                           20, -2.0, 0.8, 0, ?)""",
                (datetime.now().isoformat(),)
            )
            conn.execute(
                """INSERT OR REPLACE INTO learning_journal
                   (date_analysis, pattern_type, pattern_key, observation,
                    accuracy, bias_direction, bias_magnitude,
                    sample_size, correction_score, confidence, active, created_at)
                   VALUES ('2026-01-15', 'test_version', 'key1', 'new', 0.8, 'over', 0.2,
                           30, -1.5, 0.9, 1, ?)""",
                (datetime.now().isoformat(),)
            )
            conn.commit()

            # Les deux entrées doivent coexister
            rows = conn.execute(
                "SELECT * FROM learning_journal WHERE pattern_type = 'test_version'"
            ).fetchall()
            assert len(rows) == 2, f"Expected 2 versioned rows, got {len(rows)}"

            # Seule la plus récente est active
            active = [r for r in rows if r["active"] == 1]
            assert len(active) == 1
            assert active[0]["date_analysis"] == "2026-01-15"
        finally:
            conn.execute("DELETE FROM learning_journal WHERE pattern_type = 'test_version'")
            conn.commit()
            conn.close()


# ================================================================
# A-6 : Garde anti double-exécution
# ================================================================

class TestDeduplicationGuard:
    def test_analyze_skips_if_already_done_today(self):
        """analyze_error_patterns skip si déjà exécuté aujourd'hui."""
        from database import get_db, init_db
        init_db()
        conn = get_db()
        try:
            # Insérer une entrée d'analyse pour aujourd'hui
            today = date.today().isoformat()
            conn.execute(
                """INSERT OR REPLACE INTO learning_journal
                   (date_analysis, pattern_type, pattern_key, observation,
                    sample_size, correction_score, confidence, active, created_at)
                   VALUES (?, 'test_dedup', 'guard', 'test', 10, 0, 0, 1, ?)""",
                (today, datetime.now().isoformat())
            )
            conn.commit()
        finally:
            conn.close()

        from performance_tracker import analyze_error_patterns
        # Sans force=True, doit retourner [] car déjà analysé aujourd'hui
        result = analyze_error_patterns(days=90, force=False)
        assert result == []

    def test_analyze_runs_with_force(self):
        """analyze_error_patterns s'exécute avec force=True."""
        from performance_tracker import analyze_error_patterns
        # Avec force=True, ne skip pas (mais peut retourner [] si pas assez de données)
        result = analyze_error_patterns(days=90, force=True)
        # Le résultat est soit une liste de patterns soit [] si pas de données
        assert isinstance(result, list)


# ================================================================
# W-5 : Auto-rollback poids
# ================================================================

class TestAutoRollback:
    def test_rollback_column_exists(self):
        """La colonne rollback_of existe dans weights_history."""
        from database import get_db, init_db
        init_db()
        conn = get_db()
        try:
            # Vérifier que la colonne existe
            info = conn.execute("PRAGMA table_info(weights_history)").fetchall()
            columns = {r["name"] for r in info}
            assert "rollback_of" in columns
        finally:
            conn.close()

    def test_raw_score_columns_exist(self):
        """Les colonnes raw sub-scores existent dans predictions."""
        from database import get_db, init_db
        init_db()
        conn = get_db()
        try:
            info = conn.execute("PRAGMA table_info(predictions)").fetchall()
            columns = {r["name"] for r in info}
            for col in ["score_temperature_raw", "score_budget_raw", "score_weekday_raw",
                        "score_gradient_raw", "score_clustering_raw", "score_rte_raw"]:
                assert col in columns, f"Missing column: {col}"
        finally:
            conn.close()


# ================================================================
# C-3 : Kill-switch
# ================================================================

class TestKillSwitch:
    def test_killswitch_disables_strong_corrections(self):
        """Kill-switch désactive les corrections fortes quand accuracy < 50%."""
        from database import get_db, init_db
        init_db()
        conn = get_db()
        try:
            # Préparer : accuracy < 50% sur 14 jours
            since = (date.today() - timedelta(days=14)).isoformat()
            conn.execute("DELETE FROM performance WHERE date_cible >= ?", (since,))
            for i in range(15):
                d = (date.today() - timedelta(days=i)).isoformat()
                conn.execute(
                    """INSERT OR IGNORE INTO performance
                       (date_prediction, date_cible, jours_avance, correct,
                        couleur_predite, couleur_reelle, score_risque_predit,
                        ecart_score, contexte_meteo, timestamp_evaluation)
                       VALUES (?, ?, 1, ?, 'ROUGE', ?, 75, 10, '', ?)""",
                    (d, d, 0 if i < 10 else 1,  # 10 erreurs, 5 correctes = 33%
                     'BLEU' if i < 10 else 'ROUGE',
                     datetime.now().isoformat())
                )

            # Ajouter une correction forte
            conn.execute(
                """INSERT OR REPLACE INTO learning_journal
                   (date_analysis, pattern_type, pattern_key, observation,
                    sample_size, correction_score, confidence, active, created_at)
                   VALUES (?, 'test_killswitch', 'strong', 'test', 20, -5.0, 0.9, 1, ?)""",
                (date.today().isoformat(), datetime.now().isoformat())
            )
            conn.commit()
        finally:
            conn.close()

        from performance_tracker import killswitch_harmful_corrections
        disabled = killswitch_harmful_corrections()

        # Vérifier qu'au moins une correction a été désactivée
        assert len(disabled) >= 1

        # Vérifier en DB
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT active, disabled_at FROM learning_journal "
                "WHERE pattern_type = 'test_killswitch' AND pattern_key = 'strong'"
            ).fetchone()
            if row:
                assert row["active"] == 0
                assert row["disabled_at"] is not None
        finally:
            # Nettoyer
            conn.execute("DELETE FROM learning_journal WHERE pattern_type = 'test_killswitch'")
            conn.execute(
                "DELETE FROM performance WHERE date_cible >= ?",
                ((date.today() - timedelta(days=14)).isoformat(),)
            )
            conn.commit()
            conn.close()


# ================================================================
# Store prediction with raw sub-scores
# ================================================================

class TestStorePredictionRaw:
    def test_store_prediction_saves_raw_scores(self):
        """store_prediction persiste les raw sub-scores en DB."""
        from database import get_db, init_db
        from predictor import store_prediction
        init_db()

        pred = {
            "date": "2099-01-01",
            "couleur_predite": "ROUGE",
            "probabilite_bleu": 0.1,
            "probabilite_blanc": 0.2,
            "probabilite_rouge": 0.7,
            "score_risque": 75,
            "raison": "test",
            "score_temperature": 80,
            "score_budget": 60,
            "score_weekday": 70,
            "score_gradient": 50,
            "score_clustering": 40,
            "score_rte": 55,
            "score_temperature_raw": 85,
            "score_budget_raw": 60,
            "score_weekday_raw": 70,
            "score_gradient_raw": 50,
            "score_clustering_raw": 40,
            "score_rte_raw": 55,
        }

        store_prediction(pred, "J-1", cycle_id="test_raw")

        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM predictions WHERE date = '2099-01-01' AND horizon = 'J-1'"
            ).fetchone()
            assert row is not None
            assert row["score_temperature_raw"] == 85
            assert row["score_temperature"] == 80
        finally:
            conn.execute("DELETE FROM predictions WHERE date = '2099-01-01'")
            conn.commit()
            conn.close()


# ================================================================
# Learning health monitoring
# ================================================================

class TestLearningHealth:
    def test_get_learning_health_returns_dict(self):
        """get_learning_health retourne un dict avec les métriques attendues."""
        from database import init_db
        init_db()
        from performance_tracker import get_learning_health
        health = get_learning_health()
        assert isinstance(health, dict)
        assert "active_corrections" in health
        assert "disabled_corrections" in health
        assert "weight_rollbacks" in health
        assert "accuracy_14d" in health


# ================================================================
# Performance UNIQUE constraint with jours_avance
# ================================================================

class TestPerformanceConstraint:
    def test_same_prediction_different_horizons(self):
        """Même date_prediction et date_cible mais horizons différents coexistent."""
        from database import get_db, init_db
        init_db()
        conn = get_db()
        try:
            conn.execute("DELETE FROM performance WHERE date_cible = '2099-12-31'")

            # Horizon 1
            conn.execute(
                """INSERT INTO performance
                   (date_prediction, date_cible, jours_avance, correct,
                    couleur_predite, couleur_reelle, timestamp_evaluation)
                   VALUES ('2099-12-30', '2099-12-31', 1, 1, 'BLEU', 'BLEU', ?)""",
                (datetime.now().isoformat(),)
            )
            # Horizon 2 (same date_prediction and date_cible!)
            conn.execute(
                """INSERT INTO performance
                   (date_prediction, date_cible, jours_avance, correct,
                    couleur_predite, couleur_reelle, timestamp_evaluation)
                   VALUES ('2099-12-30', '2099-12-31', 2, 0, 'ROUGE', 'BLEU', ?)""",
                (datetime.now().isoformat(),)
            )
            conn.commit()

            rows = conn.execute(
                "SELECT * FROM performance WHERE date_cible = '2099-12-31'"
            ).fetchall()
            assert len(rows) == 2
        finally:
            conn.execute("DELETE FROM performance WHERE date_cible = '2099-12-31'")
            conn.commit()
            conn.close()

    def test_duplicate_horizon_rejected(self):
        """Même date_prediction, date_cible ET jours_avance est un doublon."""
        from database import get_db, init_db
        import sqlite3
        init_db()
        conn = get_db()
        try:
            conn.execute("DELETE FROM performance WHERE date_cible = '2099-12-31'")

            conn.execute(
                """INSERT INTO performance
                   (date_prediction, date_cible, jours_avance, correct,
                    couleur_predite, couleur_reelle, timestamp_evaluation)
                   VALUES ('2099-12-30', '2099-12-31', 1, 1, 'BLEU', 'BLEU', ?)""",
                (datetime.now().isoformat(),)
            )
            conn.commit()

            # Le doublon doit être rejeté
            conn.execute(
                """INSERT OR IGNORE INTO performance
                   (date_prediction, date_cible, jours_avance, correct,
                    couleur_predite, couleur_reelle, timestamp_evaluation)
                   VALUES ('2099-12-30', '2099-12-31', 1, 0, 'ROUGE', 'BLEU', ?)""",
                (datetime.now().isoformat(),)
            )
            conn.commit()

            rows = conn.execute(
                """SELECT * FROM performance
                   WHERE date_cible = '2099-12-31' AND jours_avance = 1"""
            ).fetchall()
            # Toujours une seule row (le doublon a été ignoré)
            assert len(rows) == 1
            assert rows[0]["correct"] == 1  # Original conservé
        finally:
            conn.execute("DELETE FROM performance WHERE date_cible = '2099-12-31'")
            conn.commit()
            conn.close()
