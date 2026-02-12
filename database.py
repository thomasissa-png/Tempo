"""Gestion de la base de données SQLite — 10 tables.

Tables :
  - predictions         : prédictions générées par l'algorithme (+ raw sub-scores v9)
  - prediction_changes  : historique des changements de couleur entre cycles
  - actuals             : couleurs réelles confirmées par EDF
  - performance         : comparaison prédiction vs réalité (UNIQUE multi-horizon v9)
  - users               : abonnés aux alertes SMS (phone_encrypted Fix #1)
  - sms_logs            : historique des SMS envoyés
  - weights_history     : versions successives des poids (+ rollback v9)
  - weather_cache       : cache des prévisions météo (Fix #17)
  - learning_journal    : patterns d'erreurs et corrections (versioned v9)
  - rte_daily           : données RTE eco2mix agrégées par jour (v15)

Migrations :
  v3  — sub-scores, index UNIQUE
  v4  — cycle_id, prediction_changes
  v5  — UNIQUE performance, model_version
  v6  — colonne synthetic sur actuals
  v7  — table learning_journal
  v8  — HMAC phone hash, couleur_originale
  v9  — raw sub-scores, perf multi-horizon, learning history, rollback
  v10 — purge des données météo simulées (predictions, weather_cache, performance)
  v11 — nettoyage apprentissage contaminé (corrections biaisées, reset poids)
  v12 — purge évaluations faussées (couleur_originale perdue par store_prediction)
  v15 — table rte_daily (données RTE eco2mix agrégées par jour)
"""

import sqlite3
import hashlib
import base64
import logging
import threading
from datetime import datetime
from config import Config
import json

logger = logging.getLogger(__name__)

# ================================================================
# Chiffrement téléphone (Fix #1 — Fernet réversible pour SMS)
# ================================================================

_fernet_instance = None
_fernet_lock = threading.Lock()


def _get_fernet():
    """Singleton Fernet. Clé depuis PHONE_ENCRYPTION_KEY ou dérivée d'ADMIN_PASSWORD.

    Fix audit v6 : threading.Lock pour éviter les race conditions
    lors de l'initialisation concurrente du singleton.
    """
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    with _fernet_lock:
        # Re-vérifier après acquisition du lock (double-checked locking)
        if _fernet_instance is not None:
            return _fernet_instance

        from cryptography.fernet import Fernet

        key_source = Config.PHONE_ENCRYPTION_KEY
        if key_source:
            _fernet_instance = Fernet(key_source.encode() if isinstance(key_source, str) else key_source)
        else:
            raw = hashlib.pbkdf2_hmac(
                "sha256",
                Config.ADMIN_PASSWORD.encode(),
                b"tempoforecast_fernet_v2",
                iterations=100_000,
            )
            _fernet_instance = Fernet(base64.urlsafe_b64encode(raw))

        return _fernet_instance


def encrypt_phone(phone: str) -> str:
    """Chiffre un numéro de téléphone (réversible, pour envoi SMS)."""
    return _get_fernet().encrypt(phone.encode()).decode()


def decrypt_phone(encrypted: str) -> str:
    """Déchiffre un numéro de téléphone pour envoi SMS."""
    return _get_fernet().decrypt(encrypted.encode()).decode()


def _get_hmac_key() -> bytes:
    """Clé HMAC pour le hachage des numéros de téléphone.
    Utilise PHONE_ENCRYPTION_KEY ou dérive d'ADMIN_PASSWORD."""
    key_source = Config.PHONE_ENCRYPTION_KEY or Config.ADMIN_PASSWORD or "tempoforecast"
    return hashlib.sha256(
        (key_source + ":phone_hmac_salt_v1").encode()
    ).digest()


def hash_phone(phone: str) -> str:
    """HMAC-SHA256 du numéro (lookup / déduplication).
    Utilise une clé secrète pour empêcher les attaques par rainbow table."""
    import hmac as _hmac
    return _hmac.new(
        _get_hmac_key(), phone.strip().encode(), hashlib.sha256
    ).hexdigest()


# ================================================================
# Connexion DB
# ================================================================

def get_db() -> sqlite3.Connection:
    """Obtenir une connexion à la base de données."""
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ================================================================
# Initialisation
# ================================================================

def init_db():
    """Créer toutes les tables et index."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS predictions (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            date                 TEXT    NOT NULL,
            couleur_predite      TEXT    NOT NULL CHECK(couleur_predite IN ('BLEU','BLANC','ROUGE')),
            probabilite_bleu     REAL    DEFAULT 0,
            probabilite_blanc    REAL    DEFAULT 0,
            probabilite_rouge    REAL    DEFAULT 0,
            score_risque         REAL    DEFAULT 0,
            temp_min_prevue      REAL,
            temp_max_prevue      REAL,
            pression_prevue      REAL,
            jours_rouges_restants INTEGER,
            jours_blancs_restants INTEGER,
            raison               TEXT    DEFAULT '',
            horizon              TEXT    DEFAULT 'J-1',
            timestamp_prediction TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS actuals (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            date                    TEXT    NOT NULL UNIQUE,
            couleur_reelle          TEXT    NOT NULL CHECK(couleur_reelle IN ('BLEU','BLANC','ROUGE')),
            synthetic               INTEGER DEFAULT 0,
            timestamp_confirmation  TEXT    NOT NULL
        );

        -- D-1 : UNIQUE(date_prediction, date_cible, jours_avance) multi-horizon
        CREATE TABLE IF NOT EXISTS performance (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            date_prediction      TEXT    NOT NULL,
            date_cible           TEXT    NOT NULL,
            jours_avance         INTEGER NOT NULL,
            correct              INTEGER NOT NULL,
            couleur_predite      TEXT    NOT NULL,
            couleur_reelle       TEXT    NOT NULL,
            score_risque_predit  REAL    DEFAULT 0,
            ecart_score          REAL    DEFAULT 0,
            contexte_meteo       TEXT    DEFAULT '',
            timestamp_evaluation TEXT    NOT NULL,
            UNIQUE(date_prediction, date_cible, jours_avance)
        );

        -- Fix #1 : phone_encrypted (Fernet) pour pouvoir envoyer les SMS
        CREATE TABLE IF NOT EXISTS users (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_hash          TEXT    NOT NULL UNIQUE,
            phone_encrypted     TEXT    NOT NULL DEFAULT '',
            phone_last4         TEXT    NOT NULL,
            seuil_alerte_rouge  INTEGER DEFAULT 70,
            delai_alerte        INTEGER DEFAULT 1,
            alerte_blanc        INTEGER DEFAULT 0,
            recap_hebdo         INTEGER DEFAULT 0,
            actif               INTEGER DEFAULT 1,
            created_at          TEXT    NOT NULL,
            updated_at          TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sms_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            type_alerte     TEXT    NOT NULL,
            couleur         TEXT    DEFAULT '',
            message_body    TEXT    NOT NULL,
            date_envoi      TEXT    NOT NULL,
            statut          TEXT    DEFAULT 'pending',
            twilio_sid      TEXT    DEFAULT '',
            erreur          TEXT    DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS weights_history (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            date_update      TEXT    NOT NULL,
            weights_json     TEXT    NOT NULL,
            precision_avant  REAL    DEFAULT 0,
            precision_apres  REAL    DEFAULT 0,
            nb_predictions   INTEGER DEFAULT 0,
            commentaire      TEXT    DEFAULT '',
            model_version    TEXT    DEFAULT '',
            timestamp_update TEXT    NOT NULL
        );

        -- Fix #17 : table déclarée ici au lieu de créée à la volée
        CREATE TABLE IF NOT EXISTS weather_cache (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT    NOT NULL,
            temp_min    REAL,
            temp_max    REAL,
            temp_moy    REAL,
            pressure    REAL,
            humidity    REAL,
            wind_speed  REAL,
            description TEXT,
            fetched_at  TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_predictions_date    ON predictions(date);
        CREATE INDEX IF NOT EXISTS idx_predictions_horizon ON predictions(horizon);
        CREATE INDEX IF NOT EXISTS idx_actuals_date        ON actuals(date);
        CREATE INDEX IF NOT EXISTS idx_performance_cible   ON performance(date_cible);
        CREATE INDEX IF NOT EXISTS idx_performance_avance  ON performance(jours_avance);
        CREATE INDEX IF NOT EXISTS idx_users_hash          ON users(phone_hash);
        CREATE INDEX IF NOT EXISTS idx_sms_logs_user       ON sms_logs(user_id);
        CREATE INDEX IF NOT EXISTS idx_sms_logs_date       ON sms_logs(date_envoi);
        CREATE INDEX IF NOT EXISTS idx_weather_cache_date  ON weather_cache(date);

        -- Fix audit DB : index composites pour les requetes frequentes
        CREATE INDEX IF NOT EXISTS idx_users_actif_seuil
            ON users(actif, seuil_alerte_rouge);
        CREATE INDEX IF NOT EXISTS idx_users_actif_blanc
            ON users(actif, alerte_blanc);
        CREATE INDEX IF NOT EXISTS idx_sms_logs_user_date
            ON sms_logs(user_id, date_envoi);
        CREATE INDEX IF NOT EXISTS idx_actuals_date_synthetic
            ON actuals(date, synthetic);
    """)

    # === Fix #14 audit v4 : migrations conditionnelles via PRAGMA user_version ===
    # Fix audit DB : verrou exclusif pour eviter les race conditions
    conn.execute("BEGIN EXCLUSIVE")
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    if version < 3:
        # Migrations v3 — Système d'apprentissage
        for col in ["score_temperature", "score_budget", "score_weekday",
                    "score_gradient", "score_clustering", "score_rte"]:
            try:
                conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} REAL DEFAULT 0")
            except sqlite3.OperationalError:
                logger.debug(f"Migration v3: colonne {col} existe deja")

        conn.execute("""DELETE FROM predictions WHERE id NOT IN
            (SELECT MAX(id) FROM predictions GROUP BY date, horizon)""")
        conn.execute("DROP INDEX IF EXISTS idx_predictions_date")
        conn.execute("DROP INDEX IF EXISTS idx_predictions_horizon")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_date_horizon "
            "ON predictions(date, horizon)")

        conn.execute("""DELETE FROM weather_cache WHERE id NOT IN
            (SELECT MAX(id) FROM weather_cache GROUP BY date)""")
        conn.execute("DROP INDEX IF EXISTS idx_weather_cache_date")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_weather_cache_date "
            "ON weather_cache(date)")

        conn.execute("PRAGMA user_version = 3")
        logger.info("Migration v3 appliquee (sub-scores, index UNIQUE)")

    if version < 4:
        # Migration v4 — Workflow cohérent : cycle_id, couleur_precedente, simulated, confirmed
        for col, coltype in [
            ("cycle_id", "TEXT DEFAULT ''"),
            ("couleur_precedente", "TEXT DEFAULT ''"),
            ("simulated", "INTEGER DEFAULT 0"),
            ("confirmed", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError:
                logger.debug(f"Migration v4: colonne {col} existe deja")

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS prediction_changes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT    NOT NULL,
                horizon         TEXT    NOT NULL,
                couleur_avant   TEXT    NOT NULL,
                couleur_apres   TEXT    NOT NULL,
                score_avant     REAL    DEFAULT 0,
                score_apres     REAL    DEFAULT 0,
                cycle_id        TEXT    NOT NULL,
                timestamp_change TEXT   NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pred_changes_date ON prediction_changes(date);
            CREATE INDEX IF NOT EXISTS idx_pred_changes_cycle ON prediction_changes(cycle_id);
        """)
        conn.execute("PRAGMA user_version = 4")
        logger.info("Migration v4 appliquee (cycle_id, prediction_changes)")

    if version < 5:
        # Migration v5 — Fix ML-3 : UNIQUE(date_prediction, date_cible) sans couleur_predite
        # + ML-16 : colonne model_version dans weights_history
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS performance_new (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_prediction      TEXT    NOT NULL,
                    date_cible           TEXT    NOT NULL,
                    jours_avance         INTEGER NOT NULL,
                    correct              INTEGER NOT NULL,
                    couleur_predite      TEXT    NOT NULL,
                    couleur_reelle       TEXT    NOT NULL,
                    score_risque_predit  REAL    DEFAULT 0,
                    ecart_score          REAL    DEFAULT 0,
                    contexte_meteo       TEXT    DEFAULT '',
                    timestamp_evaluation TEXT    NOT NULL,
                    UNIQUE(date_prediction, date_cible)
                );
                INSERT OR IGNORE INTO performance_new
                    SELECT * FROM performance;
                DROP TABLE performance;
                ALTER TABLE performance_new RENAME TO performance;
                CREATE INDEX IF NOT EXISTS idx_performance_cible
                    ON performance(date_cible);
                CREATE INDEX IF NOT EXISTS idx_performance_avance
                    ON performance(jours_avance);
            """)
        except sqlite3.OperationalError as e:
            logger.warning(f"Migration v5 performance: {e}")

        try:
            conn.execute(
                "ALTER TABLE weights_history ADD COLUMN model_version TEXT DEFAULT ''"
            )
        except sqlite3.OperationalError:
            logger.debug("Migration v5: colonne model_version existe deja")

        conn.execute("PRAGMA user_version = 5")
        conn.commit()
        logger.info("Migration v5 appliquee (UNIQUE performance, model_version)")

    if version < 6:
        # Migration v6 — Marquage des actuals synthétiques
        # Les actuals injectés par seed_from_remaining() ne sont pas des données EDF
        # et ne doivent PAS servir à évaluer la performance ni entraîner le ML.
        try:
            conn.execute(
                "ALTER TABLE actuals ADD COLUMN synthetic INTEGER DEFAULT 0"
            )
        except sqlite3.OperationalError:
            logger.debug("Migration v6: colonne synthetic existe deja")

        conn.execute("PRAGMA user_version = 6")
        conn.commit()
        logger.info("Migration v6 appliquee (colonne synthetic sur actuals)")

    if version < 7:
        # Migration v7 — Journal d'apprentissage continu
        # Stocke les patterns d'erreurs détectés et les corrections de biais
        # pour améliorer les prédictions futures.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS learning_journal (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                date_analysis    TEXT    NOT NULL,
                pattern_type     TEXT    NOT NULL,
                pattern_key      TEXT    NOT NULL,
                observation      TEXT    NOT NULL,
                accuracy         REAL    DEFAULT 0,
                bias_direction   TEXT    DEFAULT '',
                bias_magnitude   REAL    DEFAULT 0,
                sample_size      INTEGER NOT NULL,
                correction_score REAL    DEFAULT 0,
                confidence       REAL    DEFAULT 0,
                active           INTEGER DEFAULT 1,
                created_at       TEXT    NOT NULL,
                UNIQUE(pattern_type, pattern_key)
            );
            CREATE INDEX IF NOT EXISTS idx_learning_active
                ON learning_journal(active);
        """)
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
        logger.info("Migration v7 appliquee (table learning_journal)")

    if version < 8:
        # Migration v8 — HMAC phone hash + colonne couleur_originale
        # (a) Rehash tous les users existants avec HMAC au lieu de SHA-256 brut
        try:
            users = conn.execute(
                "SELECT id, phone_encrypted FROM users WHERE phone_encrypted != ''"
            ).fetchall()
            rehashed = 0
            for u in users:
                try:
                    phone = decrypt_phone(u["phone_encrypted"])
                    new_hash = hash_phone(phone)
                    conn.execute(
                        "UPDATE users SET phone_hash = ? WHERE id = ?",
                        (new_hash, u["id"]),
                    )
                    rehashed += 1
                except Exception as exc:
                    logger.debug(f"Migration v8: skip rehash user {u['id']}: {exc}")
            if rehashed:
                logger.info(f"Migration v8: {rehashed} phone hashes migrés vers HMAC")
        except Exception as e:
            logger.warning(f"Migration v8 rehash: {e}")

        # (b) Ajouter couleur_originale pour préserver les prédictions avant confirmation
        try:
            conn.execute(
                "ALTER TABLE predictions ADD COLUMN couleur_originale TEXT DEFAULT ''"
            )
        except sqlite3.OperationalError:
            logger.debug("Migration v8: colonne couleur_originale existe deja")

        conn.execute("PRAGMA user_version = 8")
        conn.commit()
        logger.info("Migration v8 appliquee (HMAC phone hash, couleur_originale)")

    if version < 9:
        # Migration v9 — Learning system improvements (audit)

        # C-1: Raw sub-scores (before corrections) for uncontaminated ML training
        for col in ["score_temperature_raw", "score_budget_raw", "score_weekday_raw",
                     "score_gradient_raw", "score_clustering_raw", "score_rte_raw"]:
            try:
                conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} REAL DEFAULT 0")
            except sqlite3.OperationalError:
                logger.debug(f"Migration v9: colonne {col} existe deja")

        # D-1: Fix performance UNIQUE to include jours_avance for multi-horizon evals
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS performance_v9 (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_prediction      TEXT    NOT NULL,
                    date_cible           TEXT    NOT NULL,
                    jours_avance         INTEGER NOT NULL,
                    correct              INTEGER NOT NULL,
                    couleur_predite      TEXT    NOT NULL,
                    couleur_reelle       TEXT    NOT NULL,
                    score_risque_predit  REAL    DEFAULT 0,
                    ecart_score          REAL    DEFAULT 0,
                    contexte_meteo       TEXT    DEFAULT '',
                    timestamp_evaluation TEXT    NOT NULL,
                    UNIQUE(date_prediction, date_cible, jours_avance)
                );
                INSERT OR IGNORE INTO performance_v9
                    SELECT * FROM performance;
                DROP TABLE performance;
                ALTER TABLE performance_v9 RENAME TO performance;
                CREATE INDEX IF NOT EXISTS idx_performance_cible
                    ON performance(date_cible);
                CREATE INDEX IF NOT EXISTS idx_performance_avance
                    ON performance(jours_avance);
            """)
        except sqlite3.OperationalError as e:
            logger.warning(f"Migration v9 performance: {e}")

        # A-5: Learning journal history — versioned by date_analysis
        # Replace UNIQUE(pattern_type, pattern_key) with UNIQUE(pattern_type, pattern_key, date_analysis)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS learning_journal_v9 (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_analysis    TEXT    NOT NULL,
                    pattern_type     TEXT    NOT NULL,
                    pattern_key      TEXT    NOT NULL,
                    observation      TEXT    NOT NULL,
                    accuracy         REAL    DEFAULT 0,
                    bias_direction   TEXT    DEFAULT '',
                    bias_magnitude   REAL    DEFAULT 0,
                    sample_size      INTEGER NOT NULL,
                    correction_score REAL    DEFAULT 0,
                    confidence       REAL    DEFAULT 0,
                    active           INTEGER DEFAULT 1,
                    created_at       TEXT    NOT NULL,
                    disabled_at      TEXT    DEFAULT NULL,
                    UNIQUE(pattern_type, pattern_key, date_analysis)
                );
                INSERT OR IGNORE INTO learning_journal_v9
                    (id, date_analysis, pattern_type, pattern_key, observation,
                     accuracy, bias_direction, bias_magnitude, sample_size,
                     correction_score, confidence, active, created_at)
                    SELECT id, date_analysis, pattern_type, pattern_key, observation,
                           accuracy, bias_direction, bias_magnitude, sample_size,
                           correction_score, confidence, active, created_at
                    FROM learning_journal;
                DROP TABLE learning_journal;
                ALTER TABLE learning_journal_v9 RENAME TO learning_journal;
                CREATE INDEX IF NOT EXISTS idx_learning_active
                    ON learning_journal(active);
                CREATE INDEX IF NOT EXISTS idx_learning_date
                    ON learning_journal(date_analysis);
            """)
        except sqlite3.OperationalError as e:
            logger.warning(f"Migration v9 learning_journal: {e}")

        # W-5: Rollback tracking in weights_history
        try:
            conn.execute(
                "ALTER TABLE weights_history ADD COLUMN rollback_of INTEGER DEFAULT NULL"
            )
        except sqlite3.OperationalError:
            logger.debug("Migration v9: colonne rollback_of existe deja")

        conn.execute("PRAGMA user_version = 9")
        conn.commit()
        logger.info("Migration v9 appliquee (raw sub-scores, perf multi-horizon, "
                    "learning history, rollback)")

    if version < 10:
        # Migration v10 — Purge des données météo simulées
        # Les prédictions basées sur des moyennes saisonnières aléatoires (ancien fallback)
        # polluent l'historique et l'apprentissage.
        # Ce fallback a été supprimé du code, on nettoie la base existante.

        # 1. Supprimer les prédictions basées sur de la météo simulée
        cursor = conn.execute("DELETE FROM predictions WHERE simulated = 1")
        deleted_preds = cursor.rowcount

        # 2. Supprimer les données météo simulées du cache
        cursor = conn.execute(
            "DELETE FROM weather_cache WHERE description = 'donnees simulees'"
        )
        deleted_weather = cursor.rowcount

        # 3. Supprimer les évaluations de performance issues de prédictions simulées
        # (la raison contenait "Meteo simulee" quand la prédiction utilisait le fallback)
        cursor = conn.execute(
            "DELETE FROM performance WHERE contexte_meteo LIKE '%simulee%'"
        )
        deleted_perf = cursor.rowcount

        conn.execute("PRAGMA user_version = 10")
        conn.commit()
        logger.info(
            f"Migration v10 appliquee (purge donnees simulees: "
            f"{deleted_preds} predictions, {deleted_weather} meteo cache, "
            f"{deleted_perf} evaluations performance)"
        )

    if version < 11:
        # Migration v11 — Nettoyage apprentissage contaminé
        # Fix ML-circular : analyze_error_patterns() utilisait les températures
        # prévues (predictions.temp_min_prevue) au lieu des réelles (weather_cache).
        # Fix ML-contamination : recalculate_weights() fallback COALESCE ramenait
        # des scores corrigés quand les raw scores étaient absents (pré-v9).
        # → On purge tout le learning_journal (corrections basées sur données biaisées)
        #   et on réinitialise les poids pour que le modèle réapprenne proprement.

        # 1. Désactiver toutes les corrections d'apprentissage existantes
        cursor = conn.execute(
            "UPDATE learning_journal SET active = 0, "
            "disabled_at = ? WHERE active = 1",
            (datetime.now().isoformat(),)
        )
        disabled_corrections = cursor.rowcount

        # 2. Réinitialiser les poids aux valeurs par défaut
        conn.execute(
            """INSERT INTO weights_history
               (date_update, weights_json, precision_avant, precision_apres,
                nb_predictions, commentaire, timestamp_update)
               VALUES (?, ?, 0, 0, 0, 'Reset v11: nettoyage apprentissage contaminé', ?)""",
            (datetime.now().strftime("%Y-%m-%d"),
             json.dumps(Config.DEFAULT_WEIGHTS),
             datetime.now().isoformat()),
        )

        conn.execute("PRAGMA user_version = 11")
        conn.commit()
        logger.info(
            f"Migration v11 appliquee (nettoyage apprentissage: "
            f"{disabled_corrections} corrections désactivées, poids réinitialisés)"
        )

    if version < 12:
        # Migration v12 — Purge des évaluations faussées
        # Fix #34 : store_prediction() ne préservait pas couleur_originale
        # lors des INSERT OR REPLACE → évaluations skippées → 100% artificiel.
        # On purge la table performance pour repartir sur des évaluations propres.
        cursor = conn.execute("DELETE FROM performance")
        deleted_perf = cursor.rowcount

        conn.execute("PRAGMA user_version = 12")
        conn.commit()
        logger.info(
            f"Migration v12 appliquee (purge {deleted_perf} evaluations faussees)"
        )

    if version < 13:
        # Migration v13 — ON DELETE CASCADE sur sms_logs.user_id
        # Fix audit DB : la suppression RGPD des users inactifs ne necessite
        # plus de supprimer manuellement les sms_logs associes.
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sms_logs_v13 (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    type_alerte     TEXT    NOT NULL,
                    couleur         TEXT    DEFAULT '',
                    message_body    TEXT    NOT NULL,
                    date_envoi      TEXT    NOT NULL,
                    statut          TEXT    DEFAULT 'pending',
                    twilio_sid      TEXT    DEFAULT '',
                    erreur          TEXT    DEFAULT '',
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                INSERT INTO sms_logs_v13 SELECT * FROM sms_logs;
                DROP TABLE sms_logs;
                ALTER TABLE sms_logs_v13 RENAME TO sms_logs;
                CREATE INDEX IF NOT EXISTS idx_sms_logs_user
                    ON sms_logs(user_id);
                CREATE INDEX IF NOT EXISTS idx_sms_logs_date
                    ON sms_logs(date_envoi);
                CREATE INDEX IF NOT EXISTS idx_sms_logs_user_date
                    ON sms_logs(user_id, date_envoi);
            """)
        except sqlite3.OperationalError as e:
            logger.warning(f"Migration v13 sms_logs CASCADE: {e}")

        # Index composites sur colonnes ajoutees en v4
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_predictions_confirmed "
                "ON predictions(confirmed, date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_predictions_horizon_sim "
                "ON predictions(horizon, simulated)"
            )
        except sqlite3.OperationalError:
            logger.debug("Migration v13: index predictions skip (colonnes v4 absentes)")

        conn.execute("PRAGMA user_version = 13")
        conn.commit()
        logger.info("Migration v13 appliquee (ON DELETE CASCADE sur sms_logs)")

    if version < 14:
        # Migration v14 — Audit ML fev 2026 : recalibrage poids v3.0
        # Les poids v2.2 sur-predisaient massivement BLANC (210 faux BLANC).
        # Temperature passe de 27% a 40%, budget de 20% a 12%.
        # Le seuil ROUGE est desormais dynamique (abaisse par temps froid).
        # On reinitialise les poids et desactive les corrections d'apprentissage
        # basees sur l'ancien systeme de scoring.
        conn.execute(
            """INSERT INTO weights_history
               (date_update, weights_json, precision_avant, precision_apres,
                nb_predictions, commentaire, timestamp_update)
               VALUES (?, ?, 0, 0, 0, ?, ?)""",
            (datetime.now().strftime("%Y-%m-%d"),
             json.dumps(Config.DEFAULT_WEIGHTS),
             "Reset v14: audit ML — poids v3.0 (temp 40%, budget 12%, seuil dynamique)",
             datetime.now().isoformat()),
        )

        # Desactiver les anciennes corrections (calibrees sur poids v2.2)
        cursor = conn.execute(
            "UPDATE learning_journal SET active = 0, "
            "disabled_at = ? WHERE active = 1",
            (datetime.now().isoformat(),)
        )
        disabled = cursor.rowcount

        conn.execute("PRAGMA user_version = 14")
        conn.commit()
        logger.info(
            f"Migration v14 appliquee (poids v3.0, "
            f"{disabled} corrections desactivees)"
        )

    if version < 15:
        # Migration v15 — Table rte_daily pour features ML RTE
        # Stocke les donnees de consommation/production RTE agreges par jour
        # (conso peak/mean, nucleaire, eolien, solaire, gaz, hydraulique).
        # Alimente les features LAG du modele ML (D-1, 3j, 7j).
        conn.execute('''CREATE TABLE IF NOT EXISTS rte_daily (
            date TEXT PRIMARY KEY,
            conso_peak_mw REAL,
            conso_mean_mw REAL,
            prevision_j1_peak_mw REAL,
            nucleaire_mean_mw REAL,
            eolien_mean_mw REAL,
            solaire_mean_mw REAL,
            gaz_mean_mw REAL,
            hydraulique_mean_mw REAL,
            taux_co2_mean REAL,
            conso_minus_prev REAL
        )''')
        conn.execute("PRAGMA user_version = 15")
        conn.commit()
        logger.info("Migration v15 appliquee (table rte_daily)")

    # Poids initiaux si vide
    existing = conn.execute("SELECT COUNT(*) as c FROM weights_history").fetchone()
    if existing["c"] == 0:
        conn.execute(
            """INSERT INTO weights_history
               (date_update, weights_json, precision_avant, precision_apres,
                nb_predictions, commentaire, timestamp_update)
               VALUES (?, ?, 0, 0, 0, 'Poids initiaux v1', ?)""",
            (datetime.now().strftime("%Y-%m-%d"),
             json.dumps(Config.DEFAULT_WEIGHTS),
             datetime.now().isoformat()),
        )

    conn.commit()
    conn.close()
    logger.info("Base de données initialisée avec 9 tables")


# ================================================================
# Utilitaires
# ================================================================

def get_current_weights() -> dict:
    """Récupérer les poids les plus récents de l'algorithme."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT weights_json FROM weights_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            return json.loads(row["weights_json"])
        return Config.DEFAULT_WEIGHTS.copy()
    finally:
        conn.close()
