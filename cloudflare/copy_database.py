"""Copie complète de la base PostgreSQL Replit vers la base Neon (Cloudflare).

À lancer depuis une session Claude Code (ou, à défaut, le Shell Replit) :
    python3 cloudflare/copy_database.py

Variables d'environnement :
    REPLIT_DATABASE_URL base source (Replit) : lue seulement, jamais modifiée.
                        Repli sur DATABASE_URL (cas du Shell Replit, où elle est déjà présente).
    NEON_DATABASE_URL   base cible (Neon) : son contenu est REMPLACÉ par celui de la source

Le script :
  1. crée le schéma sur la cible avec les migrations de l'app (database.init_db) ;
  2. vide les tables cibles puis copie toutes les lignes (ids conservés) ;
  3. recale les séquences (SERIAL) ;
  4. compare le nombre de lignes table par table et s'arrête en erreur si écart.

Relançable sans risque : chaque exécution reproduit l'état actuel de la source
(c'est ce qu'on fait juste avant la bascule pour récupérer les dernières données).
"""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import execute_values

ROOT = Path(__file__).resolve().parent.parent
BATCH = 1000
SKIP_TABLES = {"schema_version"}  # géré par init_db sur la cible
# Depuis la v23, les migrations ne font qu'ajouter des tables (v24 agent_files,
# v25 rte_forecast_log) : une source plus ancienne se copie sans perte, les
# nouvelles tables restent vides sur la cible.
ADDITIVE_SINCE = 23


def _die(msg: str) -> None:
    print(f"ERREUR : {msg}")
    sys.exit(1)


def _same_database(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.hostname, pa.port, pa.path) == (pb.hostname, pb.port, pb.path)


def _tables(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        return sorted(r[0] for r in cur.fetchall())


def _columns(conn, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
            (table,),
        )
        return [r[0] for r in cur.fetchall()]


def _parents_first(conn, tables: list[str]) -> list[str]:
    """Ordonne les tables pour que chaque table référencée soit copiée avant."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tc.table_name, ccu.table_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'"
        )
        deps = {}
        for child, parent in cur.fetchall():
            if child != parent:
                deps.setdefault(child, set()).add(parent)
    ordered, seen = [], set()

    def visit(t: str) -> None:
        if t in seen:
            return
        seen.add(t)
        for p in sorted(deps.get(t, ())):
            if p in tables:
                visit(p)
        ordered.append(t)

    for t in tables:
        visit(t)
    return ordered


def _schema_version(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(version) FROM schema_version")
            return cur.fetchone()[0]
    except psycopg2.Error:
        conn.rollback()
        return None


def _count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
        return cur.fetchone()[0]


def main() -> None:
    source_url = os.getenv("REPLIT_DATABASE_URL") or os.getenv("DATABASE_URL", "")
    target_url = os.getenv("NEON_DATABASE_URL", "")
    if not source_url:
        _die("REPLIT_DATABASE_URL (base Replit) absente.")
    if not target_url:
        _die("NEON_DATABASE_URL absente.")
    if _same_database(source_url, target_url):
        _die("source et cible sont la même base : arrêt.")

    print("1/4  Schéma sur Neon (migrations de l'app)...")
    env = {**os.environ, "DATABASE_URL": target_url}
    init = subprocess.run(
        [sys.executable, "-c", "import database; database.init_db()"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    if init.returncode != 0:
        print(init.stdout[-2000:], init.stderr[-2000:])
        _die("init_db a échoué sur la cible.")

    src = psycopg2.connect(source_url)
    src.set_session(readonly=True)
    dst = psycopg2.connect(target_url)

    v_src, v_dst = _schema_version(src), _schema_version(dst)
    if v_src is None or v_dst is None or v_src > v_dst or (v_src < v_dst and v_src < ADDITIVE_SINCE):
        _die(f"versions de schéma incompatibles (Replit v{v_src}, Neon v{v_dst}). "
             "Déployez le même code des deux côtés avant de copier.")
    if v_src < v_dst:
        print(f"     Replit v{v_src} -> Neon v{v_dst} : migrations additives, copie sans perte.")

    common = sorted(set(_tables(src)) & set(_tables(dst)) - SKIP_TABLES)
    only_src = sorted(set(_tables(src)) - set(_tables(dst)) - SKIP_TABLES)
    if only_src:
        print(f"     (ignorées, absentes du schéma de l'app : {', '.join(only_src)})")
    order = _parents_first(src, common)

    print(f"2/4  Copie de {len(order)} tables...")
    with dst.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in order) + " RESTART IDENTITY CASCADE")
    for table in order:
        cols = [c for c in _columns(src, table) if c in set(_columns(dst, table))]
        col_sql = ", ".join(f'"{c}"' for c in cols)
        copied = 0
        with src.cursor(name=f"copy_{table}") as read, dst.cursor() as write:
            read.itersize = BATCH
            read.execute(f'SELECT {col_sql} FROM "{table}"')
            while True:
                rows = read.fetchmany(BATCH)
                if not rows:
                    break
                execute_values(write, f'INSERT INTO "{table}" ({col_sql}) VALUES %s', rows,
                               page_size=BATCH)
                copied += len(rows)
        print(f"     {table:<28} {copied:>8} lignes")

    print("3/4  Séquences...")
    with dst.cursor() as cur:
        for table in order:
            for col in _columns(dst, table):
                cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (f'public."{table}"', col))
                seq = cur.fetchone()[0]
                if seq:
                    cur.execute(
                        f'SELECT setval(%s, COALESCE((SELECT MAX("{col}") FROM "{table}"), 0) + 1, false)',
                        (seq,),
                    )
    dst.commit()

    print("4/4  Vérification...")
    errors = []
    for table in order:
        a, b = _count(src, table), _count(dst, table)
        if a != b:
            errors.append(f"{table}: Replit {a} / Neon {b}")
    src.close()
    dst.close()
    if errors:
        _die("écarts de lignes :\n  " + "\n  ".join(errors))
    print(f"OK : {len(order)} tables identiques (schéma v{v_dst}).")


if __name__ == "__main__":
    main()
