"""Calibration des probabilités, politique d'alerte rouge et statut « simulé »
(docs/audits/2026-09-29-alertes-calibration.md)."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import alerts
import predictor
from database import get_db

ROOT = Path(__file__).resolve().parents[1]


def _next(weekday: int, month_ok=(1, 2, 12)) -> date:
    """Prochain jour de semaine `weekday` dans un mois de saison rouge."""
    d = date(2027, 1, 4)
    while d.weekday() != weekday or d.month not in month_ok or predictor.is_french_holiday(d):
        d += timedelta(days=1)
    return d


def _pred(d: date, couleur="ROUGE", pr=0.70, pb=0.20, twin="ROUGE", **kw):
    p = {"date": d.isoformat(), "couleur_predite": couleur, "probabilite_rouge": pr,
         "probabilite_blanc": pb, "probabilite_bleu": round(1 - pr - pb, 3), "score_risque": 70.0,
         "rte_twin": twin, "score_ml_rouge": 0.4, "jours_rouges_restants": 10,
         "jours_blancs_restants": 20, "confirmed": False}
    p.update(kw)
    return p


# ---------------------------------------------------------------- calibration

class TestCalibrationModel:
    def test_calibration_file_is_valid(self):
        model = json.load(open(ROOT / "calibration.json", encoding="utf-8"))
        assert set(model["horizons"]) == {"1", "2", "3", "4", "5"}
        for m in model["horizons"].values():
            assert sorted(m["classes"]) == ["BLANC", "BLEU", "ROUGE"]
            assert len(m["coef"]) == 3 and len(m["coef"][0]) == len(m["features"])
            assert "sans_jumeau" in m

    def test_loaded_and_enabled(self):
        assert predictor.PROBA_CALIBRATION_ENABLED is True
        assert predictor._load_calibration() is not None

    @pytest.mark.parametrize("couleur,pr,pb,twin", [
        ("ROUGE", 0.85, 0.10, "ROUGE"), ("ROUGE", 0.60, 0.30, "BLANC"), ("ROUGE", 0.55, 0.20, None),
        ("BLANC", 0.30, 0.50, "BLANC"), ("BLEU", 0.02, 0.18, "BLEU"), ("BLANC", 0.45, 0.46, "ROUGE"),
    ])
    @pytest.mark.parametrize("delta", [1, 2, 3, 5, 9])
    def test_invariants(self, couleur, pr, pb, twin, delta):
        p = _pred(_next(1), couleur, pr, pb, twin)
        assert predictor.calibrate_prediction(p, delta)
        probs = {"ROUGE": p["probabilite_rouge"], "BLANC": p["probabilite_blanc"],
                 "BLEU": p["probabilite_bleu"]}
        assert p["couleur_predite"] == couleur  # la couleur ne change jamais
        assert abs(sum(probs.values()) - 1) < 0.002
        assert all(v >= 0 for v in probs.values())
        assert probs[couleur] == max(probs.values())
        assert p["probabilites_brutes"] == [pr, pb, round(1 - pr - pb, 3)]

    def test_edf_rules_stay_at_zero(self):
        sat, sun = _next(5), _next(6)
        p = _pred(sat, "BLANC", 0.0, 0.6, None)
        predictor.calibrate_prediction(p, 3)
        assert p["probabilite_rouge"] == 0.0
        p = _pred(sun, "BLEU", 0.0, 0.0, None)
        predictor.calibrate_prediction(p, 3)
        assert p["probabilite_rouge"] == 0.0 and p["probabilite_blanc"] == 0.0 and p["probabilite_bleu"] == 1.0
        october = date(2027, 10, 13)
        p = _pred(october, "BLANC", 0.0, 0.6, None)
        predictor.calibrate_prediction(p, 2)
        assert p["probabilite_rouge"] == 0.0
        holiday = date(2027, 11, 11)
        p = _pred(holiday, "BLANC", 0.0, 0.7, None)
        predictor.calibrate_prediction(p, 2)
        assert p["probabilite_rouge"] == 0.0
        p = _pred(_next(2), "BLANC", 0.3, 0.5, "BLANC", jours_rouges_restants=0)
        predictor.calibrate_prediction(p, 2)
        assert p["probabilite_rouge"] == 0.0

    def test_confirmed_untouched(self):
        p = _pred(_next(1), confirmed=True, probabilite_rouge=1.0, probabilite_blanc=0.0,
                  probabilite_bleu=0.0)
        assert not predictor.calibrate_prediction(p, 1)
        assert p["probabilite_rouge"] == 1.0

    def test_missing_file_is_noop(self, monkeypatch):
        monkeypatch.setattr(predictor, "_CALIBRATION", None)
        monkeypatch.setattr(predictor, "_CALIBRATION_LOADED", False)
        monkeypatch.setattr(predictor, "CALIBRATION_FILE", "absent_calibration.json")
        p = _pred(_next(1))
        assert not predictor.calibrate_prediction(p, 2)
        assert p["probabilite_rouge"] == 0.70

    def test_twin_absent_uses_dedicated_model(self):
        model = predictor._load_calibration()
        a = _pred(_next(1), twin=None)
        b = dict(a)
        predictor.calibrate_prediction(a, 3, model)
        m = json.loads(json.dumps(model))
        del m["horizons"]["3"]["sans_jumeau"]
        predictor.calibrate_prediction(b, 3, m)
        assert a["probabilite_rouge"] != b["probabilite_rouge"]


class TestMinimalCoherence:
    def test_minimal_lift(self):
        p = {"couleur_predite": "ROUGE", "probabilite_rouge": 0.2, "probabilite_blanc": 0.1,
             "probabilite_bleu": 0.7}
        predictor._minimal_prob_coherence(p)
        assert p["probabilite_rouge"] > p["probabilite_bleu"] >= p["probabilite_blanc"]
        assert 0.44 <= p["probabilite_rouge"] <= 0.47  # nivellement, pas max + 5 points
        assert abs(p["probabilite_rouge"] + p["probabilite_blanc"] + p["probabilite_bleu"] - 1) < 0.002

    def test_two_competitors(self):
        p = {"couleur_predite": "BLEU", "probabilite_rouge": 0.45, "probabilite_blanc": 0.45,
             "probabilite_bleu": 0.10}
        predictor._minimal_prob_coherence(p)
        assert p["probabilite_bleu"] > max(p["probabilite_rouge"], p["probabilite_blanc"])

    def test_zero_stays_zero_and_noop_when_coherent(self):
        p = {"couleur_predite": "BLANC", "probabilite_rouge": 0.0, "probabilite_blanc": 0.3,
             "probabilite_bleu": 0.7}
        predictor._minimal_prob_coherence(p)
        assert p["probabilite_rouge"] == 0.0 and p["probabilite_blanc"] > p["probabilite_bleu"]
        q = {"couleur_predite": "BLEU", "probabilite_rouge": 0.0, "probabilite_blanc": 0.3,
             "probabilite_bleu": 0.7}
        predictor._minimal_prob_coherence(q)
        assert q["probabilite_bleu"] == 0.7


class TestPredictRangeIntegration:
    def test_colors_identical_and_probs_coherent(self, cold_weather, monkeypatch):
        monkeypatch.setattr(predictor, "PROBA_CALIBRATION_ENABLED", False)
        raw = predictor.predict_range([dict(w) for w in cold_weather])
        monkeypatch.setattr(predictor, "PROBA_CALIBRATION_ENABLED", True)
        cal = predictor.predict_range([dict(w) for w in cold_weather])
        assert [p["couleur_predite"] for p in raw] == [p["couleur_predite"] for p in cal]
        for p in cal:
            probs = {"ROUGE": p["probabilite_rouge"], "BLANC": p["probabilite_blanc"],
                     "BLEU": p["probabilite_bleu"]}
            assert probs[p["couleur_predite"]] == max(probs.values())
            assert abs(sum(probs.values()) - 1) < 0.002


# ---------------------------------------------------------------- politique d'alerte

class TestRougeAlertDue:
    def test_calibrated_thresholds(self):
        assert alerts._calibration_active()
        p = _pred(_next(1), pr=0.52)
        assert alerts.rouge_alert_due(70, p) and alerts.rouge_alert_due(80, p)
        assert not alerts.rouge_alert_due(90, p)
        assert not alerts.rouge_alert_due(70, _pred(_next(1), pr=0.49))
        assert alerts.rouge_alert_due(90, _pred(_next(1), pr=0.90))

    def test_original_rule_when_calibration_off(self, monkeypatch):
        monkeypatch.setattr(predictor, "PROBA_CALIBRATION_ENABLED", False)
        p = _pred(_next(1), pr=0.52)
        assert not alerts.rouge_alert_due(70, p)
        assert alerts.rouge_alert_due(70, _pred(_next(1), pr=0.70))
        assert not alerts.rouge_alert_due(80, _pred(_next(1), pr=0.79))

    def test_never_for_other_colors_or_disabled(self):
        assert not alerts.rouge_alert_due(70, _pred(_next(1), "BLANC", 0.45, 0.5))
        assert not alerts.rouge_alert_due(0, _pred(_next(1), pr=0.99))
        assert alerts.rouge_alert_due(55, _pred(_next(1), pr=0.55))  # valeur libre : règle d'origine

    def test_send_uses_policy_and_keeps_preferences(self, monkeypatch):
        sent = []
        monkeypatch.setattr(alerts, "_is_red_season", lambda d=None: True)
        monkeypatch.setattr(alerts, "_get_user_phone", lambda u: "+33600000000")
        monkeypatch.setattr(alerts, "send_whatsapp_template",
                            lambda phone, tpl, comp: (sent.append(tpl) or "SIM", "simulated"))
        conn = get_db()
        for i, seuil in enumerate((70, 80, 90)):
            conn.execute(
                """INSERT INTO users (phone_hash, phone_encrypted, phone_last4, seuil_alerte_rouge,
                   delai_alerte, alerte_blanc, recap_hebdo, heure_envoi, actif, manage_token, created_at,
                   updated_at)
                   VALUES (?, ?, ?, ?, 3, 0, 1, 'matin', 1, ?, '2026-09-29', '2026-09-29')""",
                (f"h{i}", "x", "0000", seuil, f"tok{i}"))
        conn.commit()
        conn.close()
        target = date.today() + timedelta(days=2)
        alerts.send_alerts_for_prediction(target, _pred(target, pr=0.55), "matin")
        assert len(sent) == 2  # 70 et 80, pas 90
        conn = get_db()
        seuils = sorted(r["seuil_alerte_rouge"] for r in conn.execute("SELECT seuil_alerte_rouge FROM users"))
        conn.close()
        assert seuils == [70, 80, 90]


# ---------------------------------------------------------------- statut simulé

class TestSimulatedPreservedOnConfirmation:
    def _store(self, d, simulated, horizon="J-3"):
        p = _pred(d)
        p.update(simulated=simulated, raison="test")
        predictor.store_prediction(p, horizon, cycle_id="test")

    def _rows(self, d):
        conn = get_db()
        rows = conn.execute("SELECT horizon, simulated, confirmed, couleur_predite FROM predictions "
                            "WHERE date = ? ORDER BY horizon", (d.isoformat(),)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def test_confirm_keeps_simulated(self):
        d = _next(2)
        self._store(d, True, "J-3")
        self._store(d, False, "J-2")
        assert predictor.confirm_prediction(d.isoformat(), "BLEU") == 2
        rows = {r["horizon"]: r for r in self._rows(d)}
        assert rows["J-3"]["simulated"] == 1 and rows["J-3"]["confirmed"] == 1
        assert rows["J-2"]["simulated"] == 0 and rows["J-2"]["confirmed"] == 1

    def test_store_confirmed_keeps_simulated(self):
        d = _next(3)
        self._store(d, True, "J-1")
        official = predictor._result_confirmed(d, "ROUGE", None)
        official.update(confirmed=True, simulated=False)
        predictor.store_prediction(official, "J-1", cycle_id="test")
        row = self._rows(d)[0]
        assert row["confirmed"] == 1 and row["simulated"] == 1

    def test_simulated_excluded_from_history_after_confirmation(self):
        d = _next(4)
        self._store(d, True, "J-3")
        predictor.confirm_prediction(d.isoformat(), "ROUGE")
        conn = get_db()
        n = conn.execute("SELECT COUNT(*) AS c FROM predictions WHERE simulated = 0 AND date = ?",
                         (d.isoformat(),)).fetchone()["c"]
        conn.close()
        assert n == 0
