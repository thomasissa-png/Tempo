"""Rapatrie sur Neon les abonnés modifiés sur Replit pendant la transition DNS.

    python3 cloudflare/merge_users.py

Pendant la bascule sans coupure, Replit et Cloudflare servent le site en même temps
quelques minutes (propagation DNS). Une inscription, une désinscription ou un changement
de préférences peut donc arriver sur Replit. Ce script, lancé une fois Replit arrêté :
  - ajoute sur Neon les abonnés présents seulement sur Replit (nouvel id côté Neon) ;
  - met à jour ceux que Replit a modifiés plus récemment (updated_at) ;
  - ne touche jamais aux abonnés créés ou modifiés plus récemment sur Cloudflare.
La clé est phone_hash. Relançable sans risque. Aucun numéro n'est affiché.

Variables : REPLIT_DATABASE_URL (source, lecture seule), NEON_DATABASE_URL (cible).
"""
import os
import sys

import psycopg2

KEY = "phone_hash"


def _columns(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'users'")
        return [r[0] for r in cur.fetchall()]


def main() -> None:
    source_url, target_url = os.getenv("REPLIT_DATABASE_URL", ""), os.getenv("NEON_DATABASE_URL", "")
    if not (source_url and target_url):
        print("ERREUR : REPLIT_DATABASE_URL et NEON_DATABASE_URL sont nécessaires.")
        sys.exit(1)

    src = psycopg2.connect(source_url)
    src.set_session(readonly=True)
    dst = psycopg2.connect(target_url)
    cols = [c for c in _columns(src) if c in set(_columns(dst)) and c != "id"]
    col_sql = ", ".join(f'"{c}"' for c in cols)

    with src.cursor() as cur:
        cur.execute(f"SELECT {col_sql} FROM users")
        replit = [dict(zip(cols, row)) for row in cur.fetchall()]
    with dst.cursor() as cur:
        cur.execute(f'SELECT "{KEY}", updated_at FROM users')
        neon = dict(cur.fetchall())

    added = updated = 0
    with dst.cursor() as cur:
        for user in replit:
            key = user[KEY]
            if key not in neon:
                cur.execute(f"INSERT INTO users ({col_sql}) VALUES ({', '.join(['%s'] * len(cols))})",
                            [user[c] for c in cols])
                added += 1
            elif str(user["updated_at"]) > str(neon[key]):
                sets = ", ".join(f'"{c}" = %s' for c in cols if c != KEY)
                cur.execute(f'UPDATE users SET {sets} WHERE "{KEY}" = %s',
                            [user[c] for c in cols if c != KEY] + [key])
                updated += 1
    dst.commit()
    src.close()
    dst.close()
    print(f"OK : {added} abonné(s) ajouté(s), {updated} mis à jour depuis Replit.")


if __name__ == "__main__":
    main()
