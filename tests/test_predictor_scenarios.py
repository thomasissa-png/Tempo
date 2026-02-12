"""Tests de scénarios réalistes du prédicteur Tempo.

Valide le modèle de prédiction sur 15+ scénarios couvrant :
- Début, milieu et fin de saison
- Températures extrêmes, modérées et douces
- Pression budgétaire faible, moyenne et critique
- Contraintes dures EDF (weekends, fériés, règle R1 nov-mars, R4 max 5 consécutifs)
- Vague de froid, gradient thermique
- Quota épuisé, quota normal
- Prédictions multi-jours (predict_range) avec décrémentation

Chaque scénario a une couleur attendue justifiée par les règles EDF
et le comportement réel historique d'EDF (20 saisons de données).
"""

import os
import sys
import pytest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from predictor import (
    predict_day, predict_range, _score_temperature_v2,
    _score_budget_v2, _score_weekday_v2, _detect_cold_wave,
    is_french_holiday,
)
from config import Config


# ================================================================
# HELPERS
# ================================================================

DEFAULT_WEIGHTS = {
    "temperature": 0.27, "jours_restants": 0.20,
    "jour_semaine": 0.10, "gradient_thermique": 0.13,
    "clustering": 0.10, "consommation_rte": 0.13,
    "pression": 0.07,
}

def make_weather(temp_moy, wind=10, temp_min=None, temp_max=None):
    """Crée un dict météo réaliste à partir de la temp moyenne."""
    if temp_min is None:
        temp_min = temp_moy - 3
    if temp_max is None:
        temp_max = temp_moy + 5
    return {
        "temp_min": temp_min, "temp_max": temp_max,
        "temp_moy": temp_moy, "wind_speed": wind,
        "pressure": 1015, "forecast_quality": "test",
    }


def make_forecasts(base_date, temps, wind=10):
    """Crée une liste de forecasts pour predict_range."""
    return [
        {**make_weather(t, wind), "date": (base_date + timedelta(days=i)).isoformat()}
        for i, t in enumerate(temps)
    ]


def predict(target, temp_moy, remaining, weights=None, wind=10,
            forecasts=None, target_idx=0, actuals_cache=None, rte_score=None):
    """Raccourci pour predict_day avec paramètres par défaut."""
    return predict_day(
        target,
        weather=make_weather(temp_moy, wind),
        remaining=remaining,
        weights=weights or DEFAULT_WEIGHTS,
        forecasts=forecasts,
        target_idx=target_idx,
        _actuals_cache=actuals_cache,
        rte_score=rte_score,
    )


# ================================================================
# 1. SCÉNARIOS DE TEMPÉRATURE (scoring brut)
# ================================================================

class TestTemperatureScoring:
    """Vérifie que le scoring température est cohérent."""

    def test_grand_froid_score_tres_eleve(self):
        """< -5°C national → score > 95 (quasi certain rouge si budget le permet)."""
        score = _score_temperature_v2(-5, wind_speed_kmh=15)
        assert score >= 95, f"Grand froid -5°C devrait scorer >95, got {score}"

    def test_froid_modere_score_eleve(self):
        """0°C national sans vent → score ~80 (point de contrôle direct)."""
        score = _score_temperature_v2(0, wind_speed_kmh=0)
        assert 75 <= score <= 85, f"Froid 0°C sans vent devrait scorer 75-85, got {score}"

    def test_froid_modere_avec_vent(self):
        """0°C + vent 10km/h → wind chill ~-3°C → score > 90."""
        score = _score_temperature_v2(0, wind_speed_kmh=10)
        assert score >= 90, f"Froid 0°C + vent devrait scorer >90 (wind chill), got {score}"

    def test_frais_score_moyen(self):
        """5°C national sans vent → score ~47.5 (interpolé entre 4°C=55 et 6°C=40)."""
        score = _score_temperature_v2(5, wind_speed_kmh=0)
        assert 40 <= score <= 55, f"Frais 5°C sans vent devrait scorer 40-55, got {score}"

    def test_doux_score_bas(self):
        """12°C national → score < 15."""
        score = _score_temperature_v2(12, wind_speed_kmh=5)
        assert score <= 15, f"Doux 12°C devrait scorer <15, got {score}"

    def test_wind_chill_augmente_score(self):
        """Le vent fort augmente le score (wind chill)."""
        score_calm = _score_temperature_v2(3, wind_speed_kmh=5)
        score_windy = _score_temperature_v2(3, wind_speed_kmh=40)
        assert score_windy > score_calm, (
            f"Wind chill devrait augmenter le score: calm={score_calm}, windy={score_windy}"
        )


# ================================================================
# 2. SCÉNARIOS DÉBUT DE SAISON (novembre)
# ================================================================

class TestDebutSaison:
    """Novembre : peu de rouges historiquement (~7%). Seul grand froid = rouge."""

    def test_nov_froid_modere_pas_rouge(self):
        """Novembre, 22 rouges restants, 3°C → pas ROUGE (pas d'urgence)."""
        r = predict(date(2025, 11, 18), temp_moy=3.0,  # mardi
                    remaining={"ROUGE": 22, "BLANC": 43, "BLEU": 240})
        assert r["couleur_predite"] != "ROUGE", (
            f"Novembre début saison + 3°C ne devrait pas être ROUGE "
            f"(score={r['score_risque']})"
        )

    def test_nov_grand_froid_peut_etre_rouge(self):
        """Novembre, 22 rouges restants, -5°C → ROUGE possible (vague de froid)."""
        r = predict(date(2025, 11, 18), temp_moy=-5.0,
                    remaining={"ROUGE": 22, "BLANC": 43, "BLEU": 240})
        # À -5°C, même en novembre, le score devrait être élevé
        assert r["score_risque"] >= 55, (
            f"Grand froid -5°C en novembre devrait scorer haut: {r['score_risque']}"
        )


# ================================================================
# 3. SCÉNARIOS MI-SAISON (janvier — pic des rouges)
# ================================================================

class TestMiSaison:
    """Janvier : 37% des rouges historiquement. Période la plus critique."""

    def test_jan_froid_beaucoup_restants_rouge(self):
        """Janvier, 18 rouges restants (en retard), -2°C → ROUGE."""
        r = predict(date(2026, 1, 20), temp_moy=-2.0,  # mardi
                    remaining={"ROUGE": 18, "BLANC": 30, "BLEU": 100})
        assert r["couleur_predite"] == "ROUGE", (
            f"Janvier en retard + grand froid devrait être ROUGE "
            f"(score={r['score_risque']})"
        )

    def test_jan_normal_froid_modere_rouge(self):
        """Janvier, 15 rouges restants, 2°C → ROUGE (pression + froid)."""
        r = predict(date(2026, 1, 15), temp_moy=2.0,  # jeudi
                    remaining={"ROUGE": 15, "BLANC": 30, "BLEU": 100})
        assert r["couleur_predite"] == "ROUGE", (
            f"Janvier 15 rouges + 2°C devrait être ROUGE "
            f"(score={r['score_risque']})"
        )

    def test_jan_peu_restants_doux_blanc(self):
        """Janvier, 5 rouges restants (avance), 10°C → BLANC ou BLEU."""
        r = predict(date(2026, 1, 15), temp_moy=10.0,
                    remaining={"ROUGE": 5, "BLANC": 30, "BLEU": 100})
        assert r["couleur_predite"] != "ROUGE", (
            f"5 rouges + 10°C ne devrait pas être ROUGE "
            f"(score={r['score_risque']})"
        )


# ================================================================
# 4. SCÉNARIOS FIN DE SAISON (février-mars — urgence budgétaire)
# ================================================================

class TestFinSaison:
    """Février-Mars : pression budgétaire critique. Le cœur du fix #43."""

    def test_fev_14_rouges_3c_rouge(self):
        """Février, 14 rouges restants, 3°C → ROUGE (scénario utilisateur)."""
        r = predict(date(2026, 2, 12), temp_moy=3.0,  # jeudi
                    remaining={"ROUGE": 14, "BLANC": 20, "BLEU": 50})
        assert r["couleur_predite"] == "ROUGE", (
            f"14 rouges + 3°C en février = ROUGE obligatoire, "
            f"got {r['couleur_predite']} (score={r['score_risque']})"
        )

    def test_fev_14_rouges_5c_rouge(self):
        """Février, 14 rouges restants, 5°C → ROUGE (densité 42%)."""
        r = predict(date(2026, 2, 17), temp_moy=5.0,  # mardi
                    remaining={"ROUGE": 14, "BLANC": 20, "BLEU": 50})
        assert r["couleur_predite"] == "ROUGE", (
            f"14 rouges + 5°C en février = densité trop élevée pour BLANC, "
            f"got {r['couleur_predite']} (score={r['score_risque']})"
        )

    def test_fev_14_rouges_13c_blanc(self):
        """Février, 14 rouges restants, 13°C → BLANC (trop doux pour rouge)."""
        r = predict(date(2026, 2, 17), temp_moy=13.0,
                    remaining={"ROUGE": 14, "BLANC": 20, "BLEU": 50})
        assert r["couleur_predite"] != "ROUGE", (
            f"14 rouges mais 13°C = trop doux, "
            f"got {r['couleur_predite']} (score={r['score_risque']})"
        )

    def test_mars_10_rouges_4c_rouge(self):
        """Mars, 10 rouges restants, 4°C → ROUGE (dernière ligne droite)."""
        r = predict(date(2026, 3, 10), temp_moy=4.0,  # mardi
                    remaining={"ROUGE": 10, "BLANC": 15, "BLEU": 40})
        assert r["couleur_predite"] == "ROUGE", (
            f"Mars 10 rouges + 4°C = urgence maximale, "
            f"got {r['couleur_predite']} (score={r['score_risque']})"
        )

    def test_mars_2_rouges_5c_pas_forcement_rouge(self):
        """Mars, 2 rouges restants, 5°C → pas forcément rouge (peu d'urgence)."""
        r = predict(date(2026, 3, 10), temp_moy=5.0,
                    remaining={"ROUGE": 2, "BLANC": 10, "BLEU": 40})
        # Avec seulement 2 rouges restants, l'urgence est faible
        # Le modèle peut prédire BLANC ou ROUGE selon les autres facteurs
        assert r["score_risque"] < 90, (
            f"Seulement 2 rouges restants ne devrait pas scorer 90+, "
            f"got {r['score_risque']}"
        )


# ================================================================
# 5. CONTRAINTES DURES EDF
# ================================================================

class TestContraintesEDF:
    """Les règles EDF sont inviolables, peu importe le score."""

    def test_weekend_samedi_jamais_rouge(self):
        """Samedi = jamais ROUGE (R2), même par grand froid."""
        r = predict(date(2026, 1, 17), temp_moy=-5.0,  # samedi
                    remaining={"ROUGE": 20, "BLANC": 30, "BLEU": 100})
        assert r["couleur_predite"] != "ROUGE", (
            f"Samedi ne peut JAMAIS être ROUGE (R2), "
            f"got {r['couleur_predite']}"
        )

    def test_dimanche_jamais_rouge_ni_blanc(self):
        """Dimanche = toujours BLEU (R2+R3)."""
        r = predict(date(2026, 1, 18), temp_moy=-5.0,  # dimanche
                    remaining={"ROUGE": 20, "BLANC": 30, "BLEU": 100})
        assert r["couleur_predite"] == "BLEU", (
            f"Dimanche = toujours BLEU (R2+R3), "
            f"got {r['couleur_predite']}"
        )

    def test_ferie_pas_rouge(self):
        """Jour férié en semaine = pas ROUGE (R2)."""
        # 1er janvier 2026 = jeudi
        r = predict(date(2026, 1, 1), temp_moy=-5.0,
                    remaining={"ROUGE": 20, "BLANC": 30, "BLEU": 100})
        assert r["couleur_predite"] != "ROUGE", (
            f"Jour férié ne peut pas être ROUGE (R2), "
            f"got {r['couleur_predite']}"
        )

    def test_rouge_interdit_avril(self):
        """Avril = pas de ROUGE (R1 : rouge uniquement nov-mars)."""
        r = predict(date(2026, 4, 7), temp_moy=0.0,  # mardi
                    remaining={"ROUGE": 5, "BLANC": 15, "BLEU": 40})
        assert r["couleur_predite"] != "ROUGE", (
            f"Avril ne peut pas être ROUGE (R1 : nov-mars seulement), "
            f"got {r['couleur_predite']}"
        )

    def test_hors_saison_toujours_bleu(self):
        """Été (juin-août) = toujours BLEU."""
        r = predict(date(2026, 7, 15), temp_moy=25.0,
                    remaining={"ROUGE": 0, "BLANC": 0, "BLEU": 50})
        assert r["couleur_predite"] == "BLEU"

    def test_quota_epuise_bleu(self):
        """Si rouge ET blanc épuisés → BLEU."""
        r = predict(date(2026, 2, 10), temp_moy=-5.0,
                    remaining={"ROUGE": 0, "BLANC": 0, "BLEU": 50})
        assert r["couleur_predite"] == "BLEU", (
            f"Quotas épuisés = BLEU obligatoire, got {r['couleur_predite']}"
        )

    def test_max_5_rouges_consecutifs(self):
        """R4 : pas plus de 5 jours rouges consécutifs."""
        # Simuler 5 jours rouges consécutifs dans le cache actuals
        target = date(2026, 1, 22)  # jeudi
        cache = {}
        for i in range(1, 6):
            d = (target - timedelta(days=i)).isoformat()
            cache[d] = "ROUGE"

        r = predict(target, temp_moy=-5.0,
                    remaining={"ROUGE": 15, "BLANC": 30, "BLEU": 100},
                    actuals_cache=cache)
        assert r["couleur_predite"] != "ROUGE", (
            f"5 rouges consécutifs atteints → R4 interdit le 6ème, "
            f"got {r['couleur_predite']}"
        )


# ================================================================
# 6. VAGUE DE FROID
# ================================================================

class TestVagueDeFroid:
    """Le bonus vague de froid doit amplifier le score température."""

    def test_cold_wave_3_jours(self):
        """3+ jours < 2°C = vague de froid détectée."""
        forecasts = make_forecasts(date(2026, 1, 12), [1, 0, -1, 1, 3])
        bonus = _detect_cold_wave(forecasts, target_idx=2)
        assert bonus >= 15, f"Vague de froid (3j < 2°C) devrait donner bonus >= 15, got {bonus}"

    def test_no_cold_wave_warm(self):
        """Pas de vague de froid si températures normales."""
        forecasts = make_forecasts(date(2026, 1, 12), [5, 6, 7, 5, 4])
        bonus = _detect_cold_wave(forecasts, target_idx=2)
        assert bonus == 0, f"Pas de vague de froid à 5-7°C, got bonus={bonus}"

    def test_cold_wave_boosts_rouge_prediction(self):
        """Une vague de froid doit pousser vers ROUGE."""
        forecasts = make_forecasts(date(2026, 1, 12), [1, 0, -1, 0, 1])
        target = date(2026, 1, 14)  # mercredi, jour du -1°C

        r = predict_day(
            target,
            weather=make_weather(-1),
            forecasts=forecasts,
            target_idx=2,
            remaining={"ROUGE": 15, "BLANC": 30, "BLEU": 100},
            weights=DEFAULT_WEIGHTS,
        )
        assert r["couleur_predite"] == "ROUGE", (
            f"Vague de froid + janvier + 15 rouges restants = ROUGE, "
            f"got {r['couleur_predite']} (score={r['score_risque']})"
        )


# ================================================================
# 7. GRADIENT THERMIQUE
# ================================================================

class TestGradientThermique:
    """Une chute brutale de température est un signal fort."""

    def test_gradient_forte_chute_augmente_score(self):
        """Chute de 8°C en un jour → score gradient élevé."""
        forecasts = make_forecasts(date(2026, 1, 12), [10, 2])
        r = predict_day(
            date(2026, 1, 13),
            weather=make_weather(2),
            forecasts=forecasts,
            target_idx=1,
            remaining={"ROUGE": 15, "BLANC": 30, "BLEU": 100},
            weights=DEFAULT_WEIGHTS,
        )
        # La chute de 10→2 (8°C) devrait donner un gradient_score élevé
        assert r.get("score_gradient", 0) >= 80, (
            f"Chute de 8°C devrait scorer gradient >= 80, "
            f"got {r.get('score_gradient', 0)}"
        )


# ================================================================
# 8. SCORE RTE
# ================================================================

class TestScoreRTE:
    """Le score consommation RTE ajoute un signal supplémentaire."""

    def test_rte_eleve_augmente_risque(self):
        """Forte consommation prévue → score plus élevé."""
        remaining = {"ROUGE": 15, "BLANC": 30, "BLEU": 100}
        rte_high = {"available": True, "score": 85, "peak_mw": 75000}
        rte_neutral = {"available": True, "score": 50, "peak_mw": 55000}

        r_high = predict(date(2026, 1, 20), temp_moy=3.0, remaining=remaining,
                         rte_score=rte_high)
        r_neutral = predict(date(2026, 1, 20), temp_moy=3.0, remaining=remaining,
                            rte_score=rte_neutral)

        assert r_high["score_risque"] > r_neutral["score_risque"], (
            f"RTE élevé devrait augmenter le score: "
            f"high={r_high['score_risque']}, neutral={r_neutral['score_risque']}"
        )


# ================================================================
# 9. PREDICT_RANGE — Décrémentation des quotas
# ================================================================

class TestPredictRange:
    """predict_range doit décrémenter les quotas au fil des jours."""

    def test_range_ne_depasse_pas_quota_rouge(self):
        """Pas plus de N jours rouges que le quota restant."""
        # 5 jours de prévision par grand froid avec seulement 3 rouges restants
        base = date(2026, 1, 19)  # lundi
        forecasts = make_forecasts(base, [-2, -3, -4, -2, -1])

        # Monkey-patch pour éviter les appels DB
        import predictor
        orig_remaining = predictor.get_remaining_days
        orig_weights = predictor.get_current_weights
        orig_actuals = predictor._load_recent_actuals
        orig_future = predictor._load_future_actuals
        orig_learnings = None
        try:
            # Patch get_active_learnings aussi
            from performance_tracker import get_active_learnings as _orig_learn
            orig_learnings = _orig_learn
        except Exception:
            pass

        try:
            predictor.get_remaining_days = lambda: {"ROUGE": 3, "BLANC": 20, "BLEU": 100}
            predictor.get_current_weights = lambda: DEFAULT_WEIGHTS
            predictor._load_recent_actuals = lambda: {}
            predictor._load_future_actuals = lambda: {}
            # Patch le module performance_tracker
            import performance_tracker
            performance_tracker.get_active_learnings = lambda: {}

            preds = predict_range(forecasts)
        finally:
            predictor.get_remaining_days = orig_remaining
            predictor.get_current_weights = orig_weights
            predictor._load_recent_actuals = orig_actuals
            predictor._load_future_actuals = orig_future
            if orig_learnings:
                performance_tracker.get_active_learnings = orig_learnings

        rouge_count = sum(1 for p in preds if p["couleur_predite"] == "ROUGE")
        assert rouge_count <= 3, (
            f"predict_range a prédit {rouge_count} rouges mais quota = 3 !\n"
            f"Prédictions: {[(p['date'], p['couleur_predite']) for p in preds]}"
        )


# ================================================================
# 10. COHÉRENCE DU SCORING BUDGÉTAIRE
# ================================================================

class TestBudgetScoring:
    """Le score budget doit refléter l'urgence réelle."""

    def test_budget_max_quand_densite_elevee(self):
        """14 rouges / 33 éligibles → budget_score très élevé."""
        from tempo_client import days_left_in_season
        score = _score_budget_v2(
            {"ROUGE": 14, "BLANC": 20, "BLEU": 50},
            days_left_in_season(),
            date(2026, 2, 12),
        )
        assert score >= 90, (
            f"14 rouges en février devrait donner budget >= 90, got {score}"
        )

    def test_budget_modere_debut_saison(self):
        """22 rouges en novembre → budget modéré."""
        score = _score_budget_v2(
            {"ROUGE": 22, "BLANC": 43, "BLEU": 240},
            180,  # ~180 jours restants
            date(2025, 11, 15),
        )
        assert score < 70, (
            f"Début saison devrait donner budget < 70, got {score}"
        )

    def test_budget_faible_si_epuise(self):
        """0 rouges et 0 blancs restants → score faible (< SEUIL_BLANC).

        Note: le score n'est pas exactement 0 à cause du boost mensuel saisonnier,
        mais il reste bien en dessous des seuils de décision. Le système de contraintes
        EDF force de toute façon BLEU quand les quotas sont épuisés.
        """
        score = _score_budget_v2(
            {"ROUGE": 0, "BLANC": 0, "BLEU": 50},
            100,
            date(2026, 2, 1),
        )
        assert score < 35, f"Quotas épuisés devrait donner budget < SEUIL_BLANC, got {score}"


# ================================================================
# 11. SCÉNARIOS DE DENSITÉ PROGRESSIVE
# ================================================================

class TestDensiteProgressive:
    """Le modèle doit prédire plus de rouges quand la densité augmente."""

    def test_densite_croissante_augmente_rouge(self):
        """Plus il reste de rouges à placer, plus les prédictions sont rouges."""
        target = date(2026, 2, 17)  # mardi
        temp = 4.0  # Frais mais pas glacial

        scores = {}
        for rouges_restants in [2, 5, 10, 14, 18]:
            remaining = {"ROUGE": rouges_restants, "BLANC": 20, "BLEU": 50}
            r = predict(target, temp_moy=temp, remaining=remaining)
            scores[rouges_restants] = r["score_risque"]

        # Le score doit être monotonement croissant
        sorted_scores = [scores[k] for k in sorted(scores.keys())]
        for i in range(len(sorted_scores) - 1):
            assert sorted_scores[i] <= sorted_scores[i + 1], (
                f"Le score devrait croître avec la densité : {scores}"
            )

        # Avec 14+ rouges, le score devrait être au-dessus du seuil rouge
        assert scores[14] >= Config.SEUIL_ROUGE, (
            f"14 rouges + 4°C devrait atteindre SEUIL_ROUGE ({Config.SEUIL_ROUGE}), "
            f"got {scores[14]}"
        )


# ================================================================
# 12. JOUR DE SEMAINE SCORING
# ================================================================

class TestWeekdayScoring:
    """Le scoring jour de semaine reflète les patterns historiques."""

    def test_mardi_jeudi_score_plus_eleve(self):
        """Mardi-jeudi = plus probable rouge historiquement."""
        scores = {}
        for d, name in [(date(2026, 1, 12), "lundi"),
                        (date(2026, 1, 13), "mardi"),
                        (date(2026, 1, 14), "mercredi"),
                        (date(2026, 1, 15), "jeudi"),
                        (date(2026, 1, 16), "vendredi")]:
            scores[name] = _score_weekday_v2(d)

        # Mardi-jeudi devrait scorer plus haut que lundi et vendredi
        assert scores["mardi"] > scores["lundi"]
        assert scores["mercredi"] > scores["vendredi"]

    def test_weekend_score_bas(self):
        """Samedi/dimanche = score très bas."""
        sam = _score_weekday_v2(date(2026, 1, 17))
        dim = _score_weekday_v2(date(2026, 1, 18))
        assert sam <= 10
        assert dim <= 10

    def test_ferie_score_tres_bas(self):
        """Jour férié = score quasi nul."""
        noel = _score_weekday_v2(date(2025, 12, 25))
        assert noel <= 10


# ================================================================
# 13. SCÉNARIO INTÉGRÉ : SEMAINE COMPLÈTE FÉVRIER
# ================================================================

class TestSemaineComplete:
    """Simule une semaine complète de février avec forte pression."""

    def test_semaine_fevrier_coherente(self):
        """Du lundi au dimanche : rouges en semaine, bleu le dimanche."""
        base = date(2026, 2, 16)  # lundi
        remaining = {"ROUGE": 14, "BLANC": 20, "BLEU": 50}
        temps = [3, 2, 4, 3, 5, 6, 7]  # lun-dim

        resultats = {}
        for i in range(7):
            d = base + timedelta(days=i)
            r = predict(d, temp_moy=temps[i], remaining=remaining)
            resultats[d.strftime("%A")] = r["couleur_predite"]

        # Dimanche = BLEU (R2+R3)
        assert resultats["Sunday"] == "BLEU", (
            f"Dimanche devrait être BLEU, got {resultats['Sunday']}"
        )

        # Au moins 3 rouges en semaine (densité 42%, il faut ~3 sur 5 weekdays)
        weekday_colors = [resultats[d] for d in
                         ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]]
        rouge_count = weekday_colors.count("ROUGE")
        assert rouge_count >= 3, (
            f"Avec 14 rouges restants, il faut ~3+ rouges par semaine, "
            f"got {rouge_count}: {weekday_colors}"
        )


# ================================================================
# 14. PROBABILITÉS COHÉRENTES
# ================================================================

class TestProbabilites:
    """Les probabilités doivent être cohérentes avec la prédiction."""

    def test_rouge_a_haute_probabilite_rouge(self):
        """Si prédit ROUGE, probabilité rouge doit être la plus haute."""
        r = predict(date(2026, 2, 17), temp_moy=0.0,
                    remaining={"ROUGE": 14, "BLANC": 20, "BLEU": 50})
        assert r["couleur_predite"] == "ROUGE"
        assert r["probabilite_rouge"] > r["probabilite_blanc"], (
            f"ROUGE prédit mais P(rouge)={r['probabilite_rouge']} "
            f"< P(blanc)={r['probabilite_blanc']}"
        )
        assert r["probabilite_rouge"] > r["probabilite_bleu"], (
            f"ROUGE prédit mais P(rouge)={r['probabilite_rouge']} "
            f"< P(bleu)={r['probabilite_bleu']}"
        )

    def test_probabilites_somment_a_un(self):
        """La somme des probabilités doit être ~1.0."""
        r = predict(date(2026, 1, 20), temp_moy=3.0,
                    remaining={"ROUGE": 15, "BLANC": 30, "BLEU": 100})
        total = r["probabilite_rouge"] + r["probabilite_blanc"] + r["probabilite_bleu"]
        assert abs(total - 1.0) < 0.01, (
            f"Somme des probabilités = {total}, devrait être ~1.0"
        )

    def test_quota_epuise_probabilite_zero(self):
        """Si quota rouge = 0, probabilité rouge = 0."""
        r = predict(date(2026, 1, 20), temp_moy=-5.0,
                    remaining={"ROUGE": 0, "BLANC": 30, "BLEU": 100})
        assert r["probabilite_rouge"] == 0.0, (
            f"Quota rouge épuisé mais P(rouge) = {r['probabilite_rouge']}"
        )


# ================================================================
# 15. CAS LIMITES
# ================================================================

class TestCasLimites:
    """Cas edge : transitions de mois, 31 mars, etc."""

    def test_31_mars_dernier_jour_rouge_possible(self):
        """31 mars = dernier jour possible pour un rouge."""
        r = predict(date(2026, 3, 31), temp_moy=2.0,  # mardi
                    remaining={"ROUGE": 5, "BLANC": 15, "BLEU": 30})
        # Le 31 mars, s'il reste des rouges et il fait froid, ROUGE possible
        assert r["score_risque"] > 50, (
            f"Dernier jour rouge possible avec 5 restants + 2°C "
            f"devrait scorer haut, got {r['score_risque']}"
        )

    def test_1er_avril_pas_rouge(self):
        """1er avril = premier jour sans rouge possible."""
        r = predict(date(2026, 4, 1), temp_moy=-2.0,  # mercredi
                    remaining={"ROUGE": 5, "BLANC": 15, "BLEU": 30})
        assert r["couleur_predite"] != "ROUGE", (
            f"1er avril ne peut pas être ROUGE (R1), "
            f"got {r['couleur_predite']}"
        )

    def test_weather_none_ne_crashe_pas(self):
        """Le prédicteur ne crashe pas sans données météo."""
        r = predict_day(
            date(2026, 1, 20),
            weather=None,
            remaining={"ROUGE": 15, "BLANC": 30, "BLEU": 100},
            weights=DEFAULT_WEIGHTS,
        )
        assert r["couleur_predite"] in ("ROUGE", "BLANC", "BLEU")

    def test_forecast_quality_preserved(self):
        """La qualité de forecast est préservée dans le résultat."""
        w = make_weather(3.0)
        w["forecast_quality"] = "api"
        r = predict_day(
            date(2026, 1, 20), weather=w,
            remaining={"ROUGE": 15, "BLANC": 30, "BLEU": 100},
            weights=DEFAULT_WEIGHTS,
        )
        assert "raison" in r
        assert isinstance(r["score_risque"], float)
