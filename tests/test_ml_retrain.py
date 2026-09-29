"""Tests du modèle ML livré (ml_model.pkl) et du script ml_train.py.

Vérifie : chargement, variables et ordre identiques à ml_scorer, même
structure de sortie pour compute_ml_score, métadonnées honnêtes (période
d'entraînement réelle, métriques hors échantillon), seuils cohérents,
et absence de fuite d'information dans la construction des variables.
"""
import json
import pickle
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("sklearn")
np = pytest.importorskip("numpy")
if not hasattr(np, "__version__"):
    pytest.skip("numpy stub : tests ML ignorés", allow_module_level=True)

import ml_scorer  # noqa: E402
import ml_train  # noqa: E402

MODEL_PATH = ROOT / "ml_model.pkl"
BACKUP_PATH = ROOT / "ml_model_2024-08.pkl"


@pytest.fixture(scope="module")
def bundle():
    with open(MODEL_PATH, "rb") as f:
        return pickle.load(f)


@pytest.fixture(scope="module")
def dump_actuals():
    tables = json.load(open(ROOT / "db_dump.json", encoding="utf-8"))["tables"]
    return {r["date"]: r["couleur_reelle"] for r in tables["actuals"] if not r.get("synthetic")}


@pytest.fixture
def no_db(monkeypatch):
    """compute_ml_score sans base : aucune donnée RTE (valeurs de repli)."""
    monkeypatch.setattr(ml_scorer, "_get_rte_lag", lambda *a, **k: [])


WINTER_WX = {"temp_moy": -1.5, "temp_min": -5.0, "temp_max": 2.0,
             "pressure": 1030, "humidity": 85, "wind_speed": 12}


class TestBundleFormat:
    def test_same_keys_as_before(self, bundle):
        assert set(bundle) == {"model", "scaler", "metadata"}
        assert bundle["scaler"] is None  # compute_ml_score n'applique pas de scaler

    def test_feature_list_and_order(self, bundle):
        meta = bundle["metadata"]
        assert meta["feature_names"] == ml_train.FEATURE_NAMES
        assert meta["n_features"] == 33
        assert bundle["model"].n_features_in_ == 33
        assert list(bundle["model"].classes_) == ["BLANC", "BLEU", "ROUGE"]

    def test_build_features_positions(self, no_db):
        """L'ordre de _build_features correspond à FEATURE_NAMES."""
        t = date(2026, 1, 14)  # mercredi
        fc = [dict(WINTER_WX, temp_moy=1.0), dict(WINTER_WX)]
        x = ml_scorer._build_features(dict(WINTER_WX), t, fc, 1,
                                      {"2026-01-13": "ROUGE"})
        names = ml_train.FEATURE_NAMES
        assert len(x) == len(names) == 33
        v = dict(zip(names, x))
        assert v["temp_moy"] == -1.5 and v["pressure"] == 1030
        assert v["gradient"] == pytest.approx(2.5)
        assert v["is_weekend"] == 0 and v["in_red_season"] == 1
        assert v["prev_rouge"] == 1 and v["reds_7"] == 1
        assert v["rte_has_data"] == 0.0 and v["rte_conso_peak_d1"] == 5.5


class TestInference:
    def test_loads_without_error(self, monkeypatch):
        monkeypatch.setattr(ml_scorer, "_MODEL", None)
        monkeypatch.setattr(ml_scorer, "_LOAD_ERROR", False)
        assert ml_scorer.ml_score_available() is True

    def test_compute_ml_score_structure(self, no_db):
        out = ml_scorer.compute_ml_score(dict(WINTER_WX), date(2026, 1, 14),
                                         [dict(WINTER_WX)] * 3, 2, {})
        assert set(out) == {"available", "score_rouge", "score_blanc",
                            "score_bleu", "prediction"}
        assert out["available"] is True
        assert out["prediction"] in ("BLEU", "BLANC", "ROUGE")
        total = out["score_rouge"] + out["score_blanc"] + out["score_bleu"]
        assert total == pytest.approx(100, abs=0.5)

    def test_prediction_uses_metadata_thresholds(self, bundle, no_db):
        meta = bundle["metadata"]
        rt, bt = meta["rouge_threshold"], meta["blanc_threshold"]
        for tm in (-4, 0, 3, 6, 10, 15):
            wx = dict(WINTER_WX, temp_moy=tm, temp_min=tm - 3, temp_max=tm + 3)
            out = ml_scorer.compute_ml_score(wx, date(2026, 1, 14), [wx] * 3, 2, {})
            p = (out["score_rouge"] / 100, out["score_blanc"] / 100, out["score_bleu"] / 100)
            # tolérance d'arrondi (scores arrondis à 0,1 point)
            if abs(p[0] - rt) > 0.001 and abs(p[1] - bt) > 0.001:
                assert out["prediction"] == ml_train.decide(p, rt, bt)

    def test_cold_weekday_ranks_above_mild(self, no_db):
        cold = ml_scorer.compute_ml_score(dict(WINTER_WX), date(2026, 1, 14),
                                          [dict(WINTER_WX)] * 3, 2, {})
        mild_wx = dict(WINTER_WX, temp_moy=14, temp_min=10, temp_max=18)
        mild = ml_scorer.compute_ml_score(mild_wx, date(2026, 1, 14), [mild_wx] * 3, 2, {})
        assert cold["score_rouge"] > mild["score_rouge"]


class TestHonestMetadata:
    def test_training_period_is_real(self, bundle, dump_actuals):
        meta = bundle["metadata"]
        start, end = meta["date_range"].split(" to ")
        assert meta["train_date_range"] == {"start": start, "end": end}
        assert start >= min(dump_actuals)
        assert end <= max(dump_actuals)  # jamais au-delà des données réelles
        # dernière date avec météo observée dans le dump (au-delà : prévisions)
        assert end == max(ml_train.load_data()["weather"])

    def test_metrics_are_out_of_sample(self, bundle):
        meta = bundle["metadata"]
        assert "LOSO" in meta["metrics_scope"]
        assert "OBSERVÉE" in meta["metrics_scope"]
        for k in ("rouge_recall", "rouge_precision", "rouge_f1", "blanc_recall", "accuracy"):
            assert isinstance(meta[k], float) and 0.0 <= meta[k] <= 1.0
        assert meta["test_size"] == meta["train_size"]  # chaque ligne évaluée hors saison
        assert set(meta["oos_by_horizon"]) == {2, 3, 4, 5}

    def test_thresholds_coherent(self, bundle):
        meta = bundle["metadata"]
        assert 0.0 < meta["rouge_threshold"] < 1.0
        assert 0.0 < meta["blanc_threshold"] < 1.0
        assert meta["rouge_weight"] == 25
        assert meta["class_weights"] == {"ROUGE": 25, "BLANC": 3, "BLEU": 1}

    def test_backup_of_previous_model(self):
        with open(BACKUP_PATH, "rb") as f:
            old = pickle.load(f)
        assert old["metadata"]["model_name"] == "GBM_v3_lagRTE"
        assert old["metadata"]["feature_names"] == ml_train.FEATURE_NAMES


class TestNoLeakage:
    """ml_train reproduit l'information disponible à 18 h le jour D = T - h."""

    @pytest.fixture(scope="class")
    def data(self):
        return ml_train.load_data()

    def test_previous_color_only_known_at_j2(self, data):
        # 2025-01-10 ROUGE (réel) ; cible 2025-01-11
        t = date(2025, 1, 11)
        assert data["actuals"]["2025-01-10"] == "ROUGE"
        names = ml_train.FEATURE_NAMES
        x2 = dict(zip(names, ml_train.features_at_horizon(data, t, 2)))
        x3 = dict(zip(names, ml_train.features_at_horizon(data, t, 3)))
        assert x2["prev_rouge"] == 1
        assert x3["prev_rouge"] == 0  # couleur de la veille inconnue à J+3

    def test_no_rte_d1_at_horizon_2_plus(self, data):
        names = ml_train.FEATURE_NAMES
        for h in (2, 3, 4, 5):
            x = dict(zip(names, ml_train.features_at_horizon(data, date(2023, 1, 18), h)))
            assert x["rte_has_data"] == 0.0

    def test_uses_ml_scorer_build_features(self, data, monkeypatch):
        calls = []
        real = ml_scorer._build_features
        monkeypatch.setattr(ml_scorer, "_build_features",
                            lambda *a, **k: calls.append(1) or real(*a, **k))
        ml_train.features_at_horizon(data, date(2023, 1, 18), 2)
        assert calls == [1]
