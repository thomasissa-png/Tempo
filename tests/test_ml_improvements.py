"""Tests pour les 7 améliorations ML (ML-1 à ML-7)."""

import os
import sys
import math
import pytest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ================================================================
# ML-2 : Interpolation piecewise-linear
# ================================================================

class TestPiecewiseLinear:
    def test_exact_control_points(self):
        """Les points de controle donnent des valeurs exactes."""
        from predictor import _piecewise_linear
        points = [(0, 0), (5, 50), (10, 100)]
        assert _piecewise_linear(0, points) == 0
        assert _piecewise_linear(5, points) == 50
        assert _piecewise_linear(10, points) == 100

    def test_interpolation(self):
        """Entre deux points, interpolation lineaire."""
        from predictor import _piecewise_linear
        points = [(0, 0), (10, 100)]
        assert _piecewise_linear(5, points) == 50
        assert _piecewise_linear(2.5, points) == 25

    def test_clamp_below(self):
        """En dessous du premier point, valeur du premier point."""
        from predictor import _piecewise_linear
        points = [(0, 10), (10, 100)]
        assert _piecewise_linear(-5, points) == 10

    def test_clamp_above(self):
        """Au-dessus du dernier point, valeur du dernier point."""
        from predictor import _piecewise_linear
        points = [(0, 10), (10, 100)]
        assert _piecewise_linear(15, points) == 100

    def test_monotone_output(self):
        """Sortie monotone pour entree monotone si points sont monotones."""
        from predictor import _piecewise_linear, _TEMP_SCORE_POINTS
        temps = [i for i in range(-10, 25)]
        scores = [_piecewise_linear(t, _TEMP_SCORE_POINTS) for t in temps]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], \
                f"Non monotone: {temps[i]}→{scores[i]}, {temps[i+1]}→{scores[i+1]}"


# ================================================================
# ML-4 : Wind Chill
# ================================================================

class TestWindChill:
    def test_no_effect_above_10(self):
        """Wind chill inactif au-dessus de 10C."""
        from predictor import _wind_chill
        assert _wind_chill(15, 30) == 15

    def test_no_effect_low_wind(self):
        """Wind chill inactif si vent < 4.8 km/h."""
        from predictor import _wind_chill
        assert _wind_chill(5, 3) == 5

    def test_reduces_temperature(self):
        """Wind chill reduit la temperature effective."""
        from predictor import _wind_chill
        wc = _wind_chill(3, 20)
        assert wc < 3

    def test_stronger_wind_colder(self):
        """Plus de vent = temperature effective plus basse."""
        from predictor import _wind_chill
        wc_light = _wind_chill(0, 10)
        wc_strong = _wind_chill(0, 40)
        assert wc_strong < wc_light

    def test_colder_temp_stronger_effect(self):
        """A vent egal, les temperatures froides ont un effet plus marque."""
        from predictor import _wind_chill
        wc_warm = _wind_chill(8, 20)
        wc_cold = _wind_chill(-5, 20)
        # La difference avec la temp reelle est plus grande quand il fait froid
        diff_warm = 8 - wc_warm
        diff_cold = -5 - wc_cold
        assert diff_cold > diff_warm


# ================================================================
# ML-1 : Budget BLANC
# ================================================================

class TestBudgetBlanc:
    def test_blanc_only_pressure(self):
        """Avec seulement des jours BLANC restants, le score n'est pas 0."""
        from predictor import _score_budget_v2
        remaining = {"ROUGE": 0, "BLANC": 30, "BLEU": 100}
        target = date(2026, 1, 15)
        score = _score_budget_v2(remaining, 120, target)
        assert score > 0, "La pression BLANC seule devrait donner un score > 0"

    def test_blanc_pressure_capped_64(self):
        """Fix #26 : pression BLANC plafonnee a 64 (zone BLANC 35-65, pas ROUGE)."""
        from predictor import _score_budget_v2
        remaining = {"ROUGE": 0, "BLANC": 43, "BLEU": 50}
        target = date(2026, 1, 15)
        score = _score_budget_v2(remaining, 20, target)
        assert score <= 64

    def test_rouge_dominates_blanc(self):
        """Quand ROUGE pressure > BLANC pressure, ROUGE domine."""
        from predictor import _score_budget_v2
        # Forte pression rouge
        remaining = {"ROUGE": 15, "BLANC": 10, "BLEU": 50}
        target = date(2026, 1, 15)
        score = _score_budget_v2(remaining, 30, target)
        assert score > 55  # Depasse le plafond BLANC


# ================================================================
# ML-2 : Gradient continu
# ================================================================

class TestGradientContinuous:
    def test_continuous_gradient(self):
        """ML-2 : le gradient donne des valeurs continues entre les points."""
        from predictor import _score_gradient
        forecasts = [{"temp_moy": 10}, {"temp_moy": 10}]
        # drop = 0 → score autour de 25
        score = _score_gradient(forecasts, 1)
        assert 20 <= score <= 30

        # drop = 1.5 → entre 35 et 50
        forecasts[0]["temp_moy"] = 11.5
        score = _score_gradient(forecasts, 1)
        assert 30 < score < 55


# ================================================================
# ML-6 : Calibration probabilites
# ================================================================

class TestProbCalibration:
    def test_rouge_at_threshold(self):
        """Au seuil ROUGE, p_rouge ≈ 50%."""
        from predictor import _compute_probabilities
        from config import Config
        remaining = {"ROUGE": 10, "BLANC": 20}
        p_r, p_b, p_bl = _compute_probabilities(Config.SEUIL_ROUGE, remaining)
        # Avec steepness 0.15, p_rouge a SEUIL_ROUGE = 0.5
        assert 0.35 <= p_r <= 0.65, f"p_rouge au seuil devrait etre ~0.5, got {p_r}"

    def test_well_above_threshold(self):
        """Bien au-dessus du seuil, p_rouge > 0.8."""
        from predictor import _compute_probabilities
        remaining = {"ROUGE": 10, "BLANC": 20}
        p_r, p_b, p_bl = _compute_probabilities(85, remaining)
        assert p_r > 0.8

    def test_well_below_threshold(self):
        """Bien en dessous du seuil BLANC, p_bleu dominant."""
        from predictor import _compute_probabilities
        remaining = {"ROUGE": 10, "BLANC": 20}
        p_r, p_b, p_bl = _compute_probabilities(10, remaining)
        assert p_bl > 0.7


# ================================================================
# ML-7 : Saturation hebdomadaire clustering
# ================================================================

class TestWeeklyRedSaturation:
    def test_saturation_3_reds(self):
        """Apres 3 rouges dans la semaine, score clustering tres attenue."""
        from predictor import _score_clustering
        target = date(2026, 1, 15)  # Jeudi
        week_start = target - timedelta(days=target.weekday())

        # 3 rouges lundi-mardi-mercredi
        actuals = {}
        for i in range(3):
            d = week_start + timedelta(days=i)
            actuals[d.isoformat()] = "ROUGE"
        # Hier (mercredi) est rouge
        yesterday = target - timedelta(days=1)
        actuals[yesterday.isoformat()] = "ROUGE"

        forecasts = [{"temp_moy": 0}]
        score = _score_clustering(target, forecasts, 0, actuals)
        # Avec 3 rouges cette semaine, forte attenuation
        assert score < 30, f"Score devrait etre attenue avec 3 rouges/semaine, got {score}"

    def test_no_saturation_1_red(self):
        """Avec seulement 1 rouge dans la semaine, pas d'attenuation."""
        from predictor import _score_clustering
        target = date(2026, 1, 13)  # Mardi
        yesterday = target - timedelta(days=1)  # Lundi

        actuals = {yesterday.isoformat(): "ROUGE"}
        forecasts = [{"temp_moy": 0}]
        score = _score_clustering(target, forecasts, 0, actuals)
        # Rouge hier + froid qui continue = score eleve
        assert score >= 70


# ================================================================
# ML-3 : Config MONTHLY_WHITE_PROFILE
# ================================================================

class TestMonthlyWhiteProfile:
    def test_profile_sums_to_one(self):
        """Le profil BLANC doit sommer a 1.0."""
        from config import Config
        total = sum(Config.MONTHLY_WHITE_PROFILE.values())
        assert abs(total - 1.0) < 0.02

    def test_january_peak(self):
        """Janvier est le mois avec le plus de jours BLANC."""
        from config import Config
        jan = Config.MONTHLY_WHITE_PROFILE[1]
        for month, pct in Config.MONTHLY_WHITE_PROFILE.items():
            if month != 1:
                assert pct <= jan, f"Mois {month} ({pct}) > janvier ({jan})"


# ================================================================
# ML-5 : Temporal decay (test unitaire)
# ================================================================

class TestTemporalDecay:
    def test_recent_correction_stronger(self):
        """Les corrections recentes sont plus fortes que les anciennes."""
        from database import get_db
        from performance_tracker import get_active_learnings

        conn = get_db()
        today = date.today().isoformat()
        old_date = (date.today() - timedelta(days=90)).isoformat()

        # Inserer deux corrections : une recente, une ancienne
        conn.execute(
            """INSERT INTO learning_journal
               (date_analysis, pattern_type, pattern_key, observation,
                accuracy, bias_direction, bias_magnitude,
                sample_size, correction_score, confidence, active, created_at)
               VALUES (?, 'test_decay', 'recent', 'test', 0.8, 'over', 0.5,
                       20, -5.0, 0.8, 1, ?)""",
            (today, today)
        )
        conn.execute(
            """INSERT INTO learning_journal
               (date_analysis, pattern_type, pattern_key, observation,
                accuracy, bias_direction, bias_magnitude,
                sample_size, correction_score, confidence, active, created_at)
               VALUES (?, 'test_decay', 'old', 'test', 0.8, 'over', 0.5,
                       20, -5.0, 0.8, 1, ?)""",
            (old_date, old_date)
        )
        conn.commit()
        conn.close()

        learnings = get_active_learnings()
        recent = abs(learnings.get("test_decay", {}).get("recent", 0))
        old = abs(learnings.get("test_decay", {}).get("old", 0))
        assert recent > old, f"Recent ({recent}) should be > old ({old})"
