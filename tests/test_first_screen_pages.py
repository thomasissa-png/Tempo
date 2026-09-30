"""Premier écran des pages publiques (revue du 2026-09-30, hors accueil).

Fil d'Ariane aligné, /calendrier (intro courte, légende compacte, case du jour),
/couleur-tempo-demain (ordre, parenthèses, format des dates), saison (intro sans
parenthèse imbriquée, « À venir » allégé), /api-tempo (endpoints jamais coupés),
mentions légales (sommaire repliable), replis de /a-propos, sommaire des articles,
saisons de /historique-previsions sur une ligne.
"""

import html
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import site_facts

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import app as app_module
    app_module._db_ready.set()
    return TestClient(app_module.app)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _rule(selector: str, css: str = CSS) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def test_breadcrumb_text_aligned_with_content():
    rule = _rule(".breadcrumb a")
    assert "padding: 10px 0" in rule  # annule le padding horizontal de « nav a »


class TestCalendrier:
    def test_short_intro_then_details_under_grid(self, client):
        page = client.get("/calendrier").text
        intro = re.search(r'<p class="section-desc cal-intro">(.*?)</p>', page, re.S).group(1)
        txt = _text(intro)
        for kw in ("calendrier Tempo EDF", "couleur officielle", "prévisions"):
            assert kw in txt
        assert len(txt) < 170 and "italique" not in txt
        note = _text(re.search(r'<p class="cal-source-note">(.*?)</p>', page, re.S).group(1))
        assert page.index('class="cal-grid"') < page.index('class="cal-source-note"')
        assert "nous n'affichons jamais de couleur par défaut" in note
        assert "Seule la couleur publiée par EDF fait foi" in note
        assert page.count("<h1") == 1

    def test_today_cell_is_labelled(self, client):
        page = client.get("/calendrier").text
        cell = re.search(r'<div class="cal-day [^"]*today[^"]*"[^>]*>(.*?)</div>', page, re.S).group(1)
        assert '<span class="cal-today-label" aria-hidden="true">aujourd\'hui</span>' in cell
        assert "(aujourd'hui)" in cell  # libellé lecteur d'écran conservé

    def test_mobile_legend_single_line(self, client):
        page = client.get("/calendrier").text
        assert 'class="cal-legend-long"' in page
        mobile = page[page.index("@media (max-width: 600px) {\n            .cal-grid"):]
        assert "flex-wrap: nowrap" in _rule(".cal-legend", mobile)
        assert "display: none" in _rule(".cal-legend-long", mobile)


class TestCouleurDemain:
    def _insert(self):
        from database import get_db
        conn = get_db()
        for off in range(0, 5):
            conn.execute(
                "INSERT INTO predictions (date, couleur_predite, probabilite_bleu, probabilite_blanc, "
                "probabilite_rouge, score_risque, horizon, timestamp_prediction) VALUES (?, 'BLEU', 0.9, 0.05, 0.05, 20, ?, ?)",
                ((date.today() + timedelta(days=off)).isoformat(), f"J-{off}", datetime.now().isoformat()),
            )
        conn.execute("INSERT INTO actuals (date, couleur_reelle, synthetic, timestamp_confirmation) VALUES (?, 'BLEU', 0, ?)",
                     (date.today().isoformat(), datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def test_same_order_answer_table_links_cta(self, client):
        self._insert()
        page = client.get("/couleur-tempo-demain").text
        i_answer = page.index("today-answer")
        i_table = page.index("Les prochains jours")
        # la phrase « alertes WhatsApp » (redite du bloc CTA) a été retirée (copy deck, section 7)
        i_links = page.index('href="/api-tempo">API Tempo</a>')
        i_cta = page.index('class="blog-cta page-cta"')
        assert i_answer < i_table < i_links < i_cta

    def test_no_double_parenthesis_and_same_date_format(self, client):
        self._insert()
        page = client.get("/couleur-tempo-demain").text
        txt = _text(page[page.index("<h1"):page.index("quelle heure EDF publie")])
        assert "))" not in txt
        tomorrow = date.today() + timedelta(days=1)
        assert f"Demain, {site_facts.fr_date(tomorrow)}" in txt  # avec l'année, comme aujourd'hui
        assert f"Aujourd'hui, {site_facts.fr_date(date.today())}" in txt


class TestSaison:
    def test_intro_single_level_and_light_upcoming(self, client):
        today = date.today()
        start = today.year if today.month >= 9 else today.year - 1
        page = client.get(f"/calendrier/{start}-{start + 1}").text
        intro = _text(re.search(r'<p class="section-desc">(.*?)</p>', page, re.S).group(1))
        assert "(" not in intro and "api-couleur-tempo.fr" in intro
        assert intro.count("publiées par EDF") == 1
        if "season-upcoming" in page:
            assert '<nav class="season-upcoming"' in page
        rule = _rule(".season-upcoming a")
        assert "font-size: 0.8rem" in rule and "text-decoration: none" in rule


class TestApiTempo:
    def test_endpoints_never_broken(self, client):
        assert "overflow-wrap: anywhere" not in _rule(".data-table code")
        assert "white-space: nowrap" in _rule(".data-table code")
        page = client.get("/api-tempo").text
        assert 'class="data-table api-endpoints"' in page
        assert ".api-endpoints code { white-space: nowrap; }" in page
        assert page.count('class="api-cache" data-label="Cache"') == 6

    def test_short_intro_source_below_table(self, client):
        page = client.get("/api-tempo").text
        intro = _text(re.search(r'<p class="section-desc">(.*?)</p>', page, re.S).group(1))
        assert "API JSON publique" in intro and "api-couleur-tempo" not in intro
        assert page.index("</table>") < page.index('class="api-source-note"')
        assert "relayées par api-couleur-tempo.fr" in page


def test_legal_unsubscribe_first_then_collapsible_toc(client):
    page = client.get("/mentions-legales").text
    toc = page[page.index('class="page-toc page-toc--compact"'):page.index("</nav>", page.index("page-toc--compact"))]
    assert toc.index("Se désinscrire") < toc.index('<details class="page-toc-more">')
    assert "<summary>Sommaire</summary>" in toc
    for anchor in ("#editeur", "#hebergeur", "#donnees-personnelles", "#cookies", "#responsabilite"):
        assert anchor in toc


def test_details_look_like_disclosures():
    assert 'content: "+"' in _rule(".history-details > summary::after")
    assert "list-style: none" in _rule(".history-details > summary")
    assert 'content: "+"' in _rule(".blog-toc summary::after")


def test_blog_toc_closed_by_default(client):
    from blog import get_published_articles
    art = next(a for a in get_published_articles() if a.toc)
    page = client.get(f"/blog/{art.slug}").text
    assert '<details class="blog-toc" id="blog-toc">' in page
    assert "t.open = true" not in page


def test_history_seasons_single_line_on_mobile():
    block = CSS[CSS.index(".history-seasons a.active"):]
    assert "flex-wrap: nowrap" in _rule(".history-seasons", block[block.index("@media"):])
