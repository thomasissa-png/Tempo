"""Rapatrie sur Neon les abonnés modifiés sur Replit pendant la transition DNS.

    python3 cloudflare/merge_users.py

Pendant la bascule sans coupure, Replit et Cloudflare servent le site en même temps
quelques minutes (propagation DNS). Une inscription, une désinscription ou un changement
de préférences peut donc arriver sur Replit. Ce script, lancé une fois Replit arrêté :
  - ajoute sur Neon les abonnés présents seulement sur Replit (nouvel id côté Neon) ;
  - met à jour ceux que Replit a modifiés plus récemment (updated_at) ;
  - ne touche jamais aux abonnés créés ou modifiés plus récemment sur Cloudflare.
La clé est phone_hash. Relançable sans risque. Aucun numéro n'est affiché.

Variables : REPLIT_DATABASE_URL (source, transaction READ ONLY), NEON_DATABASE_URL (cible).
Transport : API SQL HTTPS de Neon (port 5432 bloqué depuis les sessions).
"""
import os
import sys

from neon_http import NeonError, NeonHTTP

KEY = "phone_hash"
COLUMNS_SQL = ("SELECT column_name FROM information_schema.columns "
               "WHERE table_schema = 'public' AND table_name = 'users' ORDER BY ordinal_position")


def plan_merge(cols: list[str], replit: list[list], neon: dict[str, str]) -> list[tuple[str, list]]:
    """Requêtes à jouer sur Neon. replit = lignes (colonnes cols), neon = {phone_hash: updated_at}.

    Les dates sont des chaînes ISO de même format des deux côtés : l'ordre texte est l'ordre
    chronologique, comme dans le reste de l'app.
    """
    col_sql = ", ".join(f'"{c}"' for c in cols)
    others = [c for c in cols if c != KEY]
    stmts = []
    for row in replit:
        user = dict(zip(cols, row))
        key = user[KEY]
        if key not in neon:
            stmts.append((f"INSERT INTO users ({col_sql}) VALUES "
                          f"({', '.join(f'${i + 1}' for i in range(len(cols)))})", list(row)))
        elif str(user["updated_at"] or "") > str(neon[key] or ""):
            sets = ", ".join(f'"{c}" = ${i + 1}' for i, c in enumerate(others))
            stmts.append((f'UPDATE users SET {sets} WHERE "{KEY}" = ${len(others) + 1}',
                          [user[c] for c in others] + [key]))
    return stmts


def main() -> None:
    source_url, target_url = os.getenv("REPLIT_DATABASE_URL", ""), os.getenv("NEON_DATABASE_URL", "")
    if not (source_url and target_url):
        print("ERREUR : REPLIT_DATABASE_URL et NEON_DATABASE_URL sont nécessaires.")
        sys.exit(1)

    src, dst = NeonHTTP(source_url, readonly=True), NeonHTTP(target_url)
    try:
        dst_cols = {r[0] for r in dst.query(COLUMNS_SQL)}
        cols = [r[0] for r in src.query(COLUMNS_SQL) if r[0] in dst_cols and r[0] != "id"]
        replit = src.query(f"SELECT {', '.join(chr(34) + c + chr(34) for c in cols)} FROM users")
        neon = {h: u for h, u in dst.query(f'SELECT "{KEY}", updated_at FROM users')}
        stmts = plan_merge(cols, replit, neon)
        dst.batch(stmts)
    except NeonError as e:
        print(f"ERREUR : {e}")
        sys.exit(1)
    finally:
        src.close()
        dst.close()
    added = sum(s.startswith("INSERT") for s, _ in stmts)
    print(f"OK : {added} abonné(s) ajouté(s), {len(stmts) - added} mis à jour depuis Replit.")


if __name__ == "__main__":
    main()
