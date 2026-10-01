"""Température OBSERVÉE (v27, weather_observed), 2026-10-01.

L'archive Open-Meteo est SIMULÉE (_observed_http_get remplacé) : toutes les
températures ci-dessous sont des FIXTURES de TEST qui ne servent qu'à vérifier
l'agrégation, le repli et le branchement ; elles restent dans la base
temporaire du test et ne sont jamais publiées.
"""

import asyncio
import inspect
import logging
from datetime import date, timedelta

import pytest

import database
import scheduler
import weather_client as wc
from config import Config
from database import get_db, init_db

CITIES = Config.WEATHER_CITIES


def _payload(days: list[str], last_covered: str | None = None, missing_city: int | None = None,
             base: float = 10.0):
    """Réponse multi-coordonnées FACTICE : ville i -> moyenne base+i, min -2, max +3.
    Après `last_covered`, valeurs nulles (retard de l'archive)."""
    locs = []
    for i, _ in enumerate(CITIES):
        mean, tmin, tmax = [], [], []
        for d in days:
            off = (last_covered and d > last_covered) or (missing_city == i and d == days[0])
            mean.append(None if off else base + i)
            tmin.append(None if off else base + i - 2)
            tmax.append(None if off else base + i + 3)
        locs.append({"latitude": 0, "longitude": 0, "daily": {
            "time": days, "temperature_2m_mean": mean,
            "temperature_2m_min": tmin, "temperature_2m_max": tmax}})
    return locs


def _days(start: str, end: str) -> list[str]:
    d, e, out = date.fromisoformat(start), date.fromisoformat(end), []
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _expected(base: float = 10.0) -> float:
    w = sum(c["weight"] for c in CITIES)
    return round(sum(c["weight"] * (base + i) for i, c in enumerate(CITIES)) / w, 2)


@pytest.fixture
def fake_archive(monkeypatch):
    """Archive simulée : couvre jusqu'à `state['last']`. Enregistre les appels."""
    state = {"last": "2026-03-10", "calls": [], "fail": None}

    def get(url, params):
        state["calls"].append(dict(params))
        if state["fail"]:
            return state["fail"]
        days = _days(params["start_date"], params["end_date"])
        return 200, _payload(days, last_covered=state["last"])
    monkeypatch.setattr(wc, "_observed_http_get", get)
    return state


def _obs_row(d="2026-03-02", t=4.2, **kw):
    r = {"date": d, "temp_moy": t, "temp_min": t - 3, "temp_max": t + 4, "n_villes": 9,
         "source": wc.OBSERVED_SOURCE, "fetched_at": "2026-03-10T23:30:00+01:00"}
    r.update(kw)
    return r


def _weather_cache(d, t, fetched="2026-03-02T23:00:00"):
    conn = get_db()
    conn.execute("INSERT INTO weather_cache (date, temp_moy, description, fetched_at) "
                 "VALUES (?, ?, ?, ?)", (d, t, "open-meteo", fetched))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- agrégation / API


class TestFetch:
    def test_weighted_mean_and_request(self, fake_archive):
        rep = wc.fetch_observed_temperatures(date(2026, 3, 1), date(2026, 3, 3))
        assert [r["date"] for r in rep["rows"]] == ["2026-03-01", "2026-03-02", "2026-03-03"]
        r = rep["rows"][0]
        assert r["temp_moy"] == _expected()
        assert r["temp_min"] == round(_expected() - 2, 2)
        assert r["temp_max"] == round(_expected() + 3, 2)
        assert r["n_villes"] == 9 and r["source"] == wc.OBSERVED_SOURCE and r["fetched_at"]
        p = fake_archive["calls"][0]
        assert len(fake_archive["calls"]) == 1                      # une requête multi-villes
        assert p["latitude"].count(",") == len(CITIES) - 1
        assert p["timezone"] == "Europe/Paris"
        assert p["daily"] == "temperature_2m_mean,temperature_2m_min,temperature_2m_max"

    def test_archive_lag_never_invents(self, fake_archive):
        rep = wc.fetch_observed_temperatures(date(2026, 3, 8), date(2026, 3, 13))
        assert [r["date"] for r in rep["rows"]] == ["2026-03-08", "2026-03-09", "2026-03-10"]
        assert rep["not_covered"] == ["2026-03-11", "2026-03-12", "2026-03-13"]
        assert rep["last_covered"] == "2026-03-10" and rep["error"] is None

    def test_missing_city_skips_date(self):
        agg = wc.aggregate_observed(_payload(["2026-03-01", "2026-03-02"], missing_city=4), CITIES)
        assert list(agg) == ["2026-03-02"]                          # pas de moyenne partielle

    def test_out_of_range_retries_with_announced_end(self, monkeypatch):
        calls = []

        def get(url, params):
            calls.append(params["end_date"])
            if params["end_date"] > "2026-03-10":
                return 400, {"error": True, "reason":
                             "Parameter 'end_date' is out of allowed range from 1940-01-01 to 2026-03-10"}
            return 200, _payload(_days(params["start_date"], params["end_date"]))
        monkeypatch.setattr(wc, "_observed_http_get", get)
        rep = wc.fetch_observed_temperatures(date(2026, 3, 9), date(2026, 3, 12))
        assert calls == ["2026-03-12", "2026-03-10"]
        assert rep["last_covered"] == "2026-03-10" and rep["not_covered"] == ["2026-03-11", "2026-03-12"]

    def test_api_error_reported_not_raised(self, fake_archive):
        fake_archive["fail"] = (429, {"error": True, "reason": "Daily API request limit exceeded"})
        rep = wc.fetch_observed_temperatures(date(2026, 3, 1), date(2026, 3, 2))
        assert rep["rows"] == [] and "429" in rep["error"]

    def test_long_range_is_chunked(self, fake_archive):
        fake_archive["last"] = "2026-12-31"
        rep = wc.fetch_observed_temperatures(date(2026, 2, 15), date(2026, 9, 30))
        assert len(fake_archive["calls"]) == 2 and len(rep["rows"]) == 228


# ---------------------------------------------------------------- base


class TestMigrationV27:
    def test_version_columns_conflict(self):
        conn = get_db()
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 27
            cols = [r[1] for r in conn.execute("PRAGMA table_info(weather_observed)").fetchall()]
        finally:
            conn.close()
        assert cols == list(database._WEATHER_OBSERVED_COLS)
        assert database._CONFLICT_COLS["weather_observed"] == "(date)"

    def test_replay_is_idempotent_and_converges(self):
        conn = get_db()
        conn.execute("DROP TABLE weather_observed")
        conn.execute("CREATE TABLE weather_observed (date TEXT PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 26")
        conn.commit()
        conn.close()
        init_db()                                           # converge le schéma minimal
        assert database.store_weather_observed([_obs_row()]) == 1
        conn = get_db()
        conn.execute("PRAGMA user_version = 26")
        conn.commit()
        conn.close()
        init_db()                                           # 2e passe : sans erreur
        assert database.get_weather_observed_dates("2026-01-01") == {"2026-03-02"}

    def test_upsert_replaces(self):
        database.store_weather_observed([_obs_row(t=4.2)])
        database.store_weather_observed([_obs_row(t=5.1, source="autre")])
        conn = get_db()
        try:
            rows = conn.execute("SELECT temp_moy, source FROM weather_observed").fetchall()
        finally:
            conn.close()
        assert [(r[0], r[1]) for r in rows] == [(5.1, "autre")]

    def test_db_sync_and_admin_stats(self):
        import db_sync
        assert "weather_observed" in db_sync.SYNC_TABLES
        assert db_sync.TABLE_COLUMNS["weather_observed"] == list(database._WEATHER_OBSERVED_COLS)
        assert db_sync.CONFLICT_COLS["weather_observed"] == "(date)"
        database.store_weather_observed([_obs_row("2026-03-02"), _obs_row("2026-03-03")])
        st = database.get_forecast_log_stats()["weather_observed"]
        assert st["rows"] == 2 and st["last_forecast_date"] == "2026-03-03"


# ---------------------------------------------------------------- tâche planifiée


class TestTask:
    def test_backfill_then_sliding_window(self, fake_archive, monkeypatch):
        monkeypatch.setattr(Config, "PREDICTION_START_DATE", "2026-02-15")
        msg = asyncio.run(scheduler.task_weather_observed(today=date(2026, 3, 14)))
        assert fake_archive["calls"][0]["start_date"] == "2026-02-15"   # rattrapage
        assert fake_archive["calls"][0]["end_date"] == "2026-03-13"     # jusqu'à la veille
        assert database.get_weather_observed_dates("2026-02-15") == set(
            _days("2026-02-15", "2026-03-10"))                         # rien après l'archive
        assert "3 jour(s) pas encore couverts" in msg
        fake_archive["calls"].clear()
        fake_archive["last"] = "2026-03-12"
        asyncio.run(scheduler.task_weather_observed(today=date(2026, 3, 15)))
        assert fake_archive["calls"][0]["start_date"] == "2026-03-05"   # 10 jours glissants
        assert "2026-03-12" in database.get_weather_observed_dates("2026-03-01")

    def test_never_raises(self, monkeypatch, caplog):
        def boom(*a):
            raise RuntimeError("réseau")
        monkeypatch.setattr(wc, "fetch_observed_temperatures", boom)
        assert "échec" in asyncio.run(scheduler.task_weather_observed(today=date(2026, 3, 14)))

    def test_api_error_is_non_blocking(self, fake_archive):
        fake_archive["fail"] = (429, {"error": True, "reason": "limit"})
        msg = asyncio.run(scheduler.task_weather_observed(today=date(2026, 3, 14)))
        assert "erreur" in msg and database.get_weather_observed_dates("2026-01-01") == set()

    def test_run_task_now_and_cron(self, fake_archive):
        msg = asyncio.run(scheduler.run_task_now("weather_observed"))
        assert "[Météo observée]" in msg
        src = inspect.getsource(scheduler)
        assert "CronTrigger(hour=23, minute=30" in src and "id=OBSERVED_JOB_ID" in src

    def test_scoring_untouched(self):
        import predictor
        src = inspect.getsource(predictor)
        assert "weather_observed" not in src
        assert "weather_observed" not in inspect.getsource(wc.fetch_forecast)

    def test_admin_button_and_label(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html = open(os.path.join(root, "templates", "admin.html"), encoding="utf-8").read()
        assert "runTask('weather_observed')" in html and "weather_observed:" in html


# ---------------------------------------------------------------- branchements


class TestConsumers:
    def test_history_prefers_observed_with_fallback(self):
        import prediction_history as ph
        _weather_cache("2026-03-02", 9.9)
        _weather_cache("2026-03-03", 7.7)
        database.store_weather_observed([_obs_row("2026-03-02", 4.2),
                                         _obs_row("2026-03-04", 1.5)])
        conn = get_db()
        try:
            _, _, observed = ph._load_actuals(conn, "2026-02-15")
        finally:
            conn.close()
        assert observed["2026-03-02"] == 4.2          # vraie observation
        assert observed["2026-03-03"] == 7.7          # pas couvert : ancienne valeur
        assert observed["2026-03-04"] == 1.5          # absente de weather_cache (purge)

    def test_history_page_and_csv_use_observed(self):
        import prediction_history as ph
        conn = get_db()
        conn.execute("INSERT INTO predictions (date, couleur_predite, horizon, timestamp_prediction, "
                     "simulated, cycle_id, confirmed, temp_moy_prevue) VALUES "
                     "('2026-03-02', 'BLEU', 'J-2', '2026-02-28T18:00:00', 0, '', 0, 5.0)")
        conn.execute("INSERT INTO actuals (date, couleur_reelle, synthetic, timestamp_confirmation) "
                     "VALUES ('2026-03-02', 'BLEU', 0, '2026-03-01T11:00:00')")
        conn.commit()
        conn.close()
        _weather_cache("2026-03-02", 9.9)
        database.store_weather_observed([_obs_row("2026-03-02", 4.2)])
        data = ph.get_history(force=True)
        day = next(d for s in data["seasons"].values() for d in s["days"] if d["date"] == "2026-03-02")
        assert day["temp_observee"] == 4.2
        assert "4,2" in ph.to_csv(data) and "9,9" not in ph.to_csv(data)

    def test_weather_reliability_uses_observed(self):
        from performance_tracker import get_weather_reliability
        conn = get_db()
        for d, fc in (("2026-03-02", 6.0), ("2026-03-03", 6.0)):
            conn.execute("INSERT INTO weather_forecast_log (target_date, forecast_date, "
                         "horizon_days, temp_moy, source, fetched_at) VALUES (?, ?, 2, ?, ?, ?)",
                         (d, _days("2026-02-28", "2026-03-01")[d == "2026-03-03"], fc,
                          "open-meteo", "2026-03-01T18:00:00"))
        conn.commit()
        conn.close()
        _weather_cache("2026-03-02", 6.0)                 # ancienne « observée » = prévue
        _weather_cache("2026-03-03", 5.0)                 # non couverte : repli
        database.store_weather_observed([_obs_row("2026-03-02", 4.0)])
        res = get_weather_reliability(days=100000)
        assert res["J-2"] == {"avg_error": 1.5, "avg_bias": 1.5, "samples": 2}

    def test_compare_tool_reference(self):
        import importlib.util
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "cws", os.path.join(root, "tools", "replay", "compare_weather_sources.py"))
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        conn = get_db()
        for d, fc in (("2026-03-02", 6.0), ("2026-03-03", 6.0)):
            conn.execute("INSERT INTO weather_forecast_log (target_date, forecast_date, "
                         "horizon_days, temp_moy, source, fetched_at) VALUES (?, ?, 2, ?, ?, ?)",
                         (d, "2026-03-01", fc, "open-meteo", "2026-03-01T18:00:00"))
        conn.commit()
        conn.close()
        for d in ("2026-03-02", "2026-03-03"):
            database.store_weather_mf_log([{"target_date": d, "forecast_date": "2026-03-01",
                                            "model": "merged", "horizon_days": 2, "temp_moy": 6.0,
                                            "fetched_at": "2026-03-01T18:00:00"}])
        _weather_cache("2026-03-02", 6.0)
        _weather_cache("2026-03-03", 5.0)
        database.store_weather_observed([_obs_row("2026-03-02", 4.0)])
        res = tool.compare(get_db())                        # défaut : ERA5 + repli
        assert res["reference_mode"] == "observed" and res["horizons"]["J+2"]["n"] == 2
        assert res["horizons"]["J+2"]["open_meteo"] == {"mae": 1.5, "bias": 1.5}
        assert any("pas encore couvertes" in w for w in res["warnings"])
        res = tool.compare(get_db(), reference="observed-only")
        assert res["horizons"]["J+2"]["n"] == 1
        res = tool.compare(get_db(), reference="cache")     # ancienne référence en option
        assert res["horizons"]["J+2"]["open_meteo"] == {"mae": 0.5, "bias": 0.5}
