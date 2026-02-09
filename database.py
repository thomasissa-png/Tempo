"""Gestion de la base de données SQLite — 8 tables.

Tables :
  - predictions         : prédictions générées par l'algorithme
  - prediction_changes  : historique des changements de couleur entre cycles
  - actuals             : couleurs réelles confirmées par EDF
  - performance         : comparaison prédiction vs réalité (UNIQUE dedup Fix #7)
  - users               : abonnés aux alertes SMS (phone_encrypted Fix #1)
  - sms_logs            : historique des SMS envoyés
  - weights_history     : versions successives des poids de l'algorithme
  - weather_cache       : cache des prévisions météo (Fix #17)
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

        -- Fix #7 + ML-3 : UNIQUE(date_prediction, date_cible) evite doublons
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
            UNIQUE(date_prediction, date_cible)
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
    """)

    # === Fix #14 audit v4 : migrations conditionnelles via PRAGMA user_version ===
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    if version < 3:
        # Migrations v3 — Système d'apprentissage
        for col in ["score_temperature", "score_budget", "score_weekday",
                    "score_gradient", "score_clustering", "score_rte"]:
            try:
                conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} REAL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # Colonne existe déjà

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
                pass  # Colonne existe déjà

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
            pass  # Colonne existe déjà

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
            pass  # Colonne existe déjà

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
                except Exception:
                    pass  # Skip les users dont le chiffrement a changé
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
            pass  # Colonne existe déjà

        conn.execute("PRAGMA user_version = 8")
        conn.commit()
        logger.info("Migration v8 appliquee (HMAC phone hash, couleur_originale)")

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
    logger.info("Base de données initialisée avec 8 tables")


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
