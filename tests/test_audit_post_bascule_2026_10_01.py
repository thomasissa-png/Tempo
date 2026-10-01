"""Corrections des audits SEO et GEO post-bascule Cloudflare (2026-10-01).

Un groupe de tests par point du brief :
1. robots.txt : endpoints API publics autorisés aussi dans le groupe « * ».
2. /methodologie : l'unité des chiffres rouges/blancs est « prévisions », pas « jours ».
3. Article jours-rouges-tempo-guide : villes météo = Config.WEATHER_CITIES.
4. Article alerte-jour-rouge-tempo : aligné sur site_facts.PERFORMANCE_POLICY.
5. FAQ en texte (llms, JSON-LD) : énumération lisible, pas de « en haut de cette page ».
6. llms-full.txt et feed.xml : liens absolus.
7. Organization JSON-LD : un @id unique et stable.
8. Date du jour = date de Paris (conteneur en UTC).
9. Slash final en 301, /blog -> /blog/, meta /alertes <= 155, cache des assets versionnés.
"""

import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import pytest

import site_facts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORG_ID = "https://www.calendrier-tempo.fr/#organization"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import app as app_module
    app_module._db_ready.set()
    return TestClient(app_module.app)


def _jsonld_blocks(page: str) -> list:
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    return [json.loads(b) for b in blocks]


def _walk(node):
    """Tous les dicts d'un JSON-LD (récursif)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 1. robots.txt

class TestRobotsPublicApi:
    def test_every_group_blocking_api_allows_public_endpoints(self, client):
        from app import PUBLIC_API_ENDPOINTS
        txt = client.get("/robots.txt").text
        groups = [g for g in txt.split("\n\n") if "User-agent:" in g]
        assert any("User-agent: *\n" in g for g in groups)
        for g in groups:
            if "Disallow: /api/" in g:
                for ep in PUBLIC_API_ENDPOINTS:
                    assert f"Allow: {ep}\n" in g + "\n", (ep, g)

    def test_star_group_reads_dataset_content_url_and_policy_unchanged(self, client):
        txt = client.get("/robots.txt").text
        star = next(g for g in txt.split("\n\n") if "User-agent: *\n" in g) + "\n"
        assert "Allow: /api/history\n" in star  # contentUrl du Dataset de /calendrier
        for rule in ("Disallow: /admin\n", "Disallow: /api/\n", "Disallow: /manage/\n"):
            assert rule in star
        assert "/api/indexnow" not in star
        assert "Crawl-delay" not in star
        assert "Disallow: /\n" not in txt


# ---------------------------------------------------------------- 2. méthodologie

class TestMethodologieUnit:
    def test_counts_labelled_as_predictions(self, client, monkeypatch):
        import prediction_history
        fake = {
            "label": "2025-2026", "justes_pct": 90, "justes": 640, "emises": 714, "erronees": 74,
            "jours": 778, "sans_prevision": 64, "jours_sans_calcul": 0,
            "toujours_bleu": 615, "toujours_bleu_pct": 86,
            "ROUGE": {"annonces": 22, "reels_emis": 50}, "BLANC": {"annonces": 16, "reels_emis": 49},
        }
        monkeypatch.setattr(prediction_history, "last_complete_season_summary", lambda *a, **k: fake)
        page = client.get("/methodologie").text
        assert "22 sur 50" in page and "16 sur 49" in page
        assert "jours rouges annonc&eacute;s &agrave; l'avance" not in page
        assert "jours blancs annonc&eacute;s &agrave; l'avance" not in page
        assert "Jours rouges annonc&eacute;s" not in page
        assert "pr&eacute;visions justes pour des jours rouges" in page
        assert "pr&eacute;visions justes pour des jours blancs" in page

    def test_template_has_no_day_unit_on_prediction_counts(self):
        tpl = _read("templates/methodologie.html")
        assert not re.search(r"reels_emis \}\}</div><div class=\"label\">jours", tpl)


# ---------------------------------------------------------------- 3. villes météo

class TestArticleCities:
    def test_guide_lists_the_config_cities(self):
        from config import Config
        body = _read("articles/jours-rouges-tempo-guide.md")
        assert "Nice" not in body
        m = re.search(r"9 grandes villes françaises[^(]*\(([^)]*)\)", body)
        assert m, "liste des 9 villes introuvable"
        cited = {c.strip() for c in m.group(1).split(",")}
        assert cited == {c["name"] for c in Config.WEATHER_CITIES}


# ---------------------------------------------------------------- 4. politique de performance

class TestArticlePerformancePolicy:
    def test_alert_article_does_not_claim_backtests_are_published(self):
        body = _read("articles/alerte-jour-rouge-tempo.md")
        assert "(test rétrospectif, limites) sont publiées" not in body
        assert "les tests rétrospectifs ne sont pas publiés" in body
        assert "Les tests rétrospectifs sur des données passées ne sont pas publiés" in site_facts.PERFORMANCE_POLICY


# ---------------------------------------------------------------- 5. FAQ en texte

def _faq(question_start: str) -> dict:
    return next(it for it in site_facts.FAQ_HOME if it["question"].startswith(question_start))


class TestFaqText:
    def test_red_day_list_is_a_readable_enumeration(self):
        text = site_facts.html_to_text(_faq("Que faire concrètement")["answer_html"])
        assert "reporter au lendemain ; Lave-vaisselle" in text
        assert "utiliser l'eau stockée. Astuce" in text
        assert text.count(" ; ") == 5
        assert "lendemain Lave-vaisselle" not in text

    def test_generic_list_conversion(self):
        out = site_facts.html_to_text("<p>Intro :</p><ul><li>Un.</li>\n<li><b>Deux</b></li></ul><p>Fin.</p>")
        assert out == "Intro : Un ; Deux. Fin."

    def test_no_page_relative_wording_in_home_faq(self):
        # (« cette page de Selectra » désigne la page externe : décision fondateur, non concernée)
        for it in site_facts.FAQ_HOME:
            text = site_facts.html_to_text(it["answer_html"])
            assert "en haut de cette page" not in text, it["question"]
            assert "de cette page" not in text.replace("cette page de Selectra", ""), it["question"]

    def test_llms_files_and_jsonld_use_the_same_text(self, client):
        expected = site_facts.html_to_text(_faq("Que faire concrètement")["answer_html"])
        for path in ("/llms.txt", "/llms-full.txt"):
            txt = client.get(path).text
            assert expected in txt, path
            assert "en haut de cette page" not in txt
        ld = next(b for b in _jsonld_blocks(client.get("/").text) if b.get("@type") == "FAQPage")
        answers = {q["name"]: q["acceptedAnswer"]["text"] for q in ld["mainEntity"]}
        assert answers["Que faire concrètement un jour rouge ?"] == expected


# ---------------------------------------------------------------- 6. liens absolus

class TestAbsoluteLinks:
    def test_helpers(self):
        u = site_facts.SITE_URL
        assert site_facts.absolute_md_links("[a](/calendrier) [b](https://x.fr/y) [c](/#subscribe)") == \
            f"[a]({u}/calendrier) [b](https://x.fr/y) [c]({u}/#subscribe)"
        assert site_facts.absolute_html_links('<a href="/blog/x">x</a><a href="//cdn.x/y">y</a>') == \
            f'<a href="{u}/blog/x">x</a><a href="//cdn.x/y">y</a>'

    def test_llms_full_has_no_relative_markdown_link(self, client):
        txt = client.get("/llms-full.txt").text
        assert "](/" not in txt
        assert f"]({site_facts.SITE_URL}/calendrier)" in txt

    def test_feed_has_no_relative_link(self, client):
        xml = client.get("/feed.xml").text
        assert 'href="/' not in xml and 'src="/' not in xml
        assert f'href="{site_facts.SITE_URL}/calendrier"' in xml


# ---------------------------------------------------------------- 7. Organization @id

class TestOrganizationId:
    def test_constant(self):
        assert site_facts.ORG_ID == ORG_ID

    @pytest.mark.parametrize("path", ["/", "/a-propos", "/calendrier", "/tarif-tempo-edf", "/methodologie",
                                      "/api-tempo", "/couleur-tempo-demain", "/historique-previsions"])
    def test_every_organization_has_the_stable_id(self, client, path):
        page = client.get(path).text
        orgs = [n for b in _jsonld_blocks(page) for n in _walk(b) if n.get("@type") == "Organization"]
        assert orgs, path
        assert {o.get("@id") for o in orgs} == {ORG_ID}, path

    def test_blog_article_organizations(self, client):
        from blog import get_published_articles
        a = get_published_articles()[0]
        page = client.get(f"/blog/{a.slug}").text
        orgs = [n for b in _jsonld_blocks(page) for n in _walk(b) if n.get("@type") == "Organization"]
        assert orgs and {o.get("@id") for o in orgs} == {ORG_ID}

    def test_no_invented_fields(self):
        for rel in ("templates/dashboard.html", "templates/a_propos.html"):
            assert '"sameAs"' not in _read(rel)


# ---------------------------------------------------------------- 8. date de Paris

class _FakeDatetime(datetime):
    """23 h 30 UTC le 30 septembre = 1 h 30 le 1er octobre à Paris."""
    @classmethod
    def now(cls, tz=None):
        base = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)
        return base.astimezone(tz) if tz else base.replace(tzinfo=None)


class TestParisDate:
    def test_today_paris_after_utc_midnight(self, monkeypatch):
        import app as app_module
        import tempo_client
        monkeypatch.setattr(app_module, "datetime", _FakeDatetime)
        monkeypatch.setattr(tempo_client, "datetime", _FakeDatetime)
        assert app_module._today_paris() == date(2026, 10, 1)
        assert tempo_client._today_paris() == date(2026, 10, 1)

    def test_home_ssr_uses_paris_date(self, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "datetime", _FakeDatetime)
        app_module._db_ready.clear()
        try:
            ssr = app_module._get_ssr_data()
        finally:
            app_module._db_ready.set()
        assert ssr["today_iso"] == "2026-10-01"
        assert ssr["tomorrow_iso"] == "2026-10-02"

    def test_calendar_uses_paris_date(self, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "datetime", _FakeDatetime)
        assert app_module._month_path(2026, 10) == "/calendrier"
        assert app_module._month_path(2026, 9) == "/calendrier/2026-09"

    def test_no_container_date_left_in_rendering_modules(self):
        for rel in ("app.py", "tempo_client.py"):
            src = _read(rel)
            code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')))
            assert "date.today()" not in code.replace("UTC : date.today()", ""), rel


# ---------------------------------------------------------------- 9. SEO P2

class TestSeoP2:
    @pytest.mark.parametrize("path,target", [
        ("/tarif-tempo-edf/", "/tarif-tempo-edf"),
        ("/alertes/", "/alertes"),
        ("/methodologie/", "/methodologie"),
        ("/a-propos/", "/a-propos"),
        ("/calendrier/", "/calendrier"),
    ])
    def test_trailing_slash_is_permanent(self, client, path, target):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 301, (path, r.status_code)
        assert r.headers["location"].endswith(target)

    def test_head_trailing_slash_is_permanent(self, client):
        assert client.head("/alertes/", follow_redirects=False).status_code == 301

    def test_blog_without_slash_redirects(self, client):
        r = client.get("/blog", follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == "/blog/"
        assert client.get("/blog/").status_code == 200

    def test_is_slash_redirect_helper(self):
        from app import _is_slash_redirect
        assert _is_slash_redirect("/alertes/", "https://www.calendrier-tempo.fr/alertes")
        assert _is_slash_redirect("/alertes/", "/alertes")
        assert not _is_slash_redirect("/alertes/", "/autre")
        assert not _is_slash_redirect("/alertes", "/alertes")

    def test_alertes_meta_description_length(self, client):
        page = client.get("/alertes").text
        m = re.search(r'<meta name="description" content="([^"]*)"', page)
        assert m
        desc = m.group(1)
        assert 70 <= len(desc) <= 155, len(desc)
        assert desc.startswith("Alertes Tempo EDF gratuites par WhatsApp")
        assert "7 jours" in desc and "jour rouge" in desc

    def test_versioned_assets_long_cache(self, client):
        v = site_facts.ASSET_VERSION
        r = client.get(f"/static/css/style.min.css?v={v}")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"

    def test_unversioned_assets_keep_current_rule(self, client):
        r = client.get("/static/css/style.min.css")
        assert r.headers["cache-control"] == "public, max-age=3600, stale-while-revalidate=86400"
        r = client.get("/static/css/style.min.css?v=")
        assert r.headers["cache-control"] == "public, max-age=3600, stale-while-revalidate=86400"


def test_historique_bref_parle_de_previsions_pas_de_jours():
    """Même correction que /methodologie : les chiffres cumulent une prévision par jour
    et par délai, ce sont des prévisions justes, pas des « jours annoncés »."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "templates" / "_historique_bref.html").read_text(encoding="utf-8")
    assert "annoncés à l'avance" not in src
    assert "prévisions justes pour des jours rouges" in src
    assert "prévisions justes pour des jours blancs" in src
