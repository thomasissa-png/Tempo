# QA de bout en bout, environnement proche de la production (2026-09-29)

Périmètre : tous les changements depuis `e8d8b76` (19 commits du jour). Objectif : « tout marche bien ».
Aucune donnée inventée : météo, couleurs EDF, prévisions et poids viennent de `db_dump.json` (dump prod).
Seules des fixtures de test (numéros fictifs réservés aux tests, 4 abonnés, 4 sms_logs) ont été ajoutées.

Marquage : `[LIVE]` = exécuté sur l'image Docker réelle + PostgreSQL 16 avec sortie observée ;
`[STATIQUE]` = lecture de code, grep ou test unitaire.

## 1. Environnement

| Élément | Valeur |
|---|---|
| Base | `postgres:16-alpine` (conteneur), réseau Docker dédié |
| Application | image construite depuis le `Dockerfile` du dépôt (`python main.py`, proxy ASGI) |
| Variables | `DATABASE_URL`, `ADMIN_PASSWORD`, `PHONE_ENCRYPTION_KEY` ; pas de `WHATSAPP_TOKEN` (simulation), pas de clé Météo France, RTE ni Anthropic |
| Réseau sortant | bloqué (TLS du proxy refusé) : reproduit une panne totale des API externes |

## 2. Matrice des vérifications

| # | Vérification | Résultat |
|---|---|---|
| M1 | Base v23 créée avec le code de `e8d8b76` (worktree), `db_sync import` (8 301 lignes) + fixtures | OK [LIVE] |
| M2 | Démarrage de l'image actuelle sur la base v23 : migrations v24 puis v25 appliquées, `schema_version = 25` | OK [LIVE] |
| M3 | Comptes de lignes avant/après migration : 13 tables identiques (actuals 2365, predictions 842, performance 730, weather_cache 2380, rte_daily 1959, weights_history 27, users 4, sms_logs 4...), + `agent_files` et `rte_forecast_log` vides | OK [LIVE] |
| M4 | Redémarrage sur base v25 : aucune migration rejouée ; `init_db` idempotent (SQLite et PG) | OK [LIVE] |
| M5 | Restauration des fichiers agents : table vide sans effet ; conteneur neuf (disque éphémère) : article agent restauré et servi en 200 + présent au sitemap ; conflit (humain + agent) : version du dépôt conservée ; chemin `../main.py` ignoré | OK [LIVE] |
| D1 | Démarrage à froid : `/health` 200 en 0,6 s ; `/` = dashboard rendu (aucune balise Jinja) ; autres pages 503 + `Retry-After: 5` ; `/api/*` 503 JSON ; CSS/JS préchargés en 200 | OK [LIVE] |
| D2 | Logs de démarrage : aucune traceback ; source du mot de passe admin loggée (après correctif B7) | OK [LIVE] |
| P1 | Crawl complet (sitemap 126 URL + liens internes + assets) : 151 URL, toutes en 200, un seul H1 par page, aucun `{{`, `undefined`, `None`, `[object Object]` | OK [LIVE] |
| P2 | Tirets cadratins visibles (texte, entités, JS dynamique) | KO puis corrigé (B6) [LIVE] |
| P3 | `/historique-previsions`, `/historique-previsions/2025-2026`, CSV (BOM UTF-8, `;`, `n/a` documenté) | OK [LIVE] |
| P4 | `/calendrier`, 84 pages mois, 8 pages saison, redirections `?month=&year=` et `/calendrier/` en 301 | OK [LIVE] |
| P5 | 404 : page inconnue, mois/saison hors bornes, article inexistant, `/api/inexistant` (JSON) | OK sauf année 0000 (B5, corrigé) [LIVE] |
| P6 | `/robots.txt`, `/llms.txt`, `/llms-full.txt`, `/feed.xml`, `/sitemap.xml`, `/manifest.json`, favicon | OK [LIVE] |
| P7 | API publiques `/api/today`, `/tomorrow`, `/remaining`, `/predictions`, `/performance/badge`, `/history`, `/calendrier-data` : 200 ou 422 propre, jamais 500 | OK [LIVE] |
| P8 | Exécution du JavaScript de 17 pages (jsdom) : aucune erreur JS, aucun texte `undefined/NaN` | OK [LIVE jsdom] |
| A1 | `/admin` : connexion, 5 onglets, saisons 2025-2026 et 2026-2027, tous les appels de `admin.html` en 200, aucune erreur JS | OK [LIVE jsdom] |
| A2 | `/admin/scheduler-status` (avec `forecast_logs`), `sms-logs`, `subscribers`, `db-diagnostic`, `weights-history`, `agent-reports`, `validate-articles` (22/22 valides), `/api/performance/csv` | OK [LIVE] |
| A3 | Auth admin : sans jeton ou jeton faux = 403 | OK [LIVE] |
| A4 | `/admin/run-task` en HTTP : `evaluate_missed`, `analyze`, `db_export`, `verification` | OK [LIVE] |
| A5 | `/api/performance` sans saison ou saison mal formée | KO puis corrigé (B4) [LIVE] |
| A6 | `/admin/whatsapp-diagnostic` : `recent_errors` toujours vide | KO puis corrigé (B1) [LIVE] |
| S1 | `/api/subscribe` : honeypot 400, délai < 3 s 400, numéro invalide 400, XSS et 10 000 caractères 400, origine étrangère 403, numéro FR/DE valide 200, bornes `seuil/delai/heure` normalisées, limite 5/h puis 429 | OK [LIVE] |
| S2 | Modal d'inscription (jsdom) : ouverture, horodatage, validation du numéro, POST 200, événements Umami | OK [LIVE jsdom] |
| S3 | Aperçu WhatsApp du modal : jours rouges affichés en octobre (règle R1) | KO puis corrigé (B6) [LIVE jsdom] |
| S4 | Page et API de gestion (`/manage/{token}`, GET/POST préférences, désinscription par jeton, jeton invalide 404) | OK [LIVE] |
| S5 | « Déjà inscrit ? » `/api/resend-manage-link` | KO (500 pour tous) puis corrigé (B1) [LIVE] |
| S6 | `/api/unsubscribe` avec numéro saisi avec points ou tirets | KO puis corrigé (B3) [STATIQUE + test] |
| S7 | Webhook WhatsApp : STOP désactive ; statut `failed` 131026 marque le log et désactive | KO (échec silencieux) puis corrigé (B2) [LIVE] |
| C1 | Cycle métier rejoué au 2026-02-15 18 h (horloge figée, météo réelle de `weather_cache`) : `predict_range` 16 jours, J0/J+1 confirmés, calibration active | OK [LIVE] |
| C2 | Jumeau RTE saison 2025-2026 : actif (normalisation sur 2024-2025), filet/veto appliqués J+2..J+5 | OK [LIVE] |
| C3 | Jumeau RTE saison 2026-2027 sur ce dump : inactif, `WARNING [RTE twin] 188 jours de météo ... (< 330)` | OK (comportement prévu), voir V3 [LIVE] |
| C4 | `_refresh_predictions` : stockage 16 prédictions, 16 lignes `weather_forecast_log`, alertes soir en mode simulation | OK [LIVE] |
| C5 | Alertes : profils 70/80 alertés à p = 0,55, 90 non ; tous à p = 0,92 ; BLANC selon `alerte_blanc` ; J+4 hors délai ignoré ; rejeu = aucun doublon ; statut `simulated` | OK [LIVE] |
| C6 | EDF publie le 17/02 (ROUGE réel) : `store_actual` + `evaluate_predictions_for_date` + `confirm_prediction` (5 horizons) ; alertes officielles aux 5 abonnés actifs ; lignes `performance` J-1..J-5 ; `get_daily_recap` (4 horizons justes consécutifs) ; `prediction_history` | OK [LIVE] |
| C7 | `purge_old_data` : 792 prédictions réelles avant, 792 après | OK [LIVE] |
| C8 | Gel des poids : tâche automatique = no-op, `weights_history` 27 puis 27 | OK [LIVE] |
| C9 | Tâches du scheduler avec réseau en échec (polling EDF, 11 h 30, 7 h 30, 18 h, récap, validation, poids, agents SEO/Backlinks, rattrapage, `run_task_now` x8) : aucune exception, logs et reprise | OK [LIVE] |
| T1 | `pytest tests/` (vraies dépendances) + tests PostgreSQL (`TEMPO_TEST_PG_URL`, 4 tests) | 916 passés, 0 échec [LIVE] |

## 3. Bugs trouvés et corrigés

Tests de non-régression : `tests/test_qa_e2e_regressions.py` (38 tests, 24 échouent sur le code d'avant correctif).

| # | Gravité | Bug | Correctif | Test |
|---|---|---|---|---|
| B1 | Haute | `get_db` jamais importé dans `app.py` : `/api/resend-manage-link` renvoie 500 à tout le monde (lien « Déjà inscrit ? Gérer mon abonnement » cassé) et `/admin/whatsapp-diagnostic` ne remonte jamais d'erreur. Présent depuis mars 2026 (`eaa49bb`). | `app.py:2532`, `app.py:2913` (import local) | `test_resend_manage_link_*`, `test_whatsapp_diagnostic_reports_last_error`, `test_no_unresolved_global_names` (garde-fou AST sur 8 modules) |
| B2 | Haute | Même cause dans le webhook WhatsApp : sur un statut `failed`, le `NameError` était avalé (log DEBUG). `sms_logs` jamais marqué en échec, abonnés en erreur 131026/131047 jamais désactivés. | `app.py:2587` | `test_webhook_failed_status_marks_log_and_deactivates` (3 cas) |
| B3 | Moyenne | `/api/unsubscribe` validait le numéro nettoyé mais hachait le numéro brut : « +33 6.12.34.56.78 » jamais retrouvé. | `app.py:2493` | `test_unsubscribe_accepts_dotted_number` |
| B4 | Basse | `/api/performance` : saison par défaut figée à `2025-2026` ; saison mal formée = 500. | `app.py:2310-2319` (défaut = saison en cours, 400 si format invalide) | `test_api_performance_*` |
| B5 | Basse | `/calendrier/0000-01` et `/calendrier/0000-0001` : 500 (`year 0 is out of range`) au lieu de 404. | `app.py:978`, `app.py:987` | `test_calendar_year_zero_is_404` |
| B6 | Moyenne (marque) | Tirets cadratins visibles (règle 12) : page de gestion, manifest, libellés JS du dashboard, compteurs vides, `/alertes`, `/api-tempo`, messages WhatsApp (récap, alertes, confirmations), aperçu du modal. L'aperçu du modal montrait aussi des jours rouges en octobre (impossible, règle R1) : hors saison, il montre désormais la première semaine de décembre. | `templates/manage.html`, `templates/_subscribe_modal.html:142-147,161`, `templates/alertes.html:151`, `templates/api_tempo.html:65`, `templates/dashboard.html:277-290`, `static/js/app.js` et `app.min.js` (mêmes chaînes), `alerts.py` (textes des messages uniquement), `app.py:1308` | `test_no_em_dash_*`, `test_subscribe_modal_preview_respects_r1_and_no_em_dash` |
| B7 | Basse | En production (proxy `main.py`), le lifespan FastAPI ne tourne pas : la source du mot de passe admin (documentée dans CLAUDE.md) n'était jamais loggée. | `main.py:238-242` | `test_main_proxy_logs_admin_password_source` |
| B8 | Basse | Cache-busting incohérent : `legal.html` chargeait `style.min.css?v=20260929` (ancienne version) ; `app.min.js` modifié donc versionné `?v=20260929b`. | `templates/legal.html:26-27`, `dashboard.html`, `calendrier.html` | couvert par le crawl [LIVE] |

Aucune logique de prédiction ou d'alerte modifiée (`predictor`, `ml_scorer`, `rte_twin`, calibration, `alerts.rouge_alert_due` intacts ; dans `alerts.py`, seuls des textes de messages ont changé). Décisions fondateur non touchées.

## 4. Signalements sans correctif (logique d'alerte ou décision produit)

1. **Les alertes « changement » contournent le seuil calibré** [LIVE]. `alerts.send_change_alerts` notifie tout abonné dont la fenêtre couvre la date, sans passer par `rouge_alert_due`. Rejoué : une prévision BLEU devenue ROUGE à p = 0,30 (aucune alerte ROUGE due à 70/80/90) déclenche 3 messages « changement ROUGE » ; un abonné « Sûr » (90) reçoit aussi « ROUGE devient BLEU » pour un rouge jamais annoncé. Cela réintroduit une partie des fausses alertes que la calibration a divisées par 2. Décision attendue : appliquer `rouge_alert_due` (et le filtre matin/soir) aux changements.
2. **Anti-énumération** [LIVE] : `/api/unsubscribe` répond 200 pour un numéro inscrit et 400 pour un inconnu ; n'importe qui connaissant un numéro peut le désinscrire. Choix UX à arbitrer.
3. **Webhook sans `WHATSAPP_APP_SECRET`** : signature non vérifiée, un tiers peut poster STOP ou un faux statut `failed` et désactiver un abonné. À vérifier en production (V4).
4. **Jumeau RTE sans journal quand il est actif** : seul l'état inactif est loggé. Suggestion : une ligne INFO par cycle (actif, nombre de filets/vetos).
5. **`/api/predictions` sans prévision future et météo en panne** : chaque requête retente la météo sous verrou (2 à 17 s). Sans effet tant que la base contient des prévisions futures.
6. **Restauration des fichiers agents à +3 s** : pendant les 3 premières secondes après un démarrage à froid, un article écrit par l'agent peut répondre 404.
7. Message de log trompeur : `purge_old_data` annonce « weather_cache (>30d) » alors que la table n'est plus purgée.

## 5. Non vérifiable ici, procédure au premier déploiement Replit

| # | Point | Procédure |
|---|---|---|
| V1 | API externes réelles (Météo France, Open-Meteo, RTE, api-couleur-tempo) | Après le déploiement, attendre le job `post_startup` (+90 s) : logs `[Backfill]` sans erreur, `[startup]` ou `[18h] ... prédictions recalculées`. `/admin` onglet Actions : `forecast_logs.weather_forecast_log.last_fetched_at` = aujourd'hui ; après 18 h, `rte_forecast_log.rows` > 0 (« RTE archive : N lignes »). |
| V2 | Migration réelle | Logs de démarrage : `Migration v24 appliquee` puis `Migration v25 appliquee` (ou rien si déjà en v25). Onglet Actions, bouton « Lancer le diagnostic » : comptes predictions/actuals/performance identiques à ceux d'avant le déploiement. |
| V3 | Jumeau RTE saison 2026-2027 | Chercher `[RTE twin]` dans les logs du premier cycle. Message « N jours de météo ... (< 330) : jumeau inactif » = la table `weather_cache` de prod n'a pas assez de jours entre le 2025-09-01 et le 2026-08-31 ; le jumeau restera inactif toute la saison. Compter : `SELECT COUNT(*) FROM weather_cache WHERE date BETWEEN '2025-09-01' AND '2026-08-31'` (seuil 330). |
| V4 | WhatsApp réel | Vérifier que `WHATSAPP_APP_SECRET` est défini (sinon WARNING à chaque webhook). S'inscrire avec un vrai numéro : message `tempo_bienvenue` reçu. Tester « Déjà inscrit ? » (message `tempo_lien_gestion` reçu, corrige B1). Répondre STOP au bot : l'abonné passe inactif dans l'onglet Abonnés. `/admin/whatsapp-diagnostic` : `configured: true`. |
| V5 | Limite d'inscription derrière le proxy Replit | Faire 2 inscriptions depuis 2 réseaux différents et regarder l'IP dans les logs uvicorn. Si toutes les requêtes ont la même IP (celle du proxy), la limite 5/h est globale au site : définir `FORWARDED_ALLOW_IPS=*` (comme pour Cloudflare). |
| V6 | Mot de passe admin | Log `[Admin] Mot de passe source: ADMIN_PASSWORD env var (longueur=N)` présent au démarrage (B7). |
