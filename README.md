# Calendrier Tempo EDF (code : TempoForecast)

**Prévision des jours Tempo EDF (Bleu / Blanc / Rouge) jusqu'à 15 jours à l'avance, avec alertes WhatsApp gratuites.**

Site : https://www.calendrier-tempo.fr. Service indépendant, non affilié à EDF.

Mémoire projet détaillée (architecture, règles, pièges) : [`CLAUDE.md`](CLAUDE.md).
Algorithme en détail : [`ALGORITHME_PREDICTION.md`](ALGORITHME_PREDICTION.md).

---

## Fonctionnalités

- **Site public (rendu serveur)** : couleur du jour et de demain, prévisions 15 jours avec probabilités, compteurs de jours restants, calendrier de la saison, blog SEO, pages tarif / API / méthodologie.
- **Alertes WhatsApp** (Meta Cloud API) : alertes rouge et blanc, récapitulatif hebdomadaire, lien de gestion, désinscription. Sans jeton WhatsApp, l'app fonctionne en mode simulation.
- **Auto-amélioration** : évaluation quotidienne des prédictions contre la couleur officielle, recalibrage des poids les 1er et 15 du mois (régression logistique, avec garde-fous et retour arrière automatique).
- **Admin** (`/admin`) : performance par horizon et par version de l'algorithme, abonnés, journaux d'envoi, déclenchement manuel des tâches, état du scheduler.
- **Agents IA** (optionnels, clé Anthropic) : agent SEO (articles de blog, mardi) et agent backlinks (mercredi). Les fichiers qu'ils écrivent sont persistés en base.

## Algorithme (résumé)

1. **Scoring pondéré** sur 7 sous-scores (poids par défaut dans `config.py`, recalibrés automatiquement) :

   | Sous-score | Poids |
   |---|---|
   | Température (moyenne pondérée de 9 villes) | 38 % |
   | Budget de jours restants (pression) | 18 % |
   | Consommation nette estimée (C_nette, RTE) | 18 % |
   | Pression atmosphérique | 10 % |
   | Jour de la semaine | 8 % |
   | Gradient thermique | 6 % |
   | Regroupement (clustering) | 2 % |

2. **Seuils dynamiques** : seuil ROUGE et seuil BLANC fonctions de la température (courbes linéaires par morceaux), abaissés quand la densité de jours restants l'exige.
3. **Modèles ML** : GradientBoosting (33 variables météo, calendrier, RTE décalées) en ensemble avec le scoring, plus un micro-modèle pour la zone ambiguë.
4. **Règles EDF appliquées** : rouge uniquement du 1er novembre au 31 mars, jamais le week-end ni un jour férié ; blanc jamais le dimanche ; 22 rouges et 43 blancs par saison (1er septembre au 31 août) ; 5 rouges consécutifs au maximum.

## Tâches planifiées (heure de Paris)

| Quand | Tâche |
|---|---|
| 6h00 à 11h15, toutes les 15 min | Polling de la couleur officielle, confirmation et évaluation |
| 7h30 | Alertes matinales |
| 11h30 | Vérification quotidienne |
| 18h00 | Prédictions J+1 à J+15 et alertes |
| 23h00 | Validation des corrections d'apprentissage |
| 1er et 15 du mois, 2h00 | Recalcul des poids |
| Dimanche 20h00 | Récapitulatif hebdomadaire |
| Mardi 9h00 | Agent SEO (fréquence saisonnière) |
| Mercredi 10h00 | Agent backlinks |

## Lancer en local

```bash
pip install -r requirements.txt
cp .env.example .env   # compléter les clés utiles
python main.py         # http://localhost:5000
```

Sans `DATABASE_URL`, l'app utilise SQLite (`tempo.db`). Toutes les variables sont documentées dans [`.env.example`](.env.example).

## Hébergement

- **Production actuelle** : Replit (PostgreSQL via `DATABASE_URL`, Secrets Replit).
- **Cible préparée** : Cloudflare (Worker + conteneur + Neon), runbook dans [`cloudflare/README.md`](cloudflare/README.md). Inactive tant que la migration n'est pas décidée.

## Tests

```bash
pytest tests/ -q
```

Tous les tests doivent passer avant chaque push.

## API publique (JSON, sans clé, usage raisonnable)

| Endpoint | Description |
|---|---|
| `GET /api/today` | Couleur du jour |
| `GET /api/tomorrow` | Couleur de demain |
| `GET /api/remaining` | Jours restants par couleur |
| `GET /api/predictions` | Prévisions J+1 à J+15 |
| `GET /api/history?days=30` | Historique des couleurs réelles |
| `GET /api/performance/badge` | Badge de fiabilité |

Documentation complète : page `/api-tempo` du site.

## RGPD et sécurité

- Numéros de téléphone : empreinte SHA-256 (dédoublonnage) + chiffrement Fernet (AES-128) pour l'envoi ; 4 derniers chiffres en clair pour le support.
- Suppression des comptes inactifs depuis plus de 6 mois ; désinscription par message ou par lien de gestion.
- Webhook WhatsApp signé (HMAC-SHA256, `WHATSAPP_APP_SECRET`).
- Admin protégé par mot de passe (comparaison à temps constant, limitation de débit).

## Stack

Python 3.12, FastAPI, Uvicorn, Jinja2, APScheduler, scikit-learn, PostgreSQL (prod) / SQLite (local), HTML/CSS/JS vanilla, Chart.js (admin). APIs : couleurs Tempo (api-couleur-tempo.fr), Météo France (AROME/ARPEGE) avec repli Open-Meteo, RTE, Meta WhatsApp Cloud API, Anthropic.
