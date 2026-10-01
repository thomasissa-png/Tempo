"""Tests SEO / GEO du site public (audits 2026-09-29 : SEO blog + GEO).

Rendu réel des pages via TestClient (base SQLite temporaire de conftest.py) :
FAQ visible = JSON-LD, JSON-LD parsable, SSR de la couleur du jour, calendrier
sans couleur par défaut, pages SEO, robots.txt, llms.txt, sitemap, cohérence
des chiffres publics.
"""

import html
import json
import os
import re
from datetime import date, datetime, timedelta

import pytest

import site_facts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NEW_PAGES = ["/tarif-tempo-edf", "/couleur-tempo-demain", "/api-tempo", "/methodologie"]
PUBLIC_PAGES = ["/", "/calendrier", "/alertes", "/a-propos", "/mentions-legales", "/blog/"] + NEW_PAGES


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import app as app_module
    app_module._db_ready.set()
    return TestClient(app_module.app)


def _insert_actual(d: date, couleur: str, synthetic: int = 0):
    from database import get_db
    conn = get_db()
    conn.execute(
        "INSERT INTO actuals (date, couleur_reelle, synthetic, timestamp_confirmation) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (date) DO UPDATE SET couleur_reelle = excluded.couleur_reelle, "
        "synthetic = excluded.synthetic",
        (d.isoformat(), couleur, synthetic, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def _jsonld_blocks(page: str) -> list:
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    return [json.loads(b) for b in blocks]  # lève si un bloc n'est pas du JSON valide


def _visible_faq(page: str) -> list[tuple[str, str]]:
    from site_facts import html_to_text
    items = re.findall(
        r'<details class="faq-item"[^>]*>\s*<summary>(.*?)</summary>\s*<div class="faq-content">(.*?)</div>\s*</details>',
        page, re.S)
    out = []
    for q, a in items:
        a = re.sub(r'<p id="faq-precision-text">.*?</p>', "", a, flags=re.S)  # mesure dynamique (JS)
        out.append((html.unescape(q).strip(), html_to_text(a)))
    return out


def _faq_ld(page: str) -> list[tuple[str, str]]:
    for b in _jsonld_blocks(page):
        if b.get("@type") == "FAQPage":
            return [(q["name"], q["acceptedAnswer"]["text"]) for q in b["mainEntity"]]
    return []


# ---------------------------------------------------------------- FAQ

class TestFaqSingleSource:
    def test_home_visible_faq_equals_jsonld(self, client):
        page = client.get("/").text
        visible, ld = _visible_faq(page), _faq_ld(page)
        assert len(visible) >= 10
        assert visible == ld

    def test_calendrier_visible_faq_equals_jsonld(self, client):
        page = client.get("/calendrier").text
        visible, ld = _visible_faq(page), _faq_ld(page)
        assert len(visible) == 6
        assert visible == ld

    def test_home_faq_respects_edf_rules(self, client):
        text = html.unescape(client.get("/").text)
        assert "samedis peuvent" not in text
        assert "n'importe quand" not in text
        faq = dict(_faq_ld(client.get("/").text))
        weekend = faq["Un jour rouge peut-il tomber un samedi, un dimanche ou un jour férié ?"]
        assert weekend.startswith("Non.")
        assert "jamais rouge" in weekend and "jamais rouge ni blanc" in weekend
        assert "jamais ni" not in weekend  # double négation fautive (copy deck 14.2)

    def test_selectra_link_unchanged(self, client):
        page = client.get("/").text
        assert ('Vous pouvez consulter <a href="https://selectra.info/energie/fournisseurs/edf/tempo#tarifs">'
                'cette page de Selectra</a> qui donne la grille tarifaire en fonction de votre abonnement '
                'ou votre contrat EDF.') in page


# ---------------------------------------------------------------- JSON-LD

class TestJsonLd:
    @pytest.mark.parametrize("path", PUBLIC_PAGES + ["/calendrier/2026-01"])
    def test_all_jsonld_blocks_parse(self, client, path):
        _insert_actual(date(2025, 12, 1), "BLEU")  # rend /calendrier/2026-01 navigable
        r = client.get(path)
        assert r.status_code == 200
        _jsonld_blocks(r.text)

    def test_every_blog_article_jsonld_parses(self, client):
        from blog import get_published_articles
        for a in get_published_articles():
            page = client.get(f"/blog/{a.slug}").text
            blocks = _jsonld_blocks(page)
            article = next(b for b in blocks if b.get("@type") == "Article")
            assert article["headline"] == a.title
            assert "&#39;" not in article["headline"]
            assert article["dateModified"] == (a.updated_date or a.publish_date).isoformat()
            assert article["publisher"]["name"] == "Calendrier Tempo EDF"
            if a.faq_items:
                assert any(b.get("@type") == "FAQPage" for b in blocks)

    def test_frontmatter_quotes_stripped(self):
        from blog import _parse_frontmatter
        meta, _ = _parse_frontmatter('---\ntitle: "Titre : avec deux-points"\n---\ncorps')
        assert meta["title"] == "Titre : avec deux-points"

    def test_faq_section_foire_aux_questions(self):
        from blog import _extract_faq
        body = "## Foire aux questions\n\n### Question ?\n\nRéponse.\n"
        assert _extract_faq(body) == [("Question ?", "Réponse.")]


# ---------------------------------------------------------------- SSR couleur du jour

class TestSsrColors:
    def test_today_and_tomorrow_written_in_text(self, client):
        today = date.today()
        _insert_actual(today, "BLEU")
        _insert_actual(today + timedelta(days=1), "ROUGE")
        text = html.unescape(re.sub(r"<[^>]+>", " ", client.get("/").text))
        text = re.sub(r"\s+", " ", text)
        text = text.replace("\xa0", " ")
        # Pastilles Aujourd'hui / Demain : libellé puis couleur écrite, coche si officielle
        # (date complète lue par les lecteurs d'écran dans le texte sr-only)
        assert "Aujourd'hui ✓ Bleu" in text
        assert "Demain ✓ Rouge" in text
        assert site_facts.fr_date(today, with_year=False) + " : Bleu, couleur officielle" in text
        assert ("Couleur Tempo de demain, " + site_facts.fr_date(today + timedelta(days=1), with_year=False)
                + " : Rouge, confirmée par EDF") in client.get("/").text

    def test_tomorrow_forecast_is_labelled(self, client):
        from database import get_db
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        conn = get_db()
        conn.execute(
            "INSERT INTO predictions (date, couleur_predite, probabilite_bleu, probabilite_blanc, "
            "probabilite_rouge, score_risque, horizon, timestamp_prediction) "
            "VALUES (?, 'BLANC', 0.2, 0.7, 0.1, 50, 'J-1', ?)",
            (tomorrow, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        page = html.unescape(client.get("/couleur-tempo-demain").text)
        assert "notre prévision donne un" in page
        assert "jour blanc" in page and "70 % de chances" in page.replace("\xa0", " ")
        assert "edf publie" not in page  # ANNONCE_J1|lower mettait « EDF » en minuscules

    def test_week_dots_have_full_color_name(self, client):
        from database import get_db
        d = (date.today() + timedelta(days=2)).isoformat()
        conn = get_db()
        conn.execute(
            "INSERT INTO predictions (date, couleur_predite, probabilite_bleu, probabilite_blanc, "
            "probabilite_rouge, score_risque, horizon, timestamp_prediction) "
            "VALUES (?, 'BLEU', 0.8, 0.1, 0.1, 20, 'J-2', ?)",
            (d, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        page = client.get("/").text
        assert re.search(r'<span class="sr-only">[^<]+ : Bleu, pr', page)

    def test_db_not_ready_still_renders(self, client):
        import app as app_module
        app_module._db_ready.clear()
        try:
            r = client.get("/")
            assert r.status_code == 200
            assert 'class="today-answer"' not in r.text
        finally:
            app_module._db_ready.set()


# ---------------------------------------------------------------- Calendrier

class TestCalendar:
    def test_day_without_data_is_not_blue(self, client):
        _insert_actual(date(2025, 12, 1), "BLEU")  # seule donnée : janvier 2026 est vide
        page = client.get("/calendrier/2026-01").text
        assert "cal-day bleu" not in page
        assert "cal-day inconnu" in page

    def test_official_red_listed_and_synthetic_ignored(self, client):
        _insert_actual(date(2026, 1, 5), "ROUGE")
        _insert_actual(date(2026, 1, 6), "ROUGE", synthetic=1)
        month = html.unescape(client.get("/calendrier/2026-01").text)
        assert "lundi 5 janvier 2026" in month
        season = html.unescape(client.get("/calendrier/2025-2026").text)
        assert '<time datetime="2026-01-05">' in season
        assert '<time datetime="2026-01-06">' not in season

    def test_navigation_links_are_crawlable(self, client):
        _insert_actual(date(2024, 1, 8), "ROUGE")
        page = client.get("/calendrier").text
        assert re.search(r'<a href="/calendrier/\d{4}-\d{2}#cal" rel="prev"', page)
        month = client.get("/calendrier/2024-01").text
        assert '<link rel="canonical" href="https://www.calendrier-tempo.fr/calendrier/2024-01">' in month

    def test_legacy_query_redirects_and_bounds(self, client):
        _insert_actual(date(2024, 1, 8), "ROUGE")
        r = client.get("/calendrier?month=1&year=2024", follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == "/calendrier/2024-01"
        assert client.get("/calendrier/", follow_redirects=False).status_code == 301
        assert client.get("/calendrier/2031-01").status_code == 404
        assert client.get("/calendrier/2010-2011").status_code == 404
        assert client.get("/calendrier/2023-2024").status_code == 200


# ---------------------------------------------------------------- Pages SEO

class TestSeoPages:
    @pytest.mark.parametrize("path", NEW_PAGES + ["/", "/calendrier"])
    def test_meta_contract(self, client, path):
        r = client.get(path)
        assert r.status_code == 200
        page = r.text
        title = html.unescape(re.search(r"<title>(.*?)</title>", page, re.S).group(1))
        desc = html.unescape(re.search(r'<meta name="description" content="(.*?)">', page).group(1))
        assert 50 <= len(title) <= 65, (path, len(title), title)
        assert 140 <= len(desc) <= 155, (path, len(desc), desc)
        assert len(re.findall(r"<h1[ >]", page)) == 1
        assert f'<link rel="canonical" href="https://www.calendrier-tempo.fr{path}">' in page
        assert 'property="og:title"' in page and 'name="twitter:card"' in page
        if path != "/":
            assert any(b.get("@type") == "BreadcrumbList" for b in _jsonld_blocks(page))

    def test_home_targets_both_keyword_orders(self, client):
        title = re.search(r"<title>(.*?)</title>", client.get("/").text).group(1)
        assert "Tempo EDF" in title and "EDF Tempo" in title
        cal_title = re.search(r"<title>(.*?)</title>", client.get("/calendrier").text).group(1)
        assert title != cal_title

    def test_api_page_has_webapi_and_dataset(self, client):
        types = {b.get("@type") for b in _jsonld_blocks(client.get("/api-tempo").text)}
        assert {"WebAPI", "Dataset"} <= types

    def test_methodologie_publishes_no_backtest_score(self, client):
        """Audit algorithme 2026-09-29 : l'ancien F1 83,1 % était mesuré sur les
        jours d'entraînement. Seule la performance en conditions réelles est publiée."""
        text = html.unescape(client.get("/methodologie").text)
        assert "83,1" not in text and "83 %" not in text
        assert "uniquement notre performance mesurée en conditions réelles" in text

    def test_tarif_page_matches_constants(self, client):
        text = client.get("/tarif-tempo-edf").text
        for v in ("0,1654", "0,1356", "0,1921", "0,1536", "0,7295", "0,1615"):
            assert v in text

    def test_new_pages_linked_from_footer(self, client):
        page = client.get("/blog/").text
        for p in NEW_PAGES:
            assert f'href="{p}"' in page

    def test_head_request_supported(self, client):
        assert client.head("/").status_code == 200
        assert client.head("/tarif-tempo-edf").status_code == 200

    def test_html_404(self, client):
        r = client.get("/page-qui-n-existe-pas", headers={"accept": "*/*"})
        assert r.status_code == 404
        assert "<h1>Page introuvable</h1>" in r.text and "noindex" in r.text
        assert 'href="/tarif-tempo-edf"' in r.text
        api = client.get("/api/inexistant")
        assert api.status_code == 404 and api.json()["detail"]


# ---------------------------------------------------------------- robots / llms / sitemap / feed

class TestCrawlFiles:
    def test_robots_ai_groups(self, client):
        txt = client.get("/robots.txt").text
        for bot in ("Claude-SearchBot", "Claude-User", "OAI-SearchBot", "Perplexity-User",
                    "Applebot-Extended", "CCBot", "GPTBot", "ClaudeBot", "bingbot"):
            assert f"User-agent: {bot}\n" in txt
        groups = txt.split("\n\n")
        for g in groups:
            if "User-agent:" not in g:
                continue
            assert "Disallow: /api/\n" in g + "\n", g
            assert "/api/indexnow" not in g
        star = next(g for g in groups if "User-agent: *" in g)
        assert "Allow: /api/" not in star
        claude = next(g for g in groups if "User-agent: Claude-SearchBot" in g)
        for ep in ("/api/today", "/api/tomorrow", "/api/remaining", "/api/predictions"):
            assert f"Allow: {ep}\n" in claude

    def test_llms_txt(self, client):
        from app import _llms_revision_date
        from blog import get_published_articles
        txt = client.get("/llms.txt").text
        expected = _llms_revision_date(get_published_articles()).isoformat()
        assert f"Dernière révision du contenu : {expected}" in txt
        assert "/api/remaining" in txt and "/api-tempo" in txt
        assert "relayées par api-couleur-tempo.fr" in txt
        assert "source EDF officielle" not in txt
        assert "Précision mesurée : 83%" not in txt
        assert "83,1" not in txt and "83 %" not in txt
        assert "uniquement notre performance mesurée en conditions réelles" in txt

    def test_llms_full_is_raw_markdown(self, client):
        txt = client.get("/llms-full.txt").text
        assert "| Couleur | Heures pleines" in txt
        assert "<p>" not in txt and "BLEU0,1356" not in txt

    def test_sitemap_lastmod(self, client):
        import site_facts
        from blog import get_published_articles
        xml = client.get("/sitemap.xml").text
        assert "feed.xml" not in xml
        for p in NEW_PAGES:
            assert f"https://www.calendrier-tempo.fr{p}</loc>" in xml
        for path, d in site_facts.PAGE_LASTMOD.items():
            assert (f"<loc>https://www.calendrier-tempo.fr{path}</loc>\n    <lastmod>{d.isoformat()}</lastmod>") in xml
        for a in get_published_articles():
            lm = (a.updated_date or a.publish_date).isoformat()
            assert f"<loc>https://www.calendrier-tempo.fr/blog/{a.slug}</loc>\n    <lastmod>{lm}</lastmod>" in xml

    def test_feed_pubdate_uses_paris_offset(self, client):
        xml = client.get("/feed.xml").text
        dates = re.findall(r"<pubDate>(.*?)</pubDate>", xml)
        assert dates and all(d.endswith(("+0100", "+0200")) for d in dates)

    def test_indexnow_ping_requires_admin(self, client):
        assert client.get("/api/indexnow/ping").status_code == 403


# ---------------------------------------------------------------- Cohérence des chiffres

class TestFactsConsistency:
    def test_ratio_and_savings_computed_from_tariffs(self):
        import site_facts as f
        assert f.RATIO_ROUGE_BLEU_HP == round(0.7295 / 0.1654, 1) == 4.4
        assert f.SURCOUT_JOUR_BAS == round(25 * (0.7295 - 0.1654)) == 14
        assert f.ALERTES_HORIZON_JOURS == 7
        assert "AES-128" in f.CHIFFREMENT_TELEPHONE

    def test_tariffs_match_seo_rules_yaml(self):
        yaml = pytest.importorskip("yaml")
        import site_facts as f
        with open(os.path.join(ROOT, "articles", "_seo_rules.yaml"), encoding="utf-8") as fh:
            rules = yaml.safe_load(fh)
        t = rules["tarifs_tempo"]
        for couleur, key in (("BLEU", "bleu"), ("BLANC", "blanc"), ("ROUGE", "rouge")):
            assert float(t[f"{key}_hp"]) == f.TARIFS[couleur]["hp"]
            assert float(t[f"{key}_hc"]) == f.TARIFS[couleur]["hc"]

    @pytest.mark.parametrize("path", PUBLIC_PAGES + ["/calendrier/2026-01"])
    def test_no_contradictory_claims(self, client, path):
        _insert_actual(date(2025, 12, 1), "BLEU")
        text = html.unescape(client.get(path).text)
        assert not re.search(r"(?<!1)5 prochains jours", text), path
        for bad in ("AES-256", "5x plus cher", "TempoForecast",
                    "jusqu'à 65", "quatre fois par jour", "chaque mardi", "entre 11h et 12h"):
            assert bad not in text, (path, bad)


class TestColdStartProxy:
    """main.py : pendant le chargement de FastAPI, jamais de template Jinja brut en 200."""

    def _call(self, path):
        import asyncio
        import main
        msgs = []

        async def send(m):
            msgs.append(m)

        asyncio.run(main._serve_loading_response({"type": "http", "path": path}, send))
        return msgs[0]["status"], dict(msgs[0]["headers"]), msgs[1]["body"]

    def test_home_is_rendered_not_raw(self):
        status, _, body = self._call("/")
        assert status == 200
        assert b"{%" not in body and b"{{" not in body
        assert b"<h1" in body

    def test_other_pages_get_503_retry_after(self):
        for path in ("/calendrier", "/robots.txt", "/blog/un-article"):
            status, headers, _ = self._call(path)
            assert status == 503 and headers[b"retry-after"] == b"5"
