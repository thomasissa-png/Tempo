"""Pause du recalcul automatique des poids (2026-09-29) + correctifs get_daily_recap.

Base SQLite temporaire (conftest.py). Les lignes insérées ici sont des cas de test
unitaires contrôlés, pas des données d'analyse.
"""

import asyncio
import inspect
import logging
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ================================================================
# Helpers
# ================================================================

def _weights_count() -> int:
    from database import get_db
    conn = get_db()
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM weights_history").fetchone()["n"]
    finally:
        conn.close()


@pytest.fixture
def spies(monkeypatch):
    """Neutralise les étapes lourdes de la tâche et espionne recalculate_weights."""
    import performance_tracker as pt
    import alerts
    calls = {"recalc": 0, "cleanup": 0}

    def fake_recalc():
        calls["recalc"] += 1
        return None

    def fake_cleanup(months=6):
        calls["cleanup"] += 1

    monkeypatch.setattr(pt, "recalculate_weights", fake_recalc)
    monkeypatch.setattr(pt, "evaluate_missed_days", lambda lookback=30: 0)
    monkeypatch.setattr(pt, "analyze_error_patterns", lambda days=30, **kw: [])
    monkeypatch.setattr(pt, "get_history_depth_days", lambda: 30)
    monkeypatch.setattr(alerts, "cleanup_inactive_users", fake_cleanup)
    return calls


# ================================================================
# TÂCHE 1 : interrupteur Config.AUTO_WEIGHTS_RECALC_ENABLED
# ================================================================

class TestAutoWeightsPause:
    def test_switch_off_by_default(self):
        from config import Config
        assert Config.AUTO_WEIGHTS_RECALC_ENABLED is False
        src = (ROOT / "config.py").read_text(encoding="utf-8")
        assert "2026-09-29" in src.split("AUTO_WEIGHTS_RECALC_ENABLED = False")[0][-900:]

    def test_scheduled_run_does_not_recalculate(self, spies, caplog):
        import scheduler
        before = _weights_count()
        with caplog.at_level(logging.WARNING, logger="scheduler"):
            out = asyncio.run(scheduler.task_monthly_weights())
        assert spies["recalc"] == 0
        assert _weights_count() == before
        assert "recalcul automatique des poids désactivé" in out
        assert "recalcul automatique des poids désactivé" in caplog.text
        assert spies["cleanup"] == 1  # le nettoyage RGPD continue de tourner

    def test_production_weights_untouched(self, spies):
        import scheduler
        from database import get_current_weights
        before = get_current_weights()
        asyncio.run(scheduler.task_monthly_weights())
        assert get_current_weights() == before

    def test_recovery_path_is_automatic(self):
        """Le rattrapage cold start appelle la tâche sans manual=True."""
        import scheduler
        src = inspect.getsource(scheduler.recover_overdue_tasks)
        assert '("bimonthly_weights", task_monthly_weights,' in src

    def test_manual_admin_trigger_recalculates(self, spies, caplog):
        import scheduler
        with caplog.at_level(logging.WARNING, logger="scheduler"):
            out = asyncio.run(scheduler.run_task_now("weights"))
        assert spies["recalc"] == 1
        assert "manuelle explicite" in out
        assert "Recalcul auto en pause" in out
        assert "MANUEL" in caplog.text

    def test_switch_on_restores_auto(self, spies, monkeypatch):
        import scheduler
        monkeypatch.setattr("config.Config.AUTO_WEIGHTS_RECALC_ENABLED", True)
        asyncio.run(scheduler.task_monthly_weights())
        assert spies["recalc"] == 1

    def test_post_startup_guarded(self):
        import scheduler
        src = inspect.getsource(scheduler._task_post_startup)
        guard = src.index("if auto_weights_recalc_enabled():")
        assert guard < src.index("run_in_executor(None, recalculate_weights)")

    def test_job_still_scheduled_and_labelled_paused(self, monkeypatch):
        import scheduler

        class FakeScheduler:
            def __init__(self):
                self.jobs = {}

            def add_job(self, fn, trigger, id, name, replace_existing=True):
                self.jobs[id] = (name, trigger)

            def start(self):
                pass

        fake = FakeScheduler()
        monkeypatch.setattr(scheduler, "scheduler", fake)
        scheduler.start_scheduler()
        name, trigger = fake.jobs["bimonthly_weights"]
        assert "EN PAUSE" in name
        assert "day='1,15'" in str(trigger)

    def test_admin_shows_pause(self):
        html = (ROOT / "templates" / "admin.html").read_text(encoding="utf-8")
        assert "Recalcul poids (manuel)" in html
        assert "includes('EN PAUSE')" in html
        assert "En pause (recalcul auto d&eacute;sactiv&eacute;)" in html
        assert "recalcul MANUEL et explicite" in html


# ================================================================
# TÂCHE 2 : get_daily_recap (confiance, backtests, horizon réel)
# ================================================================

TARGET = "2026-02-10"  # mardi, saison 2025-2026


def _before(d: str, n: int) -> str:
    return (date.fromisoformat(d) - timedelta(days=n)).isoformat()


def _pred(target: str, emitted_ts: str, couleur: str, *, horizon: str = "",
          simulated: int = 0, cycle_id: str = "", confirmed: int = 0,
          originale: str = "", probas=(0.0, 0.0, 0.0)):
    from database import get_db
    conn = get_db()
    n = (date.fromisoformat(target) - date.fromisoformat(emitted_ts[:10])).days
    conn.execute(
        "INSERT INTO predictions (date, couleur_predite, horizon, timestamp_prediction, "
        "simulated, cycle_id, confirmed, couleur_originale, "
        "probabilite_bleu, probabilite_blanc, probabilite_rouge) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (target, couleur, horizon or f"J-{n}", emitted_ts, simulated, cycle_id,
         confirmed, originale, *probas),
    )
    conn.commit()
    conn.close()


def _perf(target: str, n: int, couleur: str, reel: str, contexte: str = ""):
    from database import get_db
    conn = get_db()
    conn.execute(
        "INSERT INTO performance (date_prediction, date_cible, jours_avance, correct, "
        "couleur_predite, couleur_reelle, contexte_meteo, timestamp_evaluation) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (_before(target, n), target, n, int(couleur == reel), couleur, reel, contexte,
         target + "T11:30:00"),
    )
    conn.commit()
    conn.close()


def _actual(d: str, couleur: str):
    from database import get_db
    conn = get_db()
    conn.execute(
        "INSERT INTO actuals (date, couleur_reelle, synthetic, timestamp_confirmation) "
        "VALUES (?, ?, 0, ?)", (d, couleur, d + "T11:00:00"),
    )
    conn.commit()
    conn.close()


def _recap_entry(d: str = TARGET):
    from performance_tracker import get_daily_recap
    return next((e for e in get_daily_recap("2025-2026") if e["date"] == d), None)


class TestConfidencePct:
    def test_unit_scale(self):
        from performance_tracker import _confidence_pct
        assert _confidence_pct(0.1, 0.03, 0.87) == 87
        assert _confidence_pct(1.0, 0.0, 0.0) == 100
        assert _confidence_pct(0.334, 0.333, 0.333) == 33

    def test_legacy_percent_scale(self):
        from performance_tracker import _confidence_pct
        assert _confidence_pct(10, 3, 87) == 87
        assert _confidence_pct(40.4, 30, 29.6) == 40
        assert _confidence_pct(250, 0, 0) == 100

    def test_empty(self):
        from performance_tracker import _confidence_pct
        assert _confidence_pct(0, 0, 0) is None
        assert _confidence_pct(None, None, None) is None
        assert _confidence_pct("x", None, float("nan")) is None

    def test_recap_confidence_is_percent(self):
        _pred(TARGET, _before(TARGET, 2) + "T18:00:00", "ROUGE", probas=(0.05, 0.08, 0.87))
        pred = _recap_entry()["predictions"]["J-2"]
        assert pred["confidence"] == 87

    def test_confirmed_row_has_no_confidence(self):
        """confirm_prediction écrase les probabilités (1.0 pour la couleur EDF)."""
        _actual(TARGET, "ROUGE")
        _pred(TARGET, _before(TARGET, 3) + "T18:00:00", "ROUGE", confirmed=1,
              originale="BLANC", probas=(0.0, 0.0, 1.0))
        pred = _recap_entry()["predictions"]["J-3"]
        assert pred["confidence"] is None
        assert pred["couleur"] == "BLANC" and pred["correct"] is False


class TestRecapExclusions:
    def test_backtest_cycle_excluded(self):
        _pred(TARGET, _before(TARGET, 2) + "T18:00:00", "ROUGE", cycle_id="backtest_2026")
        _pred(TARGET, _before(TARGET, 3) + "T18:00:00", "BLANC")
        preds = _recap_entry()["predictions"]
        assert "J-2" not in preds and preds["J-3"]["couleur"] == "BLANC"

    def test_backtest_only_date_disappears(self):
        _pred(TARGET, _before(TARGET, 1) + "T18:00:00", "ROUGE", cycle_id="Backtest")
        assert _recap_entry() is None

    def test_backtest_evaluation_excludes_imported_row(self):
        """Ligne importée sans cycle_id : reconnue par son évaluation « backtest »."""
        _actual(TARGET, "BLEU")
        _pred(TARGET, _before(TARGET, 1) + "T18:00:00", "ROUGE")
        _perf(TARGET, 1, "ROUGE", "BLEU", contexte="backtest temp=1.2")
        _pred(TARGET, _before(TARGET, 2) + "T18:00:00", "BLEU")
        _perf(TARGET, 2, "BLEU", "BLEU", contexte="Couleur officielle")
        preds = _recap_entry()["predictions"]
        assert "J-1" not in preds and preds["J-2"]["correct"] is True

    def test_simulated_excluded(self):
        _pred(TARGET, _before(TARGET, 2) + "T18:00:00", "ROUGE", simulated=1)
        assert _recap_entry() is None


class TestRecapRealHorizon:
    def test_label_ignored_gap_used(self):
        _pred(TARGET, _before(TARGET, 2) + "T18:00:00", "ROUGE", horizon="J-5")
        preds = _recap_entry()["predictions"]
        assert set(preds) == {"J-2"}
        assert preds["J-2"]["pred_made_date"] == _before(TARGET, 2)

    def test_same_day_row_is_not_j1(self):
        """Émise le jour même (ex. 00h04) : horizon 0, hors grille J-1..J-15."""
        _pred(TARGET, TARGET + "T00:04:14+01:00", "ROUGE", horizon="J-1")
        _pred(TARGET, _before(TARGET, 1) + "T18:00:00", "BLANC", horizon="J-2")
        preds = _recap_entry()["predictions"]
        assert set(preds) == {"J-1"} and preds["J-1"]["couleur"] == "BLANC"

    def test_confirmed_fallback_uses_real_horizon(self):
        """couleur_originale vide : la couleur émise vient de performance (même N)."""
        _actual(TARGET, "ROUGE")
        _pred(TARGET, _before(TARGET, 2) + "T18:00:00", "ROUGE", horizon="J-3",
              confirmed=1, originale="", probas=(0.0, 0.0, 1.0))
        _perf(TARGET, 2, "BLANC", "ROUGE")
        pred = _recap_entry()["predictions"]["J-2"]
        assert pred["couleur"] == "BLANC" and pred["correct"] is False

    def test_output_keys_unchanged(self):
        _actual(TARGET, "ROUGE")
        _pred(TARGET, _before(TARGET, 1) + "T18:00:00", "ROUGE", probas=(0.1, 0.2, 0.7))
        e = _recap_entry()
        assert set(e) == {"date", "actual", "actual_status", "predictions", "weather_observed",
                          "consec_correct", "diagnostic", "temp_deviation", "tool_update"}
        assert set(e["predictions"]["J-1"]) == {"couleur", "score", "correct", "temp_prevue",
                                                "confidence", "pred_made_date"}
        assert e["consec_correct"] == 1

    def test_same_rule_as_public_history(self):
        """Une seule définition de l'horizon, partagée avec prediction_history."""
        import performance_tracker as pt
        import prediction_history as ph
        assert "emission_horizon" in inspect.getsource(pt.get_daily_recap)
        assert "is_backtest" in inspect.getsource(pt.get_daily_recap)
        assert "emission_horizon" in inspect.getsource(ph._load_preds)
        assert ph.emission_horizon("2026-02-10", "2026-02-08T23:59:00+01:00") == 2
        assert ph.emission_horizon(date(2026, 2, 10), "2026-02-10T00:04") == 0
        assert ph.emission_horizon("x", "2026-02-10") is None
