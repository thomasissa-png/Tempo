"""Gestion de la base de données SQLite — 7 tables.

Tables :
  - predictions    : prédictions générées par l'algorithme
  - actuals        : couleurs réelles confirmées par EDF
  - performance    : comparaison prédiction vs réalité (UNIQUE dedup Fix #7)
  - users          : abonnés aux alertes SMS (phone_encrypted Fix #1)
  - sms_logs       : historique des SMS envoyés
  - weights_history: versions successives des poids de l'algorithme
  - weather_cache  : cache des prévisions météo (Fix #17)
"""

import sqlite3
import hashlib
import base64
import logging
from datetime import datetime
from config import Config
import json

logger = logging.getLogger(__name__)

# ================================================================
# Chiffrement téléphone (Fix #1 — Fernet réversible pour SMS)
# ================================================================

_fernet_instance = None


def _get_fernet():
    """Singleton Fernet. Clé depuis PHONE_ENCRYPTION_KEY ou dérivée d'ADMIN_PASSWORD."""
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    from cryptography.fernet import Fernet

    key_source = Config.PHONE_ENCRYPTION_KEY
    if key_source:
        _fernet_instance = Fernet(key_source.encode() if isinstance(key_source, str) else key_source)
    else:
        # Fix #9 audit v4 : PBKDF2 (100k iterations) au lieu de SHA-256 direct.
        # Le sel est fixe mais protege contre les rainbow tables.
        # En production, definir PHONE_ENCRYPTION_KEY explicitement.
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


def hash_phone(phone: str) -> str:
    """Hash SHA-256 du numéro (lookup / déduplication uniquement)."""
    return hashlib.sha256(phone.strip().encode()).hexdigest()


# ================================================================
# Connexion DB
# ================================================================

def get_db() -> sqlite3.Connection:
    """Obtenir une connexion à la base de données."""
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
            timestamp_confirmation  TEXT    NOT NULL
        );

        -- Fix #7 : contrainte UNIQUE pour éviter les doublons
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
            UNIQUE(date_prediction, date_cible, couleur_predite)
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
    logger.info("Base de données initialisée avec 7 tables")


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
