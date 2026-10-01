"""Carte « Résumé des 10 prochains jours » de l'accueil.

Retour à la mise en page de l'ancienne accueil (décision fondateur du 2026-10-01,
variante B : ancienne + mise en avant sobre d'aujourd'hui et demain) :
- accroche en une ligne, H1 discret, bandeau « Dernière mise à jour » ;
- une ligne de message en tête de carte, identique en Python (site_facts.week_outlook_html)
  et en JS (weekOutlookHtml) ;
- 10 pastilles dans une grille de 5 colonnes, 2 rangées séparées par un filet ;
  Aujourd'hui et Demain dans la même grille (couleur écrite, coche si officielle) ;
- un seul bouton, aligné à gauche sous les pastilles ;
- le rendu SSR et le rendu JS (renderWeekSummary) produisent le même HTML.
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


def _days(start: date, couleurs: list[str], today: bool = True) -> list[dict]:
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "couleur": c,
         "is_today": today and i == 0, "is_tomorrow": today and i == 1, "confirmed": i == 0}
        for i, c in enumerate(couleurs)
    ]


def _text(fragment: str) -> str:
    txt = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", txt).strip()


# ---------------------------------------------------------------- ligne de message

class TestWeekOutlook:
    def test_all_blue_good_news_single_line(self):
        txt = week_outlook_html(_days(date(2026, 10, 1), ["BLEU"] * 10))
        assert txt == ("Bonne nouvelle&nbsp;: <strong>aucun jour rouge ni blanc</strong> en vue "
                       "d'ici le samedi 10 octobre. Consommez normalement&nbsp;!")

    def test_no_extra_sentences(self):
        # ni règle du 1er novembre ni explication « EDF publie… » : une seule ligne
        for start in (date(2026, 9, 30), date(2026, 11, 30), date(2027, 4, 2)):
            txt = week_outlook_html(_days(start, ["BLEU"] * 10))
            assert "règle EDF" not in txt and "EDF publie" not in txt
            assert txt.count(". ") == 1

    def test_counts_cover_whole_period_with_day_names(self):
        # 2027-01-04 = lundi ; mercredi 6 et jeudi 7 rouges, vendredi 8 blanc
        couleurs = ["BLEU", "BLEU", "ROUGE", "ROUGE", "BLANC", "BLEU", "BLEU", "BLEU", "BLEU", "BLEU"]
        txt = week_outlook_html(_days(date(2027, 1, 4), couleurs))
        assert txt == ("<strong>2 jours rouges</strong> (mercredi 6, jeudi 7) et "
                       "<strong>1 jour blanc</strong> (vendredi 8) en vue d'ici le mercredi 13 janvier. "
                       "Planifiez vos machines les jours bleus.")

    def test_today_and_tomorrow_counted_and_named(self):
        couleurs = ["ROUGE", "BLANC"] + ["BLEU"] * 8
        txt = week_outlook_html(_days(date(2027, 1, 4), couleurs))
        assert txt.startswith("<strong>1 jour rouge</strong> (aujourd'hui) et "
                              "<strong>1 jour blanc</strong> (demain) en vue")

    def test_singular_and_first_of_month(self):
        couleurs = ["BLEU", "BLEU", "BLANC"] + ["BLEU"] * 7
        txt = week_outlook_html(_days(date(2027, 1, 30), couleurs))
        assert "<strong>1 jour blanc</strong> (lundi 1er) en vue d'ici" in txt

    def test_empty(self):
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
    return page[page.index('id="week-summary-card"'):page.index('<h2 class="section-title">O&ugrave; en est la saison')]


class TestHomeCardSsr:
    def test_top_of_page_like_old_home(self, client):
        _insert_predictions()
        page = client.get("/").text
        assert page.count("<h1") == 1
        assert "Couleur Tempo EDF aujourd'hui, demain et pr" in page  # H1 inchangé (requête n°1)
        top = page[page.index('class="home-title"'):page.index('id="week-summary-card"')]
        # ordre : H1 discret, accroche, bandeau mise à jour, titre de la carte
        assert top.index("welcome-banner") < top.index('id="last-update-bar"') < top.index('id="week-summary-title"')
        lead = _text(top[top.index("welcome-banner"):top.index('id="last-update-bar"')]).replace(" .", ".")
        ratio = site_facts.fr_num(site_facts.RATIO_ROUGE_BLEU_HP)
        assert (f"Un jour rouge coûte {ratio} fois plus cher en heures pleines qu'un jour bleu. "
                "Le Calendrier Tempo EDF vous alerte jusqu'à 15 jours à l'avance.") in lead
        assert "—" not in top

    def test_card_message_dots_then_single_left_button(self, client):
        _insert_predictions()
        card = _card(client.get("/").text)
        assert card.index('id="week-outlook"') < card.index('id="week-summary"') < card.index("btn-cta-summary")
        assert card.count('class="btn') == 1
        assert "Voir les prévisions détaillées des 15 prochains jours" in html.unescape(card)
        assert "week-summary-foot" not in card and "week-dots-hero" not in card
        assert "EDF publie la couleur de demain" not in card
        assert "Prévu à" not in card and "<time" not in card  # plus de dates sous les libellés
        assert "—" not in card

    def test_ten_dots_two_rows_of_five(self, client):
        _insert_predictions()
        card = _card(client.get("/").text)
        assert len(re.findall(r'class="week-dot[" ]', card)) == 10
        assert card.count('class="week-dots-separator"') == 1
        before_sep = card[:card.index("week-dots-separator")]
        assert len(re.findall(r'class="week-dot[" ]', before_sep)) == 5
        # libellés courts « Sam 3 » à partir d'après-demain
        j2 = date.today() + timedelta(days=2)
        assert f'<span class="week-dot-label" aria-hidden="true">{site_facts.week_dot_label(j2)}</span>' in card
        # pourcentage ou « Confirmé » sous chaque pastille
        assert card.count('class="week-dot-proba"') + card.count('class="week-dot-confirmed"') == 10

    def test_today_and_tomorrow_highlighted_in_same_grid(self, client):
        _insert_predictions()
        card = _card(client.get("/").text)
        assert card.count("week-dot-hero") == 2
        # aujourd'hui confirmé : coche + couleur écrite ; demain prévu : couleur + %
        assert '<span class="week-dot-confirmed">&#10003;&nbsp;Bleu</span>' in card
        assert '<span class="week-dot-color">Blanc</span> <span class="week-dot-proba">62&nbsp;%</span>' in card
        # la lettre reste dans la pastille (daltoniens)
        assert re.search(r'week-dot-circle dot-blanc" [^>]*aria-hidden="true">B</div>', card)

    def test_tomorrow_dot_is_the_only_link(self, client):
        _insert_predictions()
        card = _card(client.get("/").text)
        assert card.count('href="/couleur-tempo-demain"') == 1
        link = re.search(r'<a class="week-dot week-dot-highlight week-dot-hero week-dot-link" '
                         r'href="/couleur-tempo-demain" aria-label="([^"]+)">(.*?)</a>', card, re.S)
        assert link, "la pastille Demain doit être le lien"
        demain = date.today() + timedelta(days=1)
        assert link.group(1) == (f"Couleur Tempo de demain, {site_facts.fr_date(demain, with_year=False)} : "
                                 "Blanc, prévision 62&nbsp;%")
        assert "<strong>Demain</strong>" in link.group(2)

    def test_old_card_css(self):
        css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        for gone in (".week-dots-hero", ".week-summary-foot", ".week-summary-head", ".week-dot-date"):
            assert gone not in css, gone
        dots = re.search(r"\.week-summary-dots \{([^}]*)\}", css).group(1)
        assert "grid-template-columns: repeat(5, minmax(0, 1fr))" in dots
        confirmed = re.search(r"\.week-dot-confirmed \{([^}]*)\}", css).group(1)
        assert "background: #E8F5E9" in confirmed  # petit badge vert, comme avant
        bar = re.search(r"\.last-update-bar \{([^}]*)\}", css).group(1)
        assert "background" in bar and "text-align: center" in bar
        focus = re.search(r"a\.week-dot-link:focus-visible \{([^}]*)\}", css).group(1)
        assert "outline: 2px solid" in focus


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
    end = page.index('<a href="/calendrier" class="btn btn-cta-summary"')
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
    assert "week-dots-separator" in js["dots"]
