"""Page publique /historique-previsions et export CSV (prediction_history.py).

Base SQLite temporaire (conftest.py). Les lignes insérées ici sont des cas de test
unitaires contrôlés, pas des données d'analyse.
"""

import csv
import io
import json
import re
from datetime import date, timedelta

import pytest

PAGE = "/historique-previsions"


@pytest.fixture(autouse=True)
def fresh_cache():
    from prediction_history import invalidate_history_cache
    invalidate_history_cache()
    yield
    invalidate_history_cache()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import app as app_module
    app_module._db_ready.set()
    return TestClient(app_module.app)


def _pred(target: str, emitted: str, couleur: str, *, horizon: str = "", simulated: int = 0,
          cycle_id: str = "", confirmed: int = 0, originale: str = "",
          temp: float | None = None, proba_rouge: float = 0.0):
    from database import get_db
    conn = get_db()
    n = (date.fromisoformat(target) - date.fromisoformat(emitted)).days
    conn.execute(
        "INSERT INTO predictions (date, couleur_predite, horizon, timestamp_prediction, "
        "simulated, cycle_id, confirmed, couleur_originale, temp_moy_prevue, probabilite_rouge) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (target, couleur, horizon or f"J-{n}", emitted + "T18:00:00", simulated, cycle_id,
         confirmed, originale, temp, proba_rouge),
    )
    conn.commit()
    conn.close()


def _actual(d: str, couleur: str, synthetic: int = 0):
    from database import get_db
    conn = get_db()
    conn.execute(
        "INSERT INTO actuals (date, couleur_reelle, synthetic, timestamp_confirmation) "
        "VALUES (?, ?, ?, ?)", (d, couleur, synthetic, d + "T11:00:00"),
    )
    conn.commit()
    conn.close()


def _before(d: str, n: int) -> str:
    return (date.fromisoformat(d) - timedelta(days=n)).isoformat()


def _season_view(label="2025-2026"):
    from prediction_history import get_history
    return get_history(force=True)["seasons"].get(label)


def _hz(season, n):
    return next(h for h in season["horizons"] if h["n"] == n)


def _jsonld(page: str) -> list:
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    return [json.loads(b) for b in blocks]


# ------------------------------------------------------------ Robustesse

class TestEmptyAndNotReady:
    def test_empty_db_200(self, client):
        r = client.get(PAGE)
        assert r.status_code == 200
        assert "Pas encore de données" in r.text
        assert r.headers["cache-control"].startswith("public, max-age=600")

    def test_db_not_ready_200(self, client):
        import app as app_module
        app_module._db_ready.clear()
        try:
            r = client.get(PAGE)
            assert r.status_code == 200
            assert "Pas encore de données" in r.text
            assert client.get(PAGE + ".csv").status_code == 200
        finally:
            app_module._db_ready.set()

    def test_broken_db_never_raises(self, monkeypatch):
        import prediction_history as ph

        def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr("database.get_db", boom)
        assert ph.get_history(force=True)["seasons"] == {}

    def test_invalid_and_current_season_urls(self, client):
        from prediction_history import season_start_year, today_paris, season_label
        cur = season_label(season_start_year(today_paris()))
        assert client.get(PAGE + "/abc").status_code == 404
        assert client.get(PAGE + "/2025-2027").status_code == 404
        nxt = season_label(season_start_year(today_paris()) + 1)
        assert client.get(f"{PAGE}/{nxt}").status_code == 404
        r = client.get(f"{PAGE}/{cur}", follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == PAGE


# ------------------------------------------------------------ Cas contrôlé

def _controlled_case(monkeypatch):
    """4 jours officiels (saison 2025-2026) + lignes à exclure."""
    monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2026-01-01")
    for d, c in [("2026-02-02", "ROUGE"), ("2026-02-03", "ROUGE"),
                 ("2026-02-04", "BLANC"), ("2026-02-05", "BLEU")]:
        _actual(d, c)
    _actual("2026-02-06", "ROUGE", synthetic=1)  # couleur synthétique : exclue
    _pred("2026-02-06", _before("2026-02-06", 2), "ROUGE")
    for d, preds in {
        "2026-02-02": {2: "ROUGE", 3: "BLANC", 5: "ROUGE"},
        "2026-02-03": {2: "ROUGE", 3: "ROUGE", 4: "BLEU"},
        "2026-02-04": {2: "ROUGE", 3: "BLANC"},
        "2026-02-05": {2: "BLEU", 3: "BLANC"},
    }.items():
        for n, c in preds.items():
            _pred(d, _before(d, n), c)
    # Exclusions : simulée, backtest, confirmée avec couleur émise perdue + repli backtest
    _pred("2026-02-05", _before("2026-02-05", 4), "ROUGE", horizon="SIM", simulated=1)
    _pred("2026-02-04", _before("2026-02-04", 4), "ROUGE", horizon="BT", cycle_id="backtest")
    _pred("2026-02-04", _before("2026-02-04", 5), "BLANC", confirmed=1, originale="")
    # Confirmée : la couleur émise est couleur_originale, pas la couleur officielle
    _pred("2026-02-05", _before("2026-02-05", 5), "BLEU", confirmed=1, originale="BLANC")
    # Confirmée sans couleur_originale, repli sur une évaluation réelle
    _pred("2026-02-03", _before("2026-02-03", 5), "ROUGE", confirmed=1, originale="")
    from database import get_db
    conn = get_db()
    for cible, couleur, ctx in [("2026-02-03", "ROUGE", "Froid (2C moy.)"),
                                ("2026-02-04", "ROUGE", "backtest temp_moy=3.0")]:
        conn.execute(
            "INSERT INTO performance (date_prediction, date_cible, jours_avance, correct, "
            "couleur_predite, couleur_reelle, contexte_meteo, timestamp_evaluation) "
            "VALUES (?, ?, 5, 0, ?, 'ROUGE', ?, ?)",
            (_before(cible, 5), cible, couleur, ctx, cible + "T11:00:00"))
    conn.commit()
    conn.close()


class TestControlledCounts:
    def test_counts_by_horizon(self, monkeypatch):
        _controlled_case(monkeypatch)
        s = _season_view()
        assert [d["date"] for d in s["days"]] == ["2026-02-02", "2026-02-03", "2026-02-04", "2026-02-05"]
        j2 = _hz(s, 2)
        assert j2["ROUGE"] == {"reels": 2, "annonces": 2, "sans_prevision": 0, "alertes": 3,
                               "alertes_justes": 2, "rappel_pct": None, "precision_pct": None}
        assert (j2["BLANC"]["reels"], j2["BLANC"]["annonces"], j2["BLANC"]["alertes"]) == (1, 0, 0)
        assert (j2["jours"], j2["justes"], j2["toujours_bleu"]) == (4, 3, 1)
        j3 = _hz(s, 3)
        assert (j3["ROUGE"]["annonces"], j3["ROUGE"]["alertes"], j3["ROUGE"]["alertes_justes"]) == (1, 1, 1)
        assert (j3["BLANC"]["annonces"], j3["BLANC"]["alertes"], j3["BLANC"]["alertes_justes"]) == (1, 3, 1)

    def test_simulated_backtest_synthetic_excluded(self, monkeypatch):
        _controlled_case(monkeypatch)
        s = _season_view()
        j4 = _hz(s, 4)
        assert j4["ROUGE"]["alertes"] == 0  # simulée + backtest ignorées
        assert j4["sans_prevision"] == 3  # une absence compte comme manquée
        assert j4["ROUGE"]["sans_prevision"] == 1
        assert "2026-02-06" not in [d["date"] for d in s["days"]]

    def test_confirmed_rows_use_emitted_color(self, monkeypatch):
        _controlled_case(monkeypatch)
        j5 = _hz(_season_view(), 5)
        assert (j5["ROUGE"]["reels"], j5["ROUGE"]["annonces"], j5["ROUGE"]["alertes_justes"]) == (2, 2, 2)
        # 02-05 : émise BLANC (fausse), 02-04 : repli backtest refusé -> absente
        assert (j5["BLANC"]["alertes"], j5["BLANC"]["alertes_justes"]) == (1, 0)
        assert j5["BLANC"]["sans_prevision"] == 1

    def test_performance_fallback_without_predictions(self, monkeypatch):
        """Jours déjà purgés avant le 2026-09-29 (prédictions live > 90 jours) : les
        évaluations figées (performance) donnent les couleurs, sans les backtests."""
        _controlled_case(monkeypatch)
        from database import get_db
        from performance_tracker import evaluate_predictions_for_date
        for d, c in [("2026-02-02", "ROUGE"), ("2026-02-03", "ROUGE"),
                     ("2026-02-04", "BLANC"), ("2026-02-05", "BLEU")]:
            evaluate_predictions_for_date(date.fromisoformat(d), c)
        before = _season_view()
        conn = get_db()
        conn.execute("DELETE FROM predictions")
        conn.commit()
        conn.close()
        after = _season_view()
        for n in (2, 3, 4, 5):
            for c in ("ROUGE", "BLANC"):
                assert _hz(after, n)[c]["annonces"] == _hz(before, n)[c]["annonces"]
                assert _hz(after, n)[c]["alertes"] == _hz(before, n)[c]["alertes"]

    def test_before_start_date_excluded(self, monkeypatch):
        monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2026-01-10")
        _actual("2026-01-12", "ROUGE")
        _pred("2026-01-12", "2026-01-09", "ROUGE")  # émise avant le démarrage
        _pred("2026-01-12", "2026-01-10", "ROUGE")  # J-2, après
        s = _season_view()
        day = s["days"][0]
        assert day["cells"][3] == "n/a" and day["cells"][2]["couleur"] == "ROUGE"
        assert _hz(s, 3)["jours"] == 0

    def test_page_shows_raw_counts_without_percent(self, client, monkeypatch):
        _controlled_case(monkeypatch)
        page = client.get(PAGE + "/2025-2026").text
        article = re.search(r"<article.*?</article>", page, re.S).group(0)
        bilan = article[article.index('id="bilan"'):]
        assert "<strong>2</strong> sur 2" in bilan  # rouges annoncés à 2 jours
        assert "<strong>2</strong> sur 3" in bilan  # alertes rouges justes à 2 jours
        assert "hb-pct" not in article and "&nbsp;%)" not in article  # effectifs < 20
        assert article.count('<th scope="row" class="hg-date"><time') == 4
        assert "—" not in article and "&mdash;" not in article

    def test_percent_from_20(self, client, monkeypatch):
        monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2025-11-01")
        d0 = date(2025, 12, 1)
        for i in range(20):
            d = (d0 + timedelta(days=i)).isoformat()
            _actual(d, "ROUGE")
            _pred(d, _before(d, 2), "ROUGE" if i < 19 else "BLEU")
        j2 = _hz(_season_view(), 2)
        assert (j2["ROUGE"]["reels"], j2["ROUGE"]["rappel_pct"]) == (20, 95)
        assert j2["ROUGE"]["precision_pct"] is None  # 19 alertes < 20
        page = client.get(PAGE + "/2025-2026").text
        assert '<strong>19</strong> sur 20 <span class="hb-pct">(95&nbsp;%)</span>' in page
        assert "<strong>19</strong> sur 19</td>" in page  # 19 alertes : pas de pourcentage


# ------------------------------------------------------------ CSV, SEO

class TestCsvAndSeo:
    def test_csv_matches_table(self, client, monkeypatch):
        _controlled_case(monkeypatch)
        r = client.get(PAGE + ".csv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        assert r.content.startswith(b"\xef\xbb\xbf")
        rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
        head = rows[0]
        assert head[:4] == ["saison", "date", "couleur_officielle", "temp_observee"]
        assert [c for c in head if c.startswith("prevision_")] == [f"prevision_J-{n}" for n in range(15, 0, -1)]
        assert len(rows) == 1 + 4
        by_date = {row[1]: dict(zip(head, row)) for row in rows[1:]}
        got = [by_date["2026-02-02"][f"prevision_J-{n}"] for n in (5, 4, 3, 2)]
        assert got == ["ROUGE", "", "BLANC", "ROUGE"]
        got = [by_date["2026-02-05"][f"prevision_J-{n}"] for n in (5, 4, 3, 2)]
        assert got == ["BLANC", "", "BLANC", "BLEU"]
        assert by_date["2026-02-02"]["prevision_J-15"] == ""

    def test_jsonld_and_meta(self, client, monkeypatch):
        _controlled_case(monkeypatch)
        for path in (PAGE, PAGE + "/2025-2026"):
            page = client.get(path).text
            types = {b["@type"] for b in _jsonld(page)}
            assert {"BreadcrumbList", "Dataset"} <= types
            assert page.count("<h1") == 1
            assert f'<link rel="canonical" href="https://www.calendrier-tempo.fr{path}">' in page
            title = re.search(r"<title>(.*?)</title>", page).group(1)
            desc = re.search(r'<meta name="description" content="(.*?)">', page).group(1)
            assert 50 <= len(title) <= 65, title
            assert 140 <= len(desc) <= 155, desc
            assert 'property="og:title"' in page and 'name="twitter:card"' in page

    def test_season_links_crawlable(self, client, monkeypatch):
        _controlled_case(monkeypatch)
        page = client.get(PAGE).text
        assert 'href="/historique-previsions/2025-2026"' in page
        assert 'href="/historique-previsions.csv"' in page

    def test_sitemap_llms_footer_methodologie(self, client, monkeypatch):
        _controlled_case(monkeypatch)
        sm = client.get("/sitemap.xml").text
        assert "<loc>https://www.calendrier-tempo.fr/historique-previsions</loc>" in sm
        assert "<loc>https://www.calendrier-tempo.fr/historique-previsions/2025-2026</loc>" in sm
        assert "/historique-previsions" in client.get("/llms.txt").text
        assert 'href="/historique-previsions"' in client.get("/methodologie").text
        assert 'href="/historique-previsions"' in client.get("/").text  # footer


# ------------------------------------------------------------ Données réelles (db_dump.json)

class TestRealDump:
    def test_dump_page(self, client, monkeypatch):
        monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2026-02-15")
        from db_sync import import_from_file
        import_from_file()
        s = _season_view()
        assert s is not None and s["days"][0]["date"] >= "2026-02-15"
        assert _season_view("2023-2024") is None  # backtests seulement : saison absente
        r = client.get(PAGE + "/2025-2026")
        assert r.status_code == 200 and "Jour par jour" in r.text
        assert client.get(PAGE).status_code == 200
        rows = client.get(PAGE + ".csv").content.decode("utf-8-sig").splitlines()
        assert len(rows) == 1 + len(s["days"])

    def test_dump_backtest_rows_never_counted(self, monkeypatch):
        # Démarrage non borné : les backtests du dump (émis à J-1) restent exclus.
        from db_sync import import_from_file
        import_from_file()
        from prediction_history import get_history
        seasons = get_history(force=True)["seasons"]
        assert set(seasons) == {"2025-2026"}


# ------------------------------------------------------------ Grille J-15 -> J-1, températures, purge

def _weather(d: str, temp: float, fetched: str):
    from database import get_db
    conn = get_db()
    conn.execute(
        "INSERT INTO weather_cache (date, temp_min, temp_max, temp_moy, fetched_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (date) DO UPDATE SET temp_moy = excluded.temp_moy, "
        "fetched_at = excluded.fetched_at",
        (d, temp - 3, temp + 3, temp, fetched))
    conn.commit()
    conn.close()


class TestGridAndTemperatures:
    def _case(self, monkeypatch):
        monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2026-01-01")
        d = "2026-02-10"
        _actual(d, "ROUGE")
        _pred(d, _before(d, 12), "BLEU", temp=8.4)
        _pred(d, _before(d, 5), "BLANC", temp=3.2)
        _pred(d, _before(d, 2), "ROUGE", temp=-1.5, proba_rouge=0.64)
        _pred(d, _before(d, 1), "BLEU", confirmed=1, originale="ROUGE", temp=-2.0)
        _weather(d, -2.3, "2026-02-10T20:00:00")
        return d

    def test_grid_has_15_horizon_columns(self, client, monkeypatch):
        self._case(monkeypatch)
        page = client.get(PAGE + "/2025-2026").text
        grid = re.search(r'<table class="history-grid-table">.*?</table>', page, re.S).group(0)
        heads = re.findall(r"<abbr[^>]*>(J-\d+)</abbr>", grid)
        assert heads == [f"J-{n}" for n in range(15, 0, -1)]
        row = re.findall(r"<tr>.*?</tr>", grid, re.S)[1]
        assert row.count('class="hg-cell') == 15

    def test_temperatures_and_tooltips(self, client, monkeypatch):
        self._case(monkeypatch)
        page = client.get(PAGE + "/2025-2026").text
        grid = re.search(r'<table class="history-grid-table">.*?</table>', page, re.S).group(0)
        for t in ("8,4°", "3,2°", "-1,5°", "-2,0°"):
            assert t in grid
        assert "-2,3°" in grid  # température observée (weather_cache)
        assert "Prévu 2 jours avant : Rouge, juste ; probabilité 64 %" in grid
        # Ligne confirmée : couleur émise, probabilité écrasée par EDF non affichée
        assert "Prévu la veille : Rouge, juste ; température moyenne prévue -2,0 °C" in grid
        assert "Prévu 12 jours avant : Bleu, erroné" in grid
        assert "n/d" not in grid

    def test_nd_when_temperature_not_kept(self, client, monkeypatch):
        monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2026-01-01")
        _actual("2026-02-10", "BLEU")
        _pred("2026-02-10", "2026-02-07", "BLEU")  # sans temp_moy_prevue
        page = client.get(PAGE + "/2025-2026").text
        assert "n/d" in page and "température prévue non conservée" in page

    def test_csv_has_temperatures(self, client, monkeypatch):
        d = self._case(monkeypatch)
        body = client.get(PAGE + ".csv").content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(body), delimiter=";"))
        row = dict(zip(rows[0], [r for r in rows if r[1] == d][0]))
        assert row["temp_observee"] == "-2,3"
        assert (row["prevision_J-2"], row["temp_prevue_J-2"]) == ("ROUGE", "-1,5")
        assert (row["prevision_J-12"], row["temp_prevue_J-12"]) == ("BLEU", "8,4")
        assert row["prevision_J-1"] == "ROUGE"

    def test_bilan_covers_1_to_15(self, client, monkeypatch):
        self._case(monkeypatch)
        s = _season_view()
        assert [h["n"] for h in s["horizons"]] == list(range(1, 16))
        assert _hz(s, 12)["justes"] == 0 and _hz(s, 1)["justes"] == 1
        page = client.get(PAGE + "/2025-2026").text
        assert "Zone la plus fiable" in page and "Indicatif" in page
        assert page.count('<tr class="hb-fiable">') == 4


class TestPurgeKeepsRealPredictions:
    def test_purge_keeps_real_deletes_old_simulated(self):
        import app as app_module
        from database import get_db
        old = (date.today() - timedelta(days=200)).isoformat()
        _pred(old, _before(old, 3), "ROUGE", temp=1.0)
        _pred(old, _before(old, 4), "BLEU", simulated=1)
        _pred(old, _before(old, 5), "BLANC", horizon="BT", cycle_id="backtest")
        app_module.purge_old_data()
        conn = get_db()
        rows = conn.execute("SELECT simulated, cycle_id FROM predictions WHERE date = ?", (old,)).fetchall()
        conn.close()
        assert sorted((r["simulated"], r["cycle_id"]) for r in rows) == [(0, ""), (0, "backtest")]


class TestAdminUnchanged:
    def test_daily_recap_still_works(self, monkeypatch):
        """La page publique n'altère pas get_daily_recap (lecture seule, même sortie)."""
        monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2026-01-01")
        _actual("2026-02-10", "ROUGE")
        _pred("2026-02-10", "2026-02-08", "ROUGE", temp=-1.5)
        from performance_tracker import get_daily_recap
        before = get_daily_recap("2025-2026")
        _season_view()
        after = get_daily_recap("2025-2026")
        assert before == after and before and before[0]["date"] == "2026-02-10"
