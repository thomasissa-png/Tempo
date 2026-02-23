"""Synchronisation de base de données — export/import entre environnements.

Usage:
    # Exporter la DB courante vers un fichier JSON
    python db_sync.py export

    # Importer un dump JSON dans la DB courante (skip si données existent)
    python db_sync.py import

    # Importer en forçant (écrase les données existantes)
    python db_sync.py import --force

Le fichier de dump est db_dump.json (commité dans le repo pour que
le déploiement Replit l'ait automatiquement).

Tables exportées (données de prédiction/évaluation — PAS les users/SMS) :
- predictions    : nos prédictions quotidiennes
- actuals        : couleurs EDF confirmées
- performance    : évaluations de précision
- weather_cache  : dernière météo par date
- weather_forecast_log : historique des prévisions météo
- rte_daily      : données RTE agrégées
- weights_history : historique des recalibrations
"""

import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DUMP_FILE = Path(__file__).parent / "db_dump.json"

# Tables à synchroniser (ordre d'import important pour cohérence)
# On ne synchronise PAS users/sms_logs (données personnelles)
SYNC_TABLES = [
    "actuals",
    "predictions",
    "performance",
    "weather_cache",
    "weather_forecast_log",
    "rte_daily",
    "weights_history",
]

# Colonnes par table (exclut 'id' qui est auto-généré)
TABLE_COLUMNS = {
    "predictions": [
        "date", "couleur_predite", "probabilite_bleu", "probabilite_blanc",
        "probabilite_rouge", "score_risque", "temp_min_prevue", "temp_max_prevue",
        "pression_prevue", "jours_rouges_restants", "jours_blancs_restants",
        "raison", "horizon", "timestamp_prediction", "score_temperature",
        "score_budget", "score_weekday", "score_gradient", "score_clustering",
        "score_rte", "confirmed", "couleur_originale", "simulated",
        "source_meteo", "temp_moy_prevue", "humidity_prevue", "wind_speed_prevue",
        "score_c_nette", "score_pression",
    ],
    "actuals": [
        "date", "couleur_reelle", "synthetic", "timestamp_confirmation",
    ],
    "performance": [
        "date_prediction", "date_cible", "jours_avance", "correct",
        "couleur_predite", "couleur_reelle", "score_risque_predit",
        "ecart_score", "contexte_meteo", "timestamp_evaluation",
    ],
    "weather_cache": [
        "date", "temp_min", "temp_max", "temp_moy", "pressure",
        "humidity", "wind_speed", "description", "fetched_at",
    ],
    "weather_forecast_log": [
        "target_date", "forecast_date", "horizon_days",
        "temp_min", "temp_max", "temp_moy",
        "pressure", "humidity", "wind_speed", "fetched_at",
    ],
    "rte_daily": [
        "date", "conso_peak_mw", "conso_mean_mw", "prevision_j1_peak_mw",
        "nucleaire_mean_mw", "eolien_mean_mw", "solaire_mean_mw",
        "gaz_mean_mw", "hydraulique_mean_mw", "taux_co2_mean", "conso_minus_prev",
    ],
    "weights_history": [
        "date_update", "weights_json", "precision_avant", "precision_apres",
        "nb_predictions", "commentaire", "model_version", "timestamp_update",
    ],
}

# Conflict columns for ON CONFLICT DO UPDATE
CONFLICT_COLS = {
    "predictions": "(date, horizon)",
    "actuals": "(date)",
    "performance": "(date_prediction, date_cible, jours_avance)",
    "weather_cache": "(date)",  # will use latest fetched_at
    "weather_forecast_log": "(target_date, forecast_date)",
    "rte_daily": "(date)",
    "weights_history": None,  # no natural key, always insert
}


# Dedup columns for tables without natural UNIQUE keys
# Used to avoid inserting duplicate rows on repeated imports
_DEDUP_COLS = {
    "weights_history": ["date_update", "model_version", "commentaire"],
}


def _get_dedup_cols(table: str) -> list[str] | None:
    """Return dedup columns for tables without ON CONFLICT keys."""
    return _DEDUP_COLS.get(table)


def export_db() -> dict:
    """Exporte toutes les tables sync vers un dict."""
    from database import get_db

    conn = get_db()
    try:
        dump = {"exported_at": date.today().isoformat(), "tables": {}}

        for table in SYNC_TABLES:
            cols = TABLE_COLUMNS.get(table)
            if not cols:
                continue

            # Vérifier que la table existe et lire les colonnes disponibles
            try:
                info = conn.execute(
                    f"SELECT * FROM {table} LIMIT 0"
                ).description
                available_cols = {d[0] for d in info} if info else set()
            except Exception:
                logger.warning(f"[Sync] Table {table} introuvable, skip")
                dump["tables"][table] = []
                continue

            # Ne garder que les colonnes qui existent en base
            valid_cols = [c for c in cols if c in available_cols]
            if not valid_cols:
                dump["tables"][table] = []
                continue

            rows = conn.execute(
                f"SELECT {', '.join(valid_cols)} FROM {table}"
            ).fetchall()

            dump["tables"][table] = [
                {c: row[c] for c in valid_cols}
                for row in rows
            ]

        return dump
    finally:
        conn.close()


def export_to_file(path: Path | None = None) -> str:
    """Exporte la DB vers un fichier JSON."""
    path = path or DUMP_FILE
    dump = export_db()

    total = sum(len(rows) for rows in dump["tables"].values())
    dump["total_rows"] = total

    with open(path, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=None, separators=(",", ":"))

    size_kb = path.stat().st_size / 1024
    summary = ", ".join(
        f"{t}={len(rows)}" for t, rows in dump["tables"].items() if rows
    )
    logger.info(f"[Sync] Export: {total} rows ({size_kb:.0f}KB) → {path.name}")
    return f"Export terminé : {total} rows ({size_kb:.0f}KB). {summary}"


def _parse_conflict_cols(conflict_str: str) -> set[str]:
    """Parse '(date, horizon)' → {'date', 'horizon'}."""
    return {c.strip() for c in conflict_str.strip("()").split(",")}


def _get_available_cols(conn, table: str) -> set[str]:
    """Get columns that actually exist in the target DB table."""
    try:
        info = conn.execute(f"SELECT * FROM {table} LIMIT 0").description
        return {d[0] for d in info} if info else set()
    except Exception:
        return set()


def import_from_file(path: Path | None = None, force: bool = False) -> str:
    """Importe un dump JSON dans la DB courante.

    Args:
        force: Si True, écrase les données existantes (ON CONFLICT DO UPDATE).
               Si False, skip les lignes qui existent déjà (DO NOTHING).
    """
    from database import get_db

    path = path or DUMP_FILE
    if not path.exists():
        return f"Fichier {path.name} introuvable — lancez d'abord 'export'"

    with open(path, encoding="utf-8") as f:
        dump = json.load(f)

    conn = get_db()
    try:
        total_imported = 0
        total_errors = 0
        results = []

        for table in SYNC_TABLES:
            rows = dump.get("tables", {}).get(table, [])
            if not rows:
                continue

            # Filter dump columns to only those that exist in the target DB
            dump_cols = list(rows[0].keys())
            available = _get_available_cols(conn, table)
            if not available:
                logger.warning(f"[Sync] Table {table} introuvable, skip")
                continue
            cols = [c for c in dump_cols if c in available]
            if not cols:
                logger.warning(f"[Sync] {table}: aucune colonne commune, skip")
                continue
            dropped = set(dump_cols) - set(cols)
            if dropped:
                logger.info(
                    f"[Sync] {table}: colonnes ignorées (absentes en DB): "
                    f"{', '.join(sorted(dropped))}"
                )

            conflict = CONFLICT_COLS.get(table)

            # Build the SQL
            placeholders = ", ".join(["?"] * len(cols))
            col_list = ", ".join(cols)

            if conflict and force:
                # ON CONFLICT DO UPDATE — overwrite existing
                conflict_set = _parse_conflict_cols(conflict)
                update_cols = [c for c in cols if c not in conflict_set]
                update_set = ", ".join(
                    f"{c} = excluded.{c}" for c in update_cols
                )
                sql = (
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                    f"ON CONFLICT {conflict} DO UPDATE SET {update_set}"
                )
            elif conflict:
                # ON CONFLICT DO NOTHING — skip existing
                sql = (
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                    f"ON CONFLICT {conflict} DO NOTHING"
                )
            else:
                # No natural conflict key (e.g. weights_history).
                # Dedup by checking if an identical row already exists.
                if not force:
                    dedup_cols = _get_dedup_cols(table)
                    if dedup_cols:
                        dedup_where = " AND ".join(
                            f"{c} = ?" for c in dedup_cols
                        )
                        dedup_sql = (
                            f"SELECT COUNT(*) as c FROM {table} "
                            f"WHERE {dedup_where}"
                        )
                        filtered_rows = []
                        for row in rows:
                            dedup_vals = [row.get(c) for c in dedup_cols]
                            try:
                                existing = conn.execute(
                                    dedup_sql, dedup_vals
                                ).fetchone()
                                if not existing or existing["c"] == 0:
                                    filtered_rows.append(row)
                            except Exception:
                                filtered_rows.append(row)
                        skipped = len(rows) - len(filtered_rows)
                        rows = filtered_rows
                        if skipped:
                            logger.info(
                                f"[Sync] {table}: {skipped} doublons ignorés"
                            )
                sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

            count = 0
            errors = 0
            first_error = None
            batch_size = 500
            for i, row in enumerate(rows):
                try:
                    values = [row.get(c) for c in cols]
                    conn.execute(sql, values)
                    count += 1
                except Exception as e:
                    errors += 1
                    if first_error is None:
                        first_error = str(e)
                if (i + 1) % batch_size == 0:
                    conn.commit()

            conn.commit()
            total_imported += count
            total_errors += errors

            status = f"{table}: {count}/{len(rows)}"
            if errors:
                status += f" ({errors} erreurs)"
                logger.warning(
                    f"[Sync] {table}: {errors} erreurs. "
                    f"Première: {first_error}"
                )
            results.append(status)

        summary = ", ".join(results)
        mode = "force" if force else "safe"
        msg = f"Import ({mode}) terminé : {total_imported} rows"
        if total_errors:
            msg += f" ({total_errors} erreurs)"
        msg += f". {summary}"
        logger.info(f"[Sync] {msg}")
        return msg
    finally:
        conn.close()


def auto_import_if_empty() -> str | None:
    """Import automatique au démarrage si la DB production a des données manquantes.

    Appelé par le scheduler au post_startup. Compare le nombre de
    prédictions en base avec le dump. Si le dump contient significativement
    plus de données, importe les manquantes (ON CONFLICT DO NOTHING).

    Returns:
        Message de résultat ou None si rien à faire.
    """
    if not DUMP_FILE.exists():
        return None

    # Charger le dump pour connaître le volume attendu
    try:
        with open(DUMP_FILE, encoding="utf-8") as f:
            dump = json.load(f)
        dump_pred_count = len(dump.get("tables", {}).get("predictions", []))
    except Exception as e:
        logger.warning(f"[Sync] Lecture db_dump.json échouée: {e}")
        return None

    if dump_pred_count == 0:
        return None

    from database import get_db
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM predictions WHERE simulated = 0"
        ).fetchone()
        db_count = row["c"] if row else 0
    finally:
        conn.close()

    # Import si la base a moins de 90% des données du dump
    # (couvre les cas vide ET incomplet)
    if db_count >= dump_pred_count * 0.9:
        logger.info(
            f"[Sync] DB a {db_count} prédictions vs {dump_pred_count} dans le dump — sync inutile"
        )
        return None

    logger.info(
        f"[Sync] DB incomplète ({db_count} vs {dump_pred_count} dans dump) "
        f"— import automatique"
    )
    result = import_from_file(force=False)

    # Backfill missing temperatures from weather_cache
    try:
        bf = backfill_temp_moy_prevue()
        if "0/" not in bf:
            result += f" | {bf}"
    except Exception as e:
        logger.warning(f"[Sync] Backfill post-import échoué: {e}")

    # Après import, réévaluer les jours manquants
    try:
        from performance_tracker import evaluate_missed_days
        missed = evaluate_missed_days(lookback=30)
        if missed:
            result += f" + {missed} jours réévalués"
    except Exception as e:
        logger.warning(f"[Sync] Réévaluation post-import échouée: {e}")

    return result


def backfill_temp_moy_prevue() -> str:
    """Backfill predictions.temp_moy_prevue from weather_cache.temp_moy.

    When predictions were imported from a dump that didn't have temperature
    data, this fills in the blanks using the closest weather_cache entry.
    Also backfills humidity_prevue and wind_speed_prevue.
    """
    from database import get_db

    conn = get_db()
    try:
        # Count how many predictions are missing temp_moy_prevue
        row = conn.execute(
            "SELECT COUNT(*) as c FROM predictions "
            "WHERE temp_moy_prevue IS NULL AND simulated = 0"
        ).fetchone()
        missing = row["c"] if row else 0
        if missing == 0:
            return "Backfill: aucune prédiction sans température"

        # Get weather_cache data keyed by date (latest fetched_at per date)
        weather_rows = conn.execute(
            "SELECT wc.date, wc.temp_moy, wc.humidity, wc.wind_speed "
            "FROM weather_cache wc "
            "WHERE wc.temp_moy IS NOT NULL"
        ).fetchall()

        # Keep latest entry per date (rows ordered by default, take last seen)
        weather_map = {}
        for r in weather_rows:
            weather_map[r["date"]] = {
                "temp_moy": r["temp_moy"],
                "humidity": r["humidity"],
                "wind_speed": r["wind_speed"],
            }

        # Get predictions missing temp_moy_prevue
        preds = conn.execute(
            "SELECT id, date FROM predictions "
            "WHERE temp_moy_prevue IS NULL AND simulated = 0"
        ).fetchall()

        updated = 0
        for i, p in enumerate(preds):
            w = weather_map.get(p["date"])
            if w and w["temp_moy"] is not None:
                conn.execute(
                    "UPDATE predictions SET temp_moy_prevue = ?, "
                    "humidity_prevue = COALESCE(humidity_prevue, ?), "
                    "wind_speed_prevue = COALESCE(wind_speed_prevue, ?) "
                    "WHERE id = ?",
                    (w["temp_moy"], w["humidity"], w["wind_speed"], p["id"]),
                )
                updated += 1
            if (i + 1) % 500 == 0:
                conn.commit()

        conn.commit()
        msg = f"Backfill: {updated}/{missing} prédictions mises à jour avec temp_moy_prevue"
        logger.info(f"[Sync] {msg}")
        return msg
    except Exception as e:
        logger.warning(f"[Sync] Backfill temp_moy_prevue échoué: {e}")
        return f"Backfill échoué: {e}"
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("Usage: python db_sync.py [export|import] [--force]")
        sys.exit(1)

    cmd = sys.argv[1]
    force = "--force" in sys.argv

    if cmd == "export":
        print(export_to_file())
    elif cmd == "import":
        print(import_from_file(force=force))
    else:
        print(f"Commande inconnue: {cmd}")
        sys.exit(1)
