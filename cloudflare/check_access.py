"""Vérifie, avant la migration, que la session a tout ce qu'il faut. N'affiche aucun secret.

    python3 cloudflare/check_access.py

Contrôles :
  1. variables présentes (Cloudflare, Neon, base Replit, secrets de l'app) ;
  2. jeton Cloudflare valide, compte accessible, zone calendrier-tempo.fr (statut, serveurs DNS) ;
  3. base Replit joignable en lecture (version de schéma, volumes) ;
  4. base Neon joignable ;
  5. LE point critique : la clé de chiffrement de la session relit les numéros des abonnés
     (déchiffrement + empreinte identique à celle stockée). Sinon, bascule interdite.

Code de sortie 0 seulement si tout est bon.
"""
import os
import sys

import httpx
import psycopg2

API = os.getenv("CLOUDFLARE_API_BASE", "https://api.cloudflare.com/client/v4")
ZONE = "calendrier-tempo.fr"
SAMPLE = 25

ok_all = True


def report(ok: bool, label: str, detail: str = "") -> None:
    global ok_all
    ok_all &= ok
    print(f"  [{'OK' if ok else 'KO'}] {label}" + (f" : {detail}" if detail else ""))


def check_env() -> None:
    print("1. Variables")
    for name in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "NEON_DATABASE_URL", "REPLIT_DATABASE_URL"):
        report(bool(os.getenv(name)), name, "" if os.getenv(name) else "absente")
    key = [n for n in ("PHONE_ENCRYPTION_KEY", "ADMIN_PASSWORD", "SESSION_SECRET") if os.getenv(n)]
    report(bool(key), "source de la clé de chiffrement", ", ".join(key) or "aucune")
    for name in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET",
                 "RTE_CLIENT_ID", "METEOFRANCE_API_KEY", "ANTHROPIC_API_KEY"):
        # Non bloquant : l'app tourne sans (simulation, repli météo, agents coupés).
        print(f"  [{'ok' if os.getenv(name) else '--'}] {name}")


def check_cloudflare() -> None:
    print("2. Cloudflare")
    token, account = os.getenv("CLOUDFLARE_API_TOKEN", ""), os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    if not (token and account):
        report(False, "API", "jeton ou compte absent")
        return
    h = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=20, headers=h) as c:
            r = c.get(f"{API}/accounts/{account}/tokens/verify")
            if r.status_code != 200:
                r = c.get(f"{API}/user/tokens/verify")
            report(r.status_code == 200 and r.json().get("success", False), "jeton", f"HTTP {r.status_code}")
            r = c.get(f"{API}/accounts/{account}")
            report(r.status_code == 200, "compte", f"HTTP {r.status_code}")
            r = c.get(f"{API}/zones", params={"name": ZONE})
            zones = (r.json().get("result") or []) if r.status_code == 200 else []
            if not zones:
                report(False, f"zone {ZONE}", f"introuvable (HTTP {r.status_code})")
            else:
                z = zones[0]
                report(z["status"] == "active", f"zone {ZONE}",
                       f"{z['status']}, serveurs attendus {', '.join(z.get('name_servers', []))}")
    except httpx.HTTPError as e:
        report(False, "API", f"injoignable ({type(e).__name__}) : autoriser api.cloudflare.com")


def _connect(url: str, label: str):
    try:
        conn = psycopg2.connect(url, connect_timeout=15)
        conn.set_session(readonly=True)
        return conn
    except psycopg2.Error as e:
        host = url.split("@")[-1].split("/")[0].split(":")[0]
        report(False, label, f"connexion impossible à {host} ({str(e).strip().splitlines()[0][:120]})")
        return None


def check_databases():
    print("3. Base Replit (lecture seule)")
    src = None
    if os.getenv("REPLIT_DATABASE_URL"):
        src = _connect(os.environ["REPLIT_DATABASE_URL"], "connexion")
    if src:
        with src.cursor() as cur:
            cur.execute("SELECT MAX(version) FROM schema_version")
            version = cur.fetchone()[0]
            cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
            size = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE actif = 1) FROM users")
            users, active = cur.fetchone()
            cur.execute("SELECT MAX(timestamp_prediction) FROM predictions WHERE simulated = 0")
            last_pred = cur.fetchone()[0]
            cur.execute("SELECT MAX(date_envoi) FROM sms_logs")
            last_msg = cur.fetchone()[0]
        report(True, "connexion", f"schéma v{version}, {size}, {users} abonnés dont {active} actifs")
        # Base de PRODUCTION = prédiction du jour et messages récents (sinon : base de dev Replit).
        print(f"       dernière prédiction : {str(last_pred)[:16]}, dernier message : {str(last_msg)[:16]}")
    print("4. Base Neon")
    if os.getenv("NEON_DATABASE_URL"):
        dst = _connect(os.environ["NEON_DATABASE_URL"], "connexion")
        if dst:
            with dst.cursor() as cur:
                cur.execute("SHOW server_version")
                report(True, "connexion", f"PostgreSQL {cur.fetchone()[0]}")
            dst.close()
    return src


def check_phone_key(src) -> None:
    print("5. Clé de chiffrement des numéros (critique)")
    if src is None:
        report(False, "test", "base Replit non joignable")
        return
    # La clé est choisie à l'import de config/database : jamais la base de la session.
    os.environ.pop("DATABASE_URL", None)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from database import decrypt_phone, hash_phone

    with src.cursor() as cur:
        cur.execute("SELECT phone_hash, phone_encrypted FROM users WHERE phone_encrypted <> '' "
                    "ORDER BY id DESC LIMIT %s", (SAMPLE,))
        rows = cur.fetchall()
    if not rows:
        report(True, "test", "aucun abonné chiffré, rien à vérifier")
        return
    good = 0
    for phone_hash, encrypted in rows:
        try:
            good += hash_phone(decrypt_phone(encrypted)) == phone_hash
        except Exception:
            pass
    report(good == len(rows), "numéros relus avec la clé de la session", f"{good}/{len(rows)}")


def main() -> None:
    check_env()
    check_cloudflare()
    src = check_databases()
    check_phone_key(src)
    if src:
        src.close()
    print("\nTOUT EST BON" if ok_all else "\nBLOQUANT : corriger les lignes KO avant de migrer")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
