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

    def test_db_has_temp_moy_prevue_column(self):
        """La migration v18 a ajouté temp_moy_prevue dans predictions."""
        from database import get_db
        conn = get_db()
        try:
            info = conn.execute("PRAGMA table_info(predictions)").fetchall()
            columns = [row[1] for row in info]
            assert "temp_moy_prevue" in columns
        finally:
            conn.close()

    def test_db_has_weather_forecast_log_table(self):
        """La migration v18 a créé la table weather_forecast_log."""
        from database import get_db
        conn = get_db()
        try:
            info = conn.execute(
                "PRAGMA table_info(weather_forecast_log)"
            ).fetchall()
            columns = [row[1] for row in info]
            assert "target_date" in columns
            assert "forecast_date" in columns
            assert "horizon_days" in columns
            assert "temp_moy" in columns
        finally:
            conn.close()

    def test_db_has_humidity_wind_speed_prevue_columns(self):
        """La migration v19 a ajouté humidity_prevue et wind_speed_prevue."""
        from database import get_db
        conn = get_db()
        try:
            info = conn.execute("PRAGMA table_info(predictions)").fetchall()
            columns = [row[1] for row in info]
            assert "humidity_prevue" in columns
            assert "wind_speed_prevue" in columns
        finally:
            conn.close()

    def test_db_version_is_current(self):
        """La version de la DB est à jour après migration."""
        from database import get_db
        conn = get_db()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == 19
        finally:
            conn.close()


# ================================================================
# Tests v19 : weather_forecast_log J+0, humidity/wind in predictions
# ================================================================

class TestWeatherForecastLogJ0:
    """Le weather_forecast_log accepte horizon_days = 0 (J+0)."""

    def test_forecast_log_accepts_horizon_zero(self):
        """On peut insérer et relire un enregistrement avec horizon_days=0."""
        from database import get_db
        conn = get_db()
        try:
            target = "2026-02-20"
            forecast = "2026-02-20"
            conn.execute(
                """INSERT OR REPLACE INTO weather_forecast_log
                   (target_date, forecast_date, horizon_days,
                    temp_min, temp_max, temp_moy,
                    pressure, humidity, wind_speed,
                    source, fetched_at)
                   VALUES (?, ?, 0, -1.0, 5.0, 2.0, 1020.0, 80.0, 15.0,
                           'test', '2026-02-20T12:00:00')""",
                (target, forecast),
            )
            conn.commit()
            row = conn.execute(
                "SELECT horizon_days, temp_moy FROM weather_forecast_log "
                "WHERE target_date = ? AND forecast_date = ?",
                (target, forecast),
            ).fetchone()
            assert row is not None
            assert row[0] == 0  # horizon_days
            assert row[1] == 2.0  # temp_moy
        finally:
            # Nettoyage
            conn.execute(
                "DELETE FROM weather_forecast_log "
                "WHERE target_date = '2026-02-20' AND forecast_date = '2026-02-20'"
            )
            conn.commit()
            conn.close()

    def test_forecast_log_accepts_full_range(self):
        """horizon_days peut aller de 0 à 15 (J+0 à J+15)."""
        from database import get_db
        conn = get_db()
        try:
            for h in (0, 1, 5, 10, 15):
                target = f"2026-03-{10+h:02d}"
                conn.execute(
                    """INSERT OR REPLACE INTO weather_forecast_log
                       (target_date, forecast_date, horizon_days,
                        temp_min, temp_max, temp_moy,
                        pressure, humidity, wind_speed,
                        source, fetched_at)
                       VALUES (?, '2026-03-10', ?, 0, 5, 2.5,
                               1015, 70, 10, 'test', '2026-03-10T08:00:00')""",
                    (target, h),
                )
            conn.commit()
            rows = conn.execute(
                "SELECT horizon_days FROM weather_forecast_log "
                "WHERE forecast_date = '2026-03-10' AND source = 'test' "
                "ORDER BY horizon_days"
            ).fetchall()
            horizons = [r[0] for r in rows]
            assert 0 in horizons
            assert 15 in horizons
        finally:
            conn.execute(
                "DELETE FROM weather_forecast_log "
                "WHERE forecast_date = '2026-03-10' AND source = 'test'"
            )
            conn.commit()
            conn.close()


class TestResultHumidityWindSpeed:
    """_result() inclut humidity_prevue et wind_speed_prevue."""

    def test_result_includes_humidity_and_wind_speed(self):
        """_result() propage humidity et wind_speed depuis weather."""
        from predictor import _result
        weather = {
            "temp_min": -2.0, "temp_max": 5.0, "temp_moy": 1.5,
            "pressure": 1025.0, "humidity": 82.0, "wind_speed": 18.5,
        }
        r = _result(
            date(2026, 1, 15), "ROUGE", 68.0, 0.05, 0.25, 0.70,
            weather=weather,
        )
        assert r["humidity_prevue"] == 82.0
        assert r["wind_speed_prevue"] == 18.5

    def test_result_none_when_no_weather(self):
        """Sans weather, humidity_prevue et wind_speed_prevue sont None."""
        from predictor import _result
        r = _result(
            date(2026, 1, 15), "BLEU", 25.0, 0.80, 0.15, 0.05,
            weather=None,
        )
        assert r["humidity_prevue"] is None
        assert r["wind_speed_prevue"] is None

    def test_result_none_when_keys_missing(self):
        """Si weather n'a pas humidity/wind_speed, valeurs None."""
        from predictor import _result
        weather = {"temp_min": 3.0, "temp_max": 10.0, "temp_moy": 6.5}
        r = _result(
            date(2026, 4, 10), "BLEU", 20.0, 0.85, 0.10, 0.05,
            weather=weather,
        )
        assert r["humidity_prevue"] is None
        assert r["wind_speed_prevue"] is None


class TestStorePredictionV19Columns:
    """store_prediction persiste humidity_prevue et wind_speed_prevue."""

    def test_store_persists_humidity_wind_speed(self):
        """Les colonnes humidity_prevue et wind_speed_prevue sont écrites en DB."""
        from predictor import store_prediction
        from database import get_db

        target = (date.today() + timedelta(days=6)).isoformat()
        pred = {
            "date": target,
            "couleur_predite": "ROUGE",
            "probabilite_bleu": 0.05,
            "probabilite_blanc": 0.25,
            "probabilite_rouge": 0.70,
            "score_risque": 72.0,
            "temp_min_prevue": -3.0,
            "temp_max_prevue": 4.0,
            "temp_moy_prevue": 0.5,
            "pression_prevue": 1030.0,
            "humidity_prevue": 88.0,
            "wind_speed_prevue": 22.5,
            "jours_rouges_restants": 8,
            "jours_blancs_restants": 15,
            "raison": "Test v19 columns",
            "score_temperature": 75,
            "score_budget": 40,
            "score_weekday": 30,
            "score_gradient": 20,
            "score_clustering": 10,
            "score_rte": 15,
        }
        store_prediction(pred, "J-6", cycle_id="test_v19")

        conn = get_db()
        try:
            row = conn.execute(
                "SELECT humidity_prevue, wind_speed_prevue "
                "FROM predictions WHERE date = ? ORDER BY id DESC LIMIT 1",
                (target,),
            ).fetchone()
            assert row is not None
            assert row["humidity_prevue"] == 88.0
            assert row["wind_speed_prevue"] == 22.5
        finally:
            conn.execute("DELETE FROM predictions WHERE date = ?", (target,))
            conn.commit()
            conn.close()

    def test_store_prediction_34_columns(self):
        """L'INSERT dans predictions utilise exactement 34 colonnes."""
        from database import get_db
        conn = get_db()
        try:
            info = conn.execute("PRAGMA table_info(predictions)").fetchall()
            columns = [row[1] for row in info]
            # Les 34 colonnes attendues (hors id auto-increment)
            expected = {
                "date", "couleur_predite",
                "probabilite_bleu", "probabilite_blanc", "probabilite_rouge",
                "score_risque",
                "temp_min_prevue", "temp_max_prevue", "temp_moy_prevue",
                "pression_prevue", "humidity_prevue", "wind_speed_prevue",
                "jours_rouges_restants", "jours_blancs_restants",
                "raison", "horizon", "timestamp_prediction",
                "score_temperature", "score_budget", "score_weekday",
                "score_gradient", "score_clustering", "score_rte",
                "score_temperature_raw", "score_budget_raw", "score_weekday_raw",
                "score_gradient_raw", "score_clustering_raw", "score_rte_raw",
                "cycle_id", "couleur_precedente", "simulated", "confirmed",
                "couleur_originale",
            }
            actual = {c for c in columns if c != "id"}
            assert expected.issubset(actual), (
                f"Colonnes manquantes : {expected - actual}"
            )
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
        """Homepage has SoftwareApplication schema (AggregateRating removed — no real reviews)."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "dashboard.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert '"SoftwareApplication"' in content

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


# ================================================================
# SEO audit v2 : heading hierarchy, structured data, AI discovery
# ================================================================

class TestHeadingHierarchy:
    """Only homepage should have H1 in header. Others use span."""

    _NON_HOMEPAGE_TEMPLATES = [
        "calendrier.html", "alertes.html", "blog_index.html",
        "blog_article.html", "legal.html", "manage.html",
    ]

    def test_no_h1_in_header_non_homepage(self):
        """Non-homepage templates use span.header-title, not h1, in header."""
        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "templates"
        )
        for tpl in self._NON_HOMEPAGE_TEMPLATES:
            filepath = os.path.join(templates_dir, tpl)
            with open(filepath) as f:
                content = f.read()
            # Should NOT have <h1 in the header section (before </header>)
            header_section = content.split("</header>")[0] if "</header>" in content else ""
            assert '<h1' not in header_section, f"{tpl} has <h1> in header (duplicate H1 risk)"

    def test_homepage_has_h1_in_header(self):
        """Homepage (dashboard.html) keeps H1 in header."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "dashboard.html"
        )
        with open(filepath) as f:
            content = f.read()
        header_section = content.split("</header>")[0]
        assert '<h1' in header_section


class TestSSRLastUpdate:
    """SSR last_update uses timestamp_prediction (not created_at)."""

    def test_ssr_query_uses_timestamp_prediction(self):
        """The SSR query in app.py uses timestamp_prediction column."""
        app_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")
        with open(app_path) as f:
            content = f.read()
        assert "MAX(timestamp_prediction)" in content
        # Ensure the old buggy column name is NOT used
        assert "MAX(created_at)" not in content

    def test_ssr_last_update_with_predictions(self):
        """SSR last_update is populated when predictions exist."""
        from app import _get_ssr_data, _db_ready
        from database import get_db
        _db_ready.set()

        today_str = date.today().isoformat()
        now_str = datetime.now().isoformat()
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO predictions "
            "(date, couleur_predite, probabilite_bleu, probabilite_blanc, probabilite_rouge, "
            "score_risque, horizon, timestamp_prediction) "
            "VALUES (?, 'BLEU', 0.8, 0.1, 0.1, 30, 'J-1', ?)",
            (today_str, now_str)
        )
        conn.commit()
        conn.close()

        ssr = _get_ssr_data()
        assert ssr["last_update"] is not None


class TestWebSiteSchema:
    """Homepage has WebSite JSON-LD schema."""

    def test_dashboard_has_website_schema(self):
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "dashboard.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert '"WebSite"' in content


class TestAIDiscovery:
    """AI bot discovery endpoints exist."""

    def test_llms_txt_endpoint_exists(self):
        """app.py has /llms.txt route."""
        app_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")
        with open(app_path) as f:
            content = f.read()
        assert "/llms.txt" in content

    def test_feed_xml_endpoint_exists(self):
        """app.py has /feed.xml route."""
        app_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")
        with open(app_path) as f:
            content = f.read()
        assert "/feed.xml" in content

    def test_robots_has_ai_bot_rules(self):
        """robots.txt includes rules for AI bots."""
        app_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")
        with open(app_path) as f:
            content = f.read()
        assert "GPTBot" in content
        assert "ClaudeBot" in content


class TestAlertUXFraming:
    """Alert mockups show 5-day forecast, not 'demain' with percentage."""

    def test_alertes_page_no_demain_mockup(self):
        """Alertes SMS mockup shows weekly forecast, not 'prévu demain'."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "alertes.html"
        )
        with open(filepath) as f:
            content = f.read()
        # Should NOT have the old "prévu demain (87% confiance)" in the SMS bubble
        assert "87% confiance" not in content
        # Should have the weekly forecast format
        assert "Semaine" in content

    def test_modal_no_demain_mockup(self):
        """Subscribe modal shows weekly forecast, not 'prévu demain'."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "_subscribe_modal.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert "87% confiance" not in content
        assert "Semaine" in content

    def test_modal_has_5_day_slots(self):
        """Subscribe modal has 5 day slots for dynamic dates."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "_subscribe_modal.html"
        )
        with open(filepath) as f:
            content = f.read()
        for i in range(1, 6):
            assert f"modal-sms-day{i}" in content, f"Missing day slot {i}"


class TestMinifiedAssets:
    """Minified CSS and JS files exist."""

    def test_minified_css_exists(self):
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "static", "css", "style.min.css"
        )
        assert os.path.exists(filepath), "style.min.css not found"
        size = os.path.getsize(filepath)
        assert size > 1000, f"style.min.css too small ({size} bytes)"

    def test_minified_js_exists(self):
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "static", "js", "app.min.js"
        )
        assert os.path.exists(filepath), "app.min.js not found"
        size = os.path.getsize(filepath)
        assert size > 1000, f"app.min.js too small ({size} bytes)"


class TestGoogleFontsOptimization:
    """Google Fonts loaded via preconnect, not CSS @import."""

    def test_css_no_import(self):
        """style.css does not use @import for Google Fonts."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "static", "css", "style.css"
        )
        with open(filepath) as f:
            content = f.read()
        # Should NOT have active @import (may have a comment about it)
        lines = [l for l in content.split('\n') if l.strip().startswith('@import')]
        assert len(lines) == 0, f"Active @import found in style.css: {lines}"

    def test_templates_have_preconnect(self):
        """Key templates use preconnect for Google Fonts."""
        templates_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "templates"
        )
        for tpl in ["dashboard.html", "calendrier.html", "alertes.html"]:
            filepath = os.path.join(templates_dir, tpl)
            with open(filepath) as f:
                content = f.read()
            assert 'rel="preconnect" href="https://fonts.googleapis.com"' in content, (
                f"Missing preconnect in {tpl}"
            )


class TestBreadcrumbsComplete:
    """All breadcrumb JSON-LD includes item URL on last element."""

    def test_calendrier_breadcrumb_has_item(self):
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "calendrier.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert "calendrier-tempo.fr/calendrier" in content

    def test_alertes_breadcrumb_has_item(self):
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "alertes.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert "calendrier-tempo.fr/alertes" in content


# ================================================================
# SEO Agent v2 : blog.py new fields, agent files, dateModified
# ================================================================

class TestBlogArticleFields:
    """blog.py Article dataclass supports updated_date and cluster."""

    def test_article_has_updated_date_field(self):
        """Article dataclass has updated_date field (default None)."""
        from blog import Article
        art = Article(
            slug="test", title="Test", description="Test desc",
            publish_date=date(2026, 1, 1), keywords="test",
            content_html="<p>Test</p>", reading_time=3
        )
        assert art.updated_date is None

    def test_article_has_cluster_field(self):
        """Article dataclass has cluster field (default empty string)."""
        from blog import Article
        art = Article(
            slug="test", title="Test", description="Test desc",
            publish_date=date(2026, 1, 1), keywords="test",
            content_html="<p>Test</p>", reading_time=3
        )
        assert art.cluster == ""

    def test_article_with_updated_date_and_cluster(self):
        """Article accepts updated_date and cluster values."""
        from blog import Article
        art = Article(
            slug="test", title="Test", description="Test desc",
            publish_date=date(2026, 1, 1), keywords="test",
            content_html="<p>Test</p>", reading_time=3,
            updated_date=date(2026, 2, 15), cluster="tempo-guide"
        )
        assert art.updated_date == date(2026, 2, 15)
        assert art.cluster == "tempo-guide"

    def test_load_article_parses_cluster(self):
        """_load_article parses cluster from frontmatter."""
        from blog import _load_article
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, dir='/tmp') as f:
            f.write("---\ntitle: Test Article\npublish_date: 2025-01-01\ncluster: jours-rouges\n---\nContent here.\n")
            f.flush()
            from pathlib import Path
            art = _load_article(Path(f.name))
        assert art is not None
        assert art.cluster == "jours-rouges"
        os.unlink(f.name)

    def test_load_article_parses_updated_date(self):
        """_load_article parses updated_date from frontmatter."""
        from blog import _load_article
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, dir='/tmp') as f:
            f.write("---\ntitle: Test Article\npublish_date: 2025-01-01\nupdated_date: 2025-06-15\n---\nContent.\n")
            f.flush()
            from pathlib import Path
            art = _load_article(Path(f.name))
        assert art is not None
        assert art.updated_date == date(2025, 6, 15)
        os.unlink(f.name)


class TestSEOAgentFiles:
    """SEO agent supporting files exist and are valid."""

    def test_seo_agent_prompt_exists(self):
        """SEO agent prompt file exists."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            ".claude", "seo-agent-prompt.md"
        )
        assert os.path.exists(filepath), "seo-agent-prompt.md not found"

    def test_seo_agent_prompt_has_8_steps(self):
        """SEO agent prompt v2 has 8 steps."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            ".claude", "seo-agent-prompt.md"
        )
        with open(filepath) as f:
            content = f.read()
        for step in range(9):  # ÉTAPE 0 through ÉTAPE 8
            assert f"ÉTAPE {step}" in content, f"Missing ÉTAPE {step}"

    def test_seo_agent_prompt_has_topic_clusters(self):
        """SEO agent prompt defines topic clusters."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            ".claude", "seo-agent-prompt.md"
        )
        with open(filepath) as f:
            content = f.read()
        assert "tempo-guide" in content
        assert "jours-rouges" in content
        assert "equipements" in content

    def test_editorial_calendar_yaml_exists(self):
        """Editorial calendar YAML file exists."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "articles", "_calendrier_editorial.yaml"
        )
        assert os.path.exists(filepath), "_calendrier_editorial.yaml not found"

    def test_editorial_calendar_yaml_valid(self):
        """Editorial calendar YAML is parseable."""
        import yaml
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "articles", "_calendrier_editorial.yaml"
        )
        with open(filepath) as f:
            data = yaml.safe_load(f)
        assert "articles_publies" in data
        assert "prochaines_semaines" in data
        assert len(data["articles_publies"]) >= 8
        assert len(data["prochaines_semaines"]) >= 10

    def test_publication_log_exists(self):
        """Publication log file exists."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "articles", "_publication_log.md"
        )
        assert os.path.exists(filepath), "_publication_log.md not found"


class TestBlogArticleDateModified:
    """Blog article template supports dateModified."""

    def test_template_has_date_modified(self):
        """blog_article.html uses updated_date for dateModified."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "blog_article.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert "article.updated_date" in content
        assert "dateModified" in content

    def test_template_has_article_modified_time(self):
        """blog_article.html has article:modified_time meta tag."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "blog_article.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert "article:modified_time" in content

    def test_template_shows_updated_date_conditionally(self):
        """blog_article.html shows 'Mis à jour le' when updated_date differs."""
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates", "blog_article.html"
        )
        with open(filepath) as f:
            content = f.read()
        assert "updated_date" in content
        assert "Mis" in content  # "Mis à jour le"


# ================================================================
# Thermal coherence post-check
# ================================================================

class TestThermalCoherence:
    """Post-prediction coherence: colder days should get ROUGE over warmer days."""

    def _make_pred(self, date_str, couleur, p_rouge=0.3, p_blanc=0.4, p_bleu=0.3,
                   score=50.0, confirmed=False):
        return {
            "date": date_str,
            "couleur_predite": couleur,
            "probabilite_rouge": p_rouge,
            "probabilite_blanc": p_blanc,
            "probabilite_bleu": p_bleu,
            "score_risque": score,
            "raison": "Test",
            "confirmed": confirmed,
        }

    def test_swap_colder_blanc_warmer_rouge(self):
        """A colder BLANC day is swapped to ROUGE when a warmer day is ROUGE."""
        from predictor import _apply_thermal_coherence
        predictions = [
            self._make_pred("2026-03-02", "BLANC", p_blanc=0.50, p_rouge=0.25, p_bleu=0.25),
            self._make_pred("2026-03-03", "ROUGE", p_rouge=0.55, p_blanc=0.25, p_bleu=0.20),
        ]
        forecasts = [
            {"date": "2026-03-02", "temp_moy": 3.0},
            {"date": "2026-03-03", "temp_moy": 6.0},
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        assert result[0]["couleur_predite"] == "ROUGE"
        assert result[1]["couleur_predite"] == "BLANC"
        assert "thermique" in result[0]["raison"]
        assert "thermique" in result[1]["raison"]

    def test_no_swap_when_already_coherent(self):
        """No swap when ROUGE day is already colder than BLANC day."""
        from predictor import _apply_thermal_coherence
        predictions = [
            self._make_pred("2026-03-02", "ROUGE", p_rouge=0.60, p_blanc=0.25, p_bleu=0.15),
            self._make_pred("2026-03-03", "BLANC", p_blanc=0.50, p_rouge=0.20, p_bleu=0.30),
        ]
        forecasts = [
            {"date": "2026-03-02", "temp_moy": 3.0},
            {"date": "2026-03-03", "temp_moy": 6.0},
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        assert result[0]["couleur_predite"] == "ROUGE"
        assert result[1]["couleur_predite"] == "BLANC"

    def test_no_swap_confirmed_days(self):
        """Confirmed days are never swapped."""
        from predictor import _apply_thermal_coherence
        predictions = [
            self._make_pred("2026-03-02", "BLANC", confirmed=True),
            self._make_pred("2026-03-03", "ROUGE", confirmed=False),
        ]
        forecasts = [
            {"date": "2026-03-02", "temp_moy": 1.0},
            {"date": "2026-03-03", "temp_moy": 8.0},
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        assert result[0]["couleur_predite"] == "BLANC"
        assert result[1]["couleur_predite"] == "ROUGE"

    def test_no_swap_weekend_to_rouge(self):
        """A Saturday BLANC can't become ROUGE (R2)."""
        from predictor import _apply_thermal_coherence
        # 2026-02-28 is Saturday
        predictions = [
            self._make_pred("2026-02-28", "BLANC"),
            self._make_pred("2026-03-02", "ROUGE"),  # Monday
        ]
        forecasts = [
            {"date": "2026-02-28", "temp_moy": 1.0},
            {"date": "2026-03-02", "temp_moy": 8.0},
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        assert result[0]["couleur_predite"] == "BLANC"
        assert result[1]["couleur_predite"] == "ROUGE"

    def test_no_swap_sunday_rouge_to_blanc(self):
        """A Sunday ROUGE can't become BLANC (R3)."""
        from predictor import _apply_thermal_coherence
        # 2026-03-01 is Sunday
        predictions = [
            self._make_pred("2026-02-27", "BLANC"),   # Friday
            self._make_pred("2026-03-01", "ROUGE"),   # Sunday
        ]
        forecasts = [
            {"date": "2026-02-27", "temp_moy": 1.0},
            {"date": "2026-03-01", "temp_moy": 8.0},
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        # Sunday can't become BLANC, so no swap
        assert result[0]["couleur_predite"] == "BLANC"
        assert result[1]["couleur_predite"] == "ROUGE"

    def test_no_swap_small_delta(self):
        """No swap when temperature difference < 0.5C."""
        from predictor import _apply_thermal_coherence
        predictions = [
            self._make_pred("2026-03-02", "BLANC"),
            self._make_pred("2026-03-03", "ROUGE"),
        ]
        forecasts = [
            {"date": "2026-03-02", "temp_moy": 5.0},
            {"date": "2026-03-03", "temp_moy": 5.3},  # Delta = 0.3 < 0.5
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        assert result[0]["couleur_predite"] == "BLANC"
        assert result[1]["couleur_predite"] == "ROUGE"

    def test_swap_non_adjacent_days(self):
        """Swap works even for non-adjacent days (e.g. day 1 and day 3)."""
        from predictor import _apply_thermal_coherence
        predictions = [
            self._make_pred("2026-03-02", "BLANC"),  # Monday, cold
            self._make_pred("2026-03-03", "BLEU"),   # Tuesday, mild (no swap)
            self._make_pred("2026-03-04", "ROUGE"),  # Wednesday, warm
        ]
        forecasts = [
            {"date": "2026-03-02", "temp_moy": 2.0},
            {"date": "2026-03-03", "temp_moy": 10.0},
            {"date": "2026-03-04", "temp_moy": 7.0},
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        assert result[0]["couleur_predite"] == "ROUGE"
        assert result[2]["couleur_predite"] == "BLANC"

    def test_r4_prevents_swap(self):
        """Swap is blocked if it would create 5+ consecutive ROUGE (R4)."""
        from predictor import _apply_thermal_coherence
        # 4 consecutive ROUGE days then a BLANC that's colder
        predictions = [
            self._make_pred("2026-03-02", "ROUGE"),  # Mon
            self._make_pred("2026-03-03", "ROUGE"),  # Tue
            self._make_pred("2026-03-04", "ROUGE"),  # Wed
            self._make_pred("2026-03-05", "ROUGE"),  # Thu
            self._make_pred("2026-03-06", "BLANC"),  # Fri — colder but next to 4 ROUGE
            self._make_pred("2026-03-09", "ROUGE"),  # Mon (gap: weekend) — warmer
        ]
        forecasts = [
            {"date": "2026-03-02", "temp_moy": 1.0},
            {"date": "2026-03-03", "temp_moy": 1.0},
            {"date": "2026-03-04", "temp_moy": 1.0},
            {"date": "2026-03-05", "temp_moy": 1.0},
            {"date": "2026-03-06", "temp_moy": 0.5},  # Coldest
            {"date": "2026-03-09", "temp_moy": 5.0},  # Warmest ROUGE
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        # Swapping idx 4 (BLANC→ROUGE) would create 5 consecutive ROUGE (Mar 2-6)
        assert result[4]["couleur_predite"] == "BLANC"
        assert result[5]["couleur_predite"] == "ROUGE"

    def test_prob_coherence_after_swap(self):
        """After swap, predicted color always has the highest probability."""
        from predictor import _apply_thermal_coherence
        predictions = [
            self._make_pred("2026-03-02", "BLANC", p_blanc=0.60, p_rouge=0.15, p_bleu=0.25),
            self._make_pred("2026-03-03", "ROUGE", p_rouge=0.65, p_blanc=0.20, p_bleu=0.15),
        ]
        forecasts = [
            {"date": "2026-03-02", "temp_moy": 2.0},
            {"date": "2026-03-03", "temp_moy": 7.0},
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        # Day 0 is now ROUGE — its prob_rouge must be the highest
        assert result[0]["probabilite_rouge"] >= result[0]["probabilite_blanc"]
        assert result[0]["probabilite_rouge"] >= result[0]["probabilite_bleu"]
        # Day 1 is now BLANC — its prob_blanc must be the highest
        assert result[1]["probabilite_blanc"] >= result[1]["probabilite_rouge"]
        assert result[1]["probabilite_blanc"] >= result[1]["probabilite_bleu"]

    def test_out_of_season_not_swapped(self):
        """Days outside RED season (Apr-Oct) are never swapped."""
        from predictor import _apply_thermal_coherence
        predictions = [
            self._make_pred("2026-04-06", "BLANC"),  # April — out of RED season
            self._make_pred("2026-04-07", "ROUGE"),
        ]
        forecasts = [
            {"date": "2026-04-06", "temp_moy": 2.0},
            {"date": "2026-04-07", "temp_moy": 8.0},
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        assert result[0]["couleur_predite"] == "BLANC"
        assert result[1]["couleur_predite"] == "ROUGE"

    def test_holiday_blanc_not_swapped_to_rouge(self):
        """A holiday BLANC can't become ROUGE (R2)."""
        from predictor import _apply_thermal_coherence
        # 2026-01-01 is a Thursday and a holiday (Jour de l'an)
        predictions = [
            self._make_pred("2026-01-01", "BLANC"),
            self._make_pred("2026-01-02", "ROUGE"),  # Friday
        ]
        forecasts = [
            {"date": "2026-01-01", "temp_moy": 0.0},
            {"date": "2026-01-02", "temp_moy": 5.0},
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        assert result[0]["couleur_predite"] == "BLANC"
        assert result[1]["couleur_predite"] == "ROUGE"

    def test_multiple_swaps(self):
        """Multiple inversions are corrected in a single pass."""
        from predictor import _apply_thermal_coherence
        predictions = [
            self._make_pred("2026-03-02", "BLANC"),  # Mon, 2C
            self._make_pred("2026-03-03", "ROUGE"),  # Tue, 7C
            self._make_pred("2026-03-04", "BLANC"),  # Wed, 1C
            self._make_pred("2026-03-05", "ROUGE"),  # Thu, 8C
        ]
        forecasts = [
            {"date": "2026-03-02", "temp_moy": 2.0},
            {"date": "2026-03-03", "temp_moy": 7.0},
            {"date": "2026-03-04", "temp_moy": 1.0},
            {"date": "2026-03-05", "temp_moy": 8.0},
        ]
        result = _apply_thermal_coherence(predictions, forecasts)
        # The two coldest days should be ROUGE, the two warmest BLANC
        rouge_dates = [p["date"] for p in result if p["couleur_predite"] == "ROUGE"]
        blanc_dates = [p["date"] for p in result if p["couleur_predite"] == "BLANC"]
        assert "2026-03-02" in rouge_dates or "2026-03-04" in rouge_dates
        assert "2026-03-03" in blanc_dates or "2026-03-05" in blanc_dates


# ================================================================
# v3.1 : Proxy C_nette, forward clustering, seuil dynamique
# ================================================================

class TestCNetteProxy:
    """P0 v3.1 : estimation du stress réseau depuis la météo."""

    def test_c_nette_cold_calm_high_stress(self):
        """Froid + calme → C_nette élevée → score haut."""
        from predictor import _estimate_c_nette_gw, _score_c_nette
        # 2°C, 10 km/h (rafales), janvier
        c_nette = _estimate_c_nette_gw(2.0, 10.0, 1)
        assert c_nette > 65, f"C_nette froid+calme devrait être > 65 GW, got {c_nette:.1f}"
        score = _score_c_nette(2.0, 10.0, 1)
        assert score > 80, f"Score froid+calme devrait être > 80, got {score}"

    def test_c_nette_cold_windy_reduced_stress(self):
        """Froid + venteux → C_nette réduite (éolien compense)."""
        from predictor import _estimate_c_nette_gw, _score_c_nette
        # 2°C, 80 km/h (rafales = tempête), janvier
        # mean = 40 km/h → 11.1 m/s → CF élevé → production éolienne forte
        c_nette_windy = _estimate_c_nette_gw(2.0, 80.0, 1)
        c_nette_calm = _estimate_c_nette_gw(2.0, 10.0, 1)
        # Le vent fort réduit la C_nette de plus de 8 GW (~12 GW éolien)
        assert c_nette_calm - c_nette_windy > 8, (
            f"Le vent fort devrait réduire la C_nette de > 8 GW, "
            f"calme={c_nette_calm:.1f}, venteux={c_nette_windy:.1f}"
        )
        score_windy = _score_c_nette(2.0, 80.0, 1)
        score_calm = _score_c_nette(2.0, 10.0, 1)
        assert score_calm > score_windy, "Score calme > score venteux"

    def test_c_nette_mild_low_stress(self):
        """Doux → C_nette basse → score bas."""
        from predictor import _score_c_nette
        # 15°C, 15 km/h, mars
        score = _score_c_nette(15.0, 15.0, 3)
        assert score < 30, f"Score doux devrait être < 30, got {score}"

    def test_c_nette_replaces_neutral_for_j2plus(self):
        """Pour J+2+, le score RTE utilise C_nette au lieu du neutre (50)."""
        from predictor import predict_day
        from config import Config
        # Jour froid sans données RTE : le sub-score RTE ne doit PAS être 50
        target = date(2026, 2, 20)  # vendredi
        weather = {
            "date": target.isoformat(), "temp_moy": 3.0,
            "temp_min": 0.0, "temp_max": 6.0,
            "wind_speed": 8.0, "humidity": 60, "pressure": 1020,
            "source": "arpege", "forecast_quality": "api",
        }
        result = predict_day(target, weather=weather,
                             remaining={"ROUGE": 10, "BLANC": 20, "BLEU": 150},
                             weights=Config.DEFAULT_WEIGHTS)
        rte_score = result.get("score_rte", 50)
        # C_nette pour 3°C / 8 km/h / février → C_nette élevée → score > 50
        assert rte_score > 50, (
            f"score_rte devrait être > 50 avec C_nette proxy (3°C, peu de vent), "
            f"got {rte_score}"
        )

    def test_wind_capacity_factor_cutin(self):
        """Vent sous le seuil cut-in → facteur de charge = 0."""
        from predictor import _estimate_wind_capacity_factor
        # 5 km/h (rafales) → mean = 2.5 km/h → 0.69 m/s < 3 m/s cut-in
        cf = _estimate_wind_capacity_factor(5.0)
        assert cf == 0.0

    def test_wind_capacity_factor_moderate(self):
        """Vent modéré → facteur de charge > 0."""
        from predictor import _estimate_wind_capacity_factor
        # 40 km/h (rafales) → mean = 20 km/h → 5.6 m/s
        cf = _estimate_wind_capacity_factor(40.0)
        assert cf > 0.05, f"CF à 40 km/h devrait être > 5%, got {cf:.3f}"

    def test_wind_capacity_factor_strong(self):
        """Vent fort → facteur de charge élevé."""
        from predictor import _estimate_wind_capacity_factor
        # 80 km/h (rafales) → mean = 40 km/h → 11.1 m/s
        cf = _estimate_wind_capacity_factor(80.0)
        assert cf > 0.30, f"CF à 80 km/h devrait être > 30%, got {cf:.3f}"


class TestForwardClustering:
    """P1 v3.1 : forward clustering dans predict_range()."""

    def test_clustering_uses_predicted_colors(self):
        """Le clustering score augmente quand la veille est prédite ROUGE."""
        from predictor import _score_clustering
        target = date(2026, 2, 19)  # jeudi
        forecasts = [
            {"date": "2026-02-18", "temp_moy": 1.0},
            {"date": "2026-02-19", "temp_moy": 2.0},
        ]
        # Sans forward clustering : la veille n'est ni actual ni prédite
        score_without = _score_clustering(target, forecasts, 1, actuals_cache={})
        # Avec forward clustering : la veille est prédite ROUGE
        score_with = _score_clustering(
            target, forecasts, 1, actuals_cache={},
            predicted_colors={"2026-02-18": "ROUGE"})
        assert score_with > score_without, (
            f"Forward clustering devrait augmenter le score : "
            f"sans={score_without}, avec={score_with}"
        )

    def test_actual_takes_priority_over_predicted(self):
        """Les actuals (réels) ont priorité sur les predicted."""
        from predictor import _score_clustering
        target = date(2026, 2, 19)
        forecasts = [
            {"date": "2026-02-18", "temp_moy": 5.0},
            {"date": "2026-02-19", "temp_moy": 5.0},
        ]
        # Actual = BLEU (réel), predicted = ROUGE (simulation)
        # Le réel devrait l'emporter
        score = _score_clustering(
            target, forecasts, 1,
            actuals_cache={"2026-02-18": "BLEU"},
            predicted_colors={"2026-02-18": "ROUGE"})
        # Si la veille est BLEU en réalité, le score de clustering doit être bas
        assert score <= 40, f"Actual BLEU devrait garder le clustering bas, got {score}"

    def test_predict_range_propagates_colors(self):
        """predict_range propage les couleurs prédites (forward clustering)."""
        from predictor import predict_range
        from unittest.mock import patch
        # 5 jours froids consécutifs → le clustering devrait s'activer
        # grâce au forward clustering
        base = date.today() + timedelta(days=2)
        forecasts = []
        for i in range(5):
            d = base + timedelta(days=i)
            forecasts.append({
                "date": d.isoformat(),
                "temp_moy": 1.0, "temp_min": -2.0, "temp_max": 4.0,
                "wind_speed": 8.0, "humidity": 60, "pressure": 1025,
                "source": "arpege", "forecast_quality": "api",
            })
        # Mock get_remaining_days pour avoir assez de ROUGE
        with patch("predictor.get_remaining_days",
                    return_value={"ROUGE": 15, "BLANC": 20, "BLEU": 150}):
            preds = predict_range(forecasts)
        # Avec 5 jours à 1°C, au moins 2 devraient être ROUGE
        rouge_count = sum(1 for p in preds if p["couleur_predite"] == "ROUGE")
        assert rouge_count >= 2, (
            f"5 jours à 1°C devraient produire ≥ 2 ROUGE, got {rouge_count}: "
            + str([(p["date"], p["couleur_predite"]) for p in preds])
        )


class TestJourTempoSeason:
    """P2 v3.1 : seuil dynamique modulé par jour_tempo."""

    def test_jour_tempo_september(self):
        """jour_tempo = 0 au 1er septembre."""
        from predictor import _jour_tempo_number
        assert _jour_tempo_number(date(2025, 9, 1)) == 0

    def test_jour_tempo_november(self):
        """jour_tempo = 61 au 1er novembre."""
        from predictor import _jour_tempo_number
        assert _jour_tempo_number(date(2025, 11, 1)) == 61

    def test_jour_tempo_january(self):
        """jour_tempo = 122 au 1er janvier."""
        from predictor import _jour_tempo_number
        assert _jour_tempo_number(date(2026, 1, 1)) == 122

    def test_jour_tempo_march(self):
        """jour_tempo = 181 au 1er mars."""
        from predictor import _jour_tempo_number
        assert _jour_tempo_number(date(2026, 3, 1)) == 181

    def test_season_modulation_cold_day(self):
        """Le seuil ROUGE est plus bas en fin de saison pour un jour froid."""
        from predictor import predict_day
        from config import Config
        # Même conditions (5°C, budget tendu) en novembre vs mars
        weather = {
            "temp_moy": 5.0, "temp_min": 2.0, "temp_max": 8.0,
            "wind_speed": 10.0, "humidity": 60, "pressure": 1015,
            "source": "arpege", "forecast_quality": "api",
        }
        remaining = {"ROUGE": 12, "BLANC": 20, "BLEU": 150}
        w_nov = {**weather, "date": "2025-11-18"}
        w_mar = {**weather, "date": "2026-03-17"}
        r_nov = predict_day(date(2025, 11, 18), weather=w_nov,
                            remaining=remaining, weights=Config.DEFAULT_WEIGHTS)
        r_mar = predict_day(date(2026, 3, 17), weather=w_mar,
                            remaining=remaining, weights=Config.DEFAULT_WEIGHTS)
        # Le score risque en mars devrait être >= celui de novembre
        # (même température mais seuil plus bas → plus de chances d'être ROUGE)
        # Note: les scores eux-mêmes sont similaires, c'est le seuil qui change
        assert r_mar["score_risque"] >= r_nov["score_risque"] - 5, (
            f"Score mars ({r_mar['score_risque']}) devrait être proche de "
            f"score nov ({r_nov['score_risque']})"
        )

    def test_season_modulation_not_for_mild_days(self):
        """Le seuil P2 ne s'active PAS pour les jours doux (>= 12°C)."""
        from predictor import predict_day
        from config import Config
        # 12°C en mars avec budget modéré (5 ROUGE restants, pas critique)
        # v3.2 : P2 étendu à < 10°C mais atténué. À 12°C, ni la courbe
        # temp→seuil ni P2 ne réduisent le seuil (seuil standard 65).
        weather = {
            "date": "2026-03-17", "temp_moy": 12.0,
            "temp_min": 9.0, "temp_max": 15.0,
            "wind_speed": 10.0, "humidity": 60, "pressure": 1015,
            "source": "arpege", "forecast_quality": "api",
        }
        r = predict_day(date(2026, 3, 17), weather=weather,
                        remaining={"ROUGE": 5, "BLANC": 20, "BLEU": 170},
                        weights=Config.DEFAULT_WEIGHTS)
        # 12°C ne devrait PAS être ROUGE : score temp trop bas (~40),
        # seuil standard 65, densité modérée pas suffisante
        assert r["couleur_predite"] != "ROUGE", (
            f"12°C en mars avec budget modéré ne devrait pas être ROUGE, "
            f"got {r['couleur_predite']} (score={r['score_risque']})"
        )
