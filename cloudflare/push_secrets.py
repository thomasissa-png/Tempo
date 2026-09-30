"""Copie les secrets de l'app (variables d'environnement) vers le Worker Cloudflare, sans les afficher.

À lancer depuis une session Claude Code dont l'environnement contient les Secrets Replit
(ou, à défaut, depuis le Shell Replit) :
    python3 cloudflare/push_secrets.py          # phase de test : AUCUN envoi WhatsApp, agents IA coupés
    python3 cloudflare/push_secrets.py --prod   # bascule : ajoute WHATSAPP_TOKEN et ANTHROPIC_API_KEY

Variables nécessaires en plus des secrets habituels de l'app :
    CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, NEON_DATABASE_URL
Lancer d'abord cloudflare/check_access.py : il vérifie que la clé relit les numéros des abonnés.

Côté Cloudflare, DATABASE_URL reçoit la valeur de NEON_DATABASE_URL (jamais la base Replit).
Sans WHATSAPP_TOKEN, l'app passe d'elle-même en mode simulation : aucun abonné ne
reçoit de message pendant les tests, même si le scheduler tourne.
"""
import os
import sys

import httpx

from neon_http import cloudflare_token, env, phone_key

API = os.getenv("CLOUDFLARE_API_BASE", "https://api.cloudflare.com/client/v4")
SCRIPT_NAME = "calendrier-tempo"  # = "name" de cloudflare/wrangler.jsonc

# Même liste que APP_ENV_KEYS dans cloudflare/worker.ts
APP_ENV_KEYS = [
    "ADMIN_PASSWORD", "SESSION_SECRET", "PHONE_ENCRYPTION_KEY",
    "DATABASE_URL", "BASE_URL", "LOG_LEVEL",
    "TEMPO_REMAINING_ROUGE", "TEMPO_REMAINING_BLANC",
    "METEOFRANCE_API_KEY", "METEOFRANCE_APPLICATION_ID", "METEOFRANCE_AROME_KEY",
    "METEOFRANCE_ARPEGE_KEY", "METEOFRANCE_VIGILANCE_KEY",
    "RTE_CLIENT_ID", "RTE_CLIENT_SECRET", "RTE_CONSO_CLIENT_ID", "RTE_CONSO_CLIENT_SECRET",
    "RTE_GENERATION_CLIENT_ID", "RTE_GENERATION_CLIENT_SECRET", "RTE_NUCLEAR_CAPACITY_MW",
    "WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_VERIFY_TOKEN",
    "WHATSAPP_APP_SECRET", "WHATSAPP_API_VERSION", "WHATSAPP_TEMPLATE_LANG",
    "WHATSAPP_TEMPLATE_ALERT_BLANC", "WHATSAPP_TEMPLATE_ALERT_ROUGE",
    "WHATSAPP_TEMPLATE_CHANGE", "WHATSAPP_TEMPLATE_CONFIRMATION",
    "WHATSAPP_TEMPLATE_MANAGE_LINK", "WHATSAPP_TEMPLATE_RECAP", "WHATSAPP_TEMPLATE_WELCOME",
    "ANTHROPIC_API_KEY", "SEO_AGENT_MODEL", "SEO_AGENT_MAX_TURNS", "BACKLINKS_AGENT_MAX_TURNS",
    "INDEXNOW_KEY", "GOOGLE_SITE_VERIFICATION", "BING_SITE_VERIFICATION",
]
# Retenus en phase de test : ce sont eux qui font sortir quelque chose vers l'extérieur.
PROD_ONLY = {"WHATSAPP_TOKEN", "ANTHROPIC_API_KEY"}
# Clé des numéros = PHONE_ENCRYPTION_KEY, sinon dérivée d'ADMIN_PASSWORD, sinon de SESSION_SECRET
# (database._get_fernet). Au moins une doit être présente, sinon abonnés illisibles.
KEY_SOURCES = ("PHONE_ENCRYPTION_KEY", "ADMIN_PASSWORD", "SESSION_SECRET")
# Cloudflare refuse un secret de plus de 5,1 ko (ex. METEOFRANCE_AROME_KEY : 6,4 ko).
SECRET_MAX = 5000


def split_secret(key: str, value: str) -> list[tuple[str, str]]:
    """Découpe une valeur trop longue en CLÉ__PART1, CLÉ__PART2... (recollées par worker.ts)."""
    if len(value.encode()) <= SECRET_MAX:
        return [(key, value)]
    return [(f"{key}__PART{i + 1}", value[start:start + SECRET_MAX])
            for i, start in enumerate(range(0, len(value), SECRET_MAX))]


def main() -> None:
    prod = "--prod" in sys.argv[1:]
    token = cloudflare_token()
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    neon = os.getenv("NEON_DATABASE_URL", "")
    missing = [n for n, v in (("CLOUDFLARE_API_TOKEN (ou CLOUDFLARE_Token_Value)", token),
                              ("CLOUDFLARE_ACCOUNT_ID", account),
                              ("NEON_DATABASE_URL", neon)) if not v]
    if not any(os.getenv(k) for k in KEY_SOURCES):
        missing.append(" ou ".join(KEY_SOURCES))
    if missing:
        print("ERREUR : variables manquantes : " + ", ".join(missing))
        sys.exit(1)

    values = {}
    for key in APP_ENV_KEYS:
        if key in PROD_ONLY and not prod:
            continue
        # Certains noms sont refusés dans l'environnement de la session Claude (ex. ANTHROPIC_API_KEY,
        # que Claude Code prendrait pour lui) : le fondateur les range sous TEMPO_<NOM>.
        if key == "DATABASE_URL":
            value = neon
        elif key == "PHONE_ENCRYPTION_KEY":
            value = phone_key()  # « = » final restauré, prouvé par check_access.py
        else:
            value = env(key)
        if value:
            values.update(split_secret(key, value))

    url = f"{API}/accounts/{account}/workers/scripts/{SCRIPT_NAME}/secrets"
    headers = {"Authorization": f"Bearer {token}"}
    failed = []
    with httpx.Client(timeout=30) as client:
        for key, value in values.items():
            r = client.put(url, headers=headers,
                           json={"name": key, "text": value, "type": "secret_text"})
            ok = r.status_code in (200, 201) and r.json().get("success")
            print(f"  {'OK ' if ok else 'ÉCHEC'} {key}")
            if not ok:
                failed.append(f"{key} (HTTP {r.status_code})")

    if not prod:
        print("\nPhase de test : WHATSAPP_TOKEN et ANTHROPIC_API_KEY NON envoyés "
              "(aucun message aux abonnés, agents IA coupés).")
    if failed:
        print("ERREUR sur : " + ", ".join(failed))
        sys.exit(1)
    print(f"OK : {len(values)} secrets envoyés au Worker « {SCRIPT_NAME} ».")


if __name__ == "__main__":
    main()
