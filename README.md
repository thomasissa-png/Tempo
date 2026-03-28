# TempoForecast

**Prévision intelligente des jours Tempo EDF (Bleu / Blanc / Rouge) avec alertes SMS et auto-amélioration.**

Application web ciblant les 900 000 clients de l'offre Tempo EDF. Anticipe les jours rouges (tarif x5) jusqu'à 15 jours à l'avance grâce à l'analyse météo et à un algorithme qui s'améliore continuellement.

---

## Fonctionnalités

### Dashboard public
- Couleur Tempo du jour et de demain (API officielle EDF)
- Prévisions 15 jours avec code couleur et niveau de confiance
- Compteurs de jours restants par couleur dans la saison
- Alerte visuelle si jour rouge prévu dans les 3-5 jours
- Badge de fiabilité (précision calculée sur 30 jours)
- Inscription aux alertes SMS

### Alertes SMS (Twilio)
- Alerte jour rouge avec probabilité et température
- Alerte jour blanc (optionnel)
- Récapitulatif hebdomadaire (dimanche soir)
- Seuil configurable (70%, 80%, 90%)
- Délai configurable (J-1, J-2, J-3)
- Limite 1 SMS/jour/utilisateur
- Opt-out par SMS (STOP) ou via le site

### Système d'auto-amélioration
- Vérification quotidienne à 11h30 : compare prédictions vs couleur réelle EDF
- Calcul de précision globale, par horizon (J-1, J-2...), matrice de confusion
- Recalcul mensuel des poids via régression logistique (scikit-learn)
- Historique complet des versions de l'algorithme
- Export CSV des performances mensuelles

### Dashboard admin
- Métriques de performance en temps réel
- Graphiques Chart.js (précision par horizon, poids de l'algorithme)
- Top 10 erreurs récentes avec contexte météo
- Exécution manuelle des tâches du scheduler
- Stats utilisateurs SMS

---

## Architecture

```
TempoForecast/
├── app.py                  # FastAPI — routes, API endpoints, pages HTML
├── main.py                 # Point d'entrée (uvicorn)
├── config.py               # Configuration centralisée (.env)
├── database.py             # SQLite — 6 tables, init, utilitaires
├── tempo_client.py         # Client API Tempo officielle
├── weather_client.py       # Client Meteo France (AROME + ARPEGE + Vigilance)
├── predictor.py            # Algorithme de prédiction v1 (scoring par points)
├── performance_tracker.py  # Auto-amélioration, métriques, recalcul poids
├── alerts.py               # Alertes SMS Twilio, gestion users
├── scheduler.py            # APScheduler — 4 tâches automatisées
├── templates/
│   ├── dashboard.html      # Page publique
│   ├── admin.html          # Dashboard admin (Chart.js)
│   └── legal.html          # Mentions légales / RGPD
├── static/
│   ├── css/style.css       # Design system complet
│   └── js/app.js           # JavaScript dashboard
├── logs/                   # Fichiers de log (rotation)
├── requirements.txt        # Dépendances Python
├── .env.example            # Variables d'environnement template
└── README.md               # Ce fichier
```

---

## Base de données — 6 tables

| Table | Rôle |
|---|---|
| `predictions` | Chaque prédiction émise (date, couleur, probabilités, score, horizon) |
| `actuals` | Couleurs réelles confirmées par EDF |
| `performance` | Évaluation de chaque prédiction vs réalité |
| `users` | Abonnés SMS (numéro hashé, préférences) |
| `sms_logs` | Historique complet des SMS envoyés |
| `weights_history` | Versions successives des poids de l'algorithme |

---

## Algorithme de prédiction v1

**Score de risque (0-100)** basé sur 4 facteurs pondérés :

| Facteur | Poids initial | Description |
|---|---|---|
| Température | 40% | < 0°C = +60pts, < -5°C = +85pts, < -10°C = +100pts |
| Jours restants | 25% | Pression budgétaire si beaucoup de rouges à écouler |
| Jour semaine | 15% | Lun-ven favorisés (EDF évite les rouges le week-end) |
| Pression atmo | 20% | Anticyclone hivernal (> 1025 hPa) = risque accru |

**Bonus vague de froid** : +15 à +25 pts si 3-5 jours consécutifs < 0°C

**Seuils** : score ≥ 70 → ROUGE, 40-70 → BLANC, < 40 → BLEU

Les poids sont recalculés automatiquement chaque mois via régression logistique multinomiale sur l'historique des prédictions évaluées (scikit-learn).

---

## Tâches automatisées (APScheduler)

| Heure | Tâche |
|---|---|
| 11h30 quotidien | Récupère couleur EDF, évalue les prédictions, envoie alertes officielles |
| 18h00 quotidien | Génère prédictions J+1→J+15, envoie alertes SMS |
| 1er du mois 2h00 | Recalcul poids algorithme + nettoyage RGPD |
| Dimanche 20h00 | Récapitulatif hebdomadaire SMS |

---

## Déploiement sur Replit

### 1. Créer le projet
- Importer ce repo dans Replit
- Language : Python

### 2. Variables d'environnement
Dans l'onglet "Secrets" de Replit, ajouter :

```
METEOFRANCE_API_KEY=votre_cle_api_meteofrance
TWILIO_ACCOUNT_SID=votre_sid_twilio
TWILIO_AUTH_TOKEN=votre_token_twilio
TWILIO_PHONE_NUMBER=+33xxxxxxxxx
ADMIN_PASSWORD=votre_mot_de_passe_admin
```

### 3. Obtenir les clés API

**Météo France** (météo — AROME/ARPEGE/Vigilance) :
1. Créer un compte sur [portail-api.meteofrance.fr](https://portail-api.meteofrance.fr/)
2. S'abonner aux API : AROME, ARPEGE, Vigilance (gratuit)
3. Générer un token via "Mes APIs" → "Générer token"

**Twilio** (SMS) :
1. Créer un compte sur [twilio.com](https://www.twilio.com/)
2. Récupérer Account SID et Auth Token dans la console
3. Acheter un numéro français (+33) pour l'envoi

### 4. Lancer
```bash
pip install -r requirements.txt
python main.py
```

L'app sera accessible sur le port 5000 (configurable via `PORT`). Le scheduler démarre automatiquement.

---

## API Endpoints

### Données publiques
| Méthode | Endpoint | Description |
|---|---|---|
| GET | `/api/today` | Couleur Tempo du jour |
| GET | `/api/tomorrow` | Couleur de demain |
| GET | `/api/remaining` | Jours restants par couleur |
| GET | `/api/predictions` | Prévisions J+1 → J+15 |
| GET | `/api/history?days=30` | Historique des couleurs réelles |
| GET | `/api/performance/badge` | Badge de fiabilité |

### Alertes SMS
| Méthode | Endpoint | Description |
|---|---|---|
| POST | `/api/subscribe` | Inscription (phone, seuil_rouge, delai...) |
| POST | `/api/unsubscribe` | Désinscription (phone) |

### Admin (mot de passe requis)
| Méthode | Endpoint | Description |
|---|---|---|
| GET | `/api/performance` | Métriques complètes |
| GET | `/api/performance/csv` | Export CSV mensuel |
| POST | `/admin/run-task` | Exécuter une tâche manuellement |
| GET | `/admin/weights-history` | Historique des poids |
| GET | `/admin/sms-logs` | Derniers SMS envoyés |
| GET | `/api/users/stats` | Stats utilisateurs |

---

## RGPD et sécurité

- Numéros de téléphone hashés en SHA-256 en base
- Seuls les 4 derniers chiffres stockés en clair (support)
- Suppression automatique des comptes inactifs > 6 mois
- Opt-out facile : STOP par SMS ou formulaire web
- Aucune revente de données
- Mentions légales accessibles depuis chaque page

---

## Stack technique

- **Backend** : Python 3.11+, FastAPI, Uvicorn
- **Base de données** : SQLite (WAL mode)
- **ML** : scikit-learn (régression logistique multinomiale)
- **SMS** : Twilio
- **Scheduler** : APScheduler (AsyncIO)
- **Frontend** : HTML5, CSS3, JavaScript vanilla, Chart.js
- **APIs externes** : API Tempo officielle, Météo France (AROME/ARPEGE/Vigilance), RTE eco2mix
