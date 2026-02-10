"""Tests pour le module predictor.py — algorithme de prédiction Tempo."""

import pytest
from datetime import date, timedelta
from predictor import (
    predict_day, predict_range, _score_temperature_v2, _score_budget_v2,
    _score_weekday_v2, _score_gradient, _score_clustering, _detect_cold_wave,
    _compute_probabilities, _apply_learning_corrections, _apply_factor_correction,
    is_french_holiday, confirm_prediction, store_prediction,
    _piecewise_linear, _wind_chill,
)
from config import Config


# ================================================================
# SCORING FUNCTIONS
# ================================================================

class TestScoreTemperature:
    def test_control_points(self):
        """ML-2 : les points de controle donnent des valeurs exactes."""
        assert _score_temperature_v2(-5) == 98
        assert _score_temperature_v2(-2) == 90
        assert _score_temperature_v2(0) == 80
        assert _score_temperature_v2(2) == 68
        assert _score_temperature_v2(4) == 55
        assert _score_temperature_v2(6) == 40
        assert _score_temperature_v2(8) == 25
        assert _score_temperature_v2(10) == 15
        assert _score_temperature_v2(14) == 8
        assert _score_temperature_v2(20) == 3

    def test_grand_froid(self):
        """Sous -5C = score maximal."""
        assert _score_temperature_v2(-6) == 98

    def test_interpolation_continue(self):
        """ML-2 : entre deux points de controle, interpolation lineaire."""
        score = _score_temperature_v2(-3.5)  # entre -5 (98) et -2 (90)
        assert 90 < score < 98

    def test_zone_critique(self):
        """Zone 0-5C = scores entre 40 et 80 (zone de decision)."""
        for temp in [1, 2, 3, 4, 5]:
            score = _score_temperature_v2(temp)
            assert 35 <= score <= 80, f"Score inattendu pour {temp}C: {score}"

    def test_monotone_decreasing(self):
        """Le score temperature doit diminuer quand la temp augmente."""
        temps = [-6, -3, -1, 1, 3, 5, 7, 9, 12, 16]
        scores = [_score_temperature_v2(t) for t in temps]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], \
                f"Score non monotone: {temps[i]}C={scores[i]} vs {temps[i+1]}C={scores[i+1]}"

    def test_wind_chill_increases_score(self):
        """ML-4 : le wind chill augmente le score (temp effective plus froide)."""
        score_calm = _score_temperature_v2(3, wind_speed_kmh=0)
        score_windy = _score_temperature_v2(3, wind_speed_kmh=30)
        assert score_windy > score_calm

    def test_wind_chill_inactive_above_10(self):
        """ML-4 : wind chill inactif au-dessus de 10C."""
        score_calm = _score_temperature_v2(12, wind_speed_kmh=0)
        score_windy = _score_temperature_v2(12, wind_speed_kmh=50)
        assert score_calm == score_windy


class TestScoreBudget:
    def test_beaucoup_rouges_restants(self, sample_remaining):
        """Pression forte si beaucoup de rouges restent."""
        # Mois de janvier (fort profil rouge)
        target = date(2026, 1, 15)
        score = _score_budget_v2(sample_remaining, 120, target)
        assert score > 20

    def test_aucun_rouge_restant(self):
        """ML-1 : meme sans rouge, la pression BLANC contribue au score."""
        remaining = {"ROUGE": 0, "BLANC": 10, "BLEU": 50}
        target = date(2026, 1, 15)
        score = _score_budget_v2(remaining, 120, target)
        assert score >= 0  # Toujours >= 0

    def test_fin_saison_urgence(self):
        """Score eleve si beaucoup de rouges restent en fin de saison."""
        remaining = {"ROUGE": 8, "BLANC": 15, "BLEU": 30}
        target = date(2026, 3, 15)
        score = _score_budget_v2(remaining, 45, target)
        assert score >= 20  # Urgence fin de saison

    def test_blanc_pressure_capped(self):
        """Fix #26 : pression BLANC plafonnee a 64 (zone BLANC 35-65, pas ROUGE)."""
        remaining = {"ROUGE": 0, "BLANC": 43, "BLEU": 50}
        target = date(2026, 1, 15)
        score = _score_budget_v2(remaining, 30, target)
        assert score <= 64  # BLANC couvre toute sa zone sans deborder en ROUGE

    def test_continuous_scoring(self):
        """ML-2 : le score varie continument (pas de sauts)."""
        remaining = {"ROUGE": 12, "BLANC": 25, "BLEU": 100}
        target = date(2026, 1, 15)
        scores = [_score_budget_v2(remaining, d, target) for d in range(10, 200, 10)]
        # Pas de sauts > 15 points entre deux valeurs consecutives
        for i in range(len(scores) - 1):
            assert abs(scores[i] - scores[i + 1]) < 20, \
                f"Saut trop grand: d_left={10+i*10}→{scores[i]}, d_left={20+i*10}→{scores[i+1]}"


class TestScoreWeekday:
    def test_dimanche_faible(self):
        """Dimanche = quasi impossible rouge."""
        sunday = date(2026, 1, 4)  # Un dimanche
        assert sunday.weekday() == 6
        score = _score_weekday_v2(sunday)
        assert score < 15

    def test_mardi_fort(self):
        """Mardi = pic historique rouge."""
        tuesday = date(2026, 1, 6)  # Un mardi
        assert tuesday.weekday() == 1
        score = _score_weekday_v2(tuesday)
        assert score >= 60

    def test_jour_ferie(self):
        """Jour férié = quasi impossible rouge."""
        noel = date(2025, 12, 25)
        score = _score_weekday_v2(noel)
        assert score < 10


class TestScoreGradient:
    def test_chute_brutale(self, sample_weather):
        """Forte chute = score élevé."""
        # Modifier pour simuler une chute brutale
        sample_weather[0]["temp_moy"] = 10
        sample_weather[1]["temp_moy"] = 0  # -10C en un jour
        score = _score_gradient(sample_weather, 1)
        assert score >= 70

    def test_stable(self, sample_weather):
        """Température stable = score faible."""
        sample_weather[0]["temp_moy"] = 5
        sample_weather[1]["temp_moy"] = 5
        score = _score_gradient(sample_weather, 1)
        assert score <= 30

    def test_rechauffement(self, sample_weather):
        """Réchauffement = score très faible."""
        sample_weather[0]["temp_moy"] = 0
        sample_weather[1]["temp_moy"] = 8  # +8C
        score = _score_gradient(sample_weather, 1)
        assert score <= 15

    def test_pas_de_donnees(self):
        """Sans données = score neutre."""
        assert _score_gradient([], 0) == 30


class TestColdWave:
    def test_vague_de_froid(self, cold_weather):
        """5 jours < 2C = bonus vague de froid."""
        bonus = _detect_cold_wave(cold_weather, 2)
        assert bonus >= 15

    def test_pas_de_vague(self, warm_weather):
        """Temps doux = pas de bonus."""
        bonus = _detect_cold_wave(warm_weather, 2)
        assert bonus == 0


class TestClustering:
    def test_pas_de_continuite(self):
        """Sans rouge la veille, score faible."""
        target = date.today() + timedelta(days=5)
        forecasts = [{"temp_moy": 10}]
        score = _score_clustering(target, forecasts, 0, {})
        assert score <= 30


# ================================================================
# PROBABILITIES
# ================================================================

class TestProbabilities:
    def test_somme_a_un(self):
        """Les probabilités doivent sommer à 1.0."""
        remaining = {"ROUGE": 10, "BLANC": 20}
        for score in [0, 20, 35, 50, 65, 80, 100]:
            p_r, p_b, p_bl = _compute_probabilities(score, remaining)
            total = p_r + p_b + p_bl
            assert abs(total - 1.0) < 0.01, f"score={score}: sum={total}"

    def test_rouge_epuise(self):
        """Si plus de rouge, p_rouge = 0."""
        remaining = {"ROUGE": 0, "BLANC": 10}
        p_r, p_b, p_bl = _compute_probabilities(90, remaining)
        assert p_r == 0.0

    def test_tout_epuise(self):
        """Si plus rien, tout BLEU."""
        remaining = {"ROUGE": 0, "BLANC": 0}
        p_r, p_b, p_bl = _compute_probabilities(90, remaining)
        assert p_bl == 1.0

    def test_score_eleve_rouge_probable(self):
        """Score élevé → probabilité rouge > blanc > bleu."""
        remaining = {"ROUGE": 10, "BLANC": 20}
        p_r, p_b, p_bl = _compute_probabilities(85, remaining)
        assert p_r > p_b
        assert p_r > p_bl


# ================================================================
# JOURS FERIES
# ================================================================

class TestFrenchHolidays:
    def test_noel(self):
        assert is_french_holiday(date(2025, 12, 25))

    def test_jour_an(self):
        assert is_french_holiday(date(2026, 1, 1))

    def test_14_juillet(self):
        assert is_french_holiday(date(2026, 7, 14))

    def test_jour_normal(self):
        assert not is_french_holiday(date(2026, 3, 10))


# ================================================================
# LEARNING CORRECTIONS
# ================================================================

class TestLearningCorrections:
    def test_no_learnings(self):
        """Sans corrections, le score reste inchangé."""
        result = _apply_learning_corrections(50, date(2026, 1, 15), 1, 3.0, {})
        assert result == 50

    def test_horizon_correction(self):
        """Correction par horizon appliquée."""
        learnings = {"horizon": {"J-3": -5.0}}
        result = _apply_learning_corrections(50, date(2026, 1, 15), 3, 3.0, learnings)
        assert result == 45

    def test_plafonnement(self):
        """Corrections plafonnées à ±15."""
        learnings = {
            "horizon": {"J-1": -10.0},
            "temp_range": {"2_5C": -10.0},
            "month": {"jan": -10.0},
            "weekday": {"semaine": -10.0},
        }
        result = _apply_learning_corrections(50, date(2026, 1, 15), 1, 3.0, learnings)
        assert result == 35  # 50 - 15 (plafonné)

    def test_bornes_0_100(self):
        """Le score reste entre 0 et 100."""
        learnings = {"horizon": {"J-1": -15.0}}
        result = _apply_learning_corrections(5, date(2026, 1, 15), 1, 3.0, learnings)
        assert result >= 0

    def test_color_confusion_targeted(self):
        """Correction color_confusion cible la couleur prédite actuelle."""
        learnings = {
            "color_confusion": {
                "ROUGE->BLEU": -4.0,  # On sur-prédit ROUGE
                "BLANC->ROUGE": 3.0,  # On sous-prédit BLANC
            },
        }
        # Score dans la zone ROUGE (>= 65)
        result = _apply_learning_corrections(70, date(2026, 1, 15), 1, 3.0, learnings)
        assert result < 70  # La correction ROUGE->BLEU doit baisser le score

        # Score dans la zone BLANC (>= 35, < 65) — ROUGE->BLEU ne s'applique PAS
        result_blanc = _apply_learning_corrections(45, date(2026, 1, 15), 1, 3.0, learnings)
        assert result_blanc > 45  # La correction BLANC->ROUGE augmente le score


class TestFactorCorrection:
    def test_over_correction(self):
        """Correction 'over' diminue le sub-score."""
        corrections = {"temperature:over": -5.0}
        result = _apply_factor_correction(70, corrections, "temperature")
        assert result == 65

    def test_under_correction(self):
        """Correction 'under' augmente le sub-score."""
        corrections = {"budget:under": 4.0}
        result = _apply_factor_correction(30, corrections, "budget")
        assert result == 34

    def test_bornes(self):
        """Le sub-score reste entre 0 et 100."""
        corrections = {"rte:over": -15.0}  # Au-delà du plafond ±10
        result = _apply_factor_correction(5, corrections, "rte")
        assert result == 0  # max(-10) appliqué puis borné à 0

    def test_no_correction(self):
        """Sans correction pour ce facteur, score inchangé."""
        corrections = {"temperature:over": -5.0}
        result = _apply_factor_correction(50, corrections, "budget")
        assert result == 50


# ================================================================
# PREDICT_DAY
# ================================================================

class TestPredictDay:
    def test_hors_saison(self):
        """Hors saison = toujours BLEU."""
        target = date(2026, 7, 15)  # Juillet
        result = predict_day(target)
        assert result["couleur_predite"] == "BLEU"
        assert "Hors saison" in result["raison"]

    def test_quotas_epuises(self):
        """Quotas rouge et blanc épuisés = BLEU."""
        target = date(2026, 2, 15)
        remaining = {"ROUGE": 0, "BLANC": 0, "BLEU": 100}
        result = predict_day(target, remaining=remaining)
        assert result["couleur_predite"] == "BLEU"

    def test_temps_froid_rouge(self, cold_weather, sample_remaining):
        """Grand froid avec quota = ROUGE probable."""
        target = date.fromisoformat(cold_weather[0]["date"])
        result = predict_day(target, weather=cold_weather[0],
                            forecasts=cold_weather, target_idx=0,
                            remaining=sample_remaining)
        assert result["couleur_predite"] in ("ROUGE", "BLANC")
        assert result["score_risque"] > 50

    def test_temps_doux_bleu(self, warm_weather):
        """Temps doux avec peu de quota = BLEU probable."""
        # ML-1 : remaining faible pour tester le signal temperature sans biais budget
        low_remaining = {"ROUGE": 0, "BLANC": 2, "BLEU": 100}
        target = date.fromisoformat(warm_weather[0]["date"])
        result = predict_day(target, weather=warm_weather[0],
                            forecasts=warm_weather, target_idx=0,
                            remaining=low_remaining)
        assert result["couleur_predite"] == "BLEU"

    def test_sub_scores_presentes(self, sample_weather, sample_remaining):
        """Les sub-scores doivent être dans le résultat."""
        target = date.fromisoformat(sample_weather[0]["date"])
        result = predict_day(target, weather=sample_weather[0],
                            forecasts=sample_weather, target_idx=0,
                            remaining=sample_remaining)
        for key in ["score_temperature", "score_budget", "score_weekday",
                     "score_gradient", "score_clustering", "score_rte"]:
            assert key in result, f"Missing sub-score: {key}"
            assert 0 <= result[key] <= 100

    def test_no_simulated_fallback(self, sample_weather, sample_remaining):
        """Pas d'atténuation simulée — Open-Meteo down = pas de prédiction."""
        target = date.fromisoformat(sample_weather[0]["date"])
        # forecast_quality "api" est le seul cas réel
        sample_weather[0]["forecast_quality"] = "api"
        result = predict_day(target, weather=sample_weather[0],
                            forecasts=sample_weather, target_idx=0,
                            remaining=sample_remaining)
        assert result["score_risque"] >= 0


# ================================================================
# PREDICT_RANGE
# ================================================================

class TestPredictRange:
    def test_decremente_quotas(self, cold_weather):
        """predict_range doit décrémenter les quotas simulés."""
        results = predict_range(cold_weather)
        rouge_count = sum(1 for r in results if r["couleur_predite"] == "ROUGE")
        # Ne doit pas dépasser le quota total de 22
        assert rouge_count <= Config.JOURS_ROUGES_TOTAL

    def test_horizons_corrects(self, sample_weather):
        """Chaque prédiction a le bon horizon."""
        results = predict_range(sample_weather)
        for r in results:
            assert "horizon" in r
            assert r["horizon"].startswith("J-") or r["horizon"] == "J0"

    def test_confirmed_flag(self, sample_weather):
        """Les prédictions normales ne sont pas confirmées."""
        results = predict_range(sample_weather)
        for r in results:
            assert "confirmed" in r

    def test_simulated_flag_defaults_false(self, sample_weather):
        """Les prédictions normales ne sont pas marquées simulées."""
        results = predict_range(sample_weather)
        non_confirmed = [r for r in results if not r.get("confirmed")]
        for r in non_confirmed:
            assert r["simulated"] is False


# ================================================================
# STORE / CONFIRM
# ================================================================

class TestStorePredict:
    def test_store_prediction(self, sample_weather, sample_remaining):
        """store_prediction écrit et relit correctement."""
        target = date.fromisoformat(sample_weather[0]["date"])
        pred = predict_day(target, weather=sample_weather[0],
                          forecasts=sample_weather, target_idx=0,
                          remaining=sample_remaining)
        pred["confirmed"] = False
        pred["simulated"] = False
        change = store_prediction(pred, "J-1", cycle_id="test_001")
        assert change is None  # Première écriture = pas de changement

        # Relire
        from database import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM predictions WHERE date = ? AND horizon = ?",
            (sample_weather[0]["date"], "J-1")
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["couleur_predite"] == pred["couleur_predite"]
        assert row["cycle_id"] == "test_001"

    def test_store_detects_change(self, sample_weather, sample_remaining):
        """store_prediction détecte un changement de couleur."""
        target = date.fromisoformat(sample_weather[0]["date"])

        # Première prédiction
        pred1 = predict_day(target, weather=sample_weather[0],
                           forecasts=sample_weather, target_idx=0,
                           remaining=sample_remaining)
        pred1["confirmed"] = False
        pred1["simulated"] = False
        store_prediction(pred1, "J-1", cycle_id="cycle_1")

        # Forcer un changement de couleur
        pred2 = dict(pred1)
        old_couleur = pred2["couleur_predite"]
        new_couleur = "ROUGE" if old_couleur != "ROUGE" else "BLEU"
        pred2["couleur_predite"] = new_couleur
        change = store_prediction(pred2, "J-1", cycle_id="cycle_2")

        assert change is not None
        assert change["couleur_avant"] == old_couleur
        assert change["couleur_apres"] == new_couleur

    def test_confirm_prediction(self, sample_weather, sample_remaining):
        """confirm_prediction met à jour les prédictions non confirmées."""
        target_str = sample_weather[0]["date"]
        target = date.fromisoformat(target_str)

        # Stocker une prédiction
        pred = predict_day(target, weather=sample_weather[0],
                          forecasts=sample_weather, target_idx=0,
                          remaining=sample_remaining)
        pred["confirmed"] = False
        pred["simulated"] = False
        store_prediction(pred, "J-1", cycle_id="test")

        # Confirmer
        updated = confirm_prediction(target_str, "BLEU")
        assert updated >= 1

        # Vérifier
        from database import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM predictions WHERE date = ? AND horizon = 'J-1'",
            (target_str,)
        ).fetchone()
        conn.close()
        assert row["confirmed"] == 1
        assert row["couleur_predite"] == "BLEU"
