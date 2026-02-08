"""Système d'alertes SMS via Twilio.

Gestion complète :
  - Envoi d'alertes selon préférences utilisateur
  - Opt-in / opt-out par SMS (STOP / START)
  - Limite 1 alerte/jour/user
  - Log complet dans sms_logs
  - Fix #1 : chiffrement réversible (Fernet) pour envoyer les SMS
  - Fix #8 : message erreur "9 chiffres" corrigé
"""

import logging
from datetime import datetime, date, timedelta
from config import Config
from database import get_db, hash_phone, encrypt_phone, decrypt_phone

logger = logging.getLogger(__name__)

# ================================================================
# ENVOI SMS
# ================================================================


def _get_twilio_client():
    """Crée le client Twilio (lazy import)."""
    if not Config.TWILIO_ACCOUNT_SID or not Config.TWILIO_AUTH_TOKEN:
        return None
    try:
        from twilio.rest import Client
        return Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
    except ImportError:
        logger.error("[SMS] Module twilio non installé")
        return None


def send_sms(phone_number: str, message: str) -> tuple[str, str]:
    """Envoie un SMS via Twilio.
    Retourne (sid, statut) — sid vide si échec."""
    client = _get_twilio_client()
    if not client:
        logger.info(f"[SMS] Mode simulation → ****{phone_number[-4:]}: {message[:60]}...")
        return ("SIM_" + datetime.now().strftime("%H%M%S"), "simulated")

    try:
        msg = client.messages.create(
            body=message,
            from_=Config.TWILIO_PHONE_NUMBER,
            to=phone_number,
        )
        logger.info(f"[SMS] Envoyé à ****{phone_number[-4:]}: {msg.sid}")
        return (msg.sid, "sent")
    except Exception as e:
        logger.error(f"[SMS] Échec envoi à ****{phone_number[-4:]}: {e}")
        return ("", f"error: {e}")


# ================================================================
# FORMATAGE DES MESSAGES
# ================================================================

JOURS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def format_alert_rouge(target_date: date, prediction: dict) -> str:
    """Message d'alerte jour ROUGE."""
    jour = JOURS_FR[target_date.weekday()]
    date_fr = target_date.strftime("%d/%m")
    prob = round(prediction.get("probabilite_rouge", 0) * 100)
    temp = prediction.get("temp_min_prevue", "?")
    temp_str = f"{temp:.0f}°C" if isinstance(temp, (int, float)) else "?"

    return (
        f"⚠️ TempoForecast: Jour ROUGE prévu {jour} {date_fr} "
        f"({prob}% confiance). "
        f"Temp min: {temp_str}. "
        f"Limitez votre conso entre 6h-22h !\n"
        f"STOP pour se désinscrire."
    )


def format_alert_blanc(target_date: date, prediction: dict) -> str:
    """Message d'alerte jour BLANC."""
    jour = JOURS_FR[target_date.weekday()]
    date_fr = target_date.strftime("%d/%m")
    prob = round(prediction.get("probabilite_blanc", 0) * 100)

    return (
        f"📢 TempoForecast: Jour BLANC prévu {jour} {date_fr} "
        f"({prob}% confiance). "
        f"Tarif intermédiaire.\n"
        f"STOP pour se désinscrire."
    )


def format_alert_officiel(target_date: date, couleur: str) -> str:
    """Message pour couleur officielle confirmée."""
    jour = JOURS_FR[target_date.weekday()]
    date_fr = target_date.strftime("%d/%m")
    emoji = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(couleur, "")

    msg = f"{emoji} Tempo confirmé: {couleur} {jour} {date_fr}."
    if couleur == "ROUGE":
        msg += " Heures pleines 6h-22h très chères !"
    return msg + "\nSTOP pour se désinscrire."


def format_recap_hebdo(predictions: list[dict]) -> str:
    """Récapitulatif hebdomadaire (dimanche soir)."""
    lines = ["📊 TempoForecast - Semaine à venir:"]
    for p in predictions[:7]:
        d = date.fromisoformat(p["date"])
        jour = JOURS_FR[d.weekday()][:3]
        emoji = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(p["couleur_predite"], "❓")
        lines.append(f"  {emoji} {jour} {d.strftime('%d/%m')}")
    lines.append("STOP pour se désinscrire.")
    return "\n".join(lines)


# ================================================================
# Fix #1 : récupérer le vrai numéro depuis phone_encrypted
# ================================================================

def _get_user_phone(user) -> str:
    """Déchiffre le numéro de téléphone d'un utilisateur pour envoi SMS."""
    encrypted = user["phone_encrypted"]
    if not encrypted:
        logger.error(f"[SMS] User {user['id']} n'a pas de numéro chiffré")
        return ""
    try:
        return decrypt_phone(encrypted)
    except Exception as e:
        logger.error(f"[SMS] Erreur déchiffrement user {user['id']}: {e}")
        return ""


# ================================================================
# LOGIQUE D'ENVOI AVEC FILTRAGE
# ================================================================

def send_alerts_for_prediction(target_date: date, prediction: dict):
    """Envoie les alertes SMS pour une prédiction (rouge ou blanc).
    Respecte les préférences et la limite 1 alerte/jour."""
    couleur = prediction["couleur_predite"]
    if couleur == "BLEU":
        return

    conn = get_db()
    try:
        if couleur == "ROUGE":
            prob_pct = round(prediction.get("probabilite_rouge", 0) * 100)
            users = conn.execute(
                """SELECT * FROM users
                   WHERE actif = 1 AND seuil_alerte_rouge <= ?""",
                (prob_pct,)
            ).fetchall()
            format_fn = format_alert_rouge
            type_alerte = "prediction_rouge"
        else:  # BLANC
            users = conn.execute(
                "SELECT * FROM users WHERE actif = 1 AND alerte_blanc = 1"
            ).fetchall()
            format_fn = format_alert_blanc
            type_alerte = "prediction_blanc"

        today_str = date.today().isoformat()

        for user in users:
            # Limite 1 alerte/jour
            existing = conn.execute(
                """SELECT id FROM sms_logs
                   WHERE user_id = ? AND date_envoi LIKE ?
                   AND type_alerte LIKE 'prediction%'""",
                (user["id"], f"{today_str}%"),
            ).fetchone()

            if existing:
                continue

            # Vérifier le délai d'alerte (J-1, J-2, J-3)
            delta = (target_date - date.today()).days
            if delta > user["delai_alerte"]:
                continue

            # Fix #1 : déchiffrer le vrai numéro pour l'envoi
            phone = _get_user_phone(user)
            if not phone:
                continue

            message = format_fn(target_date, prediction)
            sid, statut = send_sms(phone, message)

            _log_sms(conn, user["id"], type_alerte, couleur, message, statut, sid)

        conn.commit()
        logger.info(f"[Alertes] Envoi terminé pour {couleur} {target_date}")

    finally:
        conn.close()


def send_official_alerts(target_date: date, couleur: str):
    """Envoie les alertes pour une couleur officiellement confirmée."""
    if couleur not in ("ROUGE", "BLANC"):
        return

    conn = get_db()
    try:
        if couleur == "ROUGE":
            users = conn.execute(
                "SELECT * FROM users WHERE actif = 1 AND seuil_alerte_rouge > 0"
            ).fetchall()
        else:
            users = conn.execute(
                "SELECT * FROM users WHERE actif = 1 AND alerte_blanc = 1"
            ).fetchall()

        message = format_alert_officiel(target_date, couleur)

        for user in users:
            existing = conn.execute(
                """SELECT id FROM sms_logs
                   WHERE user_id = ? AND date_envoi LIKE ?
                   AND type_alerte = 'officiel'""",
                (user["id"], f"{date.today().isoformat()}%"),
            ).fetchone()

            if existing:
                continue

            phone = _get_user_phone(user)
            if not phone:
                continue

            sid, statut = send_sms(phone, message)
            _log_sms(conn, user["id"], "officiel", couleur, message, statut, sid)

        conn.commit()
    finally:
        conn.close()


def send_weekly_recap(predictions: list[dict]):
    """Envoie le récapitulatif hebdomadaire aux users inscrits."""
    conn = get_db()
    try:
        users = conn.execute(
            "SELECT * FROM users WHERE actif = 1 AND recap_hebdo = 1"
        ).fetchall()

        if not users:
            return

        message = format_recap_hebdo(predictions)

        for user in users:
            phone = _get_user_phone(user)
            if not phone:
                continue

            sid, statut = send_sms(phone, message)
            _log_sms(conn, user["id"], "recap_hebdo", "", message, statut, sid)

        conn.commit()
        logger.info(f"[Alertes] Récap hebdo envoyé à {len(users)} users")
    finally:
        conn.close()


def _log_sms(conn, user_id: int, type_alerte: str, couleur: str,
             message: str, statut: str, sid: str):
    """Enregistre un envoi dans sms_logs."""
    erreur = statut if "error" in statut else ""
    conn.execute(
        """INSERT INTO sms_logs
           (user_id, type_alerte, couleur, message_body, date_envoi, statut, twilio_sid, erreur)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, type_alerte, couleur, message,
         datetime.now().isoformat(),
         "sent" if sid else "failed",
         sid or "",
         erreur),
    )


# ================================================================
# GESTION USERS (inscription / désinscription)
# ================================================================

def register_user(phone_number: str, seuil_rouge: int = 70,
                  delai: int = 1, alerte_blanc: bool = False,
                  recap_hebdo: bool = False) -> dict:
    """Inscrit un nouvel utilisateur aux alertes SMS."""
    phone_clean = phone_number.strip().replace(" ", "")
    # Fix #8 : message corrigé "9 chiffres"
    if not phone_clean.startswith("+33") or len(phone_clean) != 12:
        return {"error": "Format invalide. Utilisez +33XXXXXXXXX (9 chiffres après +33)."}

    phone_h = hash_phone(phone_clean)
    phone_enc = encrypt_phone(phone_clean)  # Fix #1 : chiffrement réversible
    last4 = phone_clean[-4:]

    conn = get_db()
    now = datetime.now().isoformat()
    try:
        existing = conn.execute(
            "SELECT * FROM users WHERE phone_hash = ?", (phone_h,)
        ).fetchone()

        if existing:
            if existing["actif"]:
                return {"error": "Ce numéro est déjà inscrit."}
            else:
                # Réactiver + mettre à jour le chiffré (la clé a pu changer)
                conn.execute(
                    "UPDATE users SET actif = 1, phone_encrypted = ?, updated_at = ? WHERE id = ?",
                    (phone_enc, now, existing["id"]),
                )
                conn.commit()
                return {"success": True, "user_id": existing["id"], "message": "Compte réactivé !"}

        cursor = conn.execute(
            """INSERT INTO users
               (phone_hash, phone_encrypted, phone_last4, seuil_alerte_rouge, delai_alerte,
                alerte_blanc, recap_hebdo, actif, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (phone_h, phone_enc, last4, seuil_rouge, delai,
             int(alerte_blanc), int(recap_hebdo), now, now),
        )
        conn.commit()
        return {"success": True, "user_id": cursor.lastrowid,
                "message": "Inscription réussie ! Vous recevrez les alertes Tempo."}

    finally:
        conn.close()


def unsubscribe_user(phone_number: str) -> dict:
    """Désactive un utilisateur (opt-out)."""
    phone_h = hash_phone(phone_number.strip().replace(" ", ""))
    conn = get_db()
    now = datetime.now().isoformat()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE phone_hash = ?", (phone_h,)
        ).fetchone()

        if not user:
            return {"error": "Numéro non trouvé."}

        conn.execute(
            "UPDATE users SET actif = 0, updated_at = ? WHERE id = ?",
            (now, user["id"]),
        )
        conn.commit()
        return {"success": True, "message": "Désinscription effectuée."}
    finally:
        conn.close()


def get_user_count() -> dict:
    """Stats utilisateurs pour le dashboard."""
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        actifs = conn.execute("SELECT COUNT(*) as c FROM users WHERE actif = 1").fetchone()["c"]
        return {"total": total, "actifs": actifs}
    finally:
        conn.close()


def cleanup_inactive_users(months: int = 6):
    """Supprime les users inactifs depuis plus de N mois (RGPD)."""
    cutoff = (datetime.now() - timedelta(days=months * 30)).isoformat()
    conn = get_db()
    try:
        deleted = conn.execute(
            "DELETE FROM users WHERE actif = 0 AND updated_at < ?",
            (cutoff,),
        ).rowcount
        conn.commit()
        if deleted:
            logger.info(f"[RGPD] {deleted} users inactifs supprimés")
    finally:
        conn.close()
