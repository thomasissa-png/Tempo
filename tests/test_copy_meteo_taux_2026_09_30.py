"""Passe du 2026-09-30 : copy deck, météo des prévisions 15 jours, cases sans prévision
de l'historique, taux publics calculés sur les prévisions réellement émises.

Les jours construits ici sont des cas de test unitaires contrôlés (effectifs), pas des
données d'analyse : ils vérifient l'arithmétique et l'affichage, jamais une performance.
"""

import html
import json
import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

import prediction_history as ph
import site_facts

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def fresh_cache():
    ph.invalidate_history_cache()
    yield
    ph.invalidate_history_cache()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import app as app_module
    app_module._db_ready.set()
    app_module._rate_limit_store.clear()
    return TestClient(app_module.app)


# ---------------------------------------------------------------- données de test (effectifs)

def _season(label: str, justes: int, erronees: int, sans: int) -> dict:
    """Saison dont chaque délai 2 à 5 jours a la même répartition. Jours BLEU officiels ;
    une prévision erronée = BLANC, une absence = None."""
    y = int(label[:4])
    start = date(y, 9, 1)
    total = justes + erronees + sans
    assert total % 4 == 0 and justes % 4 == 0 and erronees % 4 == 0 and sans % 4 == 0
    n_days = total // 4
    days = []
    for i in range(n_days):
        k = i
        couleur_prevue = ("BLEU" if k < justes // 4 else "BLANC" if k < (justes + erronees) // 4 else None)
        cells = {n: ph.NA for n in ph.HORIZONS}
        for n in ph.RELIABLE:
            cells[n] = ({"couleur": couleur_prevue, "emise_le": "", "temp": None, "proba": None}
                        if couleur_prevue else None)
        days.append({"date": (start + timedelta(days=i)).isoformat(), "couleur": "BLEU",
                     "cells": cells, "causes": {}, "temp_observee": None, "confirme_le": ""})
    return {"label": label, "days": days, "first_date": days[0]["date"], "last_date": days[-1]["date"],
            "last_evaluation": days[-1]["date"],
            "horizons": [ph._horizon_stats(days, n) for n in ph.HORIZONS],
            "jours_sans_calcul": []}


def _data() -> dict:
    # Effectifs réels relevés sur l'export CSV de production du 2026-09-30
    # (prévisions faites 2 à 5 jours avant) : 2025-2026 = 640 justes, 74 erronées,
    # 64 sans prévision ; 2026-2027 = 94 justes, 0 erronée, 26 sans prévision.
    # Ramenés à des multiples de 4 pour ce gabarit : mêmes taux arrondis.
    return {"seasons": {"2025-2026": _season("2025-2026", 640, 72, 64),
                        "2026-2027": _season("2026-2027", 92, 0, 24)},
            "last_evaluation": None}


# ---------------------------------------------------------------- taux sur prévisions émises

class TestTauxSurPrevisionsEmises:
    def test_rate_excludes_days_without_prediction(self):
        b = ph._bref([h for h in _data()["seasons"]["2025-2026"]["horizons"] if h["fiable"]])
        assert (b["justes"], b["erronees"], b["sans_prevision"], b["emises"]) == (640, 72, 64, 712)
        assert b["justes_pct"] == 90  # 640 / 712, et non 640 / 776
        assert b["jours"] == 776

    def test_production_figures(self):
        # Chiffres exacts demandés par le fondateur : 640 / (640 + 74) = 90 % ; 94 / 94 = 100 %
        assert ph._pct(640, 640 + 74) == 90
        assert ph._pct(94, 94 + 0) == 100
        assert ph._pct(640, 640 + 74 + 64) == 82  # ancien calcul (sur les jours) : abandonné

    def test_blue_benchmark_on_same_days(self):
        b = ph._bref([h for h in _data()["seasons"]["2025-2026"]["horizons"] if h["fiable"]])
        assert b["toujours_bleu"] == 712 and b["toujours_bleu_pct"] == 100


class TestDerniereSaisonComplete:
    def test_season_completeness(self):
        assert ph.season_is_complete("2025-2026", date(2026, 9, 1))
        assert not ph.season_is_complete("2025-2026", date(2026, 8, 31))
        assert not ph.season_is_complete("2026-2027", date(2026, 9, 30))

    def test_reference_is_last_complete_season(self):
        ref = ph.last_complete_season_summary(_data(), date(2026, 9, 30))
        assert ref["label"] == "2025-2026" and ref["justes_pct"] == 90
        # un an plus tard, la saison 2026-2027 devient la référence
        ref = ph.last_complete_season_summary(_data(), date(2027, 9, 1))
        assert ref["label"] == "2026-2027"
        assert ph.last_complete_season_summary(_data(), date(2026, 8, 31)) is None


class TestPourcentageSaisonEnCours:
    def test_hidden_before_november(self):
        ctx = ph.page_context(_data(), "2026-2027", "2026-2027", today=date(2026, 10, 31))
        assert ctx["pct_visible"] is False
        b = ctx["h"]["bref"]
        assert b["justes_pct"] is None and b["toujours_bleu_pct"] is None
        assert (b["justes"], b["erronees"], b["sans_prevision"]) == (92, 0, 24)
        assert all(h["justes_pct"] is None for h in ctx["h"]["fiables"])
        # le taux de référence (dernière saison complète) est rappelé en tête
        assert ctx["reference"]["label"] == "2025-2026" and ctx["reference"]["justes_pct"] == 90

    def test_visible_from_november_first(self):
        ctx = ph.page_context(_data(), "2026-2027", "2026-2027", today=date(2026, 11, 1))
        assert ctx["pct_visible"] is True
        assert ctx["h"]["bref"]["justes_pct"] == 100

    def test_past_season_always_visible(self):
        ctx = ph.page_context(_data(), "2025-2026", "2026-2027", today=date(2026, 9, 30))
        assert ctx["pct_visible"] is True and ctx["reference"] is None
        assert ctx["h"]["bref"]["justes_pct"] == 90

    def test_page_explains_hidden_percentage(self, client, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_history_data", _data)
        monkeypatch.setattr(ph, "today_paris", lambda: date(2026, 9, 30))
        page = html.unescape(client.get("/historique-previsions").text).replace("\xa0", " ")
        assert "taux publiés à partir du 1er novembre" in page.replace("<sup>", "").replace("</sup>", "")
        # 2026-10-02 : plus de « 100 justes sur 100 » ni d'« erronée » pour la saison en cours
        assert "Repère" not in page and "dire « bleu »" not in page
        bref = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", page[page.index('id="en-bref"'):page.index("Légende complète")]))
        # Tour 1 (HIS-T3) : chiffre de la dernière saison complète en tête, en tuile
        assert "90 % de prévisions justes en 2025-2026, dernière saison complète" in bref
        assert "(100 %)" not in bref
        # Au-dessus de la grille : une seule ligne de chiffre clé, celui de la dernière saison complète
        keyline = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", re.search(r'<p class="history-keyline">(.*?)</p>', page, re.S).group(1)))
        assert keyline == "Saison 2025-2026, dernière saison complète : 90 % de prévisions justes, 2 à 5 jours avant. Détail"
        assert page.index('class="history-keyline"') < page.index('id="jour-par-jour"')


class TestTauxPublies:
    def test_badge_uses_last_complete_season(self, client, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_history_data", _data)
        monkeypatch.setattr(ph, "today_paris", lambda: date(2026, 9, 30))
        r = client.get("/api/performance/badge").json()
        assert r["saison"] == "2025-2026" and r["precision"] == 90
        assert (r["justes"], r["emises"], r["sans_prevision"]) == (640, 712, 64)
        assert "precision_30j" not in r
        assert "Saison 2025-2026" in r["label"] and "bleu" not in r["label"]

    def test_methodologie_and_home_faq(self, client, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "_history_data", _data)
        monkeypatch.setattr(ph, "today_paris", lambda: date(2026, 9, 30))
        meth = html.unescape(client.get("/methodologie").text).replace("\xa0", " ")
        assert "Saison 2025-2026 : 90 %" in meth
        assert "30 derniers jours" not in meth
        home = html.unescape(client.get("/").text).replace("\xa0", " ")
        faq = home[home.index('id="faq-precision-text"'):]
        faq = faq[:faq.index("</p>")]
        assert "Saison 2025-2026 (dernière saison complète)" in faq and "90 % de prévisions justes" in faq
        assert "toutes couleurs confondues" in faq and "bleu »" not in faq and "Repère" not in faq

    def test_no_flattering_qualifier_in_js(self):
        js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        for word in ("Excellente", "Très bonne", "En amélioration", "precision_30j"):
            assert word not in js, word


# ---------------------------------------------------------------- historique : cases sans prévision

def _pred(target: str, emitted: str, couleur: str, confirmed: int = 0, originale: str = "", temp=None):
    from database import get_db
    conn = get_db()
    n = (date.fromisoformat(target) - date.fromisoformat(emitted)).days
    conn.execute(
        "INSERT INTO predictions (date, couleur_predite, horizon, timestamp_prediction, simulated, "
        "cycle_id, confirmed, couleur_originale, temp_moy_prevue) VALUES (?, ?, ?, ?, 0, '', ?, ?, ?)",
        (target, couleur, f"J-{n}", emitted + "T18:00:00", confirmed, originale, temp))
    conn.commit()
    conn.close()


def _actual(d: str, couleur: str):
    from database import get_db
    conn = get_db()
    conn.execute("INSERT INTO actuals (date, couleur_reelle, synthetic, timestamp_confirmation) "
                 "VALUES (?, ?, 0, ?)", (d, couleur, d + "T11:00:00"))
    conn.commit()
    conn.close()


class TestCasesSansPrevision:
    def _case(self, monkeypatch):
        monkeypatch.setattr("config.Config.PREDICTION_START_DATE", "2026-02-01")
        monkeypatch.setattr(ph, "today_paris", lambda: date(2026, 2, 12))
        # Calculs de 18 h enregistrés le 1er, 2, 3, 5, 6... février ; rien le 4 (interruption).
        for e in ("2026-02-01", "2026-02-02", "2026-02-03", "2026-02-05", "2026-02-06",
                  "2026-02-07", "2026-02-08", "2026-02-09", "2026-02-10", "2026-02-11"):
            _pred("2026-02-20", e, "BLEU")  # une ligne réelle par jour d'émission
        _actual("2026-02-07", "BLEU")
        # 2026-02-07, veille (émise le 6) : EDF avait publié avant 18 h -> ligne officielle directe
        _pred("2026-02-07", "2026-02-06", "BLEU", confirmed=1, originale="")
        # 3 jours avant (émise le 4, jour sans calcul) : aucune ligne
        # 2 jours avant (émise le 5) : prévision réelle
        _pred("2026-02-07", "2026-02-05", "BLEU")

    def test_causes_distinguished(self, monkeypatch):
        self._case(monkeypatch)
        data = ph.get_history(force=True)
        s = data["seasons"]["2025-2026"]
        assert s["jours_sans_calcul"] == ["2026-02-04"]
        day = next(d for d in s["days"] if d["date"] == "2026-02-07")
        assert day["causes"][3] == ("interruption", "2026-02-04")
        assert day["causes"][1] == ("publiee", "2026-02-06")
        assert 2 not in day["causes"]
        # jamais de case remplie ni déduite des jours voisins
        assert day["cells"][1] is None and day["cells"][3] is None

    def test_grid_labels_and_gap_list(self, client, monkeypatch):
        self._case(monkeypatch)
        page = html.unescape(client.get("/historique-previsions/2025-2026").text)
        # Décision fondateur 2026-09-30 : plus aucune case vide quand la couleur officielle est connue
        assert "3 jours avant : Bleu, juste ; Couleur des prévisions voisines, calcul non enregistré ce jour-là" in page
        assert "La veille : Bleu, juste ; Couleur publiée par EDF avant notre calcul de 18 h" in page
        assert "Jours sans calcul de prévision (1)" in page
        assert "4 février 2026." in page
        grid = re.search(r'<table class="history-grid-table">.*?</table>', page, re.S).group(0)
        assert 'class="hg-none' not in grid

    def test_small_grey_dot_css(self):
        for f in ("style.css", "style.min.css"):
            css = (ROOT / "static" / "css" / f).read_text(encoding="utf-8")
            rule = re.search(r"\.hg-none\s*\{([^}]*)\}", css).group(1)
            assert "dashed" not in rule and "6px" in rule


# ---------------------------------------------------------------- météo des prévisions 15 jours

class TestMeteoPrevisions:
    def test_fr_temp_format(self):
        assert site_facts.fr_temp(14.06) == "14,1°"
        assert site_facts.fr_temp(-3.2) == "−3,2°"
        assert site_facts.fr_temp(-0.04) == "0,0°"
        assert site_facts.fr_temp(None) is None and site_facts.fr_temp("abc") is None
        assert site_facts.fr_temp(50) is None and site_facts.fr_temp(-31) is None

    @pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
    def test_js_format_identical_to_python(self, tmp_path):
        values = [14.06, -0.04, -3.25, 16.05, 0, 7.349, -12.35, 45, -30, None, "", "abc", 50, -31]
        script = tmp_path / "t.js"
        script.write_text(
            "const fs=require('fs'),vm=require('vm');const ctx={console,document:{getElementById(){return null},"
            "addEventListener(){},createElement(){return {appendChild(){},innerHTML:''}}},window:{}};"
            "vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),ctx);"
            "process.stdout.write(JSON.stringify(JSON.parse(process.argv[3]).map(v=>ctx.fmtTemp(v))));",
            encoding="utf-8")
        for src in ("app.js", "app.min.js"):
            out = subprocess.run(["node", str(script), str(ROOT / "static" / "js" / src), json.dumps(values)],
                                 capture_output=True, text=True, timeout=30, check=True)
            assert json.loads(out.stdout) == [site_facts.fr_temp(v) for v in values], src

    def test_api_predictions_exposes_mean_temperature(self, client):
        import app as app_module
        from database import get_db
        app_module._predictions_cache["data"] = None
        today = date.today()
        conn = get_db()
        for off, t in ((0, 16.14), (1, None), (2, 99.0)):
            conn.execute(
                "INSERT INTO predictions (date, couleur_predite, probabilite_bleu, probabilite_blanc, "
                "probabilite_rouge, score_risque, horizon, timestamp_prediction, temp_min_prevue, "
                "temp_max_prevue, temp_moy_prevue) VALUES (?, 'BLEU', 0.9, 0.05, 0.05, 10, ?, ?, 10, 20, ?)",
                ((today + timedelta(days=off)).isoformat(), f"J-{off}", today.isoformat() + "T18:00:00", t))
        conn.commit()
        conn.close()
        preds = client.get("/api/predictions").json()["predictions"]
        app_module._predictions_cache["data"] = None
        assert [p["temp_moy_prevue"] for p in preds[:3]] == [16.1, None, None]
        assert preds[0]["temp_min_prevue"] == 10  # champs existants conservés

    def test_cards_render_mean_temperature(self):
        js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        assert "function meteoHtml(" in js and "Météo indisponible" in js
        assert "temp_min_prevue)}° /" not in js  # plus de min/max sur les cartes
        assert 'class="fc-proba-bar" role="img"' in js
        assert "Sous chaque jour : température moyenne prévue en France, pondérée sur 9 villes." in js
        assert "Froid" not in re.findall(r"function meteoHtml\(.*?\n\}", js, re.S)[0]  # repère non livré

    def test_far_group_no_opacity(self):
        for f in ("style.css", "style.min.css"):
            css = (ROOT / "static" / "css" / f).read_text(encoding="utf-8")
            assert not re.search(r"\.forecast-grid-far\s*\{\s*opacity", css), f
            assert ".fc-compact" in css

    def test_api_doc_mentions_field(self, client):
        assert "temp_moy_prevue" in client.get("/api-tempo").text


# ---------------------------------------------------------------- désinscription internationale

_NODE_PHONE = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const js = src.slice(src.indexOf('<script>') + 8, src.lastIndexOf('</script>'));
const ctx = { window: {}, document: { addEventListener() {}, getElementById() { return null; } }, console };
vm.createContext(ctx);
vm.runInContext(js, ctx);
process.stdout.write(JSON.stringify(JSON.parse(process.argv[3]).map(v => ctx.window.normalizePhoneIntl(v))));
"""


class TestDesinscriptionInternationale:
    @pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
    def test_same_rule_as_subscription(self, tmp_path):
        script = tmp_path / "p.js"
        script.write_text(_NODE_PHONE, encoding="utf-8")
        cases = {"06 12 34 56 78": "+33612345678", "+32 470 12 34 56": "+32470123456",
                 "0470 12 34 56": "+32470123456", "+41 79 123 45 67": "+41791234567",
                 "+352 621 123 456": "+352621123456", "+49 151 23456789": "+4915123456789",
                 "12345": None}
        out = subprocess.run(["node", str(script), str(ROOT / "templates" / "_subscribe_modal.html"),
                              json.dumps(list(cases))], capture_output=True, text=True, timeout=30, check=True)
        assert json.loads(out.stdout) == list(cases.values())

    def test_all_unsubscribe_forms_use_shared_rule(self):
        for f in ("templates/alertes.html", "templates/legal.html", "static/js/app.js"):
            assert "normalizePhoneIntl" in (ROOT / f).read_text(encoding="utf-8"), f
        modal = (ROOT / "templates" / "_subscribe_modal.html").read_text(encoding="utf-8")
        assert "typeof normalizePhone === 'function'" not in modal  # l'inscription n'est plus bridée à +33

    @pytest.mark.parametrize("phone", ["+32470123456", "+41791234567", "+352621123456", "+4915123456789"])
    def test_backend_unsubscribes_foreign_number(self, client, phone):
        from alerts import register_user
        from database import get_db
        res = register_user(phone, 70, 1, False, True, "matin")
        assert res.get("success"), res
        r = client.post("/api/unsubscribe", data={"phone": phone})
        assert r.status_code == 200, r.text
        conn = get_db()
        actif = conn.execute("SELECT actif FROM users WHERE id = ?", (res["user_id"],)).fetchone()["actif"]
        conn.close()
        assert actif == 0


# ---------------------------------------------------------------- copy deck : promesses et messages

class TestCopyDeck:
    def test_no_daily_message_promise(self):
        for f in list((ROOT / "templates").glob("*.html")) + [ROOT / "site_facts.py"]:
            txt = html.unescape(f.read_text(encoding="utf-8")).replace("\xa0", " ")
            for bad in ("1 message par jour maximum", "une alerte par jour au maximum",
                        "Maximum 1 alerte par jour"):
                assert bad not in txt, (f.name, bad)
        legal = html.unescape((ROOT / "templates" / "legal.html").read_text(encoding="utf-8"))
        assert "au plus une alerte par jour concerné" in legal

    def test_alert_levels_labels(self):
        src = (ROOT / "templates" / "manage.html").read_text(encoding="utf-8")
        assert "Recommandé : tous les jours rouges que nous prévoyons" in src
        assert "Sélectif : seulement les rouges quasi certains" in src
        assert "Prudent :" not in src and "bon équilibre" not in src

    def test_summer_banner_is_true(self):
        src = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
        assert "La saison Tempo est termin" not in src and "reprennent le 1<sup>er</sup> septembre" not in src
        assert "les alertes d&eacute;marrent en novembre" in src

    def test_ml_training_period_is_verified(self):
        import pickle
        meta = pickle.load(open(ROOT / "ml_model.pkl", "rb"))["metadata"]
        assert meta["train_date_range"] == {"start": "2019-09-03", "end": "2026-02-11"}
        meth = (ROOT / "templates" / "methodologie.html").read_text(encoding="utf-8")
        assert "3 septembre 2019 au 11 f&eacute;vrier 2026" in meth

    def test_whatsapp_messages(self):
        import alerts
        d = date(2027, 1, 12)
        pred = {"probabilite_rouge": 0.84, "probabilite_blanc": 0.1, "temp_min_prevue": -3.0}
        officiel = alerts.format_alert_officiel(d, "ROUGE", "tok")
        assert "anticipé" not in officiel.lower() and "Confirmé par EDF" in officiel
        rouge = alerts.format_alert_rouge(d, pred, "tok")
        prix = site_facts.fr_price(site_facts.TARIFS["ROUGE"]["hp"])
        assert prix in rouge and "0,73€" not in rouge and "confiance" not in rouge
        week = [{"date": (date(2027, 6, 28) + timedelta(days=i)).isoformat(),
                 "couleur_predite": "ROUGE" if i == 0 else "BLEU"} for i in range(7)]
        recap = alerts.format_recap_hebdo(week, "tok")
        assert "Jours rouges : lun." in recap and "Jours rouge :" not in recap
        assert "juin" in recap and "juil." in recap and "jui." not in recap
        src = (ROOT / "alerts.py").read_text(encoding="utf-8")
        assert "0,73" not in src and "0,19€" not in src

    def test_welcome_off_season(self, monkeypatch):
        import alerts
        monkeypatch.setattr(alerts, "_is_red_season", lambda d=None: False)
        assert "Les alertes démarrent le 1er novembre." in alerts.format_welcome([], "tok")
        monkeypatch.setattr(alerts, "_is_red_season", lambda d=None: True)
        assert "démarrent" not in alerts.format_welcome([], "tok")

    def test_no_em_dash_in_changed_client_facing_text(self, client):
        for path in ("/", "/calendrier", "/historique-previsions", "/methodologie", "/alertes",
                     "/couleur-tempo-demain", "/a-propos", "/mentions-legales"):
            body = client.get(path).text
            body = re.sub(r"<script.*?</script>|<!--.*?-->", "", body, flags=re.S)
            assert "—" not in body, path
