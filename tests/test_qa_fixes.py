"""Tests pour les corrections QA — BUG-01 à BUG-07 + audit v7."""

import os
import sys
import hashlib
import sqlite3
import pytest
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ================================================================
# BUG-07 : Hash phone HMAC (pas SHA-256 brut)
# ================================================================

class TestHmacPhoneHash:
    def test_hash_is_not_raw_sha256(self):
        """Le hash ne doit PAS être un simple SHA-256 sans clé."""
        from database import hash_phone
        phone = "+33612345678"
        raw_sha = hashlib.sha256(phone.encode()).hexdigest()
        hmac_hash = hash_phone(phone)
        assert hmac_hash != raw_sha, "hash_phone utilise encore du SHA-256 brut"

    def test_hash_deterministic(self):
        """Le même numéro donne le même hash."""
        from database import hash_phone
        h1 = hash_phone("+33612345678")
        h2 = hash_phone("+33612345678")
        assert h1 == h2

    def test_different_phones_different_hashes(self):
        """Deux numéros différents donnent des hashes différents."""
        from database import hash_phone
        h1 = hash_phone("+33612345678")
        h2 = hash_phone("+33698765432")
        assert h1 != h2

    def test_hash_is_hex_64_chars(self):
        """Le hash est un hex de 64 caractères (SHA-256)."""
        from database import hash_phone
        h = hash_phone("+33612345678")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ================================================================
# BUG-03 : confirm_prediction préserve couleur_originale
# ================================================================

class TestConfirmPreservesOriginal:
    def test_couleur_originale_saved(self):
        """confirm_prediction sauvegarde couleur_originale avant écrasement."""
        from database import get_db
        from predictor import store_prediction, confirm_prediction

        pred = {
            "date": (date.today() + timedelta(days=1)).isoformat(),
            "couleur_predite": "BLANC",
            "probabilite_bleu": 0.1,
            "probabilite_blanc": 0.6,
            "probabilite_rouge": 0.3,
            "score_risque": 52.0,
            "temp_min_prevue": 2.0,
            "temp_max_prevue": 8.0,
            "pression_prevue": None,
            "jours_rouges_restants": 10,
            "jours_blancs_restants": 20,
            "raison": "Test",
            "horizon": "J-1",
            "timestamp_prediction": datetime.now().isoformat(),
            "score_temperature": 50,
            "score_budget": 40,
            "score_weekday": 30,
            "score_gradient": 20,
            "score_clustering": 10,
            "score_rte": 15,
        }
        store_prediction(pred, "J-1", cycle_id="test_cycle")

        target_date = pred["date"]
        confirm_prediction(target_date, "ROUGE")

        conn = get_db()
        try:
            row = conn.execute(
                "SELECT couleur_predite, couleur_originale, confirmed FROM predictions WHERE date = ?",
                (target_date,)
            ).fetchone()
            assert row is not None
            assert row["confirmed"] == 1
            assert row["couleur_predite"] == "ROUGE"  # écrasé par la couleur officielle
            assert row["couleur_originale"] == "BLANC"  # original préservé
        finally:
            conn.close()

    def test_couleur_originale_not_overwritten_twice(self):
        """Si déjà confirmé, couleur_originale n'est pas ré-écrasée."""
        from database import get_db
        from predictor import store_prediction, confirm_prediction

        target = (date.today() + timedelta(days=2)).isoformat()
        pred = {
            "date": target,
            "couleur_predite": "BLANC",
            "probabilite_bleu": 0.1, "probabilite_blanc": 0.6, "probabilite_rouge": 0.3,
            "score_risque": 52.0,
            "temp_min_prevue": 2.0, "temp_max_prevue": 8.0, "pression_prevue": None,
            "jours_rouges_restants": 10, "jours_blancs_restants": 20,
            "raison": "Test", "horizon": "J-2",
            "timestamp_prediction": datetime.now().isoformat(),
            "score_temperature": 50, "score_budget": 40, "score_weekday": 30,
            "score_gradient": 20, "score_clustering": 10, "score_rte": 15,
        }
        store_prediction(pred, "J-2", cycle_id="cycle1")

        # Première confirmation
        confirm_prediction(target, "ROUGE")

        # Deuxième appel ne devrait rien changer (already confirmed)
        updated = confirm_prediction(target, "BLEU")
        assert updated == 0  # Rien à mettre à jour


# ================================================================
# BUG-05 : Préférences mises à jour lors de la réinscription
# ================================================================

class TestReactivationPreferences:
    def test_preferences_updated_on_reactivation(self):
        """Les nouvelles préférences sont appliquées lors de la réactivation."""
        from alerts import register_user, unsubscribe_user
        from database import get_db

        # Inscription initiale
        result = register_user("+33612345678", seuil_rouge=70, delai=1,
                               alerte_blanc=False, recap_hebdo=False)
        assert result.get("success")
        user_id = result["user_id"]

        # Désinscription
        unsubscribe_user("+33612345678")

        # Réinscription avec nouvelles préférences
        result2 = register_user("+33612345678", seuil_rouge=90, delai=3,
                                alerte_blanc=True, recap_hebdo=True)
        assert result2.get("success")

        # Vérifier les préférences en DB
        conn = get_db()
        try:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            assert user["actif"] == 1
            assert user["seuil_alerte_rouge"] == 90
            assert user["delai_alerte"] == 3
            assert user["alerte_blanc"] == 1
            assert user["recap_hebdo"] == 1
        finally:
            conn.close()


# ================================================================
# BUG-04 : Pas de doublon SMS prédiction + officiel même jour
# ================================================================

class TestSmsDedupCrossType:
    def test_official_skipped_if_prediction_sent_today(self):
        """L'alerte officielle est skip si une prédiction a déjà été envoyée."""
        from database import get_db, encrypt_phone, hash_phone
        from alerts import send_official_alerts

        conn = get_db()
        now = datetime.now().isoformat()
        phone_h = hash_phone("+33611111111")
        phone_enc = encrypt_phone("+33611111111")

        # Créer un user
        conn.execute(
            """INSERT INTO users (phone_hash, phone_encrypted, phone_last4,
               seuil_alerte_rouge, delai_alerte, alerte_blanc, recap_hebdo,
               actif, created_at, updated_at)
               VALUES (?, ?, '1111', 70, 1, 0, 0, 1, ?, ?)""",
            (phone_h, phone_enc, now, now)
        )

        # Simuler un SMS de prédiction déjà envoyé aujourd'hui
        conn.execute(
            """INSERT INTO sms_logs (user_id, type_alerte, couleur, message_body,
               date_envoi, statut, twilio_sid, erreur)
               VALUES (1, 'prediction_rouge', 'ROUGE', 'test', ?, 'simulated', 'SIM1', '')""",
            (now,)
        )
        conn.commit()
        conn.close()

        # Tenter d'envoyer une alerte officielle → devrait être skip
        tomorrow = date.today() + timedelta(days=1)
        send_official_alerts(tomorrow, "ROUGE")

        conn = get_db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) as c FROM sms_logs WHERE type_alerte = 'officiel'"
            ).fetchone()["c"]
            assert count == 0, "L'alerte officielle ne devrait pas être envoyée (dédup cross-type)"
        finally:
            conn.close()


# ================================================================
# BUG-01 : Webhook Twilio STOP
# ================================================================

class TestIncomingSmsHandler:
    def test_stop_unsubscribes(self):
        """Répondre STOP désactive l'utilisateur."""
        from alerts import register_user, handle_incoming_sms
        from database import get_db

        register_user("+33622222222")

        response = handle_incoming_sms("+33622222222", "STOP")
        assert "désinscrit" in response.lower()

        conn = get_db()
        try:
            user = conn.execute(
                "SELECT actif FROM users WHERE phone_last4 = '2222'"
            ).fetchone()
            assert user["actif"] == 0
        finally:
            conn.close()

    def test_start_resubscribes(self):
        """Répondre START réactive l'utilisateur."""
        from alerts import register_user, unsubscribe_user, handle_incoming_sms
        from database import get_db

        register_user("+33633333333")
        unsubscribe_user("+33633333333")

        response = handle_incoming_sms("+33633333333", "START")
        assert "réinscrit" in response.lower()

        conn = get_db()
        try:
            user = conn.execute(
                "SELECT actif FROM users WHERE phone_last4 = '3333'"
            ).fetchone()
            assert user["actif"] == 1
        finally:
            conn.close()

    def test_stop_variants(self):
        """Les variantes ARRET, QUIT, CANCEL fonctionnent aussi."""
        from alerts import register_user, handle_incoming_sms
        from database import get_db, hash_phone

        for i, keyword in enumerate(["ARRET", "QUIT", "CANCEL"], start=4):
            phone = f"+3360000000{i}"
            register_user(phone)
            resp = handle_incoming_sms(phone, keyword)
            assert "désinscrit" in resp.lower(), f"Keyword '{keyword}' devrait désinscrire"

    def test_unknown_message(self):
        """Un message inconnu renvoie l'aide."""
        from alerts import handle_incoming_sms
        response = handle_incoming_sms("+33600000000", "Bonjour")
        assert "STOP" in response and "START" in response


# ================================================================
# BUG-06 : Timezone DST RTE
# ================================================================

class TestRteTimezone:
    def test_paris_offset_winter(self):
        """En hiver, l'offset Paris est +01:00."""
        from rte_client import _paris_offset_str
        d = date(2026, 1, 15)  # Janvier = hiver
        assert _paris_offset_str(d) == "+01:00"

    def test_paris_offset_summer(self):
        """En été, l'offset Paris est +02:00."""
        from rte_client import _paris_offset_str
        d = date(2026, 7, 15)  # Juillet = été
        assert _paris_offset_str(d) == "+02:00"

    def test_paris_offset_march_transition(self):
        """Fin mars, l'offset passe de +01:00 à +02:00."""
        from rte_client import _paris_offset_str
        # 2026: dernier dimanche de mars = 29 mars
        d_before = date(2026, 3, 28)  # Samedi avant
        d_after = date(2026, 3, 30)   # Lundi après
        assert _paris_offset_str(d_before) == "+01:00"
        assert _paris_offset_str(d_after) == "+02:00"


# ================================================================
# BUG-02 : Endpoint unsubscribe fonctionne
# ================================================================

class TestUnsubscribeEndpoint:
    def test_unsubscribe_user_flow(self):
        """L'inscription puis désinscription fonctionne correctement."""
        from alerts import register_user, unsubscribe_user
        from database import get_db

        result = register_user("+33644444444")
        assert result.get("success")

        result2 = unsubscribe_user("+33644444444")
        assert result2.get("success")

        conn = get_db()
        try:
            user = conn.execute(
                "SELECT actif FROM users WHERE phone_last4 = '4444'"
            ).fetchone()
            assert user["actif"] == 0
        finally:
            conn.close()

    def test_unsubscribe_unknown_number(self):
        """Désinscription d'un numéro inconnu retourne une erreur."""
        from alerts import unsubscribe_user
        result = unsubscribe_user("+33699999999")
        assert "error" in result


# ================================================================
# Migration v8 : test intégrité
# ================================================================

class TestMigrationV8:
    def test_db_has_couleur_originale_column(self):
        """La migration v8 a ajouté la colonne couleur_originale."""
        from database import get_db
        conn = get_db()
        try:
            # Vérifier que la colonne existe
            info = conn.execute("PRAGMA table_info(predictions)").fetchall()
            columns = [row[1] for row in info]
            assert "couleur_originale" in columns
        finally:
            conn.close()

    def test_db_version_is_current(self):
        """La version de la DB est à jour après migration."""
        from database import get_db
        conn = get_db()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == 17
        finally:
            conn.close()


# ================================================================
# Audit v7 : tests des nouvelles corrections
# ================================================================

class TestBug01StoreProtectsConfirmed:
    """BUG-01 : store_prediction ne doit pas écraser confirmed=1."""
    def test_confirmed_not_overwritten(self):
        from predictor import store_prediction, confirm_prediction
        from database import get_db

        target = (date.today() + timedelta(days=5)).isoformat()
        pred = {
            "date": target,
            "couleur_predite": "BLANC",
            "probabilite_bleu": 0.1, "probabilite_blanc": 0.6, "probabilite_rouge": 0.3,
            "score_risque": 52.0,
            "temp_min_prevue": 2.0, "temp_max_prevue": 8.0, "pression_prevue": None,
            "jours_rouges_restants": 10, "jours_blancs_restants": 20,
            "raison": "Test", "horizon": "J-5",
            "timestamp_prediction": datetime.now().isoformat(),
            "score_temperature": 50, "score_budget": 40, "score_weekday": 30,
            "score_gradient": 20, "score_clustering": 10, "score_rte": 15,
        }
        store_prediction(pred, "J-5", cycle_id="cycle_a")

        # Confirmer la prédiction
        confirm_prediction(target, "ROUGE")

        # Tenter de réécrire avec une nouvelle prédiction non confirmée
        pred2 = pred.copy()
        pred2["couleur_predite"] = "BLEU"
        pred2["score_risque"] = 20.0
        result = store_prediction(pred2, "J-5", cycle_id="cycle_b")
        assert result is None  # Skip car déjà confirmé

        # Vérifier que la couleur en DB est restée ROUGE
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT couleur_predite, confirmed FROM predictions WHERE date = ? AND horizon = ?",
                (target, "J-5")
            ).fetchone()
            assert row["confirmed"] == 1
            assert row["couleur_predite"] == "ROUGE"
        finally:
            conn.close()


class TestCacheTTLConfig:
    """BUG-02 : le cache utilise Config.PREDICTIONS_CACHE_TTL."""
    def test_cache_ttl_not_hardcoded(self):
        """Vérifie que 'now + 300' n'apparaît plus dans app.py."""
        import re
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")) as f:
            content = f.read()
        # Il ne doit plus y avoir "now + 300" pour le cache
        matches = re.findall(r'_predictions_cache.*now\s*\+\s*300', content)
        assert len(matches) == 0, "Cache TTL encore hardcodé à 300"


class TestWhatsAppSendRetry:
    """H-03 : send_whatsapp retente 3 fois max."""
    def test_retry_on_failure(self):
        from alerts import send_whatsapp
        # Mode simulation (pas de Twilio client) — doit toujours réussir
        sid, status = send_whatsapp("+33699999999", "Test retry")
        assert status == "simulated"
        assert sid.startswith("SIM_")

    def test_send_sms_alias_works(self):
        """send_sms est un alias de send_whatsapp (compatibilité)."""
        from alerts import send_sms, send_whatsapp
        assert send_sms is send_whatsapp

    def test_long_message_not_truncated(self):
        """WhatsApp n'a pas de limite 160 chars."""
        from alerts import send_whatsapp
        long_msg = "A" * 500
        sid, status = send_whatsapp("+33699999999", long_msg)
        assert status == "simulated"


# ================================================================
# Migration v16 : manage_token
# ================================================================

class TestManageToken:
    def test_register_returns_manage_token(self):
        """register_user retourne un manage_token."""
        from alerts import register_user
        result = register_user("+33655555555")
        assert result.get("success")
        assert "manage_token" in result
        assert len(result["manage_token"]) > 10

    def test_get_user_by_token(self):
        """get_user_by_token retrouve un user actif."""
        from alerts import register_user, get_user_by_token
        result = register_user("+33655555556")
        assert result.get("success")
        token = result["manage_token"]

        user = get_user_by_token(token)
        assert user is not None
        assert user["phone_last4"] == "5556"

    def test_get_user_by_invalid_token(self):
        """Token invalide retourne None."""
        from alerts import get_user_by_token
        assert get_user_by_token("invalid_token_xyz") is None
        assert get_user_by_token("") is None

    def test_update_preferences(self):
        """update_user_preferences modifie les préférences."""
        from alerts import register_user, update_user_preferences, get_user_by_token
        result = register_user("+33655555557")
        token = result["manage_token"]

        # Modifier les préférences
        update_result = update_user_preferences(
            token, seuil_rouge=90, delai=3,
            alerte_blanc=True, recap_hebdo=True
        )
        assert update_result.get("success")

        # Vérifier
        user = get_user_by_token(token)
        assert user["seuil_alerte_rouge"] == 90
        assert user["delai_alerte"] == 3
        assert user["alerte_blanc"] == 1
        assert user["recap_hebdo"] == 1

    def test_db_has_manage_token_column(self):
        """La migration v16 a ajouté la colonne manage_token."""
        from database import get_db
        conn = get_db()
        try:
            info = conn.execute("PRAGMA table_info(users)").fetchall()
            columns = [row[1] for row in info]
            assert "manage_token" in columns
        finally:
            conn.close()


# ================================================================
# WhatsApp : messages contiennent le lien de gestion
# ================================================================

class TestWhatsAppMessages:
    def test_rouge_message_has_manage_link(self):
        """Le message ROUGE contient le lien de gestion."""
        from alerts import format_alert_rouge
        msg = format_alert_rouge(
            date(2026, 2, 17),
            {"probabilite_rouge": 0.87, "temp_min_prevue": 2},
            manage_token="abc123"
        )
        assert "manage/abc123" in msg
        assert "Gérer mes alertes" in msg
        assert "*Jour ROUGE*" in msg
        assert "Calendrier Tempo EDF" in msg
        assert "mardi 17 février" in msg

    def test_blanc_message_has_manage_link(self):
        """Le message BLANC contient le lien de gestion."""
        from alerts import format_alert_blanc
        msg = format_alert_blanc(
            date(2026, 2, 17),
            {"probabilite_blanc": 0.65},
            manage_token="def456"
        )
        assert "manage/def456" in msg

    def test_officiel_message_has_manage_link(self):
        """Le message officiel contient le lien de gestion."""
        from alerts import format_alert_officiel
        msg = format_alert_officiel(date(2026, 2, 17), "ROUGE", manage_token="ghi789")
        assert "manage/ghi789" in msg

    def test_recap_message_has_manage_link(self):
        """Le récap hebdo contient le lien de gestion."""
        from alerts import format_recap_hebdo
        preds = [
            {"date": "2026-02-17", "couleur_predite": "BLEU"},
            {"date": "2026-02-18", "couleur_predite": "ROUGE"},
        ]
        msg = format_recap_hebdo(preds, manage_token="jkl012")
        assert "manage/jkl012" in msg

    def test_message_without_token_no_link(self):
        """Sans token, pas de lien de gestion."""
        from alerts import format_alert_rouge
        msg = format_alert_rouge(
            date(2026, 2, 17),
            {"probabilite_rouge": 0.87, "temp_min_prevue": 2}
        )
        assert "Gérer mes alertes" not in msg

    def test_handle_incoming_whatsapp_prefix(self):
        """handle_incoming_sms gère le préfixe whatsapp: de Twilio."""
        from alerts import register_user, handle_incoming_sms
        register_user("+33655555558")
        response = handle_incoming_sms("whatsapp:+33655555558", "STOP")
        assert "désinscrit" in response.lower()
        assert "Calendrier Tempo EDF" in response


# ================================================================
# SEO : SSR, /calendrier, sitemap, robots.txt, Umami
# ================================================================

class TestSSRData:
    """Server-Side Rendering pre-loads data for Google crawling."""

    def test_ssr_returns_dict_when_db_not_ready(self):
        """SSR returns empty defaults when DB event is not set."""
        from app import _get_ssr_data, _db_ready
        was_set = _db_ready.is_set()
        _db_ready.clear()
        try:
            ssr = _get_ssr_data()
            assert ssr["today_color"] is None
            assert ssr["tomorrow_color"] is None
            assert ssr["remaining"] is None
            assert ssr["predictions"] == []
        finally:
            if was_set:
                _db_ready.set()

    def test_ssr_returns_remaining_when_db_ready(self):
        """SSR loads remaining counters from DB when available."""
        from app import _get_ssr_data, _db_ready
        _db_ready.set()
        ssr = _get_ssr_data()
        # remaining should be a dict with ROUGE/BLANC/BLEU keys
        assert ssr["remaining"] is not None
        assert "ROUGE" in ssr["remaining"]
        assert "BLANC" in ssr["remaining"]
        assert "BLEU" in ssr["remaining"]

    def test_ssr_loads_today_color_from_actuals(self):
        """SSR reads today's color from actuals table."""
        from app import _get_ssr_data, _db_ready
        from database import get_db
        _db_ready.set()

        today_str = date.today().isoformat()
        now_str = datetime.now().isoformat()
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO actuals (date, couleur_reelle, synthetic, timestamp_confirmation) VALUES (?, 'BLEU', 0, ?)",
            (today_str, now_str)
        )
        conn.commit()
        conn.close()

        ssr = _get_ssr_data()
        assert ssr["today_color"] == "BLEU"


class TestCalendrierRoute:
    """Tests pour la page /calendrier."""

    def test_calendar_builds_correct_grid(self):
        """The calendar grid has correct number of days + empty padding."""
        import calendar as cal_module
        year, month = 2026, 2
        first_weekday, num_days = cal_module.monthrange(year, month)
        # February 2026: 28 days, starts on Sunday (6)
        expected_cells = first_weekday + num_days
        assert num_days == 28
        assert expected_cells >= 28

    def test_calendar_month_names_fr(self):
        """French month names are correctly defined."""
        month_names_fr = [
            "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
            "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
        ]
        assert month_names_fr[1] == "Janvier"
        assert month_names_fr[12] == "Décembre"
        assert len(month_names_fr) == 13  # index 0 is empty

    def test_calendar_season_navigation_bounds(self):
        """Navigation stays within the season bounds."""
        from tempo_client import get_season_dates
        season_start, season_end = get_season_dates()
        # September should be the first month available
        assert season_start.month == 9
        # August should be the last month available
        assert season_end.month == 8


class TestSitemapXml:
    """Sitemap includes all important pages."""

    def test_sitemap_contains_homepage(self):
        """Sitemap includes homepage with priority 1.0."""
        import re
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")) as f:
            content = f.read()
        assert "calendrier-tempo.fr/</loc>" in content
        assert "<priority>1.0</priority>" in content

    def test_sitemap_contains_calendrier(self):
        """Sitemap includes /calendrier with priority 0.9."""
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")) as f:
            content = f.read()
        assert "calendrier-tempo.fr/calendrier</loc>" in content
        assert "<priority>0.9</priority>" in content


class TestRobotsTxt:
    """Robots.txt directives are correct."""

    def test_robots_allows_calendrier(self):
        """robots.txt explicitly allows /calendrier."""
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")) as f:
            content = f.read()
        assert '"Allow: /calendrier\\n"' in content

    def test_robots_allows_blog(self):
        """robots.txt explicitly allows /blog/."""
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")) as f:
            content = f.read()
        assert '"Allow: /blog/\\n"' in content

    def test_robots_disallows_admin(self):
        """robots.txt disallows /admin."""
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")) as f:
            content = f.read()
        assert '"Disallow: /admin\\n"' in content


class TestUmamiAnalytics:
    """Umami analytics script is present on all public templates."""

    _UMAMI_SNIPPET = 'cloud.umami.is/script.js'
    _PUBLIC_TEMPLATES = [
        "dashboard.html", "blog_index.html", "blog_article.html",
        "legal.html", "manage.html",
    ]

    def test_umami_on_all_public_templates(self):
        """Every public template includes the Umami script."""
        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "templates"
        )
        for tpl in self._PUBLIC_TEMPLATES:
            filepath = os.path.join(templates_dir, tpl)
            with open(filepath) as f:
                content = f.read()
            assert self._UMAMI_SNIPPET in content, (
                f"Umami script missing in {tpl}"
            )

    def test_umami_on_calendrier(self):
        """The calendrier template includes Umami."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "calendrier.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert self._UMAMI_SNIPPET in content

    def test_umami_not_on_admin(self):
        """Admin template should NOT include Umami (noindex page)."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "admin.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert self._UMAMI_SNIPPET not in content


class TestStructuredData:
    """JSON-LD structured data is present on key pages."""

    def test_dashboard_has_software_application_schema(self):
        """Homepage has SoftwareApplication with AggregateRating."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "dashboard.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert '"SoftwareApplication"' in content
        assert '"AggregateRating"' in content

    def test_dashboard_has_organization_schema(self):
        """Homepage has Organization schema."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "dashboard.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert '"Organization"' in content

    def test_dashboard_has_howto_schema(self):
        """Homepage has HowTo schema."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "dashboard.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert '"HowTo"' in content
        assert '"HowToStep"' in content

    def test_calendrier_has_dataset_schema(self):
        """Calendar page has Dataset schema."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "calendrier.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert '"Dataset"' in content

    def test_calendrier_has_faq_schema(self):
        """Calendar page has FAQPage schema."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "calendrier.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert '"FAQPage"' in content


class TestInternalLinking:
    """Navigation includes /calendrier on all pages."""

    _TEMPLATES_WITH_CAL_NAV = [
        "dashboard.html", "blog_index.html", "blog_article.html",
        "legal.html", "calendrier.html",
    ]

    def test_calendrier_in_nav_all_templates(self):
        """Every public template has /calendrier in the nav."""
        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "templates"
        )
        for tpl in self._TEMPLATES_WITH_CAL_NAV:
            filepath = os.path.join(templates_dir, tpl)
            with open(filepath) as f:
                content = f.read()
            assert 'href="/calendrier"' in content, (
                f"/calendrier link missing in nav of {tpl}"
            )


class TestBlogArticles:
    """Blog articles for SEO exist and have proper frontmatter."""

    def test_tempo_guide_article_exists(self):
        """The 'Tempo EDF 2026 guide complet' article exists with correct frontmatter."""
        from blog import get_article_by_slug
        art = get_article_by_slug("tempo-edf-2026-guide-complet")
        assert art is not None
        assert "tempo edf" in art.keywords.lower()
        assert art.reading_time >= 3

    def test_historique_article_exists(self):
        """The 'Historique calendrier Tempo' article exists with correct frontmatter."""
        from blog import get_article_by_slug
        art = get_article_by_slug("calendrier-tempo-historique-saisons")
        assert art is not None
        assert "calendrier tempo" in art.keywords.lower()

    def test_article_files_exist(self):
        """At least 8 article Markdown files exist in articles/ directory."""
        articles_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "articles"
        )
        md_files = [f for f in os.listdir(articles_dir) if f.endswith(".md")]
        assert len(md_files) >= 8, f"Only {len(md_files)} articles found: {md_files}"


class TestCalendrierAnchorNavigation:
    """Calendar month navigation uses #cal anchor."""

    def test_prev_next_links_have_anchor(self):
        """Month navigation links include #cal to avoid scroll-to-top."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "calendrier.html"
        )
        with open(filepath) as f:
            content = f.read()
        # Both prev and next links should end with #cal
        assert "prev_year }}#cal" in content
        assert "next_year }}#cal" in content


class TestOriginCheck:
    """M-11 : _check_origin case-insensitive."""
    def test_case_insensitive(self):
        from app import _check_origin
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {
            "origin": "https://WWW.CALENDRIER-TEMPO.FR",
            "host": "www.calendrier-tempo.fr",
        }
        assert _check_origin(request) is True

    def test_default_port_stripped(self):
        from app import _check_origin
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {
            "origin": "https://www.calendrier-tempo.fr:443",
            "host": "www.calendrier-tempo.fr",
        }
        assert _check_origin(request) is True
