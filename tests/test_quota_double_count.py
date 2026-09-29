"""Quota restant dans predict_range : les couleurs EDF de J0 et J+1 ne comptent qu'une fois.

get_remaining_days() compte déjà toutes les couleurs publiées de la saison
(J0 et J+1 compris). predict_range ne doit donc pas les décrémenter une seconde
fois avant de prédire J+2 (bug corrigé le 2026-09-29, rejeu tools/replay/ :
+12 ROUGE attrapés à J+2..J+5 sur 7 saisons).
"""
from datetime import date, timedelta

import pytest

import predictor
from config import Config
from tempo_client import store_actual


def _weather(d: date) -> dict:
    return {"date": d.isoformat(), "temp_min": -3.0, "temp_max": 2.0, "temp_moy": -0.5,
            "humidity": 80, "wind_speed": 10, "pressure": 1025, "description": "test"}


@pytest.fixture
def captured(monkeypatch):
    """Remplace predict_day par un espion qui enregistre le quota reçu."""
    calls = []

    def spy(target_date, *args, remaining=None, **kwargs):
        calls.append((target_date, dict(remaining)))
        return {"date": target_date.isoformat(), "couleur_predite": "BLEU"}

    monkeypatch.setattr(predictor, "predict_day", spy)
    monkeypatch.setattr(predictor, "_apply_thermal_coherence", lambda preds, fc: preds)
    monkeypatch.setattr(predictor, "get_current_weights", lambda: dict(Config.DEFAULT_WEIGHTS))
    try:
        import performance_tracker
        monkeypatch.setattr(performance_tracker, "get_active_learnings", lambda: {})
    except Exception:
        pass
    return calls


def _need_same_season():
    tomorrow = date.today() + timedelta(days=1)
    if tomorrow.month == 9 and tomorrow.day == 1:
        pytest.skip("J+1 dans la saison suivante : cas hors sujet")


def test_j0_j1_confirmes_non_decomptes_deux_fois(captured):
    """ROUGE à J0 et BLANC à J+1 en base : J+2 reçoit 21 ROUGE / 42 BLANC, pas 20 / 41."""
    _need_same_season()
    today = date.today()
    store_actual(today.isoformat(), "ROUGE")
    store_actual((today + timedelta(days=1)).isoformat(), "BLANC")
    expected = predictor.get_remaining_days()
    assert expected["ROUGE"] == Config.JOURS_ROUGES_TOTAL - 1
    assert expected["BLANC"] == Config.JOURS_BLANCS_TOTAL - 1

    preds = predictor.predict_range([_weather(today + timedelta(days=i)) for i in range(4)])

    assert [p.get("confirmed") for p in preds[:2]] == [True, True]
    first_target, first_remaining = captured[0]
    assert first_target == today + timedelta(days=2)
    assert first_remaining["ROUGE"] == expected["ROUGE"]
    assert first_remaining["BLANC"] == expected["BLANC"]


def test_predictions_non_confirmees_decrementent_toujours(captured, monkeypatch):
    """Le décrément des jours PRÉDITS (Fix #2) est conservé."""
    _need_same_season()
    today = date.today()
    monkeypatch.setattr(predictor, "get_remaining_days",
                        lambda: {"ROUGE": 5, "BLANC": 10, "BLEU": 100})
    monkeypatch.setattr(predictor, "_load_future_actuals", lambda: {})
    monkeypatch.setattr(predictor, "_load_recent_actuals", lambda: {})

    def spy_rouge(target_date, *args, remaining=None, **kwargs):
        captured.append((target_date, dict(remaining)))
        return {"date": target_date.isoformat(), "couleur_predite": "ROUGE"}

    monkeypatch.setattr(predictor, "predict_day", spy_rouge)
    predictor.predict_range([_weather(today + timedelta(days=i)) for i in range(3)])
    assert [r["ROUGE"] for _, r in captured] == [5, 4, 3]
