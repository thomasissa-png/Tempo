"""Non-régression des bugs trouvés par la QA de bout en bout du 2026-09-29.

Base SQLite temporaire (conftest.py). Numéros de téléphone = fixtures de test.
"""

import ast
import builtins
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import app as app_module
    app_module._db_ready.set()
    app_module._rate_limit_store.clear()
    app_module._admin_rate_limit_store.clear()
    monkeypatch.setattr("config.Config.ADMIN_PASSWORD", "qa-admin-pw")
    monkeypatch.setattr("config.Config.WHATSAPP_APP_SECRET", "")
    return TestClient(app_module.app)


@pytest.fixture
def sent(monkeypatch):
    """Capture les envois WhatsApp (aucun appel réseau)."""
    calls = []

    def fake_tpl(phone, name, components=None):
        calls.append((phone, name, components))
        return ("SIM_TEST", "simulated")

    monkeypatch.setattr("alerts.send_whatsapp_template", fake_tpl)
    return calls


def _register(phone="+33612345678"):
    from alerts import register_user
    res = register_user(phone, 70, 1, False, True, "matin")
    assert res.get("success"), res
    return res


# REGRESSION: /api/resend-manage-link levait NameError (get_db non importé
# dans app.py) : 500 pour tout le monde, lien « Déjà inscrit ? » cassé. Fixé le 2026-09-29.
def test_resend_manage_link_known_number_sends_link(client, sent):
    res = _register()
    r = client.post("/api/resend-manage-link", data={"phone": "+33612345678"})
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
    assert len(sent) == 1
    assert sent[0][0] == "+33612345678"
    assert res["manage_token"] in json.dumps(sent[0][2])


def test_resend_manage_link_unknown_number_same_generic_answer(client, sent):
    _register()
    known = client.post("/api/resend-manage-link", data={"phone": "+33612345678"})
    unknown = client.post("/api/resend-manage-link", data={"phone": "+33699999999"})
    assert unknown.status_code == 200, unknown.text
    # Anti-énumération : même réponse, aucun envoi pour le numéro inconnu
    assert unknown.json() == known.json()
    assert [c[0] for c in sent] == ["+33612345678"]


def test_resend_manage_link_inactive_user_no_send(client, sent):
    from alerts import unsubscribe_user
    _register()
    unsubscribe_user("+33612345678")
    r = client.post("/api/resend-manage-link", data={"phone": "+33612345678"})
    assert r.status_code == 200
    assert sent == []


# REGRESSION: le webhook WhatsApp (statut « failed ») avalait un NameError :
# sms_logs jamais marqué en échec, abonnés 131026/131047 jamais désactivés. Fixé le 2026-09-29.
@pytest.mark.parametrize("code,deactivated", [(131026, True), (131047, True), (131000, False)])
def test_webhook_failed_status_marks_log_and_deactivates(client, code, deactivated):
    from database import get_db
    res = _register()
    conn = get_db()
    conn.execute(
        "INSERT INTO sms_logs (user_id, type_alerte, couleur, message_body, date_envoi, statut, "
        "whatsapp_msg_id, erreur, date_cible) VALUES (?, 'prediction_rouge', 'ROUGE', 'm', "
        "'2026-01-12 18:00:00', 'sent', 'wamid.QA1', '', '2026-01-13')", (res["user_id"],))
    conn.commit()
    conn.close()
    payload = {"entry": [{"changes": [{"value": {"statuses": [{
        "id": "wamid.QA1", "status": "failed", "recipient_id": "33612345678",
        "errors": [{"code": code, "title": "test"}]}]}}]}]}
    r = client.post("/api/webhook/whatsapp", content=json.dumps(payload),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    conn = get_db()
    log = conn.execute("SELECT statut, erreur FROM sms_logs WHERE whatsapp_msg_id = 'wamid.QA1'").fetchone()
    user = conn.execute("SELECT actif FROM users WHERE id = ?", (res["user_id"],)).fetchone()
    conn.close()
    assert log["statut"] == "failed"
    assert str(code) in log["erreur"]
    assert user["actif"] == (0 if deactivated else 1)


# REGRESSION: /admin/whatsapp-diagnostic renvoyait toujours recent_errors vide
# (NameError avalé). Fixé le 2026-09-29.
def test_whatsapp_diagnostic_reports_last_error(client):
    from database import get_db
    res = _register()
    conn = get_db()
    conn.execute(
        "INSERT INTO sms_logs (user_id, type_alerte, couleur, message_body, date_envoi, statut, erreur) "
        "VALUES (?, 'prediction_rouge', 'ROUGE', 'm', '2026-01-12 18:00:00', 'error: 132018', '')",
        (res["user_id"],))
    conn.commit()
    conn.close()
    r = client.get("/admin/whatsapp-diagnostic", headers={"Authorization": "Bearer qa-admin-pw"})
    assert r.status_code == 200
    assert r.json()["recent_errors"]["last_error"]["statut"] == "error: 132018"


def _unresolved_names(path: Path) -> list[str]:
    """Noms lus dans une fonction sans être définis (module, fonction englobante, builtins)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node.col_offset == 0:
            module_names.update((a.asname or a.name).split(".")[0] for a in node.names)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_names.add(node.name)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store) and node in tree.body \
                    and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                module_names.add(sub.id)
            if isinstance(sub, (ast.Import, ast.ImportFrom)) and isinstance(node, (ast.Try, ast.If)):
                module_names.update((a.asname or a.name).split(".")[0] for a in sub.names)
    problems = []
    for fn in [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        local = set()
        for sub in ast.walk(fn):
            if isinstance(sub, (ast.Import, ast.ImportFrom)):
                local.update((a.asname or a.name).split(".")[0] for a in sub.names)
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                local.add(sub.id)
            elif isinstance(sub, ast.arg):
                local.add(sub.arg)
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local.add(sub.name)
            elif isinstance(sub, ast.ExceptHandler) and sub.name:
                local.add(sub.name)
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) \
                    and sub.id not in local | module_names and not hasattr(builtins, sub.id) \
                    and sub.id != "__file__":
                problems.append(f"{path.name}:{sub.lineno} {fn.name} -> {sub.id}")
    return problems


# REGRESSION: garde-fou statique contre les NameError latents (import local oublié).
@pytest.mark.parametrize("module", ["app.py", "scheduler.py", "alerts.py", "performance_tracker.py",
                                    "prediction_history.py", "db_sync.py", "tempo_client.py", "main.py"])
def test_no_unresolved_global_names(module):
    assert _unresolved_names(ROOT / module) == []


# REGRESSION: tirets cadratins dans le client-facing (page de gestion, manifest,
# libellés JS du dashboard, messages WhatsApp), règle 12 de CLAUDE.md. Fixé le 2026-09-29.
def _visible(html: str) -> str:
    import re
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    return re.sub(r"<script(?![^>]*ld\+json)[^>]*>.*?</script>|<style.*?</style>", "", html, flags=re.S)


def test_no_em_dash_manage_page_and_manifest(client):
    res = _register()
    page = client.get(f"/manage/{res['manage_token']}")
    assert page.status_code == 200
    assert "—" not in _visible(page.text) and "&mdash;" not in page.text
    assert "—" not in client.get("/manifest.json").text


@pytest.mark.parametrize("path", ["static/js/app.js", "static/js/app.min.js"])
def test_no_em_dash_in_js_string_literals(path):
    import re
    src = (ROOT / path).read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))
    assert "—" not in src and "\\u2014" not in src


def test_no_em_dash_in_whatsapp_messages():
    from datetime import date
    import alerts
    pred = {"couleur_predite": "ROUGE", "probabilite_rouge": 0.8, "probabilite_blanc": 0.1,
            "probabilite_bleu": 0.1, "temp_min_prevue": -3.0, "temp_moy_prevue": -1.0, "raison": "Froid"}
    d = date(2026, 1, 13)
    week = [{"date": f"2026-01-{12 + i:02d}", "couleur_predite": "BLEU", "probabilite_rouge": 0.0,
             "probabilite_blanc": 0.0, "probabilite_bleu": 1.0} for i in range(7)]
    msgs = [alerts.format_alert_rouge(d, pred, "tok"), alerts.format_alert_blanc(d, pred, "tok"),
            alerts.format_alert_officiel(d, "ROUGE", "tok"), alerts.format_alert_officiel(d, "BLANC", "tok"),
            alerts.format_recap_hebdo(week, "tok")]
    for m in msgs:
        assert "—" not in m, m


# REGRESSION: /api/unsubscribe hachait le numéro brut (points/tirets conservés) :
# « +33 6.12-34.56.78 » validé mais jamais retrouvé. Fixé le 2026-09-29.
def test_unsubscribe_accepts_dotted_number(client):
    from database import get_db
    res = _register()
    r = client.post("/api/unsubscribe", data={"phone": "+33 6.12-34.56.78"})
    assert r.status_code == 200, r.text
    conn = get_db()
    actif = conn.execute("SELECT actif FROM users WHERE id = ?", (res["user_id"],)).fetchone()["actif"]
    conn.close()
    assert actif == 0


# REGRESSION: /api/performance : saison par défaut figée à 2025-2026 et 500 sur
# une saison mal formée. Fixé le 2026-09-29.
def test_api_performance_default_season_is_current(client):
    from performance_tracker import current_season
    r = client.get("/api/performance", headers={"Authorization": "Bearer qa-admin-pw"})
    assert r.status_code == 200
    assert r.json()["season"] == current_season()


@pytest.mark.parametrize("bad", ["abc", "<script>", "2025", "2025-26"])
def test_api_performance_rejects_malformed_season(client, bad):
    r = client.get("/api/performance", params={"season": bad},
                   headers={"Authorization": "Bearer qa-admin-pw"})
    assert r.status_code == 400


# REGRESSION: /calendrier/0000-01 et /calendrier/0000-0001 renvoyaient 500
# (ValueError: year 0 is out of range) au lieu de 404. Fixé le 2026-09-29.
@pytest.mark.parametrize("path", ["/calendrier/0000-01", "/calendrier/0000-0001"])
def test_calendar_year_zero_is_404(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/", "/alertes", "/api-tempo", "/calendrier", "/couleur-tempo-demain",
                                  "/tarif-tempo-edf", "/methodologie", "/a-propos", "/mentions-legales"])
def test_no_em_dash_visible_on_public_pages(client, path):
    import html as _html
    r = client.get(path)
    assert r.status_code == 200
    visible = _html.unescape(_visible(r.text))
    assert "—" not in visible, visible[max(0, visible.find("—") - 80): visible.find("—") + 40]


# REGRESSION: l'aperçu WhatsApp du modal d'inscription affichait « — » et des jours
# rouges en octobre (impossible, règle EDF R1). Fixé le 2026-09-29 (vérifié en jsdom).
def test_subscribe_modal_preview_respects_r1_and_no_em_dash():
    src = (ROOT / "templates" / "_subscribe_modal.html").read_text(encoding="utf-8")
    js = "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
    assert "\\u2014" not in js and "—" not in js.split("<script")[1]
    assert "d.getMonth() >= 3 && d.getMonth() <= 9" in src  # semaine d'exemple ramenée en décembre


# REGRESSION: en production (proxy main.py) le lifespan FastAPI ne tourne pas :
# la source du mot de passe admin n'était jamais loggée. Fixé le 2026-09-29.
def test_main_proxy_logs_admin_password_source():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "[Admin] Mot de passe source" in src


# REGRESSION: /api/unsubscribe révélait si un numéro était inscrit (200 vs 400).
# Fixé le 2026-09-29 : réponse identique dans les deux cas.
def test_unsubscribe_does_not_reveal_membership(client):
    _register()
    known = client.post("/api/unsubscribe", data={"phone": "+33612345678"})
    unknown = client.post("/api/unsubscribe", data={"phone": "+33699999999"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


# REGRESSION: les alertes « changement » vers ROUGE contournaient la règle
# calibrée (rouge_alert_due). Fixé le 2026-09-29 : un abonné jamais alerté sur
# cette date n'est prévenu d'un passage à ROUGE que si la règle l'autorise.
def test_change_alert_to_rouge_respects_calibrated_rule(monkeypatch):
    import alerts
    from datetime import date, timedelta
    res = _register()
    target = date.today() + timedelta(days=1)  # dans la fenêtre (délai 1 jour)
    sent = []
    monkeypatch.setattr(alerts, "_is_red_season", lambda: True)
    monkeypatch.setattr(alerts, "send_whatsapp_template",
                        lambda phone, name, comps=None: (sent.append(phone) or ("SIM", "simulated")))
    low = {"couleur_predite": "ROUGE", "probabilite_rouge": 0.30,
           "probabilite_blanc": 0.35, "probabilite_bleu": 0.35}
    monkeypatch.setattr(alerts, "rouge_alert_due", lambda seuil, pred, delta=None: False)
    alerts.send_change_alerts(target, "BLEU", "ROUGE", low)
    assert sent == []
    monkeypatch.setattr(alerts, "rouge_alert_due", lambda seuil, pred, delta=None: True)
    alerts.send_change_alerts(target, "BLEU", "ROUGE", low)
    assert len(sent) == 1
