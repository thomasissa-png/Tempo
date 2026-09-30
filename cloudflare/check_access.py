"""Vérifie, avant la migration, que la session a tout ce qu'il faut. N'affiche aucun secret.

    python3 cloudflare/check_access.py

Contrôles :
  1. variables présentes (Cloudflare, Neon, base Replit, secrets de l'app) ;
  2. jeton Cloudflare valide, compte, conteneurs, sous-domaine workers.dev, zone du domaine ;
  3. base Replit joignable en lecture (version de schéma, volumes) ;
  4. base Neon joignable ;
  5. LE point critique : la clé de chiffrement de la session relit les numéros des abonnés
     (déchiffrement + empreinte identique à celle stockée). Sinon, bascule interdite.

Les bases passent par l'API SQL HTTPS de Neon (port 5432 bloqué), la base Replit
en transaction READ ONLY. Code de sortie 0 seulement si tout est bon (la zone peut
rester en attente : le domaine est traité à la fin).
"""
import os
import sys

import httpx

from neon_http import NeonError, NeonHTTP, cloudflare_token, env, phone_key

API = os.getenv("CLOUDFLARE_API_BASE", "https://api.cloudflare.com/client/v4")
ZONE = "calendrier-tempo.fr"
RECENT = 25  # les derniers inscrits doivent TOUS être relus

ok_all = True


def report(ok: bool, label: str, detail: str = "", blocking: bool = True) -> None:
    global ok_all
    if blocking:
        ok_all &= ok
    tag = "OK" if ok else ("KO" if blocking else "--")
    print(f"  [{tag}] {label}" + (f" : {detail}" if detail else ""))


def check_env() -> None:
    print("1. Variables")
    report(bool(cloudflare_token()), "jeton Cloudflare (CLOUDFLARE_API_TOKEN ou CLOUDFLARE_Token_Value)")
    for name in ("CLOUDFLARE_ACCOUNT_ID", "NEON_DATABASE_URL", "REPLIT_DATABASE_URL"):
        report(bool(os.getenv(name)), name, "" if os.getenv(name) else "absente")
    key = [n for n in ("PHONE_ENCRYPTION_KEY", "ADMIN_PASSWORD", "SESSION_SECRET") if os.getenv(n)]
    report(bool(key), "source de la clé de chiffrement", ", ".join(key) or "aucune")
    raw = os.getenv("PHONE_ENCRYPTION_KEY", "").strip()
    if raw and raw != phone_key():
        print("  [ok] PHONE_ENCRYPTION_KEY : « = » final restauré (perdu dans l'environnement de la session)")
    for name in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET",
                 "RTE_CLIENT_ID", "METEOFRANCE_API_KEY", "ANTHROPIC_API_KEY"):
        # Non bloquant : l'app tourne sans (simulation, repli météo, agents coupés).
        print(f"  [{'ok' if env(name) else '--'}] {name}")


def check_cloudflare() -> None:
    print("2. Cloudflare")
    token, account = cloudflare_token(), os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    if not (token and account):
        report(False, "API", "jeton ou compte absent")
        return
    try:
        with httpx.Client(timeout=20, headers={"Authorization": f"Bearer {token}"}) as c:
            r = c.get(f"{API}/user/tokens/verify")
            if r.status_code != 200:
                r = c.get(f"{API}/accounts/{account}/tokens/verify")
            report(r.status_code == 200 and r.json().get("success", False), "jeton", f"HTTP {r.status_code}")
            r = c.get(f"{API}/accounts/{account}")
            report(r.status_code == 200, "compte", f"HTTP {r.status_code}")
            r = c.get(f"{API}/accounts/{account}/containers/applications")
            report(r.status_code == 200, "droit Containers", f"HTTP {r.status_code}")
            r = c.get(f"{API}/accounts/{account}/workers/subdomain")
            sub = (r.json().get("result") or {}).get("subdomain") if r.status_code == 200 else None
            report(bool(sub), "sous-domaine workers.dev", f"{sub}.workers.dev" if sub else f"HTTP {r.status_code}")
            r = c.get(f"{API}/zones", params={"name": ZONE})
            zones = (r.json().get("result") or []) if r.status_code == 200 else []
            if not zones:
                report(False, f"zone {ZONE}", "pas encore créée (domaine traité à la fin)", blocking=False)
            else:
                z = zones[0]
                report(z["status"] == "active", f"zone {ZONE}",
                       f"{z['status']}, serveurs attendus {', '.join(z.get('name_servers', []))}", blocking=False)
    except httpx.HTTPError as e:
        report(False, "API", f"injoignable ({type(e).__name__}) : autoriser api.cloudflare.com")


def _open(var: str, readonly: bool):
    url = os.getenv(var, "")
    if not url:
        return None
    db = NeonHTTP(url, readonly=readonly)
    try:
        db.scalar("SELECT 1")
        return db
    except NeonError as e:
        report(False, "connexion", f"{db.host} : {e}")
        return None


def check_databases():
    print("3. Base Replit (lecture seule)")
    src = _open("REPLIT_DATABASE_URL", readonly=True)
    if src:
        (version, size, users, active, last_pred, last_msg), = src.query(
            "SELECT (SELECT MAX(version) FROM schema_version),"
            " pg_size_pretty(pg_database_size(current_database())),"
            " (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM users WHERE actif = 1),"
            " (SELECT MAX(timestamp_prediction) FROM predictions WHERE simulated = 0),"
            " (SELECT MAX(date_envoi) FROM sms_logs)")
        report(True, "connexion", f"schéma v{version}, {size}, {users} abonnés dont {active} actifs")
        # Base de PRODUCTION = prédiction du jour et messages récents (sinon : base de dev Replit).
        print(f"       dernière prédiction : {str(last_pred)[:16]}, dernier message : {str(last_msg)[:16]}")
    print("4. Base Neon")
    dst = _open("NEON_DATABASE_URL", readonly=False)
    if dst:
        version = dst.scalar("SELECT to_regclass('public.schema_version') IS NOT NULL")
        schema = dst.scalar("SELECT MAX(version) FROM schema_version") if version == "t" else None
        report(True, "connexion", f"PostgreSQL {dst.scalar('SHOW server_version')}, "
               + (f"schéma v{schema}" if schema else "vide (schéma créé au 1er démarrage du conteneur)"))
        dst.close()
    return src


def verify_phone_key(rows: list[list]) -> tuple[bool, str]:
    """rows = [id, phone_hash, phone_encrypted, created_at], du plus récent au plus ancien.

    La clé est la bonne si chaque numéro déchiffré redonne l'empreinte stockée (HMAC calculé
    avec la clé elle-même) et si les RECENT derniers inscrits sont tous relus. Un abonné plus
    ancien illisible l'est aussi sur Replit (même clé) : signalé, pas bloquant.
    """
    from database import decrypt_phone, hash_phone

    read, mismatch, unreadable = 0, [], []
    for i, (uid, phone_hash, encrypted, created) in enumerate(rows):
        try:
            phone = decrypt_phone(encrypted)
        except Exception:
            unreadable.append((i, uid, str(created)[:10]))
            continue
        if hash_phone(phone) == phone_hash:
            read += 1
        else:
            mismatch.append(uid)
    recent_ko = [u for i, u, _ in unreadable if i < RECENT]
    ok = read > 0 and not mismatch and not recent_ko
    detail = f"{read}/{len(rows)} relus"
    if mismatch:
        detail += f", empreinte différente pour {len(mismatch)} (mauvaise clé)"
    if unreadable:
        detail += ", illisibles (déjà sur Replit, même clé) : " + ", ".join(
            f"id {u} du {c}" for _, u, c in unreadable)
    return ok, detail


def check_phone_key(src) -> None:
    print("5. Clé de chiffrement des numéros (critique)")
    if src is None:
        report(False, "test", "base Replit non joignable")
        return
    # La clé est lue à l'import de config/database : jamais la base de la session.
    os.environ.pop("DATABASE_URL", None)
    if phone_key():
        os.environ["PHONE_ENCRYPTION_KEY"] = phone_key()
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    rows = src.query("SELECT id, phone_hash, phone_encrypted, created_at FROM users "
                     "WHERE phone_encrypted <> '' ORDER BY id DESC")
    if not rows:
        report(True, "test", "aucun abonné chiffré, rien à vérifier")
        return
    ok, detail = verify_phone_key(rows)
    report(ok, "numéros relus avec la clé de la session", detail)


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
