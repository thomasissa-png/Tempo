"""Fuites de notes internes dans le HTML servi + valorisation GEO de l'historique (2026-10-01).

A) Aucun commentaire HTML (« <!-- ») ni note interne (honeypot, Jean-Pierre hors adresse de
   contact, fondateur, TODO, À CONFIRMER) dans les pages publiques : les notes des gabarits
   sont des commentaires Jinja « {# #} », jamais envoyés.
B) M1-M7 de docs/audits/2026-10-01-geo-historique-differenciateur.md.

Base SQLite temporaire (conftest.py).
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CSV_URL = "https://www.calendrier-tempo.fr/historique-previsions.csv"
CONTACT = "jean-pierre@calendrier-tempo.fr"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import app as app_module
    app_module._db_ready.set()
    app_module._rate_limit_store.clear()
    return TestClient(app_module.app)


def _public_paths() -> list[str]:
    from blog import get_published_articles
    from prediction_history import today_paris
    paths = [
        "/", "/calendrier", f"/calendrier/{today_paris().strftime('%Y-%m')}",
        "/couleur-tempo-demain", "/tarif-tempo-edf", "/historique-previsions", "/methodologie",
        "/api-tempo", "/blog/", "/alertes", "/a-propos", "/mentions-legales",
    ]
    articles = get_published_articles()
    assert articles, "aucun article publié : le test doit rendre au moins un article"
    paths.append(f"/blog/{articles[0].slug}")
    return paths


def _ld_blocks(html: str) -> list:
    return [json.loads(m) for m in
            re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, flags=re.S)]


def _meta_description(html: str) -> str:
    import html as html_mod
    m = re.search(r'<meta name="description" content="([^"]*)"', html)
    assert m, "meta description absente"
    return html_mod.unescape(m.group(1))


# ---------------------------------------------------------------- A) fuites

class TestNoInternalNotesServed:
    def test_public_pages_have_no_internal_notes(self, client):
        pages = {p: client.get(p) for p in _public_paths()}
        pages["404"] = client.get("/cette-page-n-existe-pas-xyz")
        assert pages["404"].status_code == 404
        for p, r in pages.items():
            if p != "404":
                assert r.status_code == 200, (p, r.status_code)
            html = r.text
            assert "<!--" not in html, f"commentaire HTML servi sur {p}"
            low = html.lower()
            for word in ("honeypot", "fondateur", "todo", "à confirmer", "&agrave; confirmer"):
                assert word not in low, f"« {word} » servi sur {p}"
            assert "jean-pierre" not in low.replace(CONTACT, ""), f"« Jean-Pierre » hors contact sur {p}"

    def test_templates_contain_no_html_comment(self):
        for f in sorted((ROOT / "templates").glob("*.html")):
            assert "<!--" not in f.read_text(encoding="utf-8"), f.name

    def test_python_html_strings_contain_no_html_comment(self):
        for name in ("app.py", "site_facts.py", "prediction_history.py", "blog.py"):
            assert "<!--" not in (ROOT / name).read_text(encoding="utf-8"), name

    def test_honeypot_field_kept_but_not_explained(self, client):
        html = client.get("/").text
        assert 'name="website"' in html  # le piège reste en place
        assert "bots" not in html.lower().replace("robots", "")


# ---------------------------------------------------------------- B) GEO historique

class TestLlmsHistory:
    @pytest.mark.parametrize("path", ["/llms.txt", "/llms-full.txt"])
    def test_csv_url_and_history_section(self, client, path):
        txt = client.get(path).text
        assert CSV_URL in txt
        assert "## Historique public des prévisions" in txt
        assert "y compris les erreurs" in txt

    @pytest.mark.parametrize("path", ["/llms.txt", "/llms-full.txt"])
    def test_no_superlative(self, client, path):
        """Jamais « seul » ni « unique » pour qualifier le service (texte éditorial, hors articles).

        Seule exception : la phrase factuelle « Seule la couleur publiée/annoncée par EDF fait foi »."""
        txt = client.get(path).text
        own = re.sub(r"Seule la couleur (publiée|annoncée) par EDF fait foi", "", txt.split("## Articles du blog")[0])
        hit = re.search(r".{0,60}\b(seul|seule|seuls|seules|unique|uniques)\b.{0,60}", own, flags=re.I)
        assert hit is None, hit.group(0)
        assert not re.search(r"\b(le|la|les) seul(e|s|es)? (service|site|outil)", txt, flags=re.I)

    def test_llms_head_mentions_history_and_page_lines(self, client):
        txt = client.get("/llms.txt").text
        head = txt.split("\n\n", 2)[1]
        assert "Historique public et vérifiable" in head and CSV_URL in head
        assert "faites 1 à 15 jours avant" in txt and "faites 2 à 5 jours avant" not in txt
        assert f"[Export CSV de l'historique]({CSV_URL})" in txt
        # la ligne feed.xml reste dans la liste des API
        api = txt[txt.index("## API publiques"):]
        assert "/feed.xml" in api.split("\n\n")[0]

    def test_rate_line_omitted_without_rate(self, client, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_history_data", lambda: {"seasons": {}, "last_evaluation": None})
        assert "Taux 2 à 5 jours avant" not in app_module._llms_history_section()

    def test_rate_line_present_with_rate(self, monkeypatch):
        import app as app_module
        import prediction_history
        ref = {"label": "2025-2026", "justes": 300, "emises": 400, "justes_pct": 75, "toujours_bleu_pct": 70}
        monkeypatch.setattr(prediction_history, "last_complete_season_summary", lambda d, t=None: ref)
        out = app_module._llms_history_section()
        assert "saison 2025-2026 (dernière saison complète) : 300 prévisions justes sur 400 émises (75 %)" in out
        assert "« dire bleu tous les jours » : 70 %" in out

    def test_dates_bumped(self):
        import site_facts
        from datetime import date
        assert site_facts.LLMS_CONTENT_DATE == date(2026, 10, 1)
        assert site_facts.PAGE_LASTMOD["/historique-previsions"] == date(2026, 10, 1)
        assert site_facts.PAGE_LASTMOD["/methodologie"] == date(2026, 10, 1)


class TestHistoryJsonLdAndMeta:
    def test_dataset_valid_with_id_no_license(self, client):
        html = client.get("/historique-previsions").text
        ds = [b for b in _ld_blocks(html) if b.get("@type") == "Dataset"]
        assert len(ds) == 1
        d = ds[0]
        assert d["@id"] == "https://www.calendrier-tempo.fr/historique-previsions#dataset"
        assert "erreurs comprises" in d["description"]
        assert d["publisher"]["@id"] == d["creator"]["@id"] == "https://www.calendrier-tempo.fr/#organization"
        assert d["distribution"][0]["contentUrl"] == CSV_URL and d["distribution"][0]["name"]
        assert d["keywords"] and d["measurementTechnique"] and d["isBasedOn"].startswith("https://")
        assert "license" not in d

    @pytest.mark.parametrize("path", ["/historique-previsions", "/methodologie"])
    def test_meta_description_max_155(self, client, path):
        html = client.get(path).text
        desc = _meta_description(html)
        assert len(desc) <= 155, (len(desc), desc)
        assert "Tempo EDF" in desc
        og = re.search(r'<meta property="og:description" content="([^"]*)"', html).group(1)
        assert og == re.search(r'<meta name="description" content="([^"]*)"', html).group(1)

    def test_methodologie_mentions_dataset(self, client):
        art = [b for b in _ld_blocks(client.get("/methodologie").text) if b.get("@type") == "TechArticle"][0]
        assert art["mentions"]["@id"] == "https://www.calendrier-tempo.fr/historique-previsions#dataset"

    def test_organization_description_single_source(self, client):
        import site_facts
        for path in ("/", "/a-propos"):
            orgs = [b for b in _ld_blocks(client.get(path).text) if b.get("@type") == "Organization"]
            assert orgs and all(o["description"] == site_facts.ORG_DESCRIPTION for o in orgs), path

    def test_faq_fiabilite_points_to_public_history_and_csv(self):
        import site_facts
        faq = next(it for it in site_facts.FAQ_HOME if it["question"] == "Vos prévisions sont-elles fiables ?")
        assert 'href="/historique-previsions.csv"' in faq["answer_html"]
        assert "historique public des prévisions" in faq["answer_html"]
        assert "y compris nos erreurs" in faq["answer_html"]
