"""Jumeau de l'algorithme RTE (rte_twin.py) et son branchement dans predictor.

Formules : note RTE « Méthode de choix des jours Tempo », indice 2 du 7/01/2025.
Étude : docs/audits/2026-09-29-jumeau-rte.md.
"""
import json
import math
from datetime import date, timedelta

import pytest

import predictor
import rte_twin as RT
from config import Config
from predictor import is_french_holiday


# ---------- Formules de la note RTE ----------

def test_parametres_publies():
    assert RT.GAMMA == -0.1176 and RT.KAPPA == 8.3042
    assert RT.SEUIL_ROUGE == (3.15, -0.010, -0.031)
    assert RT.SEUIL_BLANC_ROUGE == (4.00, -0.015, -0.026)
    assert (RT.Q_CONSO_BAS, RT.Q_CONSO_HAUT, RT.Q_TEMP) == (0.4, 0.8, 0.3)


def test_jour_tempo():
    assert RT.jour_tempo(date(2025, 9, 1)) == 1
    assert RT.jour_tempo(date(2026, 1, 1)) == 123
    assert RT.jour_tempo(date(2026, 8, 31)) == 365


def test_seuils_formules_exactes():
    s_br, s_r = RT.seuils(date(2025, 9, 1), stock_rouge=22, stock_blanc=43)
    assert s_br == pytest.approx(4.00 - 0.015 * 1 - 0.026 * 65)
    assert s_r == pytest.approx(3.15 - 0.010 * 1 - 0.031 * 22)
    s_br, s_r = RT.seuils(date(2026, 1, 15), stock_rouge=10, stock_blanc=20)
    j = 137
    assert s_br == pytest.approx(4.00 - 0.015 * j - 0.026 * 30)
    assert s_r == pytest.approx(3.15 - 0.010 * j - 0.031 * 10)


def test_normalisation_formule_exacte():
    n = RT.Normalisation(q_conso_04=40000, q_conso_08=55000, q_temp_03=RT.KAPPA)
    assert n.echelle == pytest.approx(15000)  # exp(0) = 1
    assert n.normalise(55000) == pytest.approx(1.0)
    n = RT.Normalisation(40000, 55000, 10.0)
    expected = (60000 - 40000) / ((55000 - 40000) * math.exp(-RT.GAMMA * (10.0 - RT.KAPPA)))
    assert n.normalise(60000) == pytest.approx(expected)


def test_quantile_interpolation_lineaire():
    assert RT.quantile([5, 1, 4, 2, 3], 0.4) == pytest.approx(2.6)
    assert RT.quantile([1, 2, 3, 4, 5], 0.8) == pytest.approx(4.2)
    n = RT.Normalisation.from_history([1, 2, 3, 4, 5], [0, 10, 20])
    assert (n.q_conso_04, n.q_conso_08, n.q_temp_03) == pytest.approx((2.6, 4.2, 6.0))


# ---------- Règles de placement ----------

LUNDI_JANV = date(2026, 1, 12)


def test_decide_seuils_et_calendrier():
    hot, cold = -5.0, 10.0
    assert RT.decide(LUNDI_JANV, cold, 10, 20, 0, is_french_holiday) == "ROUGE"
    assert RT.decide(LUNDI_JANV, hot, 10, 20, 0, is_french_holiday) == "BLEU"
    s_br, s_r = RT.seuils(LUNDI_JANV, 10, 20)
    assert RT.decide(LUNDI_JANV, (s_br + s_r) / 2 if s_r > s_br else s_br + 0.01, 10, 20, 0,
                     is_french_holiday) in ("BLANC", "ROUGE")
    assert RT.decide(date(2026, 1, 17), cold, 10, 20, 0, is_french_holiday) == "BLANC"  # samedi
    assert RT.decide(date(2026, 1, 18), cold, 10, 20, 0, is_french_holiday) == "BLEU"   # dimanche
    assert RT.decide(date(2025, 10, 15), cold, 10, 20, 0, is_french_holiday) == "BLANC"  # R1
    assert RT.decide(date(2025, 11, 11), cold, 10, 20, 0, is_french_holiday) == "BLANC"  # férié
    assert RT.decide(LUNDI_JANV, cold, 10, 20, 5, is_french_holiday) == "BLANC"  # 5 consécutifs
    assert RT.decide(LUNDI_JANV, cold, 0, 20, 0, is_french_holiday) == "BLANC"   # stock ROUGE vide
    assert RT.decide(LUNDI_JANV, cold, 0, 0, 0, is_french_holiday) == "BLEU"


def test_ecoulement_du_stock():
    # 31 mars 2026 (mardi) : dernier jour ROUGE possible, stock 1 -> placé même sous le seuil
    d = date(2026, 3, 31)
    assert RT.jours_eligibles_restants(d, "ROUGE", is_french_holiday) == 1
    assert RT.decide(d, -10.0, 1, 0, 0, is_french_holiday) == "ROUGE"
    assert RT.decide(d, -10.0, 1, 0, 0, is_french_holiday, ecoulement=False) == "BLEU"


def test_simulate_decremente_le_stock():
    days = [LUNDI_JANV, LUNDI_JANV + timedelta(days=1)]
    out = RT.simulate(days, {d: 10.0 for d in days}, 1, 5, {}, is_french_holiday)
    assert out == {days[0]: "ROUGE", days[1]: "BLANC"}


def test_simulate_max_5_rouges_avec_couleurs_connues():
    known = {date(2026, 1, 5) + timedelta(days=k): "ROUGE" for k in range(5)}  # lun-ven
    d = date(2026, 1, 12)  # lundi suivant, précédé d'un week-end : pas consécutif
    assert RT.simulate([d], {d: 10.0}, 10, 10, known, is_french_holiday)[d] == "ROUGE"


# ---------- Modèle de C_nette ----------

def test_modele_livre_coherent():
    m = json.loads(RT.MODEL_PATH.read_text(encoding="utf-8"))
    assert tuple(m["feature_names"]) == RT.FEATURE_NAMES
    assert len(m["coefs"]) == len(RT.FEATURE_NAMES)
    froid = {"temp_moy": -2, "temp_min": -5, "temp_max": 1, "wind_speed": 8, "humidity": 85, "pressure": 1030}
    doux = {"temp_moy": 12, "temp_min": 8, "temp_max": 16, "wind_speed": 25, "humidity": 70, "pressure": 1005}
    c_froid = RT.estimate_cnette(m["coefs"], RT.cnette_features(date(2026, 1, 14), froid, -1, -1, is_french_holiday))
    c_doux = RT.estimate_cnette(m["coefs"], RT.cnette_features(date(2026, 1, 18), doux, 12, 12, is_french_holiday))
    assert c_froid > c_doux + 15000
    assert 30000 < c_doux < c_froid < 100000


def test_forecast_colors_quand_normalisation_disponible(monkeypatch):
    wx = {date(2024, 9, 1) + timedelta(days=k): {"temp_moy": 10 + 8 * math.cos(2 * math.pi * k / 365),
                                                   "temp_min": 5, "temp_max": 15, "wind_speed": 15,
                                                   "humidity": 75, "pressure": 1013} for k in range(-2, 365)}
    monkeypatch.setattr(RT, "_load_weather_history", lambda a, b: {d: w for d, w in wx.items() if a <= d <= b})
    RT._NORM_CACHE.clear()
    targets = [date(2026, 1, 13), date(2026, 1, 14)]
    fc = {t - timedelta(days=k): {"temp_moy": -4, "temp_min": -7, "temp_max": 0, "wind_speed": 5,
                                   "humidity": 85, "pressure": 1035} for t in targets for k in range(3)}
    out = RT.forecast_colors(targets, fc, 10, 20, {}, is_french_holiday)
    RT._NORM_CACHE.clear()
    assert out == {targets[0]: "ROUGE", targets[1]: "ROUGE"}


def test_forecast_colors_none_si_meteo_insuffisante(monkeypatch):
    monkeypatch.setattr(RT, "_load_weather_history", lambda a, b: {})
    RT._NORM_CACHE.clear()
    try:
        assert RT.forecast_colors([date(2026, 1, 13)], {}, 10, 20, {}, is_french_holiday) is None
    finally:
        RT._NORM_CACHE.clear()


# ---------- Branchement dans predictor.predict_range ----------

def _pred(d, couleur):
    p = {"ROUGE": (0.1, 0.2, 0.7), "BLANC": (0.2, 0.6, 0.2), "BLEU": (0.7, 0.2, 0.1)}[couleur]
    return {"date": d.isoformat(), "couleur_predite": couleur, "probabilite_bleu": p[0],
            "probabilite_blanc": p[1], "probabilite_rouge": p[2], "raison": "test"}


@pytest.fixture
def pipeline(monkeypatch):
    """predict_range isolé : J+2 prédit BLEU, J+3 prédit ROUGE, le reste BLEU."""
    today = date.today()
    colors = {today + timedelta(days=2): "BLEU", today + timedelta(days=3): "ROUGE"}
    monkeypatch.setattr(predictor, "predict_day",
                        lambda t, *a, **k: _pred(t, colors.get(t, "BLEU")))
    monkeypatch.setattr(predictor, "_apply_thermal_coherence", lambda p, f: p)
    monkeypatch.setattr(predictor, "get_remaining_days", lambda: {"ROUGE": 10, "BLANC": 20, "BLEU": 100})
    monkeypatch.setattr(predictor, "get_current_weights", lambda: dict(Config.DEFAULT_WEIGHTS))
    monkeypatch.setattr(predictor, "_load_recent_actuals", lambda: {})
    monkeypatch.setattr(predictor, "_load_future_actuals", lambda: {})
    try:
        import performance_tracker
        monkeypatch.setattr(performance_tracker, "get_active_learnings", lambda: {})
    except Exception:
        pass
    fc = [{"date": (today + timedelta(days=i)).isoformat(), "temp_moy": 0.0} for i in range(7)]
    return today, fc


def test_interrupteur_coupe_comportement_inchange(pipeline, monkeypatch):
    today, fc = pipeline
    monkeypatch.setattr(Config, "RTE_TWIN_ENABLED", False)
    monkeypatch.setattr(RT, "forecast_colors", lambda *a, **k: pytest.fail("jumeau appelé alors que coupé"))
    preds = predictor.predict_range(fc)
    assert [p["couleur_predite"] for p in preds[2:4]] == ["BLEU", "ROUGE"]
    assert all("rte_twin" not in p for p in preds)


def test_filet_et_veto(pipeline, monkeypatch):
    today, fc = pipeline
    monkeypatch.setattr(Config, "RTE_TWIN_ENABLED", True)
    seen = {}

    def fake(targets, wx, sr, sb, known, hol):
        seen["targets"] = targets
        return {t: {2: "ROUGE", 3: "BLEU"}.get((t - today).days, "BLANC") for t in targets}

    monkeypatch.setattr(RT, "forecast_colors", fake)
    preds = predictor.predict_range(fc)
    assert [(t - today).days for t in seen["targets"]] == [2, 3, 4, 5]
    j2, j3 = preds[2], preds[3]
    wd3 = (today + timedelta(days=3)).weekday()
    assert j2["couleur_predite"] == "ROUGE"                       # filet
    assert j3["couleur_predite"] == ("BLANC" if wd3 != 6 else "BLEU")  # veto
    for p in (j2, j3):
        probs = {"ROUGE": p["probabilite_rouge"], "BLANC": p["probabilite_blanc"], "BLEU": p["probabilite_bleu"]}
        assert max(probs, key=probs.get) == p["couleur_predite"]
    assert preds[6]["couleur_predite"] == "BLEU" and "rte_twin" not in preds[6]  # J+6 hors périmètre


def test_erreur_du_jumeau_sans_effet(pipeline, monkeypatch):
    today, fc = pipeline
    monkeypatch.setattr(Config, "RTE_TWIN_ENABLED", True)

    def boom(*a, **k):
        raise RuntimeError("panne")

    monkeypatch.setattr(RT, "forecast_colors", boom)
    preds = predictor.predict_range(fc)
    assert [p["couleur_predite"] for p in preds[2:4]] == ["BLEU", "ROUGE"]
