"""Carte « Les 10 prochains jours » de l'accueil (passe du 2026-09-30).

- Aujourd'hui et Demain en grandes pastilles avec le nom de la couleur écrit.
- Une seule phrase SEO (SSR) pour aujourd'hui et demain, sans triple redite.
- Phrase « la suite » identique en Python (site_facts.week_outlook_html) et en JS
  (weekOutlookHtml), couvrant toute la période affichée, avec la règle EDF R1.
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


def _days(start: date, couleurs: list[str], heroes: int = 2) -> list[dict]:
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "couleur": c, "is_hero": i < heroes}
        for i, c in enumerate(couleurs)
    ]


def _text(fragment: str) -> str:
    txt = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", txt).strip()


# ---------------------------------------------------------------- phrase « la suite »

class TestWeekOutlook:
    def test_all_blue_before_november_mentions_r1(self):
        txt = week_outlook_html(_days(date(2026, 9, 30), ["BLEU"] * 10))
        assert txt == ("Aucun jour rouge ni blanc prévu d'ici le vendredi 9 octobre. "
                       "Aucun jour rouge possible avant le 1er novembre (règle EDF).")

    def test_counts_cover_whole_period_with_day_numbers(self):
        # 2027-01-04 = lundi ; J+2 mercredi 6 et jeudi 7 rouges, vendredi 8 blanc
        couleurs = ["BLEU", "BLEU", "ROUGE", "ROUGE", "BLANC", "BLEU", "BLEU", "BLEU", "BLEU", "BLEU"]
        txt = week_outlook_html(_days(date(2027, 1, 4), couleurs))
        assert txt == ("<strong>2 jours rouges</strong> (mercredi 6, jeudi 7) et "
                       "<strong>1 jour blanc</strong> (vendredi 8) prévus d'ici le mercredi 13 janvier. "
                       "<strong>Planifiez vos machines les jours bleus.</strong>")
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

    def test_after_march_31(self):
        txt = week_outlook_html(_days(date(2027, 4, 2), ["BLEU"] * 10))
        assert txt.endswith("Plus de jour rouge depuis le 31 mars : aucun avant le 1er novembre (règle EDF).")

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
    def test_single_seo_sentence_today_and_tomorrow(self, client):
        today = date.today()
        _insert_actual(today, "BLEU")
        _insert_actual(today + timedelta(days=1), "ROUGE")
        page = client.get("/").text
        txt = _text(_card(page))
        expected = (f"Couleur Tempo EDF aujourd'hui, {site_facts.fr_date(today)} : Bleu . "
                    f"Demain, {site_facts.fr_date(today + timedelta(days=1), with_year=False)} : "
                    "Rouge (couleurs officielles EDF).")
        assert expected in txt
        assert txt.count("Couleur Tempo EDF") == 1
        assert "confirmé par EDF" not in txt and "cette semaine" not in txt
        assert page.count("<h1") == 1

    def test_tomorrow_not_yet_published(self, client):
        _insert_actual(date.today(), "BLANC")
        txt = _text(_card(client.get("/").text))
        assert ": Blanc (couleur officielle EDF). Demain," in txt
        assert "pas encore publiée par EDF (vers 11 h)." in txt

    def test_hero_dots_with_colour_name(self, client):
        _insert_predictions()
        card = _card(client.get("/").text)
        assert card.count("week-dot-hero") == 2
        assert card.count('class="week-dot"') == 8
        assert '<span class="week-dot-color">Bleu</span>' in card
        assert '<span class="week-dot-color">Blanc</span>' in card
        # la lettre reste dans la pastille (daltoniens)
        assert re.search(r'week-dot-circle dot-blanc" [^>]*aria-hidden="true">B</div>', card)
        assert "Prévision 62&nbsp;%" in card
        assert 'id="week-outlook"' in card and "prévus d'ici le" in card
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
    end = page.index("<!-- Réponse directe") if "<!-- Réponse directe" in page else page.index("<!-- La suite")
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
