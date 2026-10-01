"""Archive des prévisions Météo France (v26, weather_forecast_mf_log), 2026-10-01.

meteole est SIMULÉ (FakeModel) : les températures ci-dessous sont des FIXTURES
de TEST (constantes par ville) qui ne servent qu'à vérifier l'agrégation ; elles
ne sont ni stockées hors de la base temporaire du test, ni publiées.
"""

import asyncio
import inspect
import json
import logging
import os
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

import database
import scheduler
import weather_client as wc
from config import Config
from database import get_db, init_db

TODAY = date(2026, 1, 12)                      # lundi d'hiver (UTC+1)
RUN = datetime(2026, 1, 12, 12, tzinfo=timezone.utc)
RUN_STR = "2026-01-12T12.00.00Z"
VALUE_COL = {wc._INDICATORS["temperature"]: "t2m", wc._INDICATORS["humidity"]: "r2",
             wc._INDICATORS["wind_gust"]: "efg10", wc._INDICATORS["pressure"]: "sp"}


def _city_temp_c(i: int, offset: float) -> float:
    return 2.0 + i + offset                    # FIXTURE de test, constante sur la journée


def _run_dt(run_str: str) -> datetime:
    return datetime.strptime(run_str, "%Y-%m-%dT%H.%M.%SZ").replace(tzinfo=timezone.utc)


class FakeModel:
    """Imite l'API meteole utilisée par l'archive (AromeForecast/ArpegeForecast).

    runs : {run meteole: heures d'échéance disponibles}.
    """

    def __init__(self, runs, offset=0.0, fail_auth=False, missing=()):
        self.runs = {r: [timedelta(hours=h) for h in hs] for r, hs in runs.items()}
        self.offset, self.fail_auth, self.missing = offset, fail_auth, set(missing)
        self.coverage_calls = []
        self._client = types.SimpleNamespace(request_count=0)

    @property
    def capabilities(self):
        return pd.DataFrame([{"indicator": ind, "run": r}
                             for ind in VALUE_COL for r in self.runs])

    def _get_coverage_id(self, indicator, run=None):
        self._client.request_count += 1
        if self.fail_auth:
            raise wc.MFAuthError("HTTP 401 : Invalid Credentials")
        if indicator in self.missing:
            raise ValueError("Unknown `indicator`")
        run = run or max(self.runs)
        if run not in self.runs:
            raise ValueError(f"Run '{run}' is invalid")
        return f"{indicator}___{run}"

    def get_coverage_description(self, coverage_id):
        self._client.request_count += 1
        return {"forecast_horizons": list(self.runs[coverage_id.split("___")[1]])}

    def get_coverage(self, coverage_id=None, lat=None, long=None, heights=None,
                     forecast_horizons=None):
        # meteole sans échéances = échéance 0 seulement : l'archive doit TOUJOURS les passer
        assert forecast_horizons, "forecast_horizons doit être explicite"
        indicator, run = coverage_id.split("___")
        available = self.runs[run]
        for fh in forecast_horizons:
            if fh not in available:
                raise ValueError(f"`forecast_horizons={forecast_horizons}` is invalid. "
                                 f"Available forecast_horizons: {available}")
        self.coverage_calls.append((coverage_id, list(forecast_horizons), heights))
        self._client.request_count += 1 + len(forecast_horizons)
        rows = []
        for fh in forecast_horizons:
            for i, c in enumerate(Config.WEATHER_CITIES):
                if indicator == wc._INDICATORS["temperature"]:
                    v = 273.15 + _city_temp_c(i, self.offset)
                elif indicator == wc._INDICATORS["humidity"]:
                    v = 80.0
                elif indicator == wc._INDICATORS["wind_gust"]:
                    v = 5.0                    # m/s -> 18 km/h
                else:
                    v = 101300.0               # Pa -> 1013 hPa
                rows.append({"latitude": round(c["lat"], 1), "longitude": round(c["lon"], 1),
                             "run": pd.Timestamp(_run_dt(run).replace(tzinfo=None)),
                             "forecast_horizon": pd.Timedelta(fh), VALUE_COL[indicator]: v})
        return pd.DataFrame(rows)


AROME_HOURS = range(0, 52)                     # 0..51 h
ARPEGE_HOURS = [h for h in range(0, 103) if h != 63]   # 63 h manquante -> 15/01 incomplet


@pytest.fixture
def fake_mf(monkeypatch):
    """Clés : AROME refusée (401), ARPEGE valide. Retourne les modèles créés."""
    monkeypatch.setattr(Config, "METEOFRANCE_AROME_KEY", "cle-arome")
    monkeypatch.setattr(Config, "METEOFRANCE_ARPEGE_KEY", "cle-arpege")
    monkeypatch.setattr(Config, "METEOFRANCE_API_KEY", "")
    monkeypatch.setattr(wc, "_mf_fallback_logged", set())
    created = []

    def build(model_name, auth, deadline):
        if model_name == "arome":
            m = FakeModel({RUN_STR: AROME_HOURS}, 0.0, fail_auth=auth["api_key"] == "cle-arome")
        else:
            m = FakeModel({RUN_STR: ARPEGE_HOURS}, 1.0)
        created.append((model_name, auth["api_key"], m))
        return m
    monkeypatch.setattr(wc, "_mf_archive_build_model", build)
    return created


def _weighted(offset: float) -> float:
    cities = Config.WEATHER_CITIES
    tot = sum(c["weight"] for c in cities)
    return round(sum(round(_city_temp_c(i, offset), 1) * c["weight"]
                     for i, c in enumerate(cities)) / tot, 1)


def _rows_by(report, model):
    return {r["target_date"]: r for r in report["rows"] if r["model"] == model}


# ---------------------------------------------------------------- récupération


class TestFetchArchive:
    def test_horizons_passed_and_chosen_from_available(self, fake_mf):
        wc.fetch_meteofrance_archive(today=TODAY)
        for name, _key, m in fake_mf:
            for cid, horizons, _h in m.coverage_calls:
                assert horizons and all(h in m.runs[cid.split("___")[1]] for h in horizons)
                # pas de 3 h, instants UTC alignés (00, 03, ... 21 Z)
                assert all((RUN + h).hour % 3 == 0 for h in horizons)

    def test_complete_days_only(self, fake_mf):
        rep = wc.fetch_meteofrance_archive(today=TODAY)
        # AROME (51 h depuis 12 Z) : seul le 13/01 est complet (jour de Paris)
        assert sorted(_rows_by(rep, "arome")) == ["2026-01-13"]
        # ARPEGE : 13 et 14 complets, 15 incomplet (63 h absente), 16 hors portée
        assert sorted(_rows_by(rep, "arpege")) == ["2026-01-13", "2026-01-14"]
        assert sorted(_rows_by(rep, "merged")) == ["2026-01-13", "2026-01-14"]

    def test_arome_key_fallback_logged_once(self, fake_mf, caplog):
        caplog.set_level(logging.WARNING, logger="weather_client")
        rep = wc.fetch_meteofrance_archive(today=TODAY)
        wc.fetch_meteofrance_archive(today=TODAY)
        assert rep["models"]["arome"]["ok"] and rep["models"]["arome"]["key"] == "arpege"
        assert [k for n, k, _ in fake_mf if n == "arome"][:2] == ["cle-arome", "cle-arpege"]
        assert sum("nouvel essai avec la clé ARPEGE" in r.message for r in caplog.records) == 1

    def test_aggregation_nine_cities_weighted(self, fake_mf):
        rep = wc.fetch_meteofrance_archive(today=TODAY)
        aro, arp, mer = (_rows_by(rep, m) for m in ("arome", "arpege", "merged"))
        assert aro["2026-01-13"]["temp_moy"] == _weighted(0.0)
        assert arp["2026-01-13"]["temp_moy"] == _weighted(1.0)
        assert mer["2026-01-13"]["temp_moy"] == _weighted(0.0)      # AROME prioritaire
        assert mer["2026-01-14"]["temp_moy"] == _weighted(1.0)      # ARPEGE au-delà
        r = mer["2026-01-13"]
        assert r["n_villes"] == len(Config.WEATHER_CITIES) == 9
        assert (r["humidity"], r["wind_speed"], r["pressure"]) == (80.0, 18.0, 1013.0)
        assert r["horizon_days"] == 1 and r["forecast_date"] == TODAY.isoformat()
        assert "arome=2026-01-12T12:00:00+00:00" in r["run"]

    def test_missing_indicator_gives_null_not_default(self, monkeypatch, fake_mf):
        orig = wc._mf_archive_build_model

        def build(model_name, auth, deadline):
            m = orig(model_name, auth, deadline)
            m.missing = {wc._INDICATORS["humidity"]}
            return m
        monkeypatch.setattr(wc, "_mf_archive_build_model", build)
        rep = wc.fetch_meteofrance_archive(today=TODAY)
        assert rep["rows"] and all(r["humidity"] is None for r in rep["rows"])
        assert all(r["temp_moy"] is not None for r in rep["rows"])

    def test_no_key_never_raises(self, monkeypatch):
        for k in ("METEOFRANCE_AROME_KEY", "METEOFRANCE_ARPEGE_KEY", "METEOFRANCE_API_KEY",
                  "METEOFRANCE_APPLICATION_ID"):
            monkeypatch.setattr(Config, k, "")
        rep = wc.fetch_meteofrance_archive(today=TODAY)
        assert rep["rows"] == [] and not rep["models"]["arome"]["ok"]

    def test_scoring_path_untouched(self):
        for fn in (wc.fetch_forecast, wc._fetch_meteofrance, scheduler._refresh_predictions):
            assert "meteofrance_archive" not in inspect.getsource(fn)

    def test_run_choice_farthest_complete_day_then_most_recent(self):
        from zoneinfo import ZoneInfo
        days = [TODAY + timedelta(days=i) for i in range(5)]
        m = FakeModel({"2026-01-12T12.00.00Z": range(0, 115),      # 114 h : J+4 complet
                       "2026-01-12T06.00.00Z": range(0, 103),      # 102 h : J+4 incomplet
                       "2026-01-11T18.00.00Z": range(0, 103)})
        cid, run_dt, _avail, complete = wc._mf_pick_run(
            m, wc._INDICATORS["temperature"], days, ZoneInfo("Europe/Paris"))
        assert cid.endswith("2026-01-12T12.00.00Z") and max(complete) == date(2026, 1, 16)
        # à jour complet égal : le run le plus récent
        m2 = FakeModel({"2026-01-12T12.00.00Z": range(0, 52), "2026-01-12T15.00.00Z": range(0, 52),
                        "2026-01-12T18.00.00Z": range(0, 20)})   # dernier run publié en partie
        cid2, *_ = wc._mf_pick_run(m2, wc._INDICATORS["temperature"], days,
                                   ZoneInfo("Europe/Paris"))
        assert cid2.endswith("2026-01-12T15.00.00Z")


# ---------------------------------------------------------------- client HTTP


class _Resp:
    def __init__(self, code, text="", headers=None):
        self.status_code, self.text, self.headers = code, text, headers or {}
        self.content = text.encode()


class TestHttpClient:
    def _client(self, monkeypatch, responses):
        c = wc._MFArchiveClient("k", limiter=wc._RateLimiter(10 ** 6))
        calls = []

        def get(url, params=None, timeout=None):
            calls.append((url, timeout))
            return responses.pop(0)
        monkeypatch.setattr(c._session, "get", get)
        monkeypatch.setattr(wc.time, "sleep", lambda s: calls.append(("sleep", s)))
        return c, calls

    def test_401_raises_immediately(self, monkeypatch):
        c, calls = self._client(monkeypatch, [_Resp(401, '{"code":"900901"}')])
        with pytest.raises(wc.MFAuthError):
            c.get("arome/1.0/x/GetCapabilities")
        assert len(calls) == 1 and calls[0][1] == wc._MF_REQUEST_TIMEOUT_S
        assert c._session.headers["apikey"] == "k"

    def test_429_respects_retry_after(self, monkeypatch):
        c, calls = self._client(monkeypatch, [_Resp(429, headers={"Retry-After": "7"}),
                                              _Resp(200, "ok")])
        assert c.get("p").text == "ok"
        assert ("sleep", 7.0) in calls and c.request_count == 2

    def test_rate_limiter_spaces_requests(self):
        clock, slept = [100.0], []

        def sleep(s):
            slept.append(s)
            clock[0] += s
        lim = wc._RateLimiter(45, clock=lambda: clock[0], sleep=sleep)
        for _ in range(46):
            lim.wait()
        assert clock[0] - 100.0 == pytest.approx(60.0, rel=1e-6)   # 46 requêtes >= 60 s
        assert wc._MF_MAX_REQ_PER_MIN < 50


# ---------------------------------------------------------------- base


def _mf_row(**kw):
    r = {"target_date": "2026-01-14", "forecast_date": "2026-01-12", "model": "merged",
         "horizon_days": 2, "temp_min": 1.0, "temp_max": 6.0, "temp_moy": 3.5,
         "humidity": 80.0, "wind_speed": 18.0, "pressure": 1013.0, "n_villes": 9,
         "run": "arome=x;arpege=y", "fetched_at": "2026-01-12T18:05:00+01:00"}
    r.update(kw)
    return r


def _count(table="weather_forecast_mf_log"):
    conn = get_db()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


class TestMigrationV26:
    def test_version_and_columns(self):
        conn = get_db()
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 27
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(weather_forecast_mf_log)").fetchall()]
        finally:
            conn.close()
        assert cols == list(database._WEATHER_MF_LOG_COLS)
        assert database._CONFLICT_COLS["weather_forecast_mf_log"] == \
            "(target_date, forecast_date, model)"

    def test_replay_from_v25_is_idempotent(self):
        database.store_weather_mf_log([_mf_row()])
        for _ in range(2):
            conn = get_db()
            conn.execute("PRAGMA user_version = 25")
            conn.commit()
            conn.close()
            init_db()
        assert _count() == 1

    def test_converges_minimal_preexisting_table(self):
        conn = get_db()
        conn.execute("DROP TABLE weather_forecast_mf_log")
        conn.execute("CREATE TABLE weather_forecast_mf_log (target_date TEXT NOT NULL, "
                     "forecast_date TEXT NOT NULL, model TEXT NOT NULL)")
        conn.execute("PRAGMA user_version = 25")
        conn.commit()
        conn.close()
        init_db()
        assert database.store_weather_mf_log([_mf_row()]) == 1
        assert database.store_weather_mf_log([_mf_row(temp_moy=4.0)]) == 1
        assert _count() == 1

    def test_upsert_updates_and_keys_by_model(self):
        database.store_weather_mf_log([_mf_row(), _mf_row(model="arpege")])
        database.store_weather_mf_log([_mf_row(temp_moy=5.5, humidity=None)])
        conn = get_db()
        try:
            r = conn.execute("SELECT temp_moy, humidity FROM weather_forecast_mf_log "
                             "WHERE model = 'merged'").fetchone()
        finally:
            conn.close()
        assert (r[0], r[1]) == (5.5, None) and _count() == 2

    def test_admin_stats_and_template(self):
        database.store_weather_mf_log([_mf_row(), _mf_row(forecast_date="2026-01-13",
                                                          target_date="2026-01-15")])
        st = database.get_forecast_log_stats()["weather_forecast_mf_log"]
        assert st["rows"] == 2 and st["last_forecast_date"] == "2026-01-13"
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html = open(os.path.join(root, "templates", "admin.html"), encoding="utf-8").read()
        assert "weather_forecast_mf_log:" in html and "runTask('meteofrance_archive')" in html

    def test_db_sync_includes_table(self):
        import db_sync
        assert "weather_forecast_mf_log" in db_sync.SYNC_TABLES
        assert db_sync.TABLE_COLUMNS["weather_forecast_mf_log"] == \
            list(database._WEATHER_MF_LOG_COLS)


# ---------------------------------------------------------------- scheduler


class TestScheduler:
    def test_task_stores_rows_and_logs(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger="scheduler")
        report = {"forecast_date": "2026-01-12", "requests": 120, "rows": [
            _mf_row(), _mf_row(model="arpege")],
            "models": {"arome": {"ok": True, "key": "arpege", "days": 1, "cities": 9},
                       "arpege": {"ok": True, "key": "arpege", "days": 3, "cities": 9}}}
        monkeypatch.setattr(wc, "fetch_meteofrance_archive", lambda: report)
        msg = asyncio.run(scheduler.task_meteofrance_archive())
        assert _count() == 2
        assert msg.startswith("[Météo France archive] 2 lignes")
        assert "AROME OK 1 j / 9 villes (clé ARPEGE)" in msg
        assert any("[Météo France archive]" in r.message for r in caplog.records)

    def test_task_never_raises(self, monkeypatch):
        def boom():
            raise RuntimeError("panne réseau")
        monkeypatch.setattr(wc, "fetch_meteofrance_archive", boom)
        assert "échec" in asyncio.run(scheduler.task_meteofrance_archive())
        assert _count() == 0

    @pytest.mark.parametrize("count", [16, 0])
    def test_18h_finally_schedules_archive(self, monkeypatch, count):
        calls = []

        async def refresh(trigger, send_sms=False):
            calls.append("predictions")
            return count

        async def rte(trigger):
            calls.append("rte")
            return 0
        monkeypatch.setattr(scheduler, "_refresh_predictions", refresh)
        monkeypatch.setattr(scheduler, "_archive_rte_forecasts", rte)
        monkeypatch.setattr(scheduler, "_schedule_deferred_retries", lambda: None)
        monkeypatch.setattr(scheduler, "_schedule_meteofrance_archive",
                            lambda trigger: calls.append(f"mf:{trigger}") or True)
        asyncio.run(scheduler.task_daily_predictions())
        assert calls == ["predictions", "rte", "mf:18h"]

    def test_schedule_is_separate_job_and_safe(self, monkeypatch):
        added = []
        fake = types.SimpleNamespace(running=True,
                                     add_job=lambda fn, trig, **kw: added.append((fn, trig, kw)))
        monkeypatch.setattr(scheduler, "scheduler", fake)
        assert scheduler._schedule_meteofrance_archive("18h") is True
        fn, trig, kw = added[0]
        assert kw["id"] == "meteofrance_archive" and kw["replace_existing"]
        assert type(trig).__name__ == "DateTrigger"

        def broken(*a, **k):
            raise RuntimeError("scheduler HS")
        monkeypatch.setattr(fake, "add_job", broken)
        assert scheduler._schedule_meteofrance_archive("18h") is False   # ne lève pas
        monkeypatch.setattr(fake, "running", False)
        assert scheduler._schedule_meteofrance_archive("18h") is False

    def test_run_task_now(self, monkeypatch):
        async def fake_task():
            return "bilan"
        monkeypatch.setattr(scheduler, "task_meteofrance_archive", fake_task)
        monkeypatch.setattr(scheduler, "_schedule_meteofrance_archive", lambda t: False)
        assert asyncio.run(scheduler.run_task_now("meteofrance_archive")) == "bilan"
        monkeypatch.setattr(scheduler, "_schedule_meteofrance_archive", lambda t: True)
        assert "arrière-plan" in asyncio.run(scheduler.run_task_now("meteofrance_archive"))


# ---------------------------------------------------------------- outil de comparaison


def _load_tool():
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "compare_weather_sources", os.path.join(root, "tools", "replay", "compare_weather_sources.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_comparison():
    """Données FACTICES de TEST (jamais publiées) : 2 dates communes à J+2."""
    conn = get_db()
    try:
        for d, obs, src in (("2099-01-10", 2.0, "open-meteo"), ("2099-01-11", 4.0, "open-meteo"),
                            ("2099-01-12", 6.0, "arome")):
            conn.execute("INSERT INTO weather_cache (date, temp_moy, description, fetched_at) "
                         "VALUES (?, ?, ?, ?)", (d, obs, src, "2099-01-01T18:00:00"))
        for d, fc, src in (("2099-01-10", 3.0, "open-meteo"), ("2099-01-11", 3.0, "open-meteo"),
                           ("2099-01-12", 9.0, "arome"), ("2099-01-13", 1.0, "open-meteo")):
            conn.execute("INSERT INTO weather_forecast_log (target_date, forecast_date, "
                         "horizon_days, temp_moy, source, fetched_at) VALUES (?, ?, 2, ?, ?, ?)",
                         (d, (date.fromisoformat(d) - timedelta(days=2)).isoformat(), fc, src,
                          "2099-01-01T18:00:00"))
        conn.commit()
    finally:
        conn.close()
    database.store_weather_mf_log([
        _mf_row(target_date="2099-01-10", forecast_date="2099-01-08", horizon_days=2, temp_moy=2.5),
        _mf_row(target_date="2099-01-11", forecast_date="2099-01-09", horizon_days=2, temp_moy=3.5),
        _mf_row(target_date="2099-01-12", forecast_date="2099-01-10", horizon_days=2, temp_moy=6.0),
        _mf_row(target_date="2099-01-11", forecast_date="2099-01-09", horizon_days=2,
                temp_moy=-9.0, model="arpege"),          # autre modèle : ignoré par défaut
    ])


class TestCompareTool:
    def test_mae_bias_on_common_dates(self):
        tool = _load_tool()
        _seed_comparison()
        res = tool.compare(get_db())
        h2 = res["horizons"]["J+2"]
        # dates communes : 10 et 11 (12 exclu : source 'arome' côté Open-Meteo ; 13 : pas de MF)
        assert h2["n"] == 2 and (h2["first_date"], h2["last_date"]) == ("2099-01-10", "2099-01-11")
        assert h2["open_meteo"] == {"mae": 1.0, "bias": 0.0}       # +1 et -1
        assert h2["meteofrance"] == {"mae": 0.5, "bias": 0.0}      # +0,5 et -0,5
        assert res["horizons"]["J+0"]["n"] == 0
        assert any("Météo France" in w for w in res["warnings"])   # référence 12/01 = 'arome'
        txt = tool.format_text(res)
        assert "J+2" in txt and "EAM = erreur absolue moyenne" in txt

    def test_all_sources_and_ref_filter(self):
        tool = _load_tool()
        _seed_comparison()
        res = tool.compare(get_db(), om_source=None)
        assert res["horizons"]["J+2"]["n"] == 3
        res = tool.compare(get_db(), om_source=None, ref_source="open-meteo")
        assert res["horizons"]["J+2"]["n"] == 2

    def test_cli_json(self, capsys):
        tool = _load_tool()
        _seed_comparison()
        assert tool.main(["--json"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["horizons"]["J+2"]["meteofrance"]["mae"] == 0.5
        assert tool.main(["--since", "2099-01-11"]) == 0
        assert "J+4" in capsys.readouterr().out
