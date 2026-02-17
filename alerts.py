"""Système d'alertes WhatsApp via Twilio.

Gestion complète :
  - Envoi d'alertes WhatsApp selon préférences utilisateur
  - Opt-in / opt-out par message (STOP / START)
  - Limite 1 alerte/jour/user
  - Log complet dans sms_logs
  - Lien de gestion des préférences dans chaque message
  - Fix #1 : chiffrement réversible (Fernet) pour envoyer les messages
  - Fix #8 : message erreur "9 chiffres" corrigé
"""

import logging
import secrets
from datetime import datetime, date, timedelta
from config import Config
from database import get_db, hash_phone, encrypt_phone, decrypt_phone

logger = logging.getLogger(__name__)

# ================================================================
# ENVOI WHATSAPP
# ================================================================


def _get_twilio_client():
    """Crée le client Twilio (lazy import)."""
    if not Config.TWILIO_ACCOUNT_SID or not Config.TWILIO_AUTH_TOKEN:
        return None
    try:
        from twilio.rest import Client
        return Client(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
    except ImportError:
        logger.error("[WhatsApp] Module twilio non installé")
        return None


def send_whatsapp(phone_number: str, message: str) -> tuple[str, str]:
    """Envoie un message WhatsApp via Twilio avec retry exponentiel.
    H-03 QA : retry 2 fois avec backoff sur échec Twilio.
    Retourne (sid, statut) — sid vide si échec."""
    client = _get_twilio_client()
    if not client:
        logger.info(f"[WhatsApp] Mode simulation → ****{phone_number[-4:]}: {message[:80]}...")
        return ("SIM_" + datetime.now().strftime("%H%M%S"), "simulated")

    import time
    last_error = None
    wa_from = f"whatsapp:{Config.TWILIO_PHONE_NUMBER}"
    wa_to = f"whatsapp:{phone_number}"
    for attempt in range(3):  # H-03 QA : 3 tentatives max
        try:
            msg = client.messages.create(
                body=message,
                from_=wa_from,
                to=wa_to,
            )
            logger.info(f"[WhatsApp] Envoyé à ****{phone_number[-4:]}: {msg.sid}")
            return (msg.sid, "sent")
        except Exception as e:
            last_error = e
            if attempt < 2:
                wait = 2 ** attempt  # 1s, 2s
                logger.warning(f"[WhatsApp] Tentative {attempt+1} échouée, retry dans {wait}s: {e}")
                time.sleep(wait)

    logger.error(f"[WhatsApp] Échec envoi à ****{phone_number[-4:]} après 3 tentatives: {last_error}")
    return ("", f"error: {last_error}")


# Alias pour compatibilité (logs, tests existants)
send_sms = send_whatsapp


# ================================================================
# FORMATAGE DES MESSAGES
# ================================================================

JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _manage_link(manage_token: str) -> str:
    """Génère le lien de gestion des préférences."""
    if not manage_token:
        return ""
    return f"{Config.BASE_URL}/manage/{manage_token}"


def _format_date_fr(d: date) -> str:
    """Formate une date en français complet : 'mardi 18 février'."""
    jour = JOURS_FR[d.weekday()]
    mois = MOIS_FR[d.month - 1]
    return f"{jour} {d.day} {mois}"


def format_alert_rouge(target_date: date, prediction: dict, manage_token: str = "") -> str:
    """Message d'alerte jour ROUGE."""
    date_fr = _format_date_fr(target_date)
    prob = round(prediction.get("probabilite_rouge", 0) * 100)
    temp = prediction.get("temp_min_prevue", "?")
    temp_str = f"{temp:.0f}°C" if isinstance(temp, (int, float)) else "?"

    msg = (
        f"⚠️ *Calendrier Tempo EDF*\n\n"
        f"*Jour ROUGE* prévu {date_fr} ({prob}% confiance).\n"
        f"Temp min: {temp_str}.\n\n"
        f"👉 Reportez lessive, sèche-linge, four et recharge VE.\n"
        f"Limitez votre conso entre 6h-22h !"
    )
    link = _manage_link(manage_token)
    if link:
        msg += f"\n\n📋 Gérer mes alertes : {link}"
    msg += "\n\n_Répondez STOP pour vous désinscrire._"
    return msg


def format_alert_blanc(target_date: date, prediction: dict, manage_token: str = "") -> str:
    """Message d'alerte jour BLANC."""
    date_fr = _format_date_fr(target_date)
    prob = round(prediction.get("probabilite_blanc", 0) * 100)

    msg = (
        f"📢 *Calendrier Tempo EDF*\n\n"
        f"*Jour BLANC* prévu {date_fr} ({prob}% confiance).\n"
        f"Tarif intermédiaire."
    )
    link = _manage_link(manage_token)
    if link:
        msg += f"\n\n📋 Gérer mes alertes : {link}"
    msg += "\n\n_Répondez STOP pour vous désinscrire._"
    return msg


def format_alert_officiel(target_date: date, couleur: str, manage_token: str = "") -> str:
    """Message pour couleur officielle confirmée."""
    date_fr = _format_date_fr(target_date)
    emoji = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(couleur, "")

    msg = f"{emoji} *Tempo confirmé : {couleur}* {date_fr}."
    if couleur == "ROUGE":
        msg += "\nHeures pleines 6h-22h très chères !\n👉 Reportez vos machines."
    link = _manage_link(manage_token)
    if link:
        msg += f"\n\n📋 Gérer mes alertes : {link}"
    msg += "\n\n_Répondez STOP pour vous désinscrire._"
    return msg


def format_recap_hebdo(predictions: list[dict], manage_token: str = "") -> str:
    """Récapitulatif hebdomadaire (dimanche soir)."""
    lines = ["📊 *Calendrier Tempo EDF — Semaine à venir*\n"]
    for p in predictions[:7]:
        d = date.fromisoformat(p["date"])
        jour = JOURS_FR[d.weekday()][:3]
        mois = MOIS_FR[d.month - 1][:3]
        emoji = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(p["couleur_predite"], "❓")
        lines.append(f"  {emoji} {jour}. {d.day} {mois}.")
    link = _manage_link(manage_token)
    if link:
        lines.append(f"\n📋 Gérer mes alertes : {link}")
    lines.append("\n_Répondez STOP pour vous désinscrire._")
    return "\n".join(lines)


# ================================================================
# Fix #1 : récupérer le vrai numéro depuis phone_encrypted
# ================================================================

def _get_user_phone(user) -> str:
    """Déchiffre le numéro de téléphone d'un utilisateur pour envoi WhatsApp."""
    encrypted = user["phone_encrypted"]
    if not encrypted:
        logger.error(f"[WhatsApp] User {user['id']} n'a pas de numéro chiffré")
        return ""
    try:
        return decrypt_phone(encrypted)
    except Exception as e:
        logger.error(f"[WhatsApp] Erreur déchiffrement user {user['id']}: {e}")
        return ""


def _get_manage_token(user) -> str:
    """Récupère le token de gestion d'un utilisateur."""
    try:
        return user["manage_token"] or ""
    except (KeyError, IndexError):
        return ""


# ================================================================
# LOGIQUE D'ENVOI AVEC FILTRAGE
# ================================================================

def send_alerts_for_prediction(target_date: date, prediction: dict):
    """Envoie les alertes WhatsApp pour une prédiction (rouge ou blanc).
    Respecte les préférences et la limite 1 alerte/jour."""
    couleur = prediction["couleur_predite"]
    if couleur == "BLEU":
        return

    conn = get_db()
    try:
        if couleur == "ROUGE":
            prob_pct = round(prediction.get("probabilite_rouge", 0) * 100)
            users = conn.execute(
                """SELECT id, phone_encrypted, seuil_alerte_rouge, delai_alerte, manage_token
                   FROM users
                   WHERE actif = 1 AND seuil_alerte_rouge <= ?""",
                (prob_pct,)
            ).fetchall()
            format_fn = format_alert_rouge
            type_alerte = "prediction_rouge"
        else:  # BLANC
            users = conn.execute(
                """SELECT id, phone_encrypted, seuil_alerte_rouge, delai_alerte, manage_token
                   FROM users WHERE actif = 1 AND alerte_blanc = 1"""
            ).fetchall()
            format_fn = format_alert_blanc
            type_alerte = "prediction_blanc"

        today_str = date.today().isoformat()
        # Fix audit DB : range comparison au lieu de LIKE (index-friendly)
        today_start = today_str + "T00:00:00"
        today_end = today_str + "T23:59:59"

        for user in users:
            # M-09 QA : dédup par date cible (pas par date d'envoi)
            # Vérifie aussi la date d'envoi pour limiter à 1/jour
            existing = conn.execute(
                """SELECT id FROM sms_logs
                   WHERE user_id = ? AND date_envoi >= ? AND date_envoi <= ?
                   AND type_alerte IN ('prediction_rouge', 'prediction_blanc')""",
                (user["id"], today_start, today_end),
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

            token = _get_manage_token(user)
            message = format_fn(target_date, prediction, manage_token=token)
            sid, statut = send_whatsapp(phone, message)

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
                """SELECT id, phone_encrypted, manage_token
                   FROM users WHERE actif = 1 AND seuil_alerte_rouge > 0"""
            ).fetchall()
        else:
            users = conn.execute(
                """SELECT id, phone_encrypted, manage_token
                   FROM users WHERE actif = 1 AND alerte_blanc = 1"""
            ).fetchall()

        today_str = date.today().isoformat()
        # Fix audit DB : range comparison au lieu de LIKE (index-friendly)
        today_start = today_str + "T00:00:00"
        today_end = today_str + "T23:59:59"

        for user in users:
            # Dédup cross-type : pas d'alerte officielle si déjà reçu
            # une prédiction OU un officiel aujourd'hui (BUG-04 QA)
            existing = conn.execute(
                """SELECT id FROM sms_logs
                   WHERE user_id = ? AND date_envoi >= ? AND date_envoi <= ?
                   AND type_alerte IN ('officiel', 'prediction_rouge', 'prediction_blanc')""",
                (user["id"], today_start, today_end),
            ).fetchone()

            if existing:
                continue

            phone = _get_user_phone(user)
            if not phone:
                continue

            token = _get_manage_token(user)
            message = format_alert_officiel(target_date, couleur, manage_token=token)
            sid, statut = send_whatsapp(phone, message)
            _log_sms(conn, user["id"], "officiel", couleur, message, statut, sid)

        conn.commit()
    finally:
        conn.close()


def send_weekly_recap(predictions: list[dict]):
    """Envoie le récapitulatif hebdomadaire aux users inscrits."""
    conn = get_db()
    try:
        users = conn.execute(
            """SELECT id, phone_encrypted, manage_token
               FROM users WHERE actif = 1 AND recap_hebdo = 1"""
        ).fetchall()

        if not users:
            return

        for user in users:
            phone = _get_user_phone(user)
            if not phone:
                continue

            token = _get_manage_token(user)
            message = format_recap_hebdo(predictions, manage_token=token)
            sid, statut = send_whatsapp(phone, message)
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
         statut if sid else "failed",
         sid or "",
         erreur),
    )


# ================================================================
# GESTION USERS (inscription / désinscription)
# ================================================================

def register_user(phone_number: str, seuil_rouge: int = 70,
                  delai: int = 1, alerte_blanc: bool = False,
                  recap_hebdo: bool = False) -> dict:
    """Inscrit un nouvel utilisateur aux alertes WhatsApp."""
    phone_clean = phone_number.strip().replace(" ", "")
    # Fix #8 : message corrigé "9 chiffres"
    if not phone_clean.startswith("+33") or len(phone_clean) != 12:
        return {"error": "Format invalide. Utilisez +33XXXXXXXXX (9 chiffres après +33)."}
    # Fix #13 audit v4 : verifier que les 9 derniers caracteres sont des chiffres
    if not phone_clean[3:].isdigit():
        return {"error": "Le numéro ne doit contenir que des chiffres après +33."}

    phone_h = hash_phone(phone_clean)
    phone_enc = encrypt_phone(phone_clean)  # Fix #1 : chiffrement réversible
    last4 = phone_clean[-4:]
    manage_token = secrets.token_urlsafe(16)

    conn = get_db()
    now = datetime.now().isoformat()
    try:
        existing = conn.execute(
            "SELECT id, actif FROM users WHERE phone_hash = ?", (phone_h,)
        ).fetchone()

        if existing:
            if existing["actif"]:
                return {"error": "Ce numéro est déjà inscrit."}
            else:
                # Réactiver + mettre à jour chiffré ET préférences (BUG-05 QA)
                conn.execute(
                    """UPDATE users SET actif = 1, phone_encrypted = ?,
                       seuil_alerte_rouge = ?, delai_alerte = ?,
                       alerte_blanc = ?, recap_hebdo = ?,
                       manage_token = ?,
                       updated_at = ? WHERE id = ?""",
                    (phone_enc, seuil_rouge, delai,
                     int(alerte_blanc), int(recap_hebdo),
                     manage_token,
                     now, existing["id"]),
                )
                conn.commit()
                return {"success": True, "user_id": existing["id"],
                        "manage_token": manage_token,
                        "message": "Compte réactivé avec vos nouvelles préférences !"}

        cursor = conn.execute(
            """INSERT INTO users
               (phone_hash, phone_encrypted, phone_last4, seuil_alerte_rouge, delai_alerte,
                alerte_blanc, recap_hebdo, manage_token, actif, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (phone_h, phone_enc, last4, seuil_rouge, delai,
             int(alerte_blanc), int(recap_hebdo), manage_token, now, now),
        )
        conn.commit()
        return {"success": True, "user_id": cursor.lastrowid,
                "manage_token": manage_token,
                "message": "Inscription réussie ! Vous recevrez les alertes Tempo par WhatsApp."}

    finally:
        conn.close()


def unsubscribe_user(phone_number: str) -> dict:
    """Désactive un utilisateur (opt-out)."""
    phone_h = hash_phone(phone_number.strip().replace(" ", ""))
    conn = get_db()
    now = datetime.now().isoformat()
    try:
        user = conn.execute(
            "SELECT id FROM users WHERE phone_hash = ?", (phone_h,)
        ).fetchone()

        if not user:
            # H-07 QA : message générique (ne pas révéler si le numéro existe)
            return {"error": "Désinscription impossible. Vérifiez votre numéro."}

        conn.execute(
            "UPDATE users SET actif = 0, updated_at = ? WHERE id = ?",
            (now, user["id"]),
        )
        conn.commit()
        return {"success": True, "message": "Désinscription effectuée."}
    finally:
        conn.close()


def get_user_count() -> dict:
    """Stats utilisateurs pour le dashboard.
    Fix audit DB : requete unique au lieu de 2 COUNT separees."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as total, SUM(actif) as actifs FROM users"
        ).fetchone()
        return {"total": row["total"], "actifs": row["actifs"] or 0}
    finally:
        conn.close()


# ================================================================
# GESTION DES PRÉFÉRENCES PAR TOKEN
# ================================================================

def get_user_by_token(token: str) -> dict | None:
    """Récupère un utilisateur actif par son token de gestion."""
    if not token:
        return None
    conn = get_db()
    try:
        user = conn.execute(
            """SELECT id, phone_last4, seuil_alerte_rouge, delai_alerte,
                      alerte_blanc, recap_hebdo, actif, manage_token
               FROM users WHERE manage_token = ? AND actif = 1""",
            (token,)
        ).fetchone()
        if user:
            return dict(user)
        return None
    finally:
        conn.close()


def update_user_preferences(token: str, seuil_rouge: int = 70,
                            delai: int = 1, alerte_blanc: bool = False,
                            recap_hebdo: bool = False) -> dict:
    """Met à jour les préférences d'un utilisateur via son token."""
    if not token:
        return {"error": "Token invalide."}

    conn = get_db()
    now = datetime.now().isoformat()
    try:
        user = conn.execute(
            "SELECT id, actif FROM users WHERE manage_token = ?", (token,)
        ).fetchone()

        if not user:
            return {"error": "Lien invalide ou expiré."}

        if not user["actif"]:
            return {"error": "Ce compte est désactivé. Réinscrivez-vous."}

        conn.execute(
            """UPDATE users SET seuil_alerte_rouge = ?, delai_alerte = ?,
               alerte_blanc = ?, recap_hebdo = ?, updated_at = ?
               WHERE id = ?""",
            (seuil_rouge, delai, int(alerte_blanc), int(recap_hebdo),
             now, user["id"]),
        )
        conn.commit()
        return {"success": True, "message": "Préférences mises à jour !"}
    finally:
        conn.close()


def regenerate_manage_token(token: str) -> dict:
    """Régénère le token de gestion (sécurité)."""
    if not token:
        return {"error": "Token invalide."}

    conn = get_db()
    now = datetime.now().isoformat()
    try:
        user = conn.execute(
            "SELECT id FROM users WHERE manage_token = ? AND actif = 1", (token,)
        ).fetchone()

        if not user:
            return {"error": "Lien invalide ou expiré."}

        new_token = secrets.token_urlsafe(16)
        conn.execute(
            "UPDATE users SET manage_token = ?, updated_at = ? WHERE id = ?",
            (new_token, now, user["id"]),
        )
        conn.commit()
        return {"success": True, "new_token": new_token}
    finally:
        conn.close()


# ================================================================
# WEBHOOK ENTRANT (WhatsApp / SMS)
# ================================================================

def handle_incoming_sms(from_number: str, body: str) -> str:
    """Traite un message entrant (webhook Twilio). Gère STOP/START.
    Retourne le message de réponse."""
    body_clean = body.strip().upper()
    phone_clean = from_number.strip().replace(" ", "")
    # Twilio WhatsApp préfixe avec "whatsapp:", on le retire
    if phone_clean.startswith("whatsapp:"):
        phone_clean = phone_clean[len("whatsapp:"):]

    if body_clean in ("STOP", "ARRET", "DESINSCRIRE", "QUIT", "CANCEL"):
        result = unsubscribe_user(phone_clean)
        if result.get("success"):
            logger.info(f"[WhatsApp IN] Désinscription: ****{phone_clean[-4:]}")
            return "Vous êtes désinscrit du Calendrier Tempo EDF. Répondez START pour vous réinscrire."
        else:
            logger.info(f"[WhatsApp IN] Tentative STOP numéro inconnu: ****{phone_clean[-4:]}")
            return "Ce numéro n'est pas inscrit au Calendrier Tempo EDF."

    if body_clean in ("START", "OUI", "INSCRIRE"):
        result = register_user(phone_clean)
        if result.get("success"):
            logger.info(f"[WhatsApp IN] Réinscription: ****{phone_clean[-4:]}")
            return "Vous êtes réinscrit aux alertes du Calendrier Tempo EDF !"
        else:
            return result.get("error", "Erreur lors de la réinscription.")

    return "Calendrier Tempo EDF : répondez STOP pour vous désinscrire ou START pour vous réinscrire."


def cleanup_inactive_users(months: int = 6):
    """Supprime les users inactifs depuis plus de N mois (RGPD).

    Fix audit DB v13 : ON DELETE CASCADE supprime automatiquement
    les sms_logs associes via la FK.
    """
    cutoff = (datetime.now() - timedelta(days=months * 30)).isoformat()
    conn = get_db()
    try:
        deleted = conn.execute(
            "DELETE FROM users WHERE actif = 0 AND updated_at < ?",
            (cutoff,),
        ).rowcount
        conn.commit()
        if deleted:
            logger.info(f"[RGPD] {deleted} users inactifs supprimés (sms_logs cascade)")
    finally:
        conn.close()
