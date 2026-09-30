"""Filet du calcul de 18h (décision fondateur du 2026-09-30, « que ça n'arrive plus jamais »).

Rattrapage à 18h45, 20h, 22h et au démarrage après 18h15 : relance le calcul si
AUCUNE prévision réelle n'a été émise aujourd'hui (J+2..J+15), sans jamais envoyer
de message WhatsApp. Aucun appel réseau : météo, RTE et prédicteur sont simulés.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

import pytest

import scheduler as sch

TODAY = date(2026, 9, 30)


def _pred(target: date, ts: str, *, simulated: int = 0, cycle_id: str = "c", horizon: str = ""):
    from database import get_db
    conn = get_db()
    conn.execute(
        "INSERT INTO predictions (date, couleur_predite, horizon, timestamp_prediction, "
        "simulated, cycle_id, confirmed) VALUES (?, 'BLEU', ?, ?, ?, ?, 0)",
        (target.isoformat(), horizon or f"J+{(target - TODAY).days}", ts, simulated, cycle_id))
    conn.commit()
    conn.close()


@pytest.fixture
def paris_today(monkeypatch):
    monkeypatch.setattr(sch, "_now_paris",
                        lambda: datetime(2026, 9, 30, 20, 0, tzinfo=sch._PARIS_TZ))


@pytest.fixture
def no_network(monkeypatch):
    """Pipeline complet simulé ; toute tentative d'envoi WhatsApp fait échouer le test."""
    import alerts
    import predictor
    import rte_client
    import weather_client
    import app as app_module

    calls = {"refresh": 0, "whatsapp": 0}

    async def forecasts():
        calls["refresh"] += 1
        return [{"date": (TODAY + timedelta(days=3)).isoformat(), "temp_moy": 2.0}]

    async def vigilance():
        return None

    async def rte():
        return {"score": 50, "available": False}

    async def noop(*a, **k):
        return 0

    def whatsapp(*a, **k):
        calls["whatsapp"] += 1
        raise AssertionError("message WhatsApp envoyé depuis le rattrapage")

    target = (TODAY + timedelta(days=3)).isoformat()
    monkeypatch.setattr(weather_client, "fetch_forecast_extended", forecasts)
    monkeypatch.setattr(weather_client, "fetch_vigilance", vigilance)
    monkeypatch.setattr(rte_client, "get_consumption_score", rte)
    monkeypatch.setattr(predictor, "predict_range", lambda *a, **k: [
        {"date": target, "couleur_predite": "ROUGE", "horizon": "J+3", "confirmed": False}])
    # Un changement de couleur détecté : déclencherait send_change_alerts hors rattrapage
    monkeypatch.setattr(predictor, "store_prediction", lambda *a, **k: {
        "date": target, "couleur_avant": "BLEU", "couleur_apres": "ROUGE"})
    monkeypatch.setattr(alerts, "send_alerts_for_prediction", whatsapp)
    monkeypatch.setattr(alerts, "send_change_alerts", whatsapp)
    monkeypatch.setattr(app_module, "invalidate_predictions_cache", lambda: None)
    monkeypatch.setattr(sch, "_store_rte_daily", noop)
    monkeypatch.setattr(sch, "_store_weather_cache", lambda *a, **k: None)
    monkeypatch.setattr(sch, "_ping_indexnow", noop)
    monkeypatch.setattr(sch, "_archive_rte_forecasts", noop)
    return calls


class TestEmissionCount:
    def test_counts_only_real_emissions_of_the_day(self, paris_today):
        _pred(TODAY + timedelta(days=1), "2026-09-30T18:00:05+02:00")  # J+1 : hors périmètre
        _pred(TODAY + timedelta(days=3), "2026-09-30T18:00:05+02:00", simulated=1)
        _pred(TODAY + timedelta(days=3), "2026-09-30T18:00:05+02:00", cycle_id="backtest_x",
              horizon="BT")
        _pred(TODAY + timedelta(days=4), "2026-09-29T18:00:05+02:00")  # émise hier
        assert sch.count_emissions_today() == 0
        _pred(TODAY + timedelta(days=15), "2026-09-30T18:00:05+02:00")
        assert sch.count_emissions_today() == 1


class TestCatchup:
    def test_triggered_when_zero_emission(self, paris_today, no_network):
        count = asyncio.run(sch.task_catchup_predictions())
        assert count == 1 and no_network["refresh"] == 1
        assert no_network["whatsapp"] == 0

    def test_not_triggered_when_already_emitted(self, paris_today, no_network):
        _pred(TODAY + timedelta(days=2), "2026-09-30T18:00:05+02:00")
        assert asyncio.run(sch.task_catchup_predictions(final=True)) == 0
        assert no_network["refresh"] == 0

    def test_never_sends_whatsapp_even_with_changes(self, paris_today, no_network):
        # send_sms demandé par erreur : alerts=False l'emporte, aucun envoi
        n = asyncio.run(sch._refresh_predictions("rattrapage", send_sms=True, alerts=False))
        assert n == 1 and no_network["whatsapp"] == 0

    def test_final_logs_error_when_still_nothing(self, paris_today, no_network, monkeypatch, caplog):
        import weather_client

        async def empty():
            return []

        async def fast_sleep(_):
            return None

        monkeypatch.setattr(weather_client, "fetch_forecast_extended", empty)
        monkeypatch.setattr(sch.asyncio, "sleep", fast_sleep)
        with caplog.at_level(logging.ERROR, logger="scheduler"):
            assert asyncio.run(sch.task_catchup_predictions(final=True)) == 0
        assert "[Rattrapage] aucune prévision émise aujourd'hui" in caplog.text
        assert no_network["whatsapp"] == 0

    def test_post_startup_runs_catchup_after_18h15(self):
        import inspect
        src = inspect.getsource(sch._task_post_startup)
        assert "task_catchup_predictions" in src and "CATCHUP_STARTUP_AFTER" in src
        assert sch.CATCHUP_STARTUP_AFTER == (18, 15)


class TestJobs:
    def test_misfire_and_catchup_jobs(self, monkeypatch):
        monkeypatch.setattr(sch.scheduler, "start", lambda *a, **k: None)
        sch.start_scheduler()
        try:
            jobs = {j.id: j for j in sch.scheduler.get_jobs()}
            for jid in ("daily_predictions", "catchup_predictions_1845",
                        "catchup_predictions_2000", "catchup_predictions_2200"):
                assert jid in jobs, jid
                assert jobs[jid].misfire_grace_time == 3600
                assert jobs[jid].coalesce is True
            assert jobs["catchup_predictions_2200"].func.__name__ == "tracked_catchup_predictions"
        finally:
            for j in sch.scheduler.get_jobs():
                j.remove()

    def test_source_options(self):
        assert sch.PREDICTION_JOB_OPTIONS == {"misfire_grace_time": 3600, "coalesce": True}
        assert sch.CATCHUP_TIMES == ((18, 45), (20, 0), (22, 0))


class TestAdminStatus:
    def test_emission_status(self, paris_today, monkeypatch):
        monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2026-09-20")
        for e in ("2026-09-20", "2026-09-21", "2026-09-23", "2026-09-29"):
            _pred(date.fromisoformat(e) + timedelta(days=3), e + "T18:00:05+02:00")
        from database import get_db
        conn = get_db()
        conn.execute("INSERT INTO performance (date_prediction, date_cible, jours_avance, correct, "
                     "couleur_predite, couleur_reelle, contexte_meteo, timestamp_evaluation) "
                     "VALUES ('2026-09-24', '2026-09-26', 2, 1, 'BLEU', 'BLEU', '', '2026-09-26')")
        conn.commit()
        conn.close()
        st = sch.get_emission_status()
        assert st["derniere_emission"].startswith("2026-09-29T18:00")
        # 20 -> 29 septembre (aujourd'hui exclu) : 22, 25, 26, 27, 28 sans calcul
        assert st["jours_sans_calcul_30j_dates"] == [
            "2026-09-22", "2026-09-25", "2026-09-26", "2026-09-27", "2026-09-28"]
        assert st["jours_sans_calcul_30j"] == 5

    def test_endpoint_exposes_fields(self, paris_today, monkeypatch):
        from fastapi.testclient import TestClient
        import app as app_module
        app_module._db_ready.set()
        monkeypatch.setattr(app_module, "verify_admin", lambda *a, **k: None)
        _pred(TODAY + timedelta(days=2), "2026-09-30T18:00:05+02:00")
        r = TestClient(app_module.app).get("/admin/scheduler-status",
                                           headers={"Authorization": "Bearer x"})
        body = r.json()
        assert r.status_code == 200
        assert body["derniere_emission"].startswith("2026-09-30T18:00")
        assert "jours_sans_calcul_30j" in body
