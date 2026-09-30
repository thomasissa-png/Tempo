"""Copie complète de la base PostgreSQL Replit vers la base Neon (Cloudflare).

À lancer depuis une session Claude Code :
    python3 cloudflare/copy_database.py

Variables d'environnement :
    REPLIT_DATABASE_URL base source (Replit) : lue seulement, dans une transaction READ ONLY.
    NEON_DATABASE_URL   base cible (Neon) : son contenu est REMPLACÉ par celui de la source

Transport : API SQL HTTPS de Neon (le port 5432 est bloqué depuis les sessions).
Le schéma de la cible est créé par l'app elle-même au premier démarrage du conteneur
(database.init_db) : déployer le Worker avant la première copie.

Le script :
  1. vérifie que les schémas sont compatibles ;
  2. lit TOUTES les tables de la source dans une seule transaction (instantané cohérent) ;
  3. écrit tout sur la cible dans une seule transaction : vidage, copie (ids conservés),
     recalage des séquences. La cible est soit entièrement l'ancien état, soit le nouveau ;
  4. compare le nombre de lignes table par table et s'arrête en erreur si écart.

Relançable sans risque : chaque exécution reproduit l'état actuel de la source
(c'est ce qu'on fait juste avant la bascule pour récupérer les dernières données).
"""
import json
import os
import sys
from urllib.parse import urlparse

from neon_http import NeonError, NeonHTTP

SKIP_TABLES = {"schema_version"}  # géré par init_db sur la cible
# Depuis la v23, les migrations ne font qu'ajouter des tables (v24 agent_files,
# v25 rte_forecast_log) : une source plus ancienne se copie sans perte, les
# nouvelles tables restent vides sur la cible.
ADDITIVE_SINCE = 23
MAX_PARAMS = 30000        # par INSERT (limite PostgreSQL : 65535)
MAX_BODY = 8_000_000      # octets par requête HTTP (Neon plafonne vers 10 Mo)


def _die(msg: str) -> None:
    print(f"ERREUR : {msg}")
    sys.exit(1)


def _same_database(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.hostname, pa.port, pa.path) == (pb.hostname, pb.port, pb.path)


def _tables(db) -> list[str]:
    return sorted(r[0] for r in db.query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"))


def _columns(db) -> dict[str, list[str]]:
    cols: dict[str, list[str]] = {}
    for table, col in db.query(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"):
        cols.setdefault(table, []).append(col)
    return cols


def _parents_first(db, tables: list[str]) -> list[str]:
    """Ordonne les tables pour que chaque table référencée soit copiée avant."""
    deps: dict[str, set] = {}
    for child, parent in db.query(
            "SELECT tc.table_name, ccu.table_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'"):
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


def _schema_version(db):
    if db.scalar("SELECT to_regclass('public.schema_version') IS NOT NULL") != "t":
        return None
    v = db.scalar("SELECT MAX(version) FROM schema_version")
    return int(v) if v is not None else None


def insert_statements(table: str, cols: list[str], rows: list[list]) -> list[tuple[str, list]]:
    """INSERT multi-lignes paramétrés ($1, $2...), au plus MAX_PARAMS paramètres chacun."""
    if not rows:
        return []
    col_sql = ", ".join(f'"{c}"' for c in cols)
    per_stmt = max(1, MAX_PARAMS // len(cols))
    out = []
    for start in range(0, len(rows), per_stmt):
        chunk = rows[start:start + per_stmt]
        values, params = [], []
        for row in chunk:
            base = len(params)
            values.append("(" + ", ".join(f"${base + i + 1}" for i in range(len(cols))) + ")")
            params.extend(row)
        out.append((f'INSERT INTO "{table}" ({col_sql}) VALUES {", ".join(values)}', params))
    return out


def _serial_columns(db) -> list[tuple[str, str]]:
    return [(t, c) for t, c in db.query(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "AND pg_get_serial_sequence(format('public.%I', table_name), column_name) IS NOT NULL")]


def build_write(order: list[str], cols: dict[str, list[str]], data: dict[str, list[list]],
                serials: list[tuple[str, str]]) -> list[tuple[str, list]]:
    """La transaction d'écriture complète sur la cible."""
    stmts = [("TRUNCATE " + ", ".join(f'"{t}"' for t in order) + " RESTART IDENTITY CASCADE", [])]
    for table in order:
        stmts += insert_statements(table, cols[table], data[table])
    for table, col in serials:
        if table in order:
            stmts.append((
                f"SELECT setval(pg_get_serial_sequence(format('public.%I', $1::text), $2::text), "
                f'COALESCE((SELECT MAX("{col}") FROM "{table}"), 0) + 1, false)', [table, col]))
    return stmts


def main() -> None:
    source_url = os.getenv("REPLIT_DATABASE_URL", "")
    target_url = os.getenv("NEON_DATABASE_URL", "")
    if not source_url:
        _die("REPLIT_DATABASE_URL (base Replit) absente.")
    if not target_url:
        _die("NEON_DATABASE_URL absente.")
    if _same_database(source_url, target_url):
        _die("source et cible sont la même base : arrêt.")

    src = NeonHTTP(source_url, readonly=True)
    dst = NeonHTTP(target_url)
    try:
        run(src, dst)
    except NeonError as e:
        _die(str(e))
    finally:
        src.close()
        dst.close()


def run(src, dst) -> None:
    print("1/4  Schémas...")
    v_src, v_dst = _schema_version(src), _schema_version(dst)
    if v_dst is None:
        _die("schéma absent sur Neon : déployer d'abord le Worker (le conteneur crée le schéma).")
    if v_src is None or v_src > v_dst or (v_src < v_dst and v_src < ADDITIVE_SINCE):
        _die(f"versions de schéma incompatibles (Replit v{v_src}, Neon v{v_dst}). "
             "Déployez le même code des deux côtés avant de copier.")
    if v_src < v_dst:
        print(f"     Replit v{v_src} -> Neon v{v_dst} : migrations additives, copie sans perte.")

    src_tables, dst_tables = set(_tables(src)), set(_tables(dst))
    common = sorted(src_tables & dst_tables - SKIP_TABLES)
    only_src = sorted(src_tables - dst_tables - SKIP_TABLES)
    if only_src:
        print(f"     (ignorées, absentes du schéma de l'app : {', '.join(only_src)})")
    order = _parents_first(src, common)
    src_cols, dst_cols = _columns(src), _columns(dst)
    cols = {t: [c for c in src_cols[t] if c in set(dst_cols[t])] for t in order}

    print(f"2/4  Lecture de {len(order)} tables (une transaction en lecture seule)...")
    results = src.batch([(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols[t])} FROM "{t}"', [])
                         for t in order])
    data = dict(zip(order, results))

    print("3/4  Écriture sur Neon (une transaction)...")
    stmts = build_write(order, cols, data, _serial_columns(dst))
    size = len(json.dumps([{"query": q, "params": p} for q, p in stmts]))
    if size > MAX_BODY:
        _die(f"{size / 1e6:.1f} Mo à écrire : trop pour une transaction HTTP, découper par table.")
    # Comptes relus dans la même transaction : l'app qui tourne sur Neon ne peut pas les fausser.
    counts = dst.batch(stmts + [(f'SELECT COUNT(*) FROM "{t}"', []) for t in order])[-len(order):]
    for t in order:
        print(f"     {t:<28} {len(data[t]):>8} lignes")

    print("4/4  Vérification...")
    errors = [f"{t}: Replit {len(data[t])} / Neon {c[0][0]}"
              for t, c in zip(order, counts) if int(c[0][0]) != len(data[t])]
    if errors:
        _die("écarts de lignes :\n  " + "\n  ".join(errors))
    print(f"OK : {len(order)} tables identiques (schéma v{v_dst}).")


if __name__ == "__main__":
    main()
