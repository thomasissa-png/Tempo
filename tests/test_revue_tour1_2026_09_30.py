"""Revue notée, tour 1 (2026-09-30) : corrections @ux + @design appliquées.

Police de repli aux métriques d'Inter, échelle de titres, plancher 12 px, tableaux lisibles
en mobile (demain, bilan, API), calendrier (résumé du mois, note courte, texte replié),
historique (En bref sans cadre imbriqué, causes des absences), blog (date retirée des cartes,
rubriques, grille 3 colonnes, CTA contextuel), alertes (un seul bord gauche), mentions légales.
Décisions fondateur : les cases de la grille de l'historique ne changent pas.
"""

import html
import re
from pathlib import Path

import pytest

import site_facts

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
MIN_CSS = (ROOT / "static" / "css" / "style.min.css").read_text(encoding="utf-8")
FONTS = (ROOT / "static" / "css" / "fonts.css").read_text(encoding="utf-8")
TOUR1 = CSS[CSS.index("Revue notée, tour 1"):]


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import app as app_module
    app_module._db_ready.set()
    return TestClient(app_module.app)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


class TestTransversal:
    def test_inter_fallback_font_face_and_stack(self):
        block = FONTS[FONTS.index("'Inter Fallback'"):]
        for prop in ("size-adjust", "ascent-override", "descent-override", "line-gap-override", "local('Arial')"):
            assert prop in block
        assert "font-family: 'Inter', 'Inter Fallback'," in CSS
        assert "fonts.googleapis" not in FONTS + CSS

    def test_type_scale_tokens(self):
        for token in ("--fs-h1: 2rem", "--fs-h2: 1.4rem", "--fs-h2-card: 1.25rem", "--fs-lead: 1.1rem", "--fs-min: 0.75rem"):
            assert token in TOUR1
        assert "main h1 + .section-desc" in TOUR1  # chapeau au-dessus du corps

    def test_no_text_under_12px_in_tour1_block(self):
        sizes = re.findall(r"font-size:\s*([0-9.]+)rem", TOUR1)
        assert sizes and min(float(s) for s in sizes) >= 0.75

    def test_min_css_regenerated(self):
        for sel in (".hb-kpis", ".days-table", ".blog-rubriques", ".to-top-link", ".cal-seo-more", "Inter Fallback"):
            assert sel in MIN_CSS, sel

    def test_long_pages_have_to_top_link(self, client):
        for path in ("/historique-previsions", "/methodologie", "/mentions-legales", "/blog/", "/calendrier"):
            assert 'class="to-top-link"' in client.get(path).text, path
        assert 'class="to-top-link"' not in client.get("/tarif-tempo-edf").text


class TestCouleurDemain:
    def test_table_three_columns_on_mobile(self, client):
        from database import get_db
        from datetime import date, datetime, timedelta
        conn = get_db()
        for off in range(0, 4):
            conn.execute(
                "INSERT INTO predictions (date, couleur_predite, probabilite_bleu, probabilite_blanc, "
                "probabilite_rouge, score_risque, horizon, timestamp_prediction) VALUES (?, 'BLEU', 0.9, 0.05, 0.05, 20, ?, ?)",
                ((date.today() + timedelta(days=off)).isoformat(), f"J-{off}", datetime.now().isoformat()),
            )
        conn.commit()
        conn.close()
        page = client.get("/couleur-tempo-demain").text
        assert 'class="data-table days-table"' in page
        assert 'class="d-long"' in page and 'class="d-short"' in page
        assert 'class="col-statut"' in page and 'class="d-mobile-only"' in page
        assert 'class="source-label label-prediction"' in page
        mobile = TOUR1[TOUR1.index(".days-table .d-mobile-only { display: none; }"):]
        assert ".days-table .col-statut { display: none; }" in mobile


class TestHistorique:
    def test_grid_cells_unchanged_founder_decision(self):
        grid = (ROOT / "templates" / "_historique_grille.html").read_text(encoding="utf-8")
        assert "'hg-ok' if c.juste else 'hg-ko'" in grid
        assert "hg-fill" not in grid + CSS
        assert ".hg-dot.hg-ok { box-shadow: 0 0 0 2px var(--surface), 0 0 0 4px var(--vert); }" in CSS

    def test_bref_without_nested_frames(self):
        assert ".history-bref-ref { background: none; border-left: 0;" in TOUR1
        assert ".history-note--info { background: none;" in TOUR1

    def test_bilan_compact_readable(self):
        bilan = (ROOT / "templates" / "_historique_bilan.html").read_text(encoding="utf-8")
        assert 'class="show-sm"' in bilan and "Toujours «&nbsp;bleu&nbsp;»" in bilan
        assert ".history-bilan-scroll.is-compact .history-table { table-layout: fixed; }" in TOUR1
        assert ".history-bilan-scroll.is-compact { max-width: 720px; }" in TOUR1

    def test_causes_of_missing_predictions_are_counted(self):
        import prediction_history as ph
        from datetime import date
        day = {"date": "2026-09-20", "couleur": "BLEU", "cells": {n: None for n in ph.HORIZONS},
               "causes": {2: ("interruption", "2026-09-18")}, "remplies": {}, "temp_observee": None,
               "confirme_le": "2026-09-19"}
        season = {"days": [day], "first_date": "2026-09-20", "last_date": "2026-09-20",
                  "horizons": [ph._horizon_stats([day], n) for n in ph.HORIZONS],
                  "jours_sans_calcul": ["2026-09-18"]}
        ctx = ph.page_context({"seasons": {"2026-2027": season}}, "2026-2027", "2026-2027", today=date(2026, 9, 30))
        view = ctx.get("h") or next(v for v in ctx.values() if isinstance(v, dict) and "causes_fiables" in v)
        assert view["causes_fiables"] == {"interruption": 1, "autre": 3}


class TestCalendrier:
    def test_month_summary_under_grid_and_hidden_when_empty(self, client):
        page = client.get("/calendrier").text
        i_note = page.index('class="cal-source-note"')
        i_sum = page.index('class="cal-month-summary')
        i_cta = page.index('class="blog-cta page-cta"')
        assert i_note < i_sum < i_cta
        tag = re.search(r'<section class="cal-month-summary ([^"]+)"', page).group(1)
        assert tag in ("is-visible", "sr-only")

    def test_fewer_elements_before_cards(self, client):
        page = client.get("/calendrier").text
        block = page[page.index("cal-forecast-title"):page.index('id="forecast-container"')]
        assert "source-label" not in block
        assert 'style="margin-top' not in page

    def test_seo_text_folded_on_mobile_kept_in_dom(self, client):
        page = client.get("/calendrier").text
        assert '<details class="faq-item cal-seo-more" id="cal-seo-more" open>' in page
        assert "matchMedia('(max-width: 768px)')" in page
        for h in ("Comprendre le calendrier Tempo EDF", "Quand tombent les jours rouges Tempo ?", "Comment utiliser ce calendrier ?"):
            assert h in page


class TestBlog:
    def test_index_cards_without_visible_date_and_rubriques(self, client):
        page = client.get("/blog/").text
        assert '<ul class="blog-rubriques">' in page
        assert "Lire l'article" not in page
        metas = re.findall(r'<div class="blog-card-meta">(.*?)</div>', page, re.S)
        assert metas and all('class="sr-only"' in m for m in metas)
        assert "grid-template-columns: repeat(3, 1fr)" in TOUR1

    def test_mid_article_cta_position(self):
        from blog import _mid_cta_index
        body = "<p>intro</p>" + "".join(f"<h2 id='s{i}'>Partie {i}</h2><p>{'x' * 200}</p>" for i in range(5))
        pos = _mid_cta_index(body)
        assert pos is not None and body[pos:].startswith("<h2")
        ctx = body.replace("Partie 2", "Comment s'inscrire")
        assert _mid_cta_index(ctx) == ctx.index("<h2 id='s3'>")
        assert _mid_cta_index("<h2>a</h2><h2>b</h2>") is None
        faq = "<h2>T0</h2><h2>T1</h2><h2>FAQ : vos questions</h2><h2>Questions fréquentes</h2>"
        assert _mid_cta_index(faq) is None

    def test_article_has_contextual_cta(self, client):
        page = client.get("/blog/alerte-jour-rouge-tempo").text
        assert 'class="blog-cta-mid"' in page
        body = page[page.index('class="blog-article-body"'):]
        assert body.index("Comment s") < body.index('class="blog-cta-mid"') < body.index("Que contiennent les alertes")
        assert 'style="max-width:800px"' not in page


class TestAutresPages:
    def test_alertes_single_left_edge_no_emoji_label(self, client):
        page = client.get("/alertes").text
        assert '<main id="main-content" class="container page-content" role="main">' in page
        label = re.search(r'<div class="sms-preview-label">(.*?)</div>', page, re.S).group(1)
        assert "&#128172;" not in label and "💬" not in label
        assert ".alertes-hero h1 { font-size" not in page

    def test_api_copy_bar_above_code(self, client):
        page = client.get("/api-tempo").text
        assert "box.insertBefore(bar, pre)" in page
        assert "position: absolute; top: 8px; right: 8px" not in page
        assert 'class="data-table api-fields"' in page and "temp_moy_prevue" in page

    def test_tarif_key_figures_from_facts(self, client):
        page = html.unescape(client.get("/tarif-tempo-edf").text).replace("\xa0", " ")
        assert 'class="counters tarif-counters"' in page
        assert f"{site_facts.fr_num(site_facts.RATIO_ROUGE_BLEU_HP)} fois" in page
        assert page.index("Combien coûte un jour rouge") < page.index("blog-cta page-cta") < page.index("Anticiper les jours rouges")

    def test_home_faq_reliability_third(self):
        assert site_facts.FAQ_HOME[2].get("id") == "faq-fiabilite"

    def test_home_counters_without_empty_bar(self, client):
        page = client.get("/").text
        assert "counter-progress" not in page
