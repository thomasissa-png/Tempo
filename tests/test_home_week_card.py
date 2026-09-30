"""Carte « Les 10 prochains jours » de l'accueil (passe du 2026-09-30).

- Aujourd'hui et Demain en grandes pastilles avec le nom de la couleur écrit et la date
  courte visible (« mer. 30 sept. ») ; statut « Confirmé par EDF » ou « Prévu à 88 % ».
- Plus de paragraphe de redite sous les pastilles (copy deck du 2026-09-30, section 0).
- Phrase sous les pastilles (variantes V1 à V5) identique en Python
  (site_facts.week_outlook_html) et en JS (weekOutlookHtml), avec la règle EDF R1.
- Le rendu SSR des pastilles et le rendu JS (renderWeekSummary) produisent le même HTML.
"""

import html
import json
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import site_facts
from site_facts import week_outlook_html

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import app as app_module
    app_module._db_ready.set()
    return TestClient(app_module.app)


def _days(start: date, couleurs: list[str], heroes: int = 2, demain_publie: bool = True) -> list[dict]:
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "couleur": c, "is_hero": i < heroes,
         "is_tomorrow": i == 1 and heroes >= 2, "confirmed": i == 0 or (i == 1 and demain_publie)}
        for i, c in enumerate(couleurs)
    ]


def _text(fragment: str) -> str:
    txt = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", txt).strip()


# ---------------------------------------------------------------- phrase « la suite »

class TestWeekOutlook:
    def test_v1_all_blue_before_november_mentions_r1(self):
        txt = week_outlook_html(_days(date(2026, 9, 30), ["BLEU"] * 10))
        assert txt == ("Aucun jour rouge ni blanc prévu d'ici le 9 octobre. "
                       "Pas de rouge possible avant novembre (règle EDF).")
        # jamais « premier jour rouge possible le 1er novembre » (férié, dimanche en 2026)
        assert "1er novembre" not in txt

    def test_v2_all_blue_in_winter_has_no_rule(self):
        txt = week_outlook_html(_days(date(2026, 11, 30), ["BLEU"] * 10))
        assert txt == "Aucun jour rouge ni blanc prévu d'ici le 9 décembre."

    def test_v5_tomorrow_not_published(self):
        txt = week_outlook_html(_days(date(2026, 9, 30), ["BLEU"] * 10, demain_publie=False))
        assert txt == ("EDF publie la couleur de demain vers 11&nbsp;h : d'ici là, la pastille montre "
                       "notre prévision. Aucun jour rouge ni blanc prévu d'ici le 9 octobre. "
                       "Pas de rouge possible avant novembre (règle EDF).")

    def test_counts_cover_whole_period_with_day_numbers(self):
        # 2027-01-04 = lundi ; J+2 mercredi 6 et jeudi 7 rouges, vendredi 8 blanc
        couleurs = ["BLEU", "BLEU", "ROUGE", "ROUGE", "BLANC", "BLEU", "BLEU", "BLEU", "BLEU", "BLEU"]
        txt = week_outlook_html(_days(date(2027, 1, 4), couleurs))
        assert txt == ("<strong>2 jours rouges</strong> (mercredi 6, jeudi 7) et "
                       "<strong>1 jour blanc</strong> (vendredi 8) prévus d'ici le 13 janvier. "
                       "Planifiez vos machines les jours bleus.")
        assert "cette semaine" not in txt

    def test_singular_and_first_of_month(self):
        couleurs = ["BLEU", "BLEU", "BLANC"] + ["BLEU"] * 7
        txt = week_outlook_html(_days(date(2027, 1, 30), couleurs))
        assert "<strong>1 jour blanc</strong> (lundi 1er) prévu d'ici" in txt

    def test_hero_colour_not_repeated_and_prefix_ensuite(self):
        couleurs = ["BLEU", "ROUGE"] + ["BLEU"] * 8
        txt = week_outlook_html(_days(date(2027, 1, 4), couleurs))
        assert txt.startswith("Ensuite, aucun jour rouge ni blanc prévu")
        assert "mardi 5" not in txt  # demain (rouge) déjà affiché en grand

    def test_after_march_31_same_rule(self):
        txt = week_outlook_html(_days(date(2027, 4, 2), ["BLEU"] * 10))
        assert txt.endswith("Pas de rouge possible avant novembre (règle EDF).")

    def test_v4_hero_colour_in_summer_keeps_rule(self):
        txt = week_outlook_html(_days(date(2026, 10, 1), ["BLEU", "BLANC"] + ["BLEU"] * 8))
        assert txt == ("Ensuite, aucun jour rouge ni blanc prévu d'ici le 10 octobre. "
                       "Pas de rouge possible avant novembre (règle EDF).")

    def test_period_reaching_november_has_no_r1_sentence(self):
        txt = week_outlook_html(_days(date(2026, 10, 25), ["BLEU"] * 10))
        assert "règle EDF" not in txt

    def test_empty_when_only_heroes(self):
        assert week_outlook_html(_days(date(2027, 1, 4), ["BLEU", "BLEU"])) == ""
        assert week_outlook_html([]) == ""

    def test_no_em_dash(self):
        for start in (date(2026, 9, 30), date(2027, 1, 4), date(2027, 4, 2)):
            assert "—" not in week_outlook_html(_days(start, ["ROUGE", "BLANC"] + ["BLEU"] * 8))


# ---------------------------------------------------------------- rendu SSR de l'accueil

def _insert_actual(d: date, couleur: str):
    from database import get_db
    conn = get_db()
    conn.execute(
        "INSERT INTO actuals (date, couleur_reelle, synthetic, timestamp_confirmation) VALUES (?, ?, 0, ?)",
        (d.isoformat(), couleur, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


PREDS = [  # (décalage, couleur, confirmée, p_bleu, p_blanc, p_rouge)
    (0, "BLEU", 1, 0.97, 0.02, 0.01),
    (1, "BLANC", 0, 0.30, 0.62, 0.08),
    (2, "ROUGE", 0, 0.10, 0.19, 0.71),
    (3, "BLEU", 0, 0.93, 0.05, 0.02),
    (4, "BLEU", 0, 0.85, 0.10, 0.05),
    (5, "BLANC", 0, 0.30, 0.57, 0.13),
    (6, "BLEU", 0, 0.91, 0.06, 0.03),
    (7, "BLEU", 0, 0.88, 0.08, 0.04),
    (8, "BLEU", 0, 0.94, 0.04, 0.02),
    (9, "BLEU", 0, 0.92, 0.05, 0.03),
]


def _insert_predictions() -> list[dict]:
    from database import get_db
    today = date.today()
    conn = get_db()
    api_like = []
    for off, c, conf, pb, pw, pr in PREDS:
        d = (today + timedelta(days=off)).isoformat()
        conn.execute(
            "INSERT INTO predictions (date, couleur_predite, probabilite_bleu, probabilite_blanc, "
            "probabilite_rouge, score_risque, horizon, timestamp_prediction, confirmed) "
            "VALUES (?, ?, ?, ?, ?, 50, ?, ?, ?)",
            (d, c, pb, pw, pr, f"J-{off}", datetime.now().isoformat(), conf),
        )
        api_like.append({"date": d, "couleur_predite": c, "confirmed": bool(conf),
                         "probabilite_bleu": pb, "probabilite_blanc": pw, "probabilite_rouge": pr})
    conn.commit()
    conn.close()
    return api_like


def _card(page: str) -> str:
    return page[page.index('id="week-summary-card"'):page.index('<!-- Compteurs jours restants')]


class TestHomeCardSsr:
    def test_no_redundant_paragraph_under_dots(self, client):
        _insert_predictions()
        page = client.get("/").text
        card = _card(page)
        assert "week-summary-answer" not in page
        assert "couleurs officielles EDF" not in card
        assert "Calendrier de la saison" not in card  # même cible que le CTA
        assert "Voir les 15 prochains jours" in card
        assert page.count("<h1") == 1
        assert "Couleur Tempo EDF aujourd'hui, demain et pr" in page  # H1 inchangé (requête n°1)

    def test_hero_dots_show_short_date(self, client):
        _insert_predictions()
        card = _card(client.get("/").text)
        today = date.today()
        court = site_facts.fr_date_courte(today).replace("\u00a0", "&nbsp;")
        assert f'<time datetime="{today.isoformat()}">{court}</time>' in card
        assert "Confirmé par EDF" in card

    def test_hero_dots_with_colour_name(self, client):
        _insert_predictions()
        card = _card(client.get("/").text)
        assert card.count("week-dot-hero") == 2
        assert card.count('class="week-dot"') == 8
        assert '<span class="week-dot-color">Bleu</span>' in card
        assert '<span class="week-dot-color">Blanc</span>' in card
        # la lettre reste dans la pastille (daltoniens)
        assert re.search(r'week-dot-circle dot-blanc" [^>]*aria-hidden="true">B</div>', card)
        assert "Prévu à 62&nbsp;%" in card
        assert 'id="week-outlook"' in card and "prévus d'ici le" in card
        # demain (non publié) : la phrase V5 explique que la pastille montre notre prévision
        assert "EDF publie la couleur de demain vers 11&nbsp;h" in card
        assert "—" not in card

    def test_links_are_secondary_pills(self):
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        rule = re.search(r"\.week-summary-links a \{([^}]*)\}", css).group(1)
        assert "border-radius" in rule and "text-decoration: none" in rule and "min-height: 44px" in rule


# ---------------------------------------------------------------- parité SSR / JS

_NODE_RENDER = r"""
const fs = require('fs'), vm = require('vm');
const els = {};
const el = id => (els[id] = els[id] || { innerHTML: '', hidden: false, classList: { toggle() {} } });
const ctx = { console, document: { getElementById: el, addEventListener() {}, querySelector() { return null; },
                                   querySelectorAll() { return []; } },
              window: {}, navigator: {}, localStorage: { getItem() { return null; }, setItem() {} },
              setTimeout, clearTimeout, setInterval() {}, fetch() {} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
ctx.renderWeekSummary(JSON.parse(process.argv[3]));
process.stdout.write(JSON.stringify({ dots: el('week-summary').innerHTML, outlook: el('week-outlook').innerHTML }));
"""


def _norm(fragment: str) -> str:
    fragment = re.sub(r">\s+<", "><", fragment.strip())
    return re.sub(r"\s+", " ", fragment)


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_ssr_and_js_render_identical_html(client, tmp_path):
    preds = _insert_predictions()
    page = client.get("/").text
    start = page.index(">", page.index('<div id="week-summary"')) + 1
    end = page.index("<!-- Phrase sous les pastilles")
    ssr_dots = page[start:end].strip()
    assert ssr_dots.endswith("</div>")
    ssr_dots = ssr_dots[: -len("</div>")]
    ssr_outlook = re.search(r'<p class="week-summary-text" id="week-outlook"[^>]*>(.*?)</p>', page, re.S).group(1)

    script = tmp_path / "render.js"
    script.write_text(_NODE_RENDER, encoding="utf-8")
    out = subprocess.run(
        ["node", str(script), str(ROOT / "static" / "js" / "app.js"), json.dumps(preds)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    js = json.loads(out.stdout)
    assert _norm(js["dots"]) == _norm(ssr_dots)
    assert js["outlook"] == ssr_outlook
