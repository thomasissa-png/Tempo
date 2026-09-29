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
          cycle_id: str = "", confirmed: int = 0, originale: str = ""):
    from database import get_db
    conn = get_db()
    n = (date.fromisoformat(target) - date.fromisoformat(emitted)).days
    conn.execute(
        "INSERT INTO predictions (date, couleur_predite, horizon, timestamp_prediction, "
        "simulated, cycle_id, confirmed, couleur_originale) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (target, couleur, horizon or f"J-{n}", emitted + "T18:00:00", simulated, cycle_id,
         confirmed, originale),
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

    def test_survives_purge_of_live_predictions(self, monkeypatch):
        """purge_old_data() supprime les prédictions live > 90 jours : les évaluations
        figées (performance) prennent le relais, sans les lignes de backtest."""
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
        assert "2 annoncés sur 2" in article
        assert "2 justes sur 3" in article
        assert "%" not in article  # tous les effectifs < 20
        assert article.count('<th scope="row"><time') == 4
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
        assert "19 annoncés sur 20</strong> (95&nbsp;%)" in page
        assert "18 justes" not in page


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
        assert rows[0][:7] == ["saison", "date", "couleur_officielle", "prevision_J-5",
                               "prevision_J-4", "prevision_J-3", "prevision_J-2"]
        assert len(rows) == 1 + 4
        by_date = {row[1]: row for row in rows[1:]}
        assert by_date["2026-02-02"][2:7] == ["ROUGE", "ROUGE", "", "BLANC", "ROUGE"]
        assert by_date["2026-02-05"][2:7] == ["BLEU", "BLANC", "", "BLANC", "BLEU"]

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
