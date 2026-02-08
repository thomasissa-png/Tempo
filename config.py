"""Configuration centralisée de l'application TempoForecast."""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # --- Base de données ---
    DATABASE_PATH = os.getenv("DATABASE_PATH", "tempo.db")

    # --- API Tempo officielle ---
    TEMPO_API_BASE = "https://www.api-couleur-tempo.fr/api"

    # --- OpenWeatherMap ---
    OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
    # Paris comme référence nationale pour la consommation électrique
    WEATHER_LAT = 48.8566
    WEATHER_LON = 2.3522

    # --- Twilio ---
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

    # --- Admin ---
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin_tempo_2024")

    # --- Règles Tempo saison ---
    JOURS_ROUGES_TOTAL = 22
    JOURS_BLANCS_TOTAL = 43
    JOURS_BLEUS_TOTAL = 300
    # Saison : 1er septembre → 31 mai (273 jours)

    # --- Poids initiaux algorithme v1 ---
    DEFAULT_WEIGHTS = {
        "temperature": 0.40,
        "jours_restants": 0.25,
        "jour_semaine": 0.15,
        "pression_meteo": 0.20,
    }

    # --- Seuils de scoring ---
    SEUIL_ROUGE = 70
    SEUIL_BLANC = 40

    # --- Logging ---
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = "logs/app.log"
