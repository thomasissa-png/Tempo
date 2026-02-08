"""Gestion de la base de données SQLite — 6 tables.

Tables :
  - predictions    : prédictions générées par l'algorithme
  - actuals        : couleurs réelles confirmées par EDF
  - performance    : comparaison prédiction vs réalité
  - users          : abonnés aux alertes SMS
  - sms_logs       : historique des SMS envoyés
  - weights_history: versions successives des poids de l'algorithme
"""

import sqlite3
import hashlib
from datetime import datetime
from config import Config
import json
import logging

logger = logging.getLogger(__name__)


def get_db() -> sqlite3.Connection:
    """Obtenir une connexion à la base de données."""
    conn = sqlite3.connect(Config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Créer toutes les tables et index si elles n'existent pas."""
    conn = get_db()
    conn.executescript("""
        -- ============================================================
        -- TABLE 1 : predictions — chaque ligne = une prédiction émise
        -- ============================================================
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

        -- ============================================================
        -- TABLE 2 : actuals — couleur réelle confirmée par EDF
        -- ============================================================
        CREATE TABLE IF NOT EXISTS actuals (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            date                    TEXT    NOT NULL UNIQUE,
            couleur_reelle          TEXT    NOT NULL CHECK(couleur_reelle IN ('BLEU','BLANC','ROUGE')),
            timestamp_confirmation  TEXT    NOT NULL
        );

        -- ============================================================
        -- TABLE 3 : performance — évaluation de chaque prédiction
        -- ============================================================
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
            timestamp_evaluation TEXT    NOT NULL
        );

        -- ============================================================
        -- TABLE 4 : users — abonnés aux alertes SMS (RGPD-friendly)
        -- ============================================================
        CREATE TABLE IF NOT EXISTS users (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_hash          TEXT    NOT NULL UNIQUE,
            phone_last4         TEXT    NOT NULL,
            seuil_alerte_rouge  INTEGER DEFAULT 70,
            delai_alerte        INTEGER DEFAULT 1,
            alerte_blanc        INTEGER DEFAULT 0,
            recap_hebdo         INTEGER DEFAULT 0,
            actif               INTEGER DEFAULT 1,
            created_at          TEXT    NOT NULL,
            updated_at          TEXT    NOT NULL
        );

        -- ============================================================
        -- TABLE 5 : sms_logs — historique complet des envois
        -- ============================================================
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

        -- ============================================================
        -- TABLE 6 : weights_history — versions des poids algorithme
        -- ============================================================
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

        -- ============================================================
        -- INDEX pour les requêtes fréquentes
        -- ============================================================
        CREATE INDEX IF NOT EXISTS idx_predictions_date      ON predictions(date);
        CREATE INDEX IF NOT EXISTS idx_predictions_horizon   ON predictions(horizon);
        CREATE INDEX IF NOT EXISTS idx_actuals_date          ON actuals(date);
        CREATE INDEX IF NOT EXISTS idx_performance_cible     ON performance(date_cible);
        CREATE INDEX IF NOT EXISTS idx_performance_avance    ON performance(jours_avance);
        CREATE INDEX IF NOT EXISTS idx_users_hash            ON users(phone_hash);
        CREATE INDEX IF NOT EXISTS idx_sms_logs_user         ON sms_logs(user_id);
        CREATE INDEX IF NOT EXISTS idx_sms_logs_date         ON sms_logs(date_envoi);
    """)

    # Insérer les poids initiaux s'il n'y en a pas encore
    existing = conn.execute("SELECT COUNT(*) as c FROM weights_history").fetchone()
    if existing["c"] == 0:
        conn.execute(
            """INSERT INTO weights_history
               (date_update, weights_json, precision_avant, precision_apres,
                nb_predictions, commentaire, timestamp_update)
               VALUES (?, ?, 0, 0, 0, 'Poids initiaux v1', ?)""",
            (
                datetime.now().strftime("%Y-%m-%d"),
                json.dumps(Config.DEFAULT_WEIGHTS),
                datetime.now().isoformat(),
            ),
        )

    conn.commit()
    conn.close()
    logger.info("Base de données initialisée avec 6 tables")


# === Utilitaires ===

def hash_phone(phone: str) -> str:
    """Hash SHA-256 du numéro de téléphone (RGPD)."""
    return hashlib.sha256(phone.strip().encode()).hexdigest()


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
