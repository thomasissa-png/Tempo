"""Tests pour le module performance_tracker.py — évaluation et apprentissage."""

import pytest
from datetime import date, timedelta, datetime
from performance_tracker import (
    evaluate_predictions_for_date, get_accuracy_global,
    get_confusion_matrix, get_precision_recall_f1,
    _compute_bias, _compute_correction, _color_rank,
    get_active_learnings, get_learning_summary,
    analyze_error_patterns, evaluate_missed_days,
)
from database import get_db
from predictor import predict_day, store_prediction


# ================================================================
# HELPER : insert test data
# ================================================================

def _insert_performance_rows(rows):
    """Insère des lignes dans la table performance pour les tests."""
    conn = get_db()
    for r in rows:
        conn.execute(
            """INSERT OR IGNORE INTO performance
               (date_prediction, date_cible, jours_avance, correct,
                couleur_predite, couleur_reelle, score_risque_predit,
                ecart_score, contexte_meteo, timestamp_evaluation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (r.get("date_prediction", date.today().isoformat()),
             r["date_cible"], r.get("jours_avance", 1),
             r["correct"], r["couleur_predite"], r["couleur_reelle"],
             r.get("score_risque_predit", 50), r.get("ecart_score", 10),
             r.get("contexte_meteo", ""), datetime.now().isoformat()),
        )
    conn.commit()
    conn.close()


def _insert_actual(date_str, couleur, synthetic=0):
    """Insère un actual dans la base."""
    conn = get_db()
    conn.execute(
        """INSERT OR IGNORE INTO actuals
           (date, couleur_reelle, synthetic, timestamp_confirmation)
           VALUES (?, ?, ?, ?)""",
        (date_str, couleur, synthetic, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ================================================================
# BIAS COMPUTATION
# ================================================================

class TestComputeBias:
    def test_empty_group(self):
        acc, over, under, direction, magnitude = _compute_bias([])
        assert acc == 0
        assert direction == "balanced"

    def test_all_correct(self):
        group = [
            {"correct": 1, "couleur_predite": "ROUGE", "couleur_reelle": "ROUGE"},
            {"correct": 1, "couleur_predite": "BLEU", "couleur_reelle": "BLEU"},
        ]
        acc, over, under, direction, magnitude = _compute_bias(group)
        assert acc == 1.0
        assert direction == "balanced"

    def test_over_prediction(self):
        """Quand on prédit systématiquement trop haut."""
        group = [
            {"correct": 0, "couleur_predite": "ROUGE", "couleur_reelle": "BLEU"},
            {"correct": 0, "couleur_predite": "ROUGE", "couleur_reelle": "BLANC"},
            {"correct": 0, "couleur_predite": "BLANC", "couleur_reelle": "BLEU"},
            {"correct": 1, "couleur_predite": "BLEU", "couleur_reelle": "BLEU"},
        ]
        acc, over, under, direction, magnitude = _compute_bias(group)
        assert direction == "over"
        assert over > under

    def test_under_prediction(self):
        """Quand on prédit systématiquement trop bas."""
        group = [
            {"correct": 0, "couleur_predite": "BLEU", "couleur_reelle": "ROUGE"},
            {"correct": 0, "couleur_predite": "BLEU", "couleur_reelle": "BLANC"},
            {"correct": 0, "couleur_predite": "BLANC", "couleur_reelle": "ROUGE"},
            {"correct": 1, "couleur_predite": "ROUGE", "couleur_reelle": "ROUGE"},
        ]
        acc, over, under, direction, magnitude = _compute_bias(group)
        assert direction == "under"
        assert under > over


class TestComputeCorrection:
    def test_over_negative_correction(self):
        correction, confidence = _compute_correction("over", 0.5, 20)
        assert correction < 0  # Sur-prédiction → correction négative

    def test_under_positive_correction(self):
        correction, confidence = _compute_correction("under", 0.5, 20)
        assert correction > 0  # Sous-prédiction → correction positive

    def test_balanced_no_correction(self):
        correction, confidence = _compute_correction("balanced", 0.0, 20)
        assert correction == 0

    def test_confidence_scales_with_sample(self):
        _, conf_small = _compute_correction("over", 0.5, 5)
        _, conf_large = _compute_correction("over", 0.5, 30)
        assert conf_large >= conf_small


class TestColorRank:
    def test_order(self):
        assert _color_rank("BLEU") < _color_rank("BLANC")
        assert _color_rank("BLANC") < _color_rank("ROUGE")


# ================================================================
# ACCURACY METRICS
# ================================================================

class TestAccuracyGlobal:
    def test_no_data(self):
        result = get_accuracy_global(30)
        assert result["total"] == 0
        assert result["precision"] == 0

    def test_with_data(self):
        today = date.today()
        rows = [
            {"date_cible": (today - timedelta(days=i)).isoformat(),
             "correct": 1 if i % 3 != 0 else 0,
             "couleur_predite": "BLEU", "couleur_reelle": "BLEU" if i % 3 != 0 else "BLANC"}
            for i in range(1, 11)
        ]
        _insert_performance_rows(rows)
        result = get_accuracy_global(30)
        assert result["total"] == 10
        # 7 correct out of 10 (i=1,2,4,5,7,8,10 are correct; i=3,6,9 are wrong)
        assert result["correct"] == 7
        assert result["precision"] == 70.0


class TestConfusionMatrix:
    def test_structure(self):
        matrix = get_confusion_matrix(30)
        for predicted in ("BLEU", "BLANC", "ROUGE"):
            assert predicted in matrix
            for actual in ("BLEU", "BLANC", "ROUGE"):
                assert actual in matrix[predicted]


class TestPrecisionRecallF1:
    def test_structure(self):
        metrics = get_precision_recall_f1(30)
        assert "weighted_f1" in metrics
        for couleur in ("BLEU", "BLANC", "ROUGE"):
            assert couleur in metrics
            assert "precision" in metrics[couleur]
            assert "recall" in metrics[couleur]
            assert "f1" in metrics[couleur]


# ================================================================
# LEARNING JOURNAL
# ================================================================

class TestActiveLearnings:
    def test_empty_journal(self):
        learnings = get_active_learnings()
        assert isinstance(learnings, dict)

    def test_learning_summary(self):
        summary = get_learning_summary()
        assert isinstance(summary, list)


class TestEvaluateMissedDays:
    def test_no_missed_days(self):
        """Sans actuals non évalués, retourne 0."""
        result = evaluate_missed_days(7)
        assert result == 0

    def test_evaluates_missed(self, sample_weather, sample_remaining):
        """Évalue les jours manqués ayant un actual mais pas de performance."""
        target = date.today() - timedelta(days=2)
        target_str = target.isoformat()

        # Créer une prédiction pour cette date
        weather = {
            "date": target_str, "temp_min": 2, "temp_max": 8,
            "temp_moy": 5, "humidity": 70, "wind_speed": 10,
            "pressure": None, "description": "test", "forecast_quality": "api",
        }
        pred = predict_day(target, weather=weather, remaining=sample_remaining)
        pred["confirmed"] = False
        pred["simulated"] = False
        store_prediction(pred, "J-1", cycle_id="test")

        # Créer un actual
        _insert_actual(target_str, "BLEU")

        # Évaluer
        result = evaluate_missed_days(7)
        assert result >= 1


# ================================================================
# EVALUATE PREDICTIONS FOR DATE
# ================================================================

class TestEvaluatePredictionsForDate:
    def test_creates_performance_entry(self, sample_weather, sample_remaining):
        """evaluate_predictions_for_date crée des entrées dans performance."""
        target = date.today() - timedelta(days=1)
        target_str = target.isoformat()

        # Stocker une prédiction avec un timestamp antérieur à la date cible
        # (simule une prédiction faite 2 jours avant)
        weather = {
            "date": target_str, "temp_min": 2, "temp_max": 8,
            "temp_moy": 5, "humidity": 70, "wind_speed": 10,
            "pressure": None, "description": "test", "forecast_quality": "api",
        }
        pred = predict_day(target, weather=weather, remaining=sample_remaining)
        pred["confirmed"] = False
        pred["simulated"] = False
        store_prediction(pred, "J-1", cycle_id="test")

        # Corriger le timestamp_prediction pour simuler une prédiction faite 2j avant
        conn = get_db()
        past_ts = (target - timedelta(days=1)).isoformat() + "T18:00:00"
        conn.execute(
            "UPDATE predictions SET timestamp_prediction = ? WHERE date = ?",
            (past_ts, target_str)
        )
        conn.commit()
        conn.close()

        # Évaluer
        evaluate_predictions_for_date(target, "BLEU")

        # Vérifier qu'une entrée performance existe
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM performance WHERE date_cible = ?", (target_str,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["couleur_reelle"] == "BLEU"


# ================================================================
# ANALYZE ERROR PATTERNS
# ================================================================

class TestAnalyzeErrorPatterns:
    def test_not_enough_data(self):
        """Pas assez de données → liste vide."""
        result = analyze_error_patterns(days=90)
        assert result == []

    def test_with_sufficient_data(self):
        """Avec assez de données, retourne des patterns."""
        today = date.today()
        rows = []
        for i in range(1, 30):
            d = today - timedelta(days=i)
            # Alternance pour créer des patterns
            predicted = "ROUGE" if i % 4 == 0 else "BLEU"
            actual = "BLEU"  # La plupart sont BLEU
            correct = 1 if predicted == actual else 0
            rows.append({
                "date_prediction": (d - timedelta(days=1)).isoformat(),
                "date_cible": d.isoformat(),
                "jours_avance": 1,
                "correct": correct,
                "couleur_predite": predicted,
                "couleur_reelle": actual,
            })
        _insert_performance_rows(rows)

        result = analyze_error_patterns(days=90)
        assert isinstance(result, list)
        # Devrait trouver au moins un pattern de sur-prédiction
        assert len(result) > 0
