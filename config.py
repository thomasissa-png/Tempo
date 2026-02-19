"""Configuration centralisee de l'application TempoForecast."""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # --- Base de donnees ---
    DATABASE_PATH = os.getenv("DATABASE_PATH", "tempo.db")

    # --- API Tempo officielle ---
    TEMPO_API_BASE = "https://www.api-couleur-tempo.fr/api"

    # --- Meteo France API ---
    # Portail : https://portail-api.meteofrance.fr/
    # Necessite un abonnement (gratuit) aux API AROME, ARPEGE et Vigilance
    #
    # Sur le portail, chaque API necessite sa propre application et sa propre cle.
    # Configurer une cle par modele :
    #   - METEOFRANCE_AROME_KEY    : cle pour l'API AROME (haute resolution, J+0 a J+2)
    #   - METEOFRANCE_ARPEGE_KEY   : cle pour l'API ARPEGE (global, J+2 a J+5)
    #   - METEOFRANCE_VIGILANCE_KEY: cle pour l'API Vigilance (alertes meteo)
    #
    # Fallback : METEOFRANCE_API_KEY est utilise si une cle specifique est absente.
    # Modes d'auth : cle permanente (duree=0) recommandee, ou application_id OAuth2.
    METEOFRANCE_API_KEY = os.getenv("METEOFRANCE_API_KEY", "")
    METEOFRANCE_APPLICATION_ID = os.getenv("METEOFRANCE_APPLICATION_ID", "")
    METEOFRANCE_AROME_KEY = os.getenv("METEOFRANCE_AROME_KEY", "")
    METEOFRANCE_ARPEGE_KEY = os.getenv("METEOFRANCE_ARPEGE_KEY", "")
    METEOFRANCE_VIGILANCE_KEY = os.getenv("METEOFRANCE_VIGILANCE_KEY", "")

    # --- Villes meteo ponderees par population / parc chauffage electrique ---
    # API : Meteo France (AROME haute resolution + ARPEGE global)
    # (lat, lon, poids) — poids normalises a 1.0
    # Fix meteo #2 : reequilibrage des poids
    #   - Marseille reduit (climat mediterraneen doux, biaise la moyenne vers le haut)
    #   - Lille et Strasbourg augmentes (climat froid + fort parc chauffage electrique)
    #   - Clermont-Ferrand ajoute (representatif du Massif Central, climat continental)
    #   - Bordeaux reduit (climat oceanique doux)
    WEATHER_CITIES = [
        {"name": "Paris",           "lat": 48.8566, "lon": 2.3522,  "weight": 0.20},
        {"name": "Lyon",            "lat": 45.7640, "lon": 4.8357,  "weight": 0.13},
        {"name": "Lille",           "lat": 50.6292, "lon": 3.0573,  "weight": 0.14},
        {"name": "Strasbourg",      "lat": 48.5734, "lon": 7.7521,  "weight": 0.12},
        {"name": "Nantes",          "lat": 47.2184, "lon": -1.5536, "weight": 0.10},
        {"name": "Toulouse",        "lat": 43.6047, "lon": 1.4442,  "weight": 0.09},
        {"name": "Bordeaux",        "lat": 44.8378, "lon": -0.5792, "weight": 0.08},
        {"name": "Marseille",       "lat": 43.2965, "lon": 5.3698,  "weight": 0.07},
        {"name": "Clermont-Ferrand","lat": 45.7772, "lon": 3.0870,  "weight": 0.07},
    ]

    # --- RTE eco2mix API ---
    # Sur le portail RTE, chaque API necessite sa propre application :
    #   - RTE_CONSO_CLIENT_ID / _SECRET : API Consumption (prevision conso J+1)
    #   - RTE_GENERATION_CLIENT_ID / _SECRET : API Generation Forecast (nucleaire)
    # Fallback : RTE_CLIENT_ID / _SECRET utilise si cle specifique absente.
    RTE_CLIENT_ID = os.getenv("RTE_CLIENT_ID", "")
    RTE_CLIENT_SECRET = os.getenv("RTE_CLIENT_SECRET", "")
    RTE_CONSO_CLIENT_ID = os.getenv("RTE_CONSO_CLIENT_ID", "")
    RTE_CONSO_CLIENT_SECRET = os.getenv("RTE_CONSO_CLIENT_SECRET", "")
    RTE_GENERATION_CLIENT_ID = os.getenv("RTE_GENERATION_CLIENT_ID", "")
    RTE_GENERATION_CLIENT_SECRET = os.getenv("RTE_GENERATION_CLIENT_SECRET", "")
    RTE_API_BASE = "https://digital.iservices.rte-france.com"
    # Seuils de consommation nationale (MW) pour scoring
    RTE_CONSO_SEUIL_CRITIQUE = 80000   # > 80 GW = risque rouge tres eleve
    RTE_CONSO_SEUIL_HAUT = 70000       # > 70 GW = risque rouge
    RTE_CONSO_SEUIL_MOYEN = 60000      # > 60 GW = risque blanc
    # Fix #12 : capacite nucleaire configurable (evolue avec fermetures/mises en service)
    RTE_NUCLEAR_CAPACITY_MW = int(os.getenv("RTE_NUCLEAR_CAPACITY_MW", "61370"))

    # --- Twilio (WhatsApp) ---
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

    # --- URL de base du site (pour les liens dans les messages WhatsApp) ---
    BASE_URL = os.getenv("BASE_URL", "https://www.calendrier-tempo.fr")

    # --- Admin ---
    # Fix audit v6 : ne plus utiliser de mot de passe par défaut en dur
    # Fallback sur SESSION_SECRET (fourni par Replit) pour avoir un password stable
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "") or os.getenv("SESSION_SECRET", "")

    # --- Chiffrement telephone (RGPD) ---
    PHONE_ENCRYPTION_KEY = os.getenv("PHONE_ENCRYPTION_KEY", "")

    # --- Regles Tempo saison ---
    JOURS_ROUGES_TOTAL = 22
    JOURS_BLANCS_TOTAL = 43
    # Les jours bleus = total saison - rouges - blancs (varie si annee bissextile)
    # Calculé dynamiquement dans tempo_client.get_blue_days_total()

    # --- Poids initiaux algorithme v3.0 (recalibres audit ML fev 2026) ---
    # Audit : temperature etait le SEUL signal discriminant (ecart 53pts BLEU→ROUGE)
    # mais ne pesait que 27%. Budget (20%) sur-predisait massivement BLANC
    # (210 faux BLANC = 30% des evaluations). Gradient quasi inutile (3pts d'ecart).
    # RTE inactif (toujours 50) tant que credentials non configurees.
    DEFAULT_WEIGHTS = {
        "temperature": 0.40,        # +13 : signal dominant (seul ecart > 50pts)
        "jours_restants": 0.12,     # -8  : reduire sur-prediction BLANC
        "jour_semaine": 0.10,       # =   : discrimine weekends/feries
        "gradient_thermique": 0.08, # -5  : ecart 3pts entre couleurs = bruit
        "clustering": 0.12,         # +2  : signal modere (40.6 vs 20.5)
        "consommation_rte": 0.10,   # -3  : reactiver quand RTE configure
        "pression": 0.08,           # +1  : signal conditionnel (anticyclone+froid)
    }

    # --- Profil mensuel de distribution des jours rouges ---
    # Base sur 20 saisons d'historique (% des 22 jours rouges par mois)
    # Fix audit ML #39 : avril et mai mis a 0% car la regle EDF R1 interdit
    # les jours rouges hors novembre-mars. Les 5% d'avril/mai sont redistribues
    # proportionnellement sur les mois eligibles.
    MONTHLY_RED_PROFILE = {
        9: 0.00,   # Septembre : 0%
        10: 0.00,  # Octobre : 0%
        11: 0.07,  # Novembre : ~7% (1-2 jours)
        12: 0.19,  # Decembre : ~19% (4-5 jours)
        1: 0.37,   # Janvier : ~37% (7-9 jours)
        2: 0.24,   # Fevrier : ~24% (4-6 jours)
        3: 0.13,   # Mars : ~13% (2-3 jours)
        4: 0.00,   # Avril : INTERDIT (regle R1)
        5: 0.00,   # Mai : INTERDIT (regle R1)
    }

    # Fix ML-20 : validation somme du profil mensuel
    assert abs(sum(MONTHLY_RED_PROFILE.values()) - 1.0) < 0.02, \
        f"MONTHLY_RED_PROFILE doit sommer a ~1.0, got {sum(MONTHLY_RED_PROFILE.values())}"

    # --- Profil mensuel de distribution des jours BLANCS (ML-1) ---
    # Base sur 20 saisons d'historique (% des 43 jours blancs par mois)
    MONTHLY_WHITE_PROFILE = {
        9: 0.00,   # Septembre : 0%
        10: 0.05,  # Octobre : ~5% (2 jours)
        11: 0.09,  # Novembre : ~9% (4 jours)
        12: 0.15,  # Decembre : ~15% (6-7 jours)
        1: 0.21,   # Janvier : ~21% (9 jours)
        2: 0.18,   # Fevrier : ~18% (8 jours)
        3: 0.15,   # Mars : ~15% (6-7 jours)
        4: 0.10,   # Avril : ~10% (4-5 jours)
        5: 0.07,   # Mai : ~7% (3 jours)
    }

    assert abs(sum(MONTHLY_WHITE_PROFILE.values()) - 1.0) < 0.02, \
        f"MONTHLY_WHITE_PROFILE doit sommer a ~1.0, got {sum(MONTHLY_WHITE_PROFILE.values())}"

    # --- Seuils de scoring ---
    SEUIL_ROUGE = 65   # seuil standard (abaisse dynamiquement si froid — voir predictor)
    SEUIL_BLANC = 35   # abaisse de 40 a 35

    # --- Seuil ROUGE dynamique (audit ML fev 2026) ---
    # 22 rouges reels avaient un score entre 45-65 (sous SEUIL_ROUGE).
    # Le seuil est abaisse conditionnellement quand la temperature est basse
    # ET que le budget montre une pression. Cela ameliore le recall ROUGE
    # sans augmenter les faux positifs en periodes douces.
    SEUIL_ROUGE_FROID = 55       # seuil quand temp < SEUIL_ROUGE_TEMP_TRIGGER
    SEUIL_ROUGE_TRES_FROID = 50  # seuil quand temp < SEUIL_ROUGE_TEMP_TRES_FROID
    SEUIL_ROUGE_TEMP_TRIGGER = 7     # degres C : active le seuil abaisse
    SEUIL_ROUGE_TEMP_TRES_FROID = 4  # degres C : active le seuil tres bas
    SEUIL_ROUGE_BUDGET_MIN = 50      # budget_score minimum pour activer le seuil abaisse

    # --- Probabilites softmax (centres et steepness) ---
    # Fix audit ML #4 : parametres extraits pour calibration future
    # Centres des distributions : BLEU bas, BLANC milieu, ROUGE haut
    # A calibrer sur les distributions reelles apres backtest
    PROBA_CENTER_BLEU = 15    # zone 0-35
    PROBA_CENTER_BLANC = 50   # zone 35-65
    PROBA_CENTER_ROUGE = 85   # zone 65-100
    PROBA_STEEPNESS = 0.08    # pente des transitions (plus petit = plus progressif)

    # --- Cache ---
    PREDICTIONS_CACHE_TTL = 900  # 15 minutes

    # --- Rate limiting ---
    SUBSCRIBE_RATE_LIMIT = 5
    SUBSCRIBE_RATE_WINDOW = 3600

    # --- Agent SEO autonome (publication blog saisonnière) ---
    # Clé API Anthropic pour l'agent Claude qui rédige les articles.
    # À configurer dans Replit Secrets (une seule fois).
    # Si vide, la tâche est silencieusement ignorée.
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    # Modèle Claude à utiliser (Sonnet = bon rapport qualité/coût)
    SEO_AGENT_MODEL = os.getenv("SEO_AGENT_MODEL", "claude-sonnet-4-5-20250929")
    # Nombre max de tours d'interaction agent (sécurité anti-boucle infinie)
    SEO_AGENT_MAX_TURNS = int(os.getenv("SEO_AGENT_MAX_TURNS", "40"))

    # Calendrier de publication saisonnier :
    #   Nov-Mar (saison active)  → chaque mardi (hebdo)
    #   Sep-Oct (pré-saison)     → 1er et 3e mardi du mois (bimensuel)
    #   Avr-Mai (post-saison)    → 1er mardi du mois uniquement
    #   Juin-Août (morte-saison) → pause complète
    # Valeur = semaines du mois où publier (1=1ère semaine, 2=2ème, etc.)
    SEO_SEASON_SCHEDULE = {
        1: "weekly",    # Janvier — saison active
        2: "weekly",    # Février
        3: "weekly",    # Mars
        4: "monthly",   # Avril — post-saison
        5: "monthly",   # Mai
        6: "off",       # Juin — morte-saison
        7: "off",       # Juillet
        8: "off",       # Août
        9: "bimonthly", # Septembre — pré-saison
        10: "bimonthly",# Octobre
        11: "weekly",   # Novembre — saison active
        12: "weekly",   # Décembre
    }

    # --- Logging ---
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = "logs/app.log"
    LOG_MAX_BYTES = 5 * 1024 * 1024
    LOG_BACKUP_COUNT = 3
