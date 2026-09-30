"""Audits UX et design des pages publiques du 2026-09-30 : garde-fous de non-régression.

Base SQLite temporaire (conftest.py). Les lignes insérées sont des cas de test contrôlés.
"""

import html
import re
from pathlib import Path

import pytest

from tests.test_history_page import PAGE, _actual, _before, _controlled_case, _pred

ROOT = Path(__file__).resolve().parent.parent


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


def _article(page: str) -> str:
    return re.search(r"<article.*?</article>", page, re.S).group(0)


# ---------------------------------------------------------------- Historique (HIS-01 à HIS-05)

class TestHistoryPage:
    def test_csv_is_a_discreet_tracked_link_at_the_bottom(self, client, monkeypatch):
        _controlled_case(monkeypatch)
        art = _article(client.get(PAGE + "/2025-2026").text)
        assert 'class="btn" href="/historique-previsions.csv"' not in art
        link = re.search(r'<a href="/historique-previsions.csv"[^>]*>', art).group(0)
        assert 'data-umami-event="csv_download"' in link and "btn" not in link
        assert art.index('id="methode"') < art.index("/historique-previsions.csv")

    def test_en_bref_comes_before_the_grid_and_the_bilan(self, client, monkeypatch):
        _controlled_case(monkeypatch)
        art = _article(client.get(PAGE + "/2025-2026").text)
        assert art.index('id="en-bref"') < art.index('id="jour-par-jour"') < art.index('id="bilan"')
        bref = art[art.index('id="en-bref"'):art.index('id="jour-par-jour"')]
        assert "Repère" in bref and "sur" in bref
        assert "hb-pct" not in bref  # effectifs < 20 : pas de pourcentage

    def test_bref_aggregates_reliable_horizons(self, monkeypatch):
        _controlled_case(monkeypatch)
        from prediction_history import get_history, page_context
        ctx = page_context(get_history(force=True), "2025-2026", "2026-2027")
        fiables = ctx["h"]["fiables"]
        bref = ctx["h"]["bref"]
        assert bref["ROUGE"]["reels"] == sum(h["ROUGE"]["reels"] for h in fiables)
        assert bref["ROUGE"]["annonces"] == sum(h["ROUGE"]["annonces"] for h in fiables)
        assert bref["jours"] == sum(h["jours"] for h in fiables)
        assert bref["toujours_bleu"] == sum(h["toujours_bleu"] for h in fiables)

    def test_grid_shows_14_rows_then_the_rest_on_demand(self, client, monkeypatch):
        monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2025-11-01")
        days = [f"2026-01-{i:02d}" for i in range(1, 21)]
        for d in days:
            _actual(d, "BLEU")
            _pred(d, _before(d, 2), "BLEU")
        page = client.get(PAGE + "/2025-2026").text
        grid = re.search(r'<table class="history-grid-table">.*?</table>', page, re.S).group(0)
        assert grid.count('<tr class="hg-more">') == 20 - 14
        assert grid.count('<th scope="row" class="hg-date"><time') == 20  # tout reste dans le DOM
        assert 'id="hg-show-all"' in page and 'data-umami-event="history_expand_all"' in page
        assert "<noscript>" in page

    def test_red_white_columns_hidden_when_season_has_none(self, client, monkeypatch):
        monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2025-11-01")
        for d in ("2026-01-05", "2026-01-06"):
            _actual(d, "BLEU")
            _pred(d, _before(d, 2), "BLEU")
        page = html.unescape(client.get(PAGE + "/2025-2026").text)
        bilan = page[page.index('id="bilan"'):]
        assert "Rouges prévus à l'avance" not in bilan
        assert "les colonnes rouge et blanc s'afficheront" in bilan

    def test_bilan_uses_plain_vocabulary(self, client, monkeypatch):
        _controlled_case(monkeypatch)
        page = html.unescape(client.get(PAGE + "/2025-2026").text)
        assert "Alertes rouges qui étaient justes" in page
        assert "Repère : toujours dire « bleu »" in page.replace("\xa0", " ")


# ---------------------------------------------------------------- Accueil (ACC-01 à ACC-10)

class TestHomepage:
    def test_no_separate_today_block_answer_inside_summary_card(self, client):
        from database import get_db
        from datetime import date, timedelta
        conn = get_db()
        today = date.today()
        conn.execute("INSERT INTO actuals (date, couleur_reelle, synthetic, timestamp_confirmation) "
                     "VALUES (?, 'BLEU', 0, ?)", (today.isoformat(), today.isoformat() + "T11:00:00"))
        conn.commit()
        conn.close()
        page = client.get("/").text
        assert 'class="today-answer"' not in page
        card = page[page.index('id="week-summary-card"'):page.index('id="comment-ca-marche"')]
        # la couleur du jour est dans la grande pastille (plus de paragraphe de redite)
        assert "week-summary-answer" not in card
        assert "week-dot-hero" in card or "Chargement" in card
        assert page.count("<h1") == 1

    def test_prices_come_from_site_facts(self, client):
        import site_facts
        page = client.get("/").text
        hp = "{:.4f}".format(site_facts.TARIFS["ROUGE"]["hp"]).replace(".", ",")
        assert f"{hp}&nbsp;&euro;/kWh en heures pleines" in page
        assert "HP 0,73&nbsp;" not in page and "HP " + hp not in page  # « HP » : jargon retiré

    def test_counters_placeholder_is_a_dash_not_a_question_mark(self):
        src = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
        assert "{% else %}?{% endif %}" not in src
        js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        assert "setText('count-rouge', '?')" not in js

    def test_section_order_counters_then_cta_then_steps(self, client):
        page = client.get("/").text
        assert page.index('id="count-rouge"') < page.index('id="subscribe"') < page.index('id="comment-ca-marche"')
        assert page.count('id="subscribe"') == 1


# ---------------------------------------------------------------- Transverse

class TestSharedParts:
    def test_modal_never_closes_by_itself_and_example_is_labelled(self):
        src = (ROOT / "templates" / "_subscribe_modal.html").read_text(encoding="utf-8")
        assert "setTimeout(function() { closeSubscribeModal(); }" not in src
        assert "couleurs fictives" in src
        assert "offsetParent !== null" in src  # focus trap limité aux éléments visibles

    def test_legal_host_is_cloudflare(self, client):
        page = html.unescape(client.get("/mentions-legales").text)
        host = page[page.index("2. Hébergeur"):page.index("3. Nature du service")]
        assert "Cloudflare, Inc." in host and "Replit" not in host

    def test_asset_version_is_single_and_used(self, client):
        import site_facts
        page = client.get("/").text
        assert f"style.min.css?v={site_facts.ASSET_VERSION}" in page
        assert f"app.min.js?v={site_facts.ASSET_VERSION}" in page
        for tpl in (ROOT / "templates").glob("*.html"):
            assert "?v=2026" not in tpl.read_text(encoding="utf-8"), tpl.name

    def test_minified_assets_in_sync_with_sources(self):
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        css_min = (ROOT / "static" / "css" / "style.min.css").read_text(encoding="utf-8")
        for sel in (".data-table", ".history-bref", ".notfound-links", ".btn-outline",
                    ".week-dots-hero", ".week-dot-color", "a.week-dot-link", ".week-summary-foot", ".week-dot-date",
                    ".fc-temp-val", ".forecast-temp-note", ".fc-compact", ".hg-none"):
            assert sel in css and sel in css_min, sel
        js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        js_min = (ROOT / "static" / "js" / "app.min.js").read_text(encoding="utf-8")
        for fn in re.findall(r"^(?:async )?function (\w+)", js, re.M):
            assert f"function {fn}(" in js_min, fn

    def test_blog_index_pillars_then_clusters(self, client):
        page = html.unescape(client.get("/blog/").text)
        assert page.index("Les guides essentiels") < page.index("Comprendre Tempo")
        pillars = page[page.index("Les guides essentiels"):page.index("Comprendre Tempo")]
        assert pillars.count("blog-card--pillar") == 3
        assert page.count("<h1") == 1

    def test_blog_article_tables_are_scrollable(self, client):
        page = client.get("/blog/alerte-jour-rouge-tempo").text
        if "<table>" in page:
            assert '<div class="table-scroll" role="region" aria-label="Tableau" tabindex="0">\n<table>' in page
