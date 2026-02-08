"""Configuration centralisee de l'application TempoForecast."""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # --- Base de donnees ---
    DATABASE_PATH = os.getenv("DATABASE_PATH", "tempo.db")

    # --- API Tempo officielle ---
    TEMPO_API_BASE = "https://www.api-couleur-tempo.fr/api"

    # --- OpenWeatherMap ---
    OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")

    # --- Villes meteo ponderees par population / parc chauffage electrique ---
    # (lat, lon, poids) — poids normalises a 1.0
    # Le sud-est pese plus car fort parc de chauffage electrique
    WEATHER_CITIES = [
        {"name": "Paris",      "lat": 48.8566, "lon": 2.3522,  "weight": 0.22},
        {"name": "Lyon",       "lat": 45.7640, "lon": 4.8357,  "weight": 0.14},
        {"name": "Marseille",  "lat": 43.2965, "lon": 5.3698,  "weight": 0.12},
        {"name": "Toulouse",   "lat": 43.6047, "lon": 1.4442,  "weight": 0.10},
        {"name": "Lille",      "lat": 50.6292, "lon": 3.0573,  "weight": 0.10},
        {"name": "Strasbourg", "lat": 48.5734, "lon": 7.7521,  "weight": 0.10},
        {"name": "Nantes",     "lat": 47.2184, "lon": -1.5536, "weight": 0.10},
        {"name": "Bordeaux",   "lat": 44.8378, "lon": -0.5792, "weight": 0.12},
    ]

    # Fallback Paris seul (compatibilite)
    WEATHER_LAT = 48.8566
    WEATHER_LON = 2.3522

    # --- RTE eco2mix API ---
    RTE_CLIENT_ID = os.getenv("RTE_CLIENT_ID", "")
    RTE_CLIENT_SECRET = os.getenv("RTE_CLIENT_SECRET", "")
    RTE_API_BASE = "https://digital.iservices.rte-france.com"
    # Seuils de consommation nationale (MW) pour scoring
    RTE_CONSO_SEUIL_CRITIQUE = 80000   # > 80 GW = risque rouge tres eleve
    RTE_CONSO_SEUIL_HAUT = 70000       # > 70 GW = risque rouge
    RTE_CONSO_SEUIL_MOYEN = 60000      # > 60 GW = risque blanc

    # --- Twilio ---
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

    # --- Admin ---
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin_tempo_2024")

    # --- Chiffrement telephone (RGPD) ---
    PHONE_ENCRYPTION_KEY = os.getenv("PHONE_ENCRYPTION_KEY", "")

    # --- Regles Tempo saison ---
    JOURS_ROUGES_TOTAL = 22
    JOURS_BLANCS_TOTAL = 43
    JOURS_BLEUS_TOTAL = 208

    # --- Poids initiaux algorithme v2 ---
    # temperature (nationale ponderee) + budget + jour semaine/feries
    # + gradient thermique + clustering + consommation RTE
    DEFAULT_WEIGHTS = {
        "temperature": 0.30,
        "jours_restants": 0.20,
        "jour_semaine": 0.10,
        "gradient_thermique": 0.15,
        "clustering": 0.10,
        "consommation_rte": 0.15,
    }

    # --- Profil mensuel de distribution des jours rouges ---
    # Base sur 20 saisons d'historique (% des 22 jours rouges par mois)
    MONTHLY_RED_PROFILE = {
        9: 0.00,   # Septembre : 0%
        10: 0.00,  # Octobre : 0%
        11: 0.07,  # Novembre : ~7% (1-2 jours)
        12: 0.18,  # Decembre : ~18% (3-5 jours)
        1: 0.35,   # Janvier : ~35% (6-9 jours)
        2: 0.23,   # Fevrier : ~23% (4-6 jours)
        3: 0.12,   # Mars : ~12% (1-3 jours)
        4: 0.04,   # Avril : ~4% (0-1 jour)
        5: 0.01,   # Mai : ~1% (0-1 jour)
    }

    # --- Seuils de scoring ---
    SEUIL_ROUGE = 65   # abaisse de 70 a 65 pour meilleur recall
    SEUIL_BLANC = 35   # abaisse de 40 a 35

    # --- Cache ---
    PREDICTIONS_CACHE_TTL = 900  # 15 minutes

    # --- Rate limiting ---
    SUBSCRIBE_RATE_LIMIT = 5
    SUBSCRIBE_RATE_WINDOW = 3600

    # --- Logging ---
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = "logs/app.log"
    LOG_MAX_BYTES = 5 * 1024 * 1024
    LOG_BACKUP_COUNT = 3
