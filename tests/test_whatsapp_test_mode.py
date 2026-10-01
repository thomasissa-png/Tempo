"""Mode test WhatsApp (Config.WHATSAPP_TEST_NUMBERS), bascule Cloudflare.

Mode test = liste non vide : les diffusions (alertes, changements, confirmations,
récap hebdo) ne partent qu'aux numéros de test, les autres sont retenues
(sms_logs statut « held », aucun appel Meta). Les réponses à une action de la
personne (bienvenue, lien de gestion, bot) partent à tous.

Aucun appel réseau : le module httpx vu par alerts.py est remplacé par un faux
client Meta (sys.modules, pas httpx.Client : TestClient hérite de httpx.Client).
Numéros = fixtures de test.
"""

import ast
import json
import logging
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient  # importé avant tout patch de httpx

import alerts
from config import Config, _parse_phone_list
from database import get_db

ROOT = Path(__file__).resolve().parent.parent
TEST_PHONE = "+33612345678"   # numéro du fondateur (liste de test)
OTHER_PHONE = "+33698765432"  # abonné ordinaire


class _Resp:
    status_code = 200
    text = "{}"

    def json(self):
        return {"messages": [{"id": "wamid.TEST"}]}


@pytest.fixture
def meta(monkeypatch):
    """Faux endpoint Meta : enregistre chaque POST (destinataire sans « + », type)."""
    calls = []

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            calls.append((json["to"], json["type"]))
            return _Resp()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(Client=_Client))
    monkeypatch.setattr(Config, "WHATSAPP_TOKEN", "test-token")
    monkeypatch.setattr(Config, "WHATSAPP_PHONE_NUMBER_ID", "123")
    monkeypatch.setattr(alerts, "_is_red_season", lambda d=None: True)
    return calls


@pytest.fixture
def test_mode(monkeypatch):
    monkeypatch.setattr(Config, "WHATSAPP_TEST_NUMBERS", (TEST_PHONE,))


@pytest.fixture
def normal_mode(monkeypatch):
    monkeypatch.setattr(Config, "WHATSAPP_TEST_NUMBERS", ())


@pytest.fixture
def users():
    """Deux abonnés actifs : le numéro de test et un abonné ordinaire."""
    ids = {}
    for phone in (TEST_PHONE, OTHER_PHONE):
        res = alerts.register_user(phone, 70, 3, True, True, "matin")
        assert res.get("success"), res
        ids[phone] = res["user_id"]
    return ids


def _logs():
    conn = get_db()
    rows = conn.execute(
        "SELECT user_id, type_alerte, statut, whatsapp_msg_id FROM sms_logs ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _to(phone):
    return phone.lstrip("+")


def _rouge(target):
    return {"date": target.isoformat(), "couleur_predite": "ROUGE", "probabilite_rouge": 0.9,
            "probabilite_blanc": 0.05, "probabilite_bleu": 0.05, "temp_moy_prevue": 1.0}


# ---------------------------------------------------------------- configuration

class TestConfig:
    def test_parse_normalizes_like_register_user(self):
        raw = " +33 6 12 34 56 78, 33698765432,, +33.6-12-34-56-78 "
        assert _parse_phone_list(raw) == (TEST_PHONE, OTHER_PHONE)

    def test_parse_french_national_format(self):
        # Secret saisi « 06 12 34 56 78 » : doit valoir le numéro inscrit, pas « +0612... »
        assert _parse_phone_list("06 12 34 56 78") == (TEST_PHONE,)
        assert _parse_phone_list("0033612345678, 06.98.76.54.32") == (TEST_PHONE, OTHER_PHONE)

    def test_empty_means_normal_mode(self):
        assert _parse_phone_list("") == ()
        assert _parse_phone_list(" , ") == ()

    def test_allowed_recipient(self, test_mode):
        assert alerts._allowed_recipient(TEST_PHONE, alerts.KIND_BROADCAST)
        assert not alerts._allowed_recipient(OTHER_PHONE, alerts.KIND_BROADCAST)
        assert not alerts._allowed_recipient(OTHER_PHONE, alerts.KIND_TRANSACTIONAL)  # strict
        assert alerts._allowed_recipient(TEST_PHONE, alerts.KIND_TRANSACTIONAL)
        # Sans kind ou kind inconnu : diffusion (fail closed)
        assert not alerts._allowed_recipient(OTHER_PHONE)
        assert not alerts._allowed_recipient(OTHER_PHONE, "autre")


# ---------------------------------------------------------------- mode normal

class TestNormalMode:
    def test_broadcast_reaches_everyone(self, normal_mode, meta, users):
        assert not alerts.is_whatsapp_test_mode()
        alerts.send_weekly_recap([])
        assert sorted(c[0] for c in meta) == sorted([_to(TEST_PHONE), _to(OTHER_PHONE)])
        assert [log["statut"] for log in _logs()] == ["sent", "sent"]

    def test_no_test_mode_log(self, normal_mode, meta, users, caplog):
        with caplog.at_level(logging.INFO, logger="alerts"):
            alerts.send_weekly_recap([])
        assert "mode test" not in caplog.text


# ---------------------------------------------------------------- diffusions en mode test

class TestBroadcastHeld:
    def _check(self, meta, users, type_alerte):
        assert [c[0] for c in meta] == [_to(TEST_PHONE)]
        by_user = {log["user_id"]: log for log in _logs() if log["type_alerte"] == type_alerte}
        assert by_user[users[TEST_PHONE]]["statut"] == "sent"
        held = by_user[users[OTHER_PHONE]]
        assert held["statut"] == "held"
        assert held["whatsapp_msg_id"] == ""

    def test_weekly_recap(self, test_mode, meta, users, caplog):
        with caplog.at_level(logging.INFO, logger="alerts"):
            alerts.send_weekly_recap([])
        self._check(meta, users, "recap_hebdo")
        assert "1 envois retenus, 1 envoyés aux numéros de test" in caplog.text
        # Jamais de numéro complet dans les logs
        assert _to(OTHER_PHONE) not in caplog.text and _to(TEST_PHONE) not in caplog.text

    def test_prediction_alert(self, test_mode, meta, users, monkeypatch):
        monkeypatch.setattr(alerts, "rouge_alert_due", lambda seuil, pred, delta=None: True)
        target = date.today() + timedelta(days=2)
        alerts.send_alerts_for_prediction(target, _rouge(target), "matin")
        self._check(meta, users, "prediction_rouge")

    def test_change_alert(self, test_mode, meta, users, monkeypatch):
        monkeypatch.setattr(alerts, "rouge_alert_due", lambda seuil, pred, delta=None: True)
        target = date.today() + timedelta(days=2)
        alerts.send_change_alerts(target, "BLEU", "ROUGE", _rouge(target))
        self._check(meta, users, "changement")

    def test_official_alert(self, test_mode, meta, users):
        alerts.send_official_alerts(date.today() + timedelta(days=1), "BLANC")
        self._check(meta, users, "officiel")

    def test_call_without_kind_is_broadcast(self, test_mode, meta):
        assert alerts.send_whatsapp_template(OTHER_PHONE, "tpl") == ("", "held")
        assert alerts.send_whatsapp(OTHER_PHONE, "msg") == ("", "held")
        assert meta == []
        assert alerts.send_whatsapp_template(TEST_PHONE, "tpl")[1] == "sent"
        assert meta == [(_to(TEST_PHONE), "template")]

    def test_held_even_in_simulation(self, test_mode, monkeypatch):
        monkeypatch.setattr(Config, "WHATSAPP_TOKEN", "")
        assert alerts.send_whatsapp_template(OTHER_PHONE, "tpl")[1] == "held"
        assert alerts.send_whatsapp_template(TEST_PHONE, "tpl")[1] == "simulated"


# ---------------------------------------------------------------- transactionnel en mode test

@pytest.fixture
def client(monkeypatch):
    import app as app_module
    app_module._db_ready.set()
    app_module._rate_limit_store.clear()
    app_module._admin_rate_limit_store.clear()
    monkeypatch.setattr(Config, "ADMIN_PASSWORD", "qa-admin-pw")
    monkeypatch.setattr(Config, "WHATSAPP_APP_SECRET", "")
    return TestClient(app_module.app)


def _incoming(client, phone, body):
    payload = {"entry": [{"changes": [{"value": {"messages": [{
        "from": _to(phone), "type": "text", "text": {"body": body}}]}}]}]}
    r = client.post("/api/webhook/whatsapp", content=json.dumps(payload),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text


class TestTransactionalHeldInTestMode:
    """Décision fondateur du 2026-10-01 : en mode test, 0 message hors numéro de test,
    réponses automatiques comprises (bienvenue, lien de gestion, bot)."""
    # `client` avant `users` : importer app.py ne doit pas suivre l'inscription
    # (même ordre que tests/test_qa_e2e_regressions.py).
    def test_welcome(self, test_mode, meta):
        alerts.send_welcome(OTHER_PHONE, "tok")
        assert meta == []
        alerts.send_welcome(TEST_PHONE, "tok")
        assert meta == [(_to(TEST_PHONE), "template")]

    def test_resend_manage_link(self, test_mode, meta, client, users):
        r = client.post("/api/resend-manage-link", data={"phone": OTHER_PHONE})
        assert r.status_code == 200, r.text
        assert meta == []

    def test_bot_reply(self, test_mode, meta, client, users):
        _incoming(client, OTHER_PHONE, "RECAP")
        assert meta == []
        _incoming(client, TEST_PHONE, "RECAP")
        assert meta == [(_to(TEST_PHONE), "text")]

    def test_stop_always_unsubscribes(self, test_mode, meta, client, users):
        _incoming(client, OTHER_PHONE, "STOP")
        conn = get_db()
        actif = conn.execute("SELECT actif FROM users WHERE id = ?",
                             (users[OTHER_PHONE],)).fetchone()["actif"]
        conn.close()
        assert actif == 0  # la désinscription est appliquée
        assert meta == []  # mais aucun message ne part


# ---------------------------------------------------------------- admin

def test_diagnostic_reports_test_mode_without_numbers(test_mode, client):
    r = client.get("/admin/whatsapp-diagnostic", headers={"Authorization": "Bearer qa-admin-pw"})
    assert r.status_code == 200
    assert r.json()["test_mode"] is True
    assert r.json()["test_numbers_count"] == 1
    assert _to(TEST_PHONE)[-8:] not in r.text


def test_diagnostic_normal_mode(normal_mode, client):
    r = client.get("/admin/whatsapp-diagnostic", headers={"Authorization": "Bearer qa-admin-pw"})
    assert r.json()["test_mode"] is False
    assert r.json()["test_numbers_count"] == 0


# ---------------------------------------------------------------- garde-fou statique

# Seules ces fonctions ont le droit d'envoyer en « transactional ».
TRANSACTIONAL_ALLOWED = {"send_welcome", "_send_manage_link", "whatsapp_webhook_incoming"}
SEND_FUNCS = {"send_whatsapp", "send_whatsapp_template", "send_sms"}


@pytest.mark.parametrize("filename", ["alerts.py", "app.py", "scheduler.py"])
def test_every_send_call_declares_kind(filename):
    """Chaque appel d'envoi passe son kind explicitement ; « transactional »
    uniquement dans les réponses à une action de la personne."""
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    problems = []

    def visit(node, fn_name):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_name = node.name  # fonction englobante la plus proche
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in SEND_FUNCS):
            kinds = [kw.value for kw in node.keywords if kw.arg == "kind"]
            name = getattr(kinds[0], "id", None) if kinds else None
            if not kinds:
                problems.append(f"{fn_name}:{node.lineno} sans kind")
            elif name == "KIND_TRANSACTIONAL" and fn_name not in TRANSACTIONAL_ALLOWED:
                problems.append(f"{fn_name}:{node.lineno} transactional non autorisé")
            elif name not in ("KIND_TRANSACTIONAL", "KIND_BROADCAST"):
                problems.append(f"{fn_name}:{node.lineno} kind non constant")
        for child in ast.iter_child_nodes(node):
            visit(child, fn_name)

    visit(tree, "<module>")
    assert problems == []
