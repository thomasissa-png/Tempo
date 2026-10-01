"""Archive des prévisions RTE (migration v25, table rte_forecast_log)
et alimentation de weather_forecast_log par le cycle quotidien.

Les réponses RTE ci-dessous sont des FIXTURES de parsing au format documenté
(short_term / weekly_forecasts / generation_forecast v3 : listes de
{"start_date", "end_date", "value"}). Elles ne servent à aucune analyse.
"""

import asyncio
import os
import subprocess
import sys
import textwrap
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

import database
from database import get_db, init_db

TODAY = date(2026, 1, 12)  # lundi, hors changement d'heure


def _count(table="rte_forecast_log"):
    conn = get_db()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _row(target, forecast, table="rte_forecast_log"):
    conn = get_db()
    try:
        r = conn.execute(f"SELECT * FROM {table} WHERE target_date = ? AND forecast_date = ?",
                         (target, forecast)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


# ---------------------------------------------------------------- migration

class TestMigrationV25:
    def test_version_and_columns(self):
        conn = get_db()
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 26
            cols = [r[1] for r in conn.execute("PRAGMA table_info(rte_forecast_log)").fetchall()]
        finally:
            conn.close()
        assert cols == ["target_date", "forecast_date", "horizon_days", "conso_mw",
                        "wind_onshore_mw", "wind_offshore_mw", "solar_mw",
                        "net_conso_mw", "source", "fetched_at"]

    def test_conflict_cols_mapping(self):
        assert database._CONFLICT_COLS["rte_forecast_log"] == "(target_date, forecast_date)"

    def test_replay_from_v24_is_idempotent(self):
        database.store_rte_forecast_log([_sample_row()])
        for _ in range(2):  # rejouer 2 fois la migration depuis v24
            conn = get_db()
            conn.execute("PRAGMA user_version = 24")
            conn.commit()
            conn.close()
            init_db()
        conn = get_db()
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 26
        finally:
            conn.close()
        assert _count() == 1

    def test_converges_minimal_preexisting_table(self):
        conn = get_db()
        conn.execute("DROP TABLE rte_forecast_log")
        conn.execute("CREATE TABLE rte_forecast_log (target_date TEXT, forecast_date TEXT)")
        conn.execute("PRAGMA user_version = 24")
        conn.commit()
        conn.close()
        init_db()
        assert database.store_rte_forecast_log([_sample_row()]) == 1
        assert database.store_rte_forecast_log([_sample_row(conso_mw=70000.0)]) == 1
        assert _count() == 1  # index unique créé -> upsert
        assert _row("2026-01-13", "2026-01-12")["conso_mw"] == 70000.0


def _sample_row(**kw):
    r = {"target_date": "2026-01-13", "forecast_date": "2026-01-12", "horizon_days": 1,
         "conso_mw": 65000.0, "wind_onshore_mw": 5000.0, "wind_offshore_mw": 800.0,
         "solar_mw": 1200.0, "net_conso_mw": 58000.0, "source": "rte/6h-6h;test",
         "fetched_at": "2026-01-12T18:00:00+01:00"}
    r.update(kw)
    return r


class TestStorage:
    def test_upsert_same_day_updates(self):
        assert database.store_rte_forecast_log([_sample_row()]) == 1
        database.store_rte_forecast_log([_sample_row(conso_mw=66000.0, net_conso_mw=59000.0)])
        r = _row("2026-01-13", "2026-01-12")
        assert _count() == 1 and r["conso_mw"] == 66000.0 and r["net_conso_mw"] == 59000.0

    def test_other_forecast_date_keeps_history(self):
        database.store_rte_forecast_log([_sample_row()])
        database.store_rte_forecast_log([_sample_row(forecast_date="2026-01-11", horizon_days=2)])
        assert _count() == 2

    def test_empty_rows(self):
        assert database.store_rte_forecast_log([]) == 0

    def test_stats(self):
        s = database.get_forecast_log_stats()
        assert s["rte_forecast_log"]["rows"] == 0
        assert s["rte_forecast_log"]["last_forecast_date"] is None
        database.store_rte_forecast_log([_sample_row(),
                                         _sample_row(forecast_date="2026-01-11")])
        s = database.get_forecast_log_stats()
        assert s["rte_forecast_log"]["rows"] == 2
        assert s["rte_forecast_log"]["last_forecast_date"] == "2026-01-12"
        assert "weather_forecast_log" in s


# ---------------------------------------------------------------- parsing RTE

from zoneinfo import ZoneInfo  # noqa: E402

import rte_client  # noqa: E402

_PARIS = ZoneInfo("Europe/Paris")


def _hourly(day: date, fn):
    """Pas horaires de la journée calendaire `day` (heure de Paris)."""
    out, t = [], datetime(day.year, day.month, day.day, tzinfo=_PARIS).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(),
                           tzinfo=_PARIS).astimezone(timezone.utc)
    while t < end:
        loc = t.astimezone(_PARIS)
        out.append({"start_date": loc.isoformat(),
                    "end_date": (t + timedelta(hours=1)).astimezone(_PARIS).isoformat(),
                    "updated_date": "2026-01-12T09:00:00+01:00",
                    "value": fn((day - TODAY).days, loc.hour)})
        t += timedelta(hours=1)
    return out


def _conso(k, h):
    return 50000 + 1000 * k + (10000 if h < 6 else 0)


_GEN_FN = {"WIND_ONSHORE": lambda k, h: 4000 + 100 * k,
           "WIND_OFFSHORE": lambda k, h: 500,
           "SOLAR": lambda k, h: 1000 + 100 * k}


def _d(k):
    return TODAY + timedelta(days=k)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _fake_client(handler):
    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            return handler(url, params or {})
    return _Client


def _rte_handler(url, params, fail_generation=False):
    if url.endswith("/consumption/v1/short_term"):
        k = int(params["type"].split("-")[1])
        vals = _hourly(_d(k), _conso)
        if k == 2:  # point hors du jour de la série : doit être ignoré
            vals += [dict(v, value=999999) for v in _hourly(_d(1), _conso)]
        return _Resp({"short_term": [{"type": params["type"], "values": vals}]})
    if url.endswith("/consumption/v1/weekly_forecasts"):
        vals = [v for k in range(3, 10) for v in _hourly(_d(k), _conso)]
        return _Resp({"weekly_forecasts": [{"updated_date": "2026-01-12T12:00:00+01:00",
                                            "values": vals}]})
    if url.endswith("/generation_forecast/v3/forecasts"):
        if fail_generation:
            raise ConnectionError("réseau indisponible")
        k = int(params["type"].split("-")[1])
        pt = params["production_type"]
        return _Resp({"forecasts": [{"type": params["type"], "production_type": pt,
                                     "values": _hourly(_d(k), _GEN_FN[pt])}]})
    return _Resp({}, 404)


@pytest.fixture
def rte_ok(monkeypatch):
    async def _tok(api="consumption"):
        return "tok"
    monkeypatch.setattr(rte_client, "_get_token", _tok)
    monkeypatch.setattr(rte_client, "_get_credentials", lambda api: ("id", "secret"))

    def install(handler):
        monkeypatch.setattr(rte_client.httpx, "AsyncClient", _fake_client(handler))
    return install


def _by_h(rows):
    return {r["horizon_days"]: r for r in rows}


class TestParsing:
    def test_all_horizons_and_tempo_day_means(self, rte_ok):
        rte_ok(_rte_handler)
        rows = _by_h(asyncio.run(rte_client.fetch_forecasts_for_archive(TODAY)))
        assert sorted(rows) == list(range(1, 10))  # J+1..J+9, rien au-delà
        r1 = rows[1]
        assert r1["forecast_date"] == "2026-01-12" and r1["target_date"] == "2026-01-13"
        # 6h-6h : 18 h du jour J+1 + 6 h de nuit de J+2 (le point 999999 est ignoré)
        assert r1["conso_mw"] == 53750.0
        assert (r1["wind_onshore_mw"], r1["wind_offshore_mw"], r1["solar_mw"]) == (4125.0, 500.0, 1125.0)
        assert r1["net_conso_mw"] == 48000.0
        assert r1["source"].startswith("rte/6h-6h;") and "conso=D-1+D-2" in r1["source"]
        assert rows[2]["net_conso_mw"] == 54750.0 - 4225.0 - 500.0 - 1225.0

    def test_calendar_fallback_is_declared(self, rte_ok):
        rte_ok(_rte_handler)
        rows = _by_h(asyncio.run(rte_client.fetch_forecasts_for_archive(TODAY)))
        # J+3 : éolien/solaire D-3 ne couvrent pas la nuit de J+4 -> calendaire pour tous
        assert rows[3]["source"].startswith("rte/calendaire;")
        assert rows[3]["conso_mw"] == 55500.0 and rows[3]["net_conso_mw"] == 55500.0 - 6100.0
        # J+4..J+8 : consommation hebdo seule, pas de conso nette
        assert rows[4]["conso_mw"] == 56750.0 and rows[4]["net_conso_mw"] is None
        assert rows[4]["wind_onshore_mw"] is None
        assert "conso=hebdo(maj 2026-01-12)" in rows[4]["source"]
        # J+9 : fenêtre hebdo s'arrête à J+10 00h -> calendaire
        assert rows[9]["source"].startswith("rte/calendaire;") and rows[9]["conso_mw"] == 61500.0

    def test_generation_failure_is_silent(self, rte_ok):
        rte_ok(lambda u, p: _rte_handler(u, p, fail_generation=True))
        rows = _by_h(asyncio.run(rte_client.fetch_forecasts_for_archive(TODAY)))
        assert rows[1]["conso_mw"] == 53750.0
        assert rows[1]["wind_onshore_mw"] is None and rows[1]["net_conso_mw"] is None

    def test_total_failure_returns_empty(self, rte_ok):
        def boom(url, params):
            raise TimeoutError("timeout")
        rte_ok(boom)
        assert asyncio.run(rte_client.fetch_forecasts_for_archive(TODAY)) == []

    def test_401_resets_token_cache(self, rte_ok):
        rte_ok(lambda u, p: _Resp({}, 401))
        rte_client._token_conso["token"] = "old"
        assert asyncio.run(rte_client.fetch_forecasts_for_archive(TODAY)) == []
        assert rte_client._token_conso["token"] is None

    def test_no_credentials_no_network(self, monkeypatch):
        monkeypatch.setattr(rte_client, "_get_credentials", lambda api: None)

        def forbid(*a, **kw):
            raise AssertionError("aucun appel réseau attendu")
        monkeypatch.setattr(rte_client.httpx, "AsyncClient", forbid)
        assert asyncio.run(rte_client.fetch_forecasts_for_archive(TODAY)) == []

    def test_incomplete_day_is_null(self):
        pts = {}
        vals = _hourly(_d(1), _conso)[:12]  # 12 h sur 24 : couverture insuffisante
        rte_client._collect_points([{"values": vals}], "D-1", _d(1), None, pts)
        assert rte_client.build_forecast_log_rows({"conso_mw": pts}, TODAY, "x") == []

    def test_dst_day_has_23_hours(self):
        d = date(2026, 3, 29)
        span = rte_client._paris_bound(d + timedelta(days=1), 0) - rte_client._paris_bound(d, 0)
        assert span == timedelta(hours=23)

    def test_existing_functions_untouched(self):
        for name in ("fetch_consumption_forecast", "get_consumption_score",
                     "fetch_realised_consumption", "fetch_nuclear_availability"):
            assert callable(getattr(rte_client, name))


# ---------------------------------------------------------------- scheduler

import scheduler  # noqa: E402


class TestSchedulerArchive:
    def test_archive_stores_rows(self, monkeypatch):
        async def fake():
            return [_sample_row(), _sample_row(target_date="2026-01-14", horizon_days=2)]
        monkeypatch.setattr(rte_client, "fetch_forecasts_for_archive", fake)
        assert asyncio.run(scheduler._archive_rte_forecasts("test")) == 2
        assert _count() == 2

    def test_archive_never_raises(self, monkeypatch):
        async def boom():
            raise RuntimeError("panne")
        monkeypatch.setattr(rte_client, "fetch_forecasts_for_archive", boom)
        assert asyncio.run(scheduler._archive_rte_forecasts("test")) == 0

    @pytest.mark.parametrize("count", [16, 0])
    def test_18h_task_archives_after_predictions(self, monkeypatch, count):
        calls = []

        async def refresh(trigger, send_sms=False):
            calls.append("predictions")
            return count

        async def archive(trigger):
            calls.append("archive")
            return 0
        monkeypatch.setattr(scheduler, "_refresh_predictions", refresh)
        monkeypatch.setattr(scheduler, "_archive_rte_forecasts", archive)
        monkeypatch.setattr(scheduler, "_schedule_deferred_retries", lambda: None)
        asyncio.run(scheduler.task_daily_predictions())
        assert calls == ["predictions", "archive"]

    def test_refresh_predictions_does_not_archive(self):
        # Le polling / 11h30 / 7h30 n'ajoutent aucun appel RTE supplémentaire
        import inspect
        assert "_archive_rte_forecasts" not in inspect.getsource(scheduler._refresh_predictions)


class TestWeatherForecastLog:
    def _forecasts(self):
        today = date.today()
        return [{"date": (today + timedelta(days=i)).isoformat(), "temp_min": 1.0,
                 "temp_max": 6.0, "temp_moy": 3.5, "pressure": 1015.0, "humidity": 80,
                 "wind_speed": 12, "source": "arome"} for i in range(0, 17)]

    def test_daily_cycle_feeds_log(self):
        scheduler._store_weather_cache(self._forecasts())
        assert _count("weather_forecast_log") == 16  # J+0..J+15 (J+16 exclu)
        assert _count("weather_cache") >= 17
        r = _row((date.today() + timedelta(days=3)).isoformat(), date.today().isoformat(),
                 "weather_forecast_log")
        assert r["horizon_days"] == 3 and r["source"] == "arome" and r["temp_moy"] == 3.5

    def test_log_survives_weather_cache_failure(self, caplog):
        conn = get_db()
        conn.execute("DROP TABLE weather_cache")
        conn.commit()
        conn.close()
        with caplog.at_level("WARNING"):
            scheduler._store_weather_cache(self._forecasts())
        assert _count("weather_forecast_log") == 16
        assert any("weather_cache" in m for m in caplog.messages)

    def test_log_failure_is_a_warning(self, caplog):
        conn = get_db()
        conn.execute("DROP TABLE weather_forecast_log")
        conn.commit()
        conn.close()
        with caplog.at_level("WARNING"):
            scheduler._store_weather_cache(self._forecasts())
        assert any("weather_forecast_log" in m for m in caplog.messages)


# ---------------------------------------------------------------- db_sync / admin

class TestSyncAndAdmin:
    def test_export_includes_both_logs(self):
        import db_sync
        assert "rte_forecast_log" in db_sync.SYNC_TABLES
        assert "source" in db_sync.TABLE_COLUMNS["weather_forecast_log"]
        database.store_rte_forecast_log([_sample_row()])
        scheduler._store_weather_cache([{"date": date.today().isoformat(), "temp_moy": 2.0,
                                         "source": "arome"}])
        dump = db_sync.export_db()["tables"]
        assert dump["rte_forecast_log"][0]["net_conso_mw"] == 58000.0
        assert dump["weather_forecast_log"][0]["source"] == "arome"

    def test_import_roundtrip(self, tmp_path):
        import db_sync
        database.store_rte_forecast_log([_sample_row()])
        path = tmp_path / "dump.json"
        db_sync.export_to_file(path)
        conn = get_db()
        conn.execute("DELETE FROM rte_forecast_log")
        conn.commit()
        conn.close()
        db_sync.import_from_file(path)
        assert _row("2026-01-13", "2026-01-12")["conso_mw"] == 65000.0

    def test_admin_diag_wired(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        app_src = open(os.path.join(root, "app.py"), encoding="utf-8").read()
        html = open(os.path.join(root, "templates", "admin.html"), encoding="utf-8").read()
        assert "get_forecast_log_stats" in app_src and '"forecast_logs"' in app_src
        assert "renderForecastLogs(data.forecast_logs)" in html
        assert "rte_forecast_log" in html and "weather_forecast_log" in html


# ---------------------------------------------------------------- PostgreSQL réel

_PG_URL = os.environ.get("TEMPO_TEST_PG_URL")


@pytest.mark.skipif(not _PG_URL, reason="TEMPO_TEST_PG_URL non défini")
class TestPostgres:
    @pytest.fixture
    def pg_db_url(self):
        import psycopg2
        name = f"tempo_test_{uuid.uuid4().hex[:10]}"
        admin = psycopg2.connect(_PG_URL)
        admin.autocommit = True
        admin.cursor().execute(f"CREATE DATABASE {name}")
        base, _, _ = _PG_URL.rpartition("/")
        yield f"{base}/{name}"
        admin.cursor().execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()

    def _run(self, db_url, tmp_path, code):
        env = dict(os.environ, DATABASE_URL=db_url)
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = "import sys; sys.path.insert(0, %r)\n" % repo + textwrap.dedent(code)
        res = subprocess.run([sys.executable, "-c", script], env=env, cwd=str(tmp_path),
                             capture_output=True, text=True, timeout=180)
        assert res.returncode == 0, res.stdout + res.stderr
        return res.stdout

    def test_migration_v24_to_v25_and_storage(self, pg_db_url, tmp_path):
        out = self._run(pg_db_url, tmp_path, f"""
            import database
            assert database._USE_POSTGRES
            database.init_db()
            c = database.get_db()
            # Revenir à un état v24 réel (sans la table), puis migrer
            c.execute("DROP TABLE rte_forecast_log")
            c.execute("PRAGMA user_version = 24")
            c.commit(); c.close()
            database.init_db()
            row = {_sample_row()!r}
            assert database.store_rte_forecast_log([row]) == 1
            row["conso_mw"] = 66000.0
            assert database.store_rte_forecast_log([row]) == 1
            # Rejeu de la migration 2x : idempotente, données conservées
            for _ in range(2):
                c = database.get_db(); c.execute("PRAGMA user_version = 24"); c.commit(); c.close()
                database.init_db()
            c = database.get_db()
            assert c.execute("PRAGMA user_version").fetchone()[0] == 26
            r = c.execute("SELECT COUNT(*) AS n, MAX(conso_mw) AS m FROM rte_forecast_log").fetchone()
            assert (r["n"], r["m"]) == (1, 66000.0), dict(r)
            c.close()
            s = database.get_forecast_log_stats()
            assert s["rte_forecast_log"]["rows"] == 1, s
            assert s["rte_forecast_log"]["last_forecast_date"] == "2026-01-12", s
            print("PG_V25_OK")
        """)
        assert "PG_V25_OK" in out

    def test_weather_log_written_on_pg(self, pg_db_url, tmp_path):
        out = self._run(pg_db_url, tmp_path, """
            from datetime import date, timedelta
            import database, scheduler
            database.init_db()
            today = date.today()
            fc = [{"date": (today + timedelta(days=i)).isoformat(), "temp_moy": 3.0,
                   "source": "arome"} for i in range(17)]
            scheduler._store_weather_cache(fc)
            scheduler._store_weather_cache(fc)  # 2e cycle du jour : upsert
            c = database.get_db()
            assert c.execute("SELECT COUNT(*) FROM weather_forecast_log").fetchone()[0] == 16
            c.close()
            print("PG_WFL_OK")
        """)
        assert "PG_WFL_OK" in out
