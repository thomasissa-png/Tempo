"""Tests de cohérence système — garde-fous permanents.

Ces tests vérifient les INVARIANTS du projet pour empêcher les régressions
silencieuses (fenêtres temporelles incohérentes, purge de données ML, etc.).

Exécutés à chaque CI/commit. Si un test casse, c'est qu'une modification
a introduit une incohérence qu'il faut corriger AVANT de merger.
"""

import ast
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ================================================================
# 1. AUCUN analyze_error_patterns(days=<constante>) hardcodé
# ================================================================

class TestNoHardcodedAnalysisWindow:
    """Vérifie que analyze_error_patterns n'est JAMAIS appelé avec days=<int>
    sauf dans sa définition (paramètre par défaut) et les tests."""

    def _scan_file(self, filepath: str) -> list[tuple[int, str]]:
        """Retourne les lignes où analyze_error_patterns est appelé avec days=<int>."""
        violations = []
        with open(filepath) as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                # Skip comments and the function definition itself
                if stripped.startswith("#"):
                    continue
                if "def analyze_error_patterns" in stripped:
                    continue
                # Look for calls with hardcoded integer days=
                if "analyze_error_patterns" in stripped:
                    match = re.search(r"analyze_error_patterns\([^)]*days\s*=\s*\d+", stripped)
                    if match:
                        violations.append((lineno, stripped))
        return violations

    def test_no_hardcoded_days_in_app(self):
        violations = self._scan_file(os.path.join(ROOT, "app.py"))
        assert not violations, (
            f"app.py contient analyze_error_patterns(days=<int>) hardcodé :\n"
            + "\n".join(f"  L{ln}: {code}" for ln, code in violations)
        )

    def test_no_hardcoded_days_in_scheduler(self):
        violations = self._scan_file(os.path.join(ROOT, "scheduler.py"))
        assert not violations, (
            f"scheduler.py contient analyze_error_patterns(days=<int>) hardcodé :\n"
            + "\n".join(f"  L{ln}: {code}" for ln, code in violations)
        )


# ================================================================
# 2. PURGE NE DÉTRUIT PAS LES DONNÉES BACKTEST
# ================================================================

class TestPurgePreservesBacktest:
    """La purge ne doit JAMAIS supprimer les données backtest ML."""

    def test_purge_predictions_excludes_backtest(self):
        """La requête DELETE predictions doit exclure cycle_id backtest."""
        with open(os.path.join(ROOT, "app.py")) as f:
            source = f.read()
        # Trouver la fonction purge_old_data
        assert "purge_old_data" in source
        # Vérifier que la requête DELETE predictions exclut backtest
        # On cherche un DELETE FROM predictions qui N'a PAS de condition backtest
        delete_pattern = re.findall(
            r"DELETE FROM predictions WHERE[^\"]+", source
        )
        for query in delete_pattern:
            if "orphan" in query.lower() or "confirmed" in query:
                continue  # skip orphan cleanup
            assert "backtest" in query.lower(), (
                f"DELETE FROM predictions sans exclusion backtest :\n  {query}\n"
                "Les données backtest seront supprimées !"
            )

    def test_purge_performance_not_deleted(self):
        """La table performance ne doit PAS être purgée (données ML essentielles)."""
        with open(os.path.join(ROOT, "app.py")) as f:
            source = f.read()
        # Vérifier qu'on ne DELETE pas de la table performance
        perf_deletes = re.findall(
            r"DELETE FROM performance WHERE date_cible", source
        )
        assert not perf_deletes, (
            f"purge_old_data supprime des données performance :\n"
            f"  {perf_deletes}\n"
            "Ces données sont essentielles pour l'apprentissage ML !"
        )


# ================================================================
# 3. COLD WAVE PASSE PAR LE SYSTÈME DE POIDS
# ================================================================

class TestColdWaveInWeightSystem:
    """Le bonus cold wave ne doit PAS court-circuiter la somme pondérée."""

    def test_no_direct_cold_wave_addition_to_score(self):
        """Vérifie que cold_wave n'est pas ajouté directement à score_risque/base_score."""
        with open(os.path.join(ROOT, "predictor.py")) as f:
            source = f.read()
        # Pattern interdit : base_score + cold_wave (bypass des poids)
        forbidden = re.findall(
            r"base_score\s*\+\s*cold_wave", source
        )
        assert not forbidden, (
            "cold_wave ajouté directement à base_score (bypass des poids) !\n"
            "Le cold wave doit être intégré dans un sub-score (ex: temp_score)."
        )

    def test_cold_wave_integrated_in_temp_score(self):
        """Vérifie que cold_wave est intégré dans temp_score avant la somme."""
        with open(os.path.join(ROOT, "predictor.py")) as f:
            source = f.read()
        assert "temp_score + cold_wave" in source or "temp_score+cold_wave" in source, (
            "cold_wave n'est pas intégré dans temp_score ! "
            "Il doit passer par le système de poids."
        )


# ================================================================
# 4. CV VALIDATION UTILISE F1-MACRO (PAS ACCURACY SEULE)
# ================================================================

class TestCVValidationMetric:
    """Le seuil de validation des poids doit utiliser F1-macro."""

    def test_f1_macro_used_in_recalculate_weights(self):
        with open(os.path.join(ROOT, "performance_tracker.py")) as f:
            source = f.read()
        assert "f1_macro" in source, (
            "recalculate_weights n'utilise pas f1_macro ! "
            "L'accuracy seule est trompeuse (baseline always-BLEU = 76%)."
        )

    def test_no_accuracy_only_threshold(self):
        """Pas de seuil basé uniquement sur accuracy dans le recalcul des poids."""
        with open(os.path.join(ROOT, "performance_tracker.py")) as f:
            source = f.read()
        # Chercher un pattern "if cv_accuracy < N" sans mention de f1
        # dans un bloc de 5 lignes
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if re.match(r"\s+if cv_accuracy < \d+:", line):
                context = "\n".join(lines[max(0, i-3):i+3])
                if "cv_f1" not in context and "f1_macro" not in context:
                    pytest.fail(
                        f"Seuil basé uniquement sur accuracy (sans F1-macro) :\n"
                        f"  L{i+1}: {line.strip()}"
                    )


# ================================================================
# 5. get_history_depth_days() EXISTE ET EST UTILISÉ
# ================================================================

class TestHistoryDepthCentralized:
    """La profondeur historique doit être calculée par la fonction centralisée."""

    def test_function_exists(self):
        from performance_tracker import get_history_depth_days
        result = get_history_depth_days()
        assert isinstance(result, int)
        assert result >= 90

    def test_scheduler_uses_centralized_function(self):
        with open(os.path.join(ROOT, "scheduler.py")) as f:
            source = f.read()
        assert "get_history_depth_days" in source, (
            "scheduler.py n'utilise pas get_history_depth_days() !"
        )

    def test_app_uses_centralized_function(self):
        with open(os.path.join(ROOT, "app.py")) as f:
            source = f.read()
        assert "get_history_depth_days" in source, (
            "app.py n'utilise pas get_history_depth_days() !"
        )

    def test_no_inline_min_date_query_in_scheduler(self):
        """Le scheduler ne doit pas recalculer la profondeur manuellement."""
        with open(os.path.join(ROOT, "scheduler.py")) as f:
            source = f.read()
        assert "MIN(date_cible)" not in source, (
            "scheduler.py contient une requête MIN(date_cible) inline !\n"
            "Utiliser get_history_depth_days() à la place."
        )

    def test_no_inline_min_date_query_in_app(self):
        """app.py ne doit pas recalculer la profondeur manuellement."""
        with open(os.path.join(ROOT, "app.py")) as f:
            source = f.read()
        assert "MIN(date_cible)" not in source, (
            "app.py contient une requête MIN(date_cible) inline !\n"
            "Utiliser get_history_depth_days() à la place."
        )


# ================================================================
# 6. BLANC PRESSURE CAP LIÉ À SEUIL_ROUGE
# ================================================================

class TestBlancPressureCap:
    """Le plafond BLANC doit être dynamiquement lié à SEUIL_ROUGE."""

    def test_no_magic_number_64(self):
        """Pas de min(64, ...) hardcodé pour blanc_pressure."""
        with open(os.path.join(ROOT, "predictor.py")) as f:
            source = f.read()
        assert "min(64," not in source, (
            "blanc_pressure utilise encore min(64, ...) hardcodé !\n"
            "Doit utiliser min(Config.SEUIL_ROUGE - 1, ...) dynamique."
        )

    def test_uses_seuil_rouge(self):
        with open(os.path.join(ROOT, "predictor.py")) as f:
            source = f.read()
        assert "SEUIL_ROUGE - 1" in source, (
            "blanc_pressure n'est pas lié dynamiquement à SEUIL_ROUGE !"
        )


# ================================================================
# 7. PROBABILITÉS CONFIGURABLES (PAS HARDCODÉES)
# ================================================================

class TestProbabilityConstants:
    """Les centres softmax doivent être dans Config, pas hardcodés."""

    def test_no_hardcoded_centers_in_predictor(self):
        """_compute_probabilities ne doit pas contenir 15/50/85 en dur."""
        with open(os.path.join(ROOT, "predictor.py")) as f:
            source = f.read()
        # Trouver la fonction _compute_probabilities
        func_start = source.find("def _compute_probabilities")
        func_end = source.find("\ndef ", func_start + 1)
        if func_end == -1:
            func_end = len(source)
        func_body = source[func_start:func_end]
        # Vérifier qu'elle utilise Config, pas des constantes
        assert "Config.PROBA_" in func_body, (
            "_compute_probabilities n'utilise pas Config.PROBA_* !"
        )

    def test_config_has_probability_constants(self):
        from config import Config
        assert hasattr(Config, "PROBA_CENTER_BLEU")
        assert hasattr(Config, "PROBA_CENTER_BLANC")
        assert hasattr(Config, "PROBA_CENTER_ROUGE")
        assert hasattr(Config, "PROBA_STEEPNESS")


# ================================================================
# 8. STARTUP SÉQUENCE COMPLÈTE
# ================================================================

class TestStartupSequence:
    """Le startup doit exécuter la chaîne ML complète dans le bon ordre."""

    def test_startup_calls_analyze_before_refresh(self):
        """analyze_error_patterns doit être appelé AVANT _refresh_predictions."""
        with open(os.path.join(ROOT, "app.py")) as f:
            source = f.read()
        pos_analyze = source.find("analyze_error_patterns")
        pos_refresh = source.find("_refresh_predictions")
        assert pos_analyze > 0, "analyze_error_patterns absent du startup"
        assert pos_refresh > 0, "_refresh_predictions absent du startup"
        assert pos_analyze < pos_refresh, (
            "analyze_error_patterns doit être appelé AVANT _refresh_predictions !\n"
            "Sinon les prédictions n'utilisent pas les corrections à jour."
        )

    def test_startup_calls_recalculate_weights(self):
        with open(os.path.join(ROOT, "app.py")) as f:
            source = f.read()
        assert "recalculate_weights" in source

    def test_startup_calls_evaluate_missed_days(self):
        with open(os.path.join(ROOT, "app.py")) as f:
            source = f.read()
        assert "evaluate_missed_days" in source


# ================================================================
# 9. PROFILS MENSUELS COHÉRENTS AVEC LES CONTRAINTES EDF
# ================================================================

class TestProfileConstraintCoherence:
    """Les profils mensuels doivent être cohérents avec les règles EDF."""

    def test_red_profile_zero_outside_nov_march(self):
        """ROUGE interdit hors nov-mars (regle R1) → profil doit être 0%."""
        from config import Config
        for month in [4, 5, 6, 7, 8, 9, 10]:
            pct = Config.MONTHLY_RED_PROFILE.get(month, 0.0)
            assert pct == 0.0, (
                f"MONTHLY_RED_PROFILE[{month}] = {pct} mais la regle R1 "
                f"interdit les jours rouges hors novembre-mars !"
            )

    def test_red_profile_sums_to_one(self):
        from config import Config
        total = sum(Config.MONTHLY_RED_PROFILE.values())
        assert abs(total - 1.0) < 0.02, (
            f"MONTHLY_RED_PROFILE somme = {total}, devrait etre ~1.0"
        )

    def test_red_urgency_uses_march_deadline(self):
        """La pression ROUGE doit utiliser la deadline mars, pas mai."""
        with open(os.path.join(ROOT, "predictor.py")) as f:
            source = f.read()
        # Vérifier que _score_budget_v2 calcule une deadline spécifique pour RED
        assert "red_d_left" in source or "red_deadline" in source, (
            "_score_budget_v2 n'a pas de deadline spécifique ROUGE !\n"
            "L'urgence doit utiliser les jours jusqu'au 31 mars (R1), pas le 31 mai."
        )
