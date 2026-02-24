"""Système d'alertes WhatsApp via Meta Cloud API (Facebook Developer).

Gestion complète :
  - Envoi d'alertes WhatsApp selon préférences utilisateur
  - Opt-in / opt-out par message (STOP / START / RECAP)
  - Limite 1 alerte/jour/user
  - Log complet dans sms_logs
  - Lien de gestion des préférences dans chaque message
  - Saison active uniquement (1er nov — 31 mars)
  - Message de bienvenue à l'inscription
  - Alerte sur changement de prédiction (ex: BLEU → ROUGE)
"""

import logging
import secrets
from datetime import datetime, date, timedelta
from config import Config
from database import get_db, hash_phone, encrypt_phone, decrypt_phone

logger = logging.getLogger(__name__)

# ================================================================
# ENVOI WHATSAPP (Meta Cloud API)
# ================================================================


def _is_whatsapp_configured() -> bool:
    """Vérifie si les credentials Meta WhatsApp sont configurées."""
    return bool(Config.WHATSAPP_TOKEN and Config.WHATSAPP_PHONE_NUMBER_ID)


def _is_red_season(d: date | None = None) -> bool:
    """Vérifie si la date est en saison rouge (1er nov — 31 mars).
    Les alertes ne sont envoyées que pendant cette période."""
    d = d or date.today()
    return d.month >= 11 or d.month <= 3


def send_whatsapp(phone_number: str, message: str) -> tuple[str, str]:
    """Envoie un message WhatsApp via Meta Cloud API avec retry exponentiel.
    H-03 QA : retry 2 fois avec backoff sur échec API.
    Retourne (message_id, statut) — message_id vide si échec."""
    if not _is_whatsapp_configured():
        logger.info(f"[WhatsApp] Mode simulation → ****{phone_number[-4:]}: {message[:80]}...")
        return ("SIM_" + datetime.now().strftime("%H%M%S"), "simulated")

    import time
    import httpx

    last_error = None
    url = (
        f"https://graph.facebook.com/{Config.WHATSAPP_API_VERSION}"
        f"/{Config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    headers = {
        "Authorization": f"Bearer {Config.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    # Format international sans le "+" pour l'API Meta
    recipient = phone_number.lstrip("+")
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": message},
    }

    for attempt in range(3):  # H-03 QA : 3 tentatives max
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(url, headers=headers, json=payload)
            if resp.status_code == 200 or resp.status_code == 201:
                data = resp.json()
                msg_id = data.get("messages", [{}])[0].get("id", "")
                logger.info(f"[WhatsApp] Envoyé à ****{phone_number[-4:]}: {msg_id}")
                return (msg_id, "sent")
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning(f"[WhatsApp] Tentative {attempt+1} échouée: {last_error}")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[WhatsApp] Tentative {attempt+1} échouée: {last_error}")
        if attempt < 2:
            wait = 2 ** attempt  # 1s, 2s
            time.sleep(wait)

    logger.error(f"[WhatsApp] Échec envoi à ****{phone_number[-4:]} après 3 tentatives: {last_error}")
    return ("", f"error: {last_error}")


def send_whatsapp_template(phone_number: str, template_name: str,
                           components: list[dict] | None = None) -> tuple[str, str]:
    """Envoie un message WhatsApp via template pré-approuvé Meta.
    Requis pour les messages business-initiated (hors fenêtre 24h).
    Retourne (message_id, statut)."""
    if not _is_whatsapp_configured():
        logger.info(f"[WhatsApp] Mode simulation template '{template_name}' → ****{phone_number[-4:]}")
        return ("SIM_" + datetime.now().strftime("%H%M%S"), "simulated")

    import time
    import httpx

    last_error = None
    url = (
        f"https://graph.facebook.com/{Config.WHATSAPP_API_VERSION}"
        f"/{Config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    headers = {
        "Authorization": f"Bearer {Config.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    recipient = phone_number.lstrip("+")
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": Config.WHATSAPP_TEMPLATE_LANG},
        },
    }
    if components:
        payload["template"]["components"] = components

    for attempt in range(3):
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                msg_id = data.get("messages", [{}])[0].get("id", "")
                logger.info(f"[WhatsApp] Template '{template_name}' envoyé à ****{phone_number[-4:]}: {msg_id}")
                return (msg_id, "sent")
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning(f"[WhatsApp] Template tentative {attempt+1} échouée: {last_error}")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[WhatsApp] Template tentative {attempt+1} échouée: {last_error}")
        if attempt < 2:
            wait = 2 ** attempt
            time.sleep(wait)

    logger.error(f"[WhatsApp] Échec template '{template_name}' → ****{phone_number[-4:]} après 3 tentatives: {last_error}")
    return ("", f"error: {last_error}")


def _tpl_body(*params: str) -> list[dict]:
    """Construit les components body pour un template WhatsApp."""
    if not params:
        return []
    return [{
        "type": "body",
        "parameters": [{"type": "text", "text": str(p)} for p in params],
    }]


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


def _msg_footer(manage_token: str) -> str:
    """Footer commun : lien gestion + STOP."""
    parts = []
    link = _manage_link(manage_token)
    if link:
        parts.append(f"📋 Gérer mes alertes : {link}")
    parts.append("_Répondez STOP ou RECAP._")
    return "\n\n".join(parts)


# ================================================================
# TEMPLATE BUILDERS (paramètres pour templates Meta)
# ================================================================

def _build_welcome_template(predictions: list[dict], manage_token: str) -> tuple[str, list[dict]]:
    """Construit le template bienvenue. Params: {{1}}=prévisions, {{2}}=URL gestion."""
    pred_lines = []
    for p in predictions[:5]:
        d = date.fromisoformat(p["date"])
        jour = JOURS_FR[d.weekday()][:3]
        mois = MOIS_FR[d.month - 1][:3]
        couleur = p.get("couleur_predite", "?")
        emoji = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(couleur, "❓")
        pred_lines.append(f"{emoji} {jour}. {d.day} {mois}.")
    pred_text = "\n".join(pred_lines) if pred_lines else "Aucune prévision disponible."
    manage_url = _manage_link(manage_token) or Config.BASE_URL
    return Config.WHATSAPP_TEMPLATE_WELCOME, _tpl_body(pred_text, manage_url)


def _build_rouge_template(target_date: date, prediction: dict, manage_token: str) -> tuple[str, list[dict]]:
    """Construit le template alerte rouge. Params: {{1}}=date, {{2}}=proba, {{3}}=temp, {{4}}=URL."""
    date_fr = _format_date_fr(target_date)
    prob = str(round(prediction.get("probabilite_rouge", 0) * 100))
    temp = prediction.get("temp_min_prevue", "?")
    temp_str = f"{temp:.0f}°C" if isinstance(temp, (int, float)) else "?"
    manage_url = _manage_link(manage_token) or Config.BASE_URL
    return Config.WHATSAPP_TEMPLATE_ALERT_ROUGE, _tpl_body(date_fr, prob, temp_str, manage_url)


def _build_blanc_template(target_date: date, prediction: dict, manage_token: str) -> tuple[str, list[dict]]:
    """Construit le template alerte blanc. Params: {{1}}=date, {{2}}=proba, {{3}}=URL."""
    date_fr = _format_date_fr(target_date)
    prob = str(round(prediction.get("probabilite_blanc", 0) * 100))
    manage_url = _manage_link(manage_token) or Config.BASE_URL
    return Config.WHATSAPP_TEMPLATE_ALERT_BLANC, _tpl_body(date_fr, prob, manage_url)


def _build_confirmation_template(target_date: date, couleur: str, manage_token: str) -> tuple[str, list[dict]]:
    """Construit le template confirmation. Params: {{1}}=emoji, {{2}}=couleur, {{3}}=date, {{4}}=conseil, {{5}}=URL."""
    emoji = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(couleur, "")
    date_fr = _format_date_fr(target_date)
    if couleur == "ROUGE":
        advice = "Heures pleines 6h-22h à 0,76€/kWh. Reportez vos machines !"
    elif couleur == "BLANC":
        advice = "Tarif intermédiaire. OK pour les machines, évitez le four en heures pleines."
    else:
        advice = "Tarif bleu, le moins cher. Profitez-en !"
    manage_url = _manage_link(manage_token) or Config.BASE_URL
    return Config.WHATSAPP_TEMPLATE_CONFIRMATION, _tpl_body(emoji, couleur, date_fr, advice, manage_url)


def _build_change_template(target_date: date, old_color: str, new_color: str,
                           manage_token: str) -> tuple[str, list[dict]]:
    """Construit le template changement. Params: {{1}}=date, {{2}}=ancien, {{3}}=nouveau, {{4}}=conseil, {{5}}=URL."""
    date_fr = _format_date_fr(target_date)
    emoji_old = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(old_color, "")
    emoji_new = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(new_color, "")
    old_str = f"{emoji_old} {old_color}"
    new_str = f"{emoji_new} {new_color}"
    if new_color == "ROUGE":
        advice = "Heures pleines à 0,76€/kWh. Reportez vos machines !"
    elif new_color == "BLANC" and old_color == "ROUGE":
        advice = "Bonne nouvelle ! Tarif intermédiaire, moins cher que prévu."
    elif new_color == "BLEU" and old_color == "ROUGE":
        advice = "Bonne nouvelle ! Tarif bleu, le moins cher."
    else:
        advice = "Consultez le calendrier pour adapter vos usages."
    manage_url = _manage_link(manage_token) or Config.BASE_URL
    return Config.WHATSAPP_TEMPLATE_CHANGE, _tpl_body(date_fr, old_str, new_str, advice, manage_url)


def _build_recap_template(predictions: list[dict], manage_token: str) -> tuple[str, list[dict]]:
    """Construit le template recap hebdo. Params: {{1}}=prévisions, {{2}}=résumé, {{3}}=URL."""
    lines = []
    rouge_count = 0
    blanc_count = 0
    bleu_days = []
    rouge_days = []

    for p in predictions[:7]:
        d = date.fromisoformat(p["date"])
        jour = JOURS_FR[d.weekday()][:3]
        mois = MOIS_FR[d.month - 1][:3]
        couleur = p["couleur_predite"]
        emoji = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(couleur, "❓")
        lines.append(f"{emoji} {jour}. {d.day} {mois}.")
        if couleur == "ROUGE":
            rouge_count += 1
            rouge_days.append(JOURS_FR[d.weekday()][:3])
        elif couleur == "BLANC":
            blanc_count += 1
        else:
            bleu_days.append(JOURS_FR[d.weekday()][:3])

    pred_text = "\n".join(lines)

    summary_parts = []
    if rouge_count:
        summary_parts.append(f"{rouge_count} jour{'s' if rouge_count > 1 else ''} rouge{'s' if rouge_count > 1 else ''}")
    if blanc_count:
        summary_parts.append(f"{blanc_count} jour{'s' if blanc_count > 1 else ''} blanc{'s' if blanc_count > 1 else ''}")

    summary_lines = []
    if summary_parts:
        summary_lines.append("⚠️ " + " et ".join(summary_parts) + " cette semaine.")
    else:
        summary_lines.append("✅ Semaine 100% bleue — profitez-en !")
    if bleu_days:
        summary_lines.append(f"👉 Lancez vos machines {', '.join(bleu_days)} (bleu).")
    if rouge_days:
        summary_lines.append(f"👉 Reportez lessive et four {', '.join(rouge_days)} (rouge).")

    summary_text = "\n".join(summary_lines)
    manage_url = _manage_link(manage_token) or Config.BASE_URL
    return Config.WHATSAPP_TEMPLATE_RECAP, _tpl_body(pred_text, summary_text, manage_url)


# ================================================================
# FORMATAGE DES MESSAGES (texte libre — pour logs + réponses webhook 24h)
# ================================================================


def format_alert_rouge(target_date: date, prediction: dict, manage_token: str = "") -> str:
    """Message d'alerte jour ROUGE — avec impact financier."""
    date_fr = _format_date_fr(target_date)
    prob = round(prediction.get("probabilite_rouge", 0) * 100)
    temp = prediction.get("temp_min_prevue", "?")
    temp_str = f"{temp:.0f}°C" if isinstance(temp, (int, float)) else "?"

    msg = (
        f"⚠️ *Calendrier Tempo EDF*\n\n"
        f"*Jour ROUGE* prévu {date_fr} ({prob}% confiance).\n"
        f"Temp min: {temp_str}.\n\n"
        f"💰 Heures pleines (6h-22h) à *0,76€/kWh* — jusqu'à 10× le tarif bleu !\n\n"
        f"👉 Reportez lessive, sèche-linge, four et recharge VE.\n"
        f"Évitez le chauffage d'appoint."
    )
    msg += "\n\n" + _msg_footer(manage_token)
    return msg


def format_alert_blanc(target_date: date, prediction: dict, manage_token: str = "") -> str:
    """Message d'alerte jour BLANC — avec conseil actionnable."""
    date_fr = _format_date_fr(target_date)
    prob = round(prediction.get("probabilite_blanc", 0) * 100)

    msg = (
        f"📢 *Calendrier Tempo EDF*\n\n"
        f"*Jour BLANC* prévu {date_fr} ({prob}% confiance).\n\n"
        f"💰 Heures pleines à *0,19€/kWh* — tarif intermédiaire.\n\n"
        f"👉 OK pour lancer les machines.\n"
        f"Évitez four et chauffage d'appoint en heures pleines (6h-22h)."
    )
    msg += "\n\n" + _msg_footer(manage_token)
    return msg


def format_alert_officiel(target_date: date, couleur: str, manage_token: str = "") -> str:
    """Message pour couleur officielle confirmée — reconnaît la prédiction."""
    date_fr = _format_date_fr(target_date)
    emoji = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(couleur, "")

    msg = f"{emoji} *Tempo confirmé : {couleur}* {date_fr}."
    if couleur == "ROUGE":
        msg += (
            "\n✅ Comme anticipé — heures pleines 6h-22h à *0,76€/kWh*."
            "\n👉 Reportez vos machines !"
        )
    elif couleur == "BLANC":
        msg += (
            "\n💡 Tarif intermédiaire — OK pour les machines,"
            "\névitez le four en heures pleines."
        )
    msg += "\n\n" + _msg_footer(manage_token)
    return msg


def format_change_alert(target_date: date, old_color: str, new_color: str,
                        manage_token: str = "") -> str:
    """Message d'alerte changement de prédiction (ex: BLEU → ROUGE)."""
    date_fr = _format_date_fr(target_date)
    emoji_new = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(new_color, "")
    emoji_old = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(old_color, "")

    msg = (
        f"🔄 *Mise à jour Tempo EDF*\n\n"
        f"La prévision pour *{date_fr}* a changé :\n"
        f"{emoji_old} {old_color} → {emoji_new} *{new_color}*"
    )
    if new_color == "ROUGE":
        msg += (
            "\n\n💰 Heures pleines à *0,76€/kWh*."
            "\n👉 Reportez vos machines ce jour-là !"
        )
    elif new_color == "BLANC" and old_color == "ROUGE":
        msg += "\n\n✅ Bonne nouvelle ! Tarif intermédiaire, moins cher que prévu."
    elif new_color == "BLEU" and old_color == "ROUGE":
        msg += "\n\n✅ Bonne nouvelle ! Tarif bleu, le moins cher."
    msg += "\n\n" + _msg_footer(manage_token)
    return msg


def format_recap_hebdo(predictions: list[dict], manage_token: str = "") -> str:
    """Récapitulatif hebdomadaire enrichi (dimanche soir)."""
    lines = ["📊 *Calendrier Tempo EDF — Semaine à venir*\n"]

    rouge_count = 0
    blanc_count = 0
    bleu_days = []
    rouge_days = []

    for p in predictions[:7]:
        d = date.fromisoformat(p["date"])
        jour = JOURS_FR[d.weekday()][:3]
        mois = MOIS_FR[d.month - 1][:3]
        couleur = p["couleur_predite"]
        emoji = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(couleur, "❓")
        lines.append(f"  {emoji} {jour}. {d.day} {mois}.")

        if couleur == "ROUGE":
            rouge_count += 1
            rouge_days.append(JOURS_FR[d.weekday()][:3])
        elif couleur == "BLANC":
            blanc_count += 1
        else:
            bleu_days.append(JOURS_FR[d.weekday()][:3])

    # Résumé
    lines.append("")
    summary_parts = []
    if rouge_count:
        summary_parts.append(f"*{rouge_count} jour{'s' if rouge_count > 1 else ''} rouge{'s' if rouge_count > 1 else ''}*")
    if blanc_count:
        summary_parts.append(f"*{blanc_count} jour{'s' if blanc_count > 1 else ''} blanc{'s' if blanc_count > 1 else ''}*")
    if summary_parts:
        lines.append("⚠️ " + " et ".join(summary_parts) + " cette semaine.")
    else:
        lines.append("✅ *Semaine 100% bleue* — profitez-en !")

    # Plan d'action
    lines.append("")
    lines.append("💡 *Plan d'action :*")
    if bleu_days:
        lines.append(f"👉 Lancez vos machines {', '.join(bleu_days)} (bleu).")
    if rouge_days:
        lines.append(f"👉 Reportez lessive et four {', '.join(rouge_days)} (rouge).")
    if not rouge_days and not bleu_days:
        lines.append("👉 Semaine intermédiaire, privilégiez les heures creuses.")

    lines.append("")
    lines.append(_msg_footer(manage_token))
    return "\n".join(lines)


def format_welcome(predictions: list[dict], manage_token: str = "") -> str:
    """Message de bienvenue envoyé immédiatement après inscription."""
    lines = [
        "👋 *Bienvenue sur le Calendrier Tempo EDF !*\n",
        "Vous recevrez chaque matin les prévisions Tempo pour anticiper vos dépenses.",
    ]

    if predictions:
        lines.append("\n📊 *Prochains jours :*")
        for p in predictions[:5]:
            d = date.fromisoformat(p["date"])
            jour = JOURS_FR[d.weekday()][:3]
            mois = MOIS_FR[d.month - 1][:3]
            couleur = p.get("couleur_predite", "?")
            emoji = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(couleur, "❓")
            lines.append(f"  {emoji} {jour}. {d.day} {mois}.")

    lines.append("")
    lines.append(_msg_footer(manage_token))
    return "\n".join(lines)


def format_recap_on_demand(predictions: list[dict]) -> str:
    """Récap envoyé quand l'utilisateur écrit RECAP."""
    lines = ["📊 *Prochains jours Tempo :*\n"]
    for p in predictions[:5]:
        d = date.fromisoformat(p["date"])
        jour = JOURS_FR[d.weekday()][:3]
        mois = MOIS_FR[d.month - 1][:3]
        couleur = p.get("couleur_predite", "?")
        emoji = {"ROUGE": "🔴", "BLANC": "⚪", "BLEU": "🔵"}.get(couleur, "❓")
        lines.append(f"  {emoji} {jour}. {d.day} {mois}.")

    if not predictions:
        lines.append("  Aucune prévision disponible pour le moment.")

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

def send_alerts_for_prediction(target_date: date, prediction: dict,
                               heure_filter: str = ""):
    """Envoie les alertes WhatsApp pour une prédiction (rouge ou blanc).
    Respecte les préférences et la limite 1 alerte/jour.

    Args:
        heure_filter: si renseigné ('matin' ou 'soir'), n'envoie qu'aux users
                      correspondant à cette préférence d'horaire.
    """
    if not _is_red_season():
        return

    couleur = prediction["couleur_predite"]
    if couleur == "BLEU":
        return

    conn = get_db()
    try:
        if couleur == "ROUGE":
            prob_pct = round(prediction.get("probabilite_rouge", 0) * 100)
            users = conn.execute(
                """SELECT id, phone_encrypted, seuil_alerte_rouge, delai_alerte,
                          manage_token, heure_envoi
                   FROM users
                   WHERE actif = 1 AND seuil_alerte_rouge <= ?""",
                (prob_pct,)
            ).fetchall()
            format_fn = format_alert_rouge
            type_alerte = "prediction_rouge"
        else:  # BLANC
            users = conn.execute(
                """SELECT id, phone_encrypted, seuil_alerte_rouge, delai_alerte,
                          manage_token, heure_envoi
                   FROM users WHERE actif = 1 AND alerte_blanc = 1"""
            ).fetchall()
            format_fn = format_alert_blanc
            type_alerte = "prediction_blanc"

        today_str = date.today().isoformat()
        # Fix audit DB : range comparison au lieu de LIKE (index-friendly)
        today_start = today_str + "T00:00:00"
        today_end = today_str + "T23:59:59"

        for user in users:
            # Filtre horaire (matin/soir)
            if heure_filter:
                user_pref = "matin"
                try:
                    user_pref = user["heure_envoi"] or "matin"
                except (KeyError, IndexError):
                    pass
                if user_pref != heure_filter:
                    continue

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

            # Envoi via template (business-initiated, hors fenêtre 24h)
            if couleur == "ROUGE":
                tpl_name, tpl_components = _build_rouge_template(target_date, prediction, token)
            else:
                tpl_name, tpl_components = _build_blanc_template(target_date, prediction, token)
            sid, statut = send_whatsapp_template(phone, tpl_name, tpl_components)

            _log_sms(conn, user["id"], type_alerte, couleur, message, statut, sid)

        conn.commit()
        logger.info(f"[Alertes] Envoi terminé pour {couleur} {target_date}")

    finally:
        conn.close()


def send_change_alerts(target_date: date, old_color: str, new_color: str):
    """Envoie une alerte quand une prédiction change de couleur.

    Seuls les changements impliquant ROUGE sont notifiés (J+1 à J+3) :
    - vers ROUGE : critique, l'utilisateur doit reporter ses usages
    - depuis ROUGE : bonne nouvelle, l'utilisateur peut planifier
    """
    if not _is_red_season():
        return
    if old_color == new_color:
        return
    # N'alerter que pour les changements impliquant ROUGE
    if "ROUGE" not in (old_color, new_color):
        return

    delta = (target_date - date.today()).days
    if delta < 1 or delta > 3:
        return

    conn = get_db()
    try:
        users = conn.execute(
            """SELECT id, phone_encrypted, manage_token
               FROM users WHERE actif = 1 AND seuil_alerte_rouge > 0"""
        ).fetchall()

        today_str = date.today().isoformat()
        today_start = today_str + "T00:00:00"
        today_end = today_str + "T23:59:59"

        for user in users:
            # Pas de changement alert si déjà reçu une alerte aujourd'hui
            existing = conn.execute(
                """SELECT id FROM sms_logs
                   WHERE user_id = ? AND date_envoi >= ? AND date_envoi <= ?
                   AND type_alerte = 'changement'""",
                (user["id"], today_start, today_end),
            ).fetchone()
            if existing:
                continue

            phone = _get_user_phone(user)
            if not phone:
                continue

            token = _get_manage_token(user)
            message = format_change_alert(target_date, old_color, new_color,
                                          manage_token=token)

            # Envoi via template (business-initiated)
            tpl_name, tpl_components = _build_change_template(target_date, old_color, new_color, token)
            sid, statut = send_whatsapp_template(phone, tpl_name, tpl_components)
            _log_sms(conn, user["id"], "changement", new_color, message, statut, sid)

        conn.commit()
        logger.info(f"[Alertes] Changement {old_color}→{new_color} pour {target_date}")
    finally:
        conn.close()


def send_official_alerts(target_date: date, couleur: str):
    """Envoie les alertes pour une couleur officiellement confirmée."""
    if not _is_red_season():
        return
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

            # Envoi via template (business-initiated)
            tpl_name, tpl_components = _build_confirmation_template(target_date, couleur, token)
            sid, statut = send_whatsapp_template(phone, tpl_name, tpl_components)
            _log_sms(conn, user["id"], "officiel", couleur, message, statut, sid)

        conn.commit()
    finally:
        conn.close()


def send_weekly_recap(predictions: list[dict]):
    """Envoie le récapitulatif hebdomadaire aux users inscrits."""
    if not _is_red_season():
        return

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

            # Envoi via template (business-initiated)
            tpl_name, tpl_components = _build_recap_template(predictions, token)
            sid, statut = send_whatsapp_template(phone, tpl_name, tpl_components)
            _log_sms(conn, user["id"], "recap_hebdo", "", message, statut, sid)

        conn.commit()
        logger.info(f"[Alertes] Récap hebdo envoyé à {len(users)} users")
    finally:
        conn.close()


def send_welcome(phone_number: str, manage_token: str = ""):
    """Envoie le message de bienvenue avec les 5 prochains jours via template."""
    predictions = _get_upcoming_predictions()

    # Envoi via template (business-initiated — l'user vient de s'inscrire sur le site)
    tpl_name, tpl_components = _build_welcome_template(predictions, manage_token)
    sid, statut = send_whatsapp_template(phone_number, tpl_name, tpl_components)
    logger.info(f"[WhatsApp] Bienvenue envoyé à ****{phone_number[-4:]}: {statut}")
    return sid, statut


def _get_upcoming_predictions() -> list[dict]:
    """Récupère les 5 prochaines prédictions non confirmées depuis la DB."""
    conn = get_db()
    try:
        today_str = date.today().isoformat()
        rows = conn.execute(
            """SELECT date, couleur_predite
               FROM predictions
               WHERE date >= ? AND confirmed = 0
               ORDER BY date LIMIT 5""",
            (today_str,)
        ).fetchall()
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        logger.debug(f"[Welcome] Erreur récup prédictions: {e}")
        return []
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
                  delai: int = 3, alerte_blanc: bool = False,
                  recap_hebdo: bool = True,
                  heure_envoi: str = "matin") -> dict:
    """Inscrit un nouvel utilisateur aux alertes WhatsApp.

    Defaults changés pour maximiser la valeur perçue :
    - delai=3 (anticipation J+2→J+3, notre valeur ajoutée)
    - recap_hebdo=True (message à plus forte valeur perçue)
    - heure_envoi='matin' (quand l'utilisateur planifie sa journée)
    """
    phone_clean = phone_number.strip().replace(" ", "")
    # Fix #8 : message corrigé "9 chiffres"
    if not phone_clean.startswith("+33") or len(phone_clean) != 12:
        return {"error": "Format invalide. Utilisez +33XXXXXXXXX (9 chiffres après +33)."}
    # Fix #13 audit v4 : verifier que les 9 derniers caracteres sont des chiffres
    if not phone_clean[3:].isdigit():
        return {"error": "Le numéro ne doit contenir que des chiffres après +33."}

    heure_envoi = heure_envoi if heure_envoi in ("matin", "soir") else "matin"

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
                       heure_envoi = ?,
                       manage_token = ?,
                       updated_at = ? WHERE id = ?""",
                    (phone_enc, seuil_rouge, delai,
                     int(alerte_blanc), int(recap_hebdo),
                     heure_envoi,
                     manage_token,
                     now, existing["id"]),
                )
                conn.commit()
                return {"success": True, "user_id": existing["id"],
                        "manage_token": manage_token,
                        "phone": phone_clean,
                        "message": "Compte réactivé avec vos nouvelles préférences !"}

        cursor = conn.execute(
            """INSERT INTO users
               (phone_hash, phone_encrypted, phone_last4, seuil_alerte_rouge, delai_alerte,
                alerte_blanc, recap_hebdo, heure_envoi, manage_token, actif, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (phone_h, phone_enc, last4, seuil_rouge, delai,
             int(alerte_blanc), int(recap_hebdo), heure_envoi,
             manage_token, now, now),
        )
        conn.commit()
        return {"success": True, "user_id": cursor.lastrowid,
                "manage_token": manage_token,
                "phone": phone_clean,
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
                      alerte_blanc, recap_hebdo, heure_envoi, actif, manage_token
               FROM users WHERE manage_token = ? AND actif = 1""",
            (token,)
        ).fetchone()
        if user:
            return dict(user)
        return None
    finally:
        conn.close()


def update_user_preferences(token: str, seuil_rouge: int = 70,
                            delai: int = 3, alerte_blanc: bool = False,
                            recap_hebdo: bool = True,
                            heure_envoi: str = "matin") -> dict:
    """Met à jour les préférences d'un utilisateur via son token."""
    if not token:
        return {"error": "Token invalide."}

    heure_envoi = heure_envoi if heure_envoi in ("matin", "soir") else "matin"

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
               alerte_blanc = ?, recap_hebdo = ?, heure_envoi = ?,
               updated_at = ?
               WHERE id = ?""",
            (seuil_rouge, delai, int(alerte_blanc), int(recap_hebdo),
             heure_envoi, now, user["id"]),
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
# WEBHOOK ENTRANT (WhatsApp)
# ================================================================

def handle_incoming_sms(from_number: str, body: str) -> str:
    """Traite un message entrant (webhook Meta WhatsApp). Gère STOP/START/RECAP.
    Retourne le message de réponse."""
    body_clean = body.strip().upper()
    phone_clean = from_number.strip().replace(" ", "")
    # Normaliser le numéro : retirer le préfixe "whatsapp:" si présent
    if phone_clean.startswith("whatsapp:"):
        phone_clean = phone_clean[len("whatsapp:"):]
    # Meta envoie les numéros sans "+", on le rajoute si absent
    if phone_clean and not phone_clean.startswith("+"):
        phone_clean = f"+{phone_clean}"

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

    if body_clean in ("RECAP", "RESUME", "SEMAINE", "PROCHAINS"):
        predictions = _get_upcoming_predictions()
        logger.info(f"[WhatsApp IN] RECAP demandé: ****{phone_clean[-4:]}")
        return format_recap_on_demand(predictions)

    return ("Calendrier Tempo EDF :\n"
            "STOP = désinscrire\n"
            "START = réinscrire\n"
            "RECAP = prochains jours")


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
