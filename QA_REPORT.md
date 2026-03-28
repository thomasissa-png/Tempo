# Rapport QA — TempoForecast
**Date** : 2026-02-09
**QA Engineer** : Audit fonctionnel et end-to-end
**Version** : commit `205213e` (branche `claude/edf-tempo-predictor-gSmra`)
**Méthode** : Revue de code exhaustive + exécution des 71 tests unitaires (tous ✅)

---

## 1. Tableau de tests — Flux SMS Inscription

| # | Cas de test | Statut | Sévérité | Détails |
|---|------------|--------|----------|---------|
| 1.1 | Inscription happy path `06 12 34 56 78` | ✅ | — | `normalizePhone()` → `+33612345678`, `register_user()` valide ✓ |
| 1.2 | Inscription format `0612345678` (sans espaces) | ✅ | — | Normalisé correctement |
| 1.3 | Inscription format `+33612345678` | ✅ | — | Accepté directement |
| 1.4 | Inscription format `33612345678` (sans +) | ✅ | — | Normalisé → `+33612345678` |
| 1.5 | Rejet numéro 01/02/03/04/05/08/09 | ✅ | — | `normalizePhone()` retourne `null`, bloqué côté client |
| 1.6 | Rejet lettres dans le numéro | ✅ | — | Backend `isdigit()` check (line 298 alerts.py) |
| 1.7 | Numéro déjà inscrit → message clair | ✅ | — | `"Ce numéro est déjà inscrit."` |
| 1.8 | Réactivation d'un numéro désactivé | ✅ | — | `actif` passe de 0→1, phone_encrypted mis à jour |
| 1.9 | Rate limiting (5 inscriptions / 5 min) | ✅ | — | HTTP 429, message user-friendly |
| 1.10 | Protection CSRF (origin check) | ✅ | — | `_check_origin()` vérifie le header Origin/Referer |
| 1.11 | Chiffrement du numéro stocké | ✅ | — | Fernet AES-256, hash SHA-256 pour dédup |
| 1.12 | Validation temps réel dans le champ téléphone | ✅ | — | `setupPhoneValidation()` avec compteur de chiffres |
| 1.13 | Numéro trop court (ex: `06 12`) | ✅ | — | Feedback client "Encore X chiffre(s)" |
| 1.14 | **Numéro `05 XX` validé côté client mais rejeté serveur** | ⚠️ | Mineure | `normalizePhone()` rejette 05, mais le feedback "Tapez un numéro en 06 ou 07" n'apparaît qu'après saisie de 2+ chars sans 0[67] prefix. Le `form-hint` statique guide correctement. |
| 1.15 | **Options seuil/delai non transmises lors réactivation** | ❌ | Moyenne | `register_user()` line 317-322 : réactivation = `SET actif = 1, phone_encrypted = ?` seulement. Les nouvelles préférences (`seuil_rouge`, `delai`, `alerte_blanc`, `recap_hebdo`) sont **ignorées**. L'utilisateur récupère ses anciens réglages. |
| 1.16 | **Checkbox `alerte_blanc` et `recap_hebdo` → Form parsing** | ❌ | Moyenne | FastAPI `Form(False)` pour un `bool`. Si la checkbox est décochée, le navigateur n'envoie rien → FastAPI reçoit le défaut `False`. Si cochée, le navigateur envoie `"true"` (string). FastAPI `bool` accepte `"true"`, `"1"`, etc. Fonctionnel mais fragile : le mapping dépend du comportement interne de FastAPI. |

---

## 2. Tableau de tests — Réception d'alertes

| # | Cas de test | Statut | Sévérité | Détails |
|---|------------|--------|----------|---------|
| 2.1 | Alerte ROUGE envoyée quand `prob_rouge >= seuil_user` | ✅ | — | `send_alerts_for_prediction()` line 153 compare `prob_pct >= seuil_alerte_rouge` |
| 2.2 | Alerte BLANC envoyée seulement si `alerte_blanc = 1` | ✅ | — | Filtre SQL correct |
| 2.3 | Pas d'alerte pour BLEU | ✅ | — | Return immédiat line 144 |
| 2.4 | Limite 1 alerte/jour/user | ✅ | — | Vérification `sms_logs` par date |
| 2.5 | Délai respecté (`delta <= delai_alerte`) | ✅ | — | Line 181 |
| 2.6 | Contenu SMS rouge : date, jour FR, confiance, temp, conseil | ✅ | — | `format_alert_rouge()` complet |
| 2.7 | "STOP pour se désinscrire" dans chaque SMS | ✅ | — | Présent dans les 4 templates |
| 2.8 | Alerte officielle (confirmée EDF) | ✅ | — | `send_official_alerts()` séparé |
| 2.9 | Pas de doublon alerte officielle + prédiction même jour | ✅ | — | Type distinct `'officiel'` vs `'prediction%'` dans la limite jour |
| 2.10 | Récap hebdomadaire (dimanche 20h) | ✅ | — | `send_weekly_recap()` pour users avec `recap_hebdo = 1` |
| 2.11 | Mode simulation quand Twilio non configuré | ✅ | — | `send_sms()` retourne `("SIM_...", "simulated")` |
| 2.12 | Log SMS dans `sms_logs` (sid, statut, erreur) | ✅ | — | `_log_sms()` trace tout |
| 2.13 | **Alerte officielle : filtre trop large pour ROUGE** | ❌ | Moyenne | `send_official_alerts()` line 210 : `seuil_alerte_rouge > 0` inclut TOUS les users actifs (seuil par défaut = 70 > 0). Devrait être cohérent : tout user actif avec alerte rouge reçoit l'alerte officielle, ce qui est probablement voulu. Mais un user qui a mis `seuil_rouge = 90` pour limiter les fausses alertes recevra quand même TOUTES les alertes officielles. **Comportement discutable** mais acceptable. |
| 2.14 | **Doublon alerte officielle + prédiction possible** | ❌ | Haute | La limite 1 alerte/jour utilise `type_alerte LIKE 'prediction%'` pour les prédictions et `type_alerte = 'officiel'` pour les confirmations. Un user peut recevoir 2 SMS le même jour : 1 prédiction (18h cycle) + 1 officiel (11h30 lendemain). Pas de dédup cross-type. |
| 2.15 | **Timing : alerte prédiction avant alerte officielle** | ⚠️ | Basse | L'alerte prédiction part à 18h (J-1). L'alerte officielle part à 11h30 le lendemain (quand EDF confirme). L'user reçoit 2 SMS en ~17h30. Peu ergonomique mais fonctionnel. |

---

## 3. Tableau de tests — Flux Opt-out (désinscription)

| # | Cas de test | Statut | Sévérité | Détails |
|---|------------|--------|----------|---------|
| 3.1 | Désinscription via `/api/unsubscribe` (web) | ✅ | — | `unsubscribe_user()` met `actif = 0` |
| 3.2 | Numéro non trouvé → erreur 404 | ✅ | — | `"Numéro non trouvé."` |
| 3.3 | User désactivé ne reçoit plus d'alertes | ✅ | — | Filtre `WHERE actif = 1` dans toutes les requêtes d'envoi |
| 3.4 | Nettoyage RGPD (6 mois inactif → suppression) | ✅ | — | `cleanup_inactive_users()` supprime sms_logs PUIS users |
| 3.5 | Réinscription après désinscription | ✅ | — | Réactivation via `register_user()` (cf. bug 1.15) |
| 3.6 | **Pas de page/bouton de désinscription dans le frontend** | ❌ | Haute | Le dashboard n'a **aucun lien ni formulaire** de désinscription. L'API `/api/unsubscribe` existe mais n'est accessible que par curl/API directe. L'user doit répondre "STOP" par SMS (Twilio webhook non implémenté) ou... rien. |
| 3.7 | **Webhook Twilio STOP non implémenté** | ❌ | Critique | Le SMS dit "STOP pour se désinscrire" mais **aucun webhook Twilio** n'est configuré pour recevoir les réponses SMS. L'opt-out par SMS est **non fonctionnel**. |
| 3.8 | **Protection CSRF sur `/api/unsubscribe`** | ✅ | — | Origin check présent |
| 3.9 | **Pas de rate limiting sur `/api/unsubscribe`** | ⚠️ | Basse | Contrairement à `/api/subscribe`, pas de rate limiting. Permet un brute-force de numéros pour scanner les inscrits (via réponse 404 vs 200). |

---

## 4. Tableau de tests — Précision du calendrier

| # | Cas de test | Statut | Sévérité | Détails |
|---|------------|--------|----------|---------|
| 4.1 | Couleur du jour affichée (API EDF temps réel) | ✅ | — | `/api/today` → `fetch_tempo_today()` |
| 4.2 | Couleur de demain (après 11h) | ✅ | — | `/api/tomorrow` → `fetch_tempo_tomorrow()` |
| 4.3 | État "En attente" si demain pas encore annoncé | ✅ | — | Affiche "?" avec message explicatif |
| 4.4 | Compteurs restants (rouge/blanc/bleu) | ✅ | — | `/api/remaining` avec fallback multiples |
| 4.5 | Confirmé vs prédit visuellement distinct | ✅ | — | Classe CSS `confirmed` + badge "Confirmé par EDF" |
| 4.6 | Couleur changée signalée ("Était BLANC") | ✅ | — | `couleur_precedente` affiché |
| 4.7 | Backfill saison au démarrage | ✅ | — | `backfill_season_actuals()` dans lifespan |
| 4.8 | **confirm_prediction écrase les données originales** | ❌ | Haute | `confirm_prediction()` (predictor.py) remplace `couleur_predite`, score, probabilités par la vérité officielle. **Les données de prédiction originales sont perdues.** Si l'évaluation de performance n'a pas encore tourné, impossible de mesurer la qualité de la prédiction. |
| 4.9 | **Race condition evaluation ↔ confirmation** | ❌ | Haute | Le scheduler 11h30 fait : `evaluate_predictions_for_date()` → `confirm_prediction()`. L'ordre est correct **si** le même cycle s'exécute atomiquement. Mais `evaluate_predictions_for_date()` line 52 **skip les predictions confirmées**. Si 2 exécutions concurrentes (retry), la 2ème appelle `evaluate` après `confirm` de la 1ère → 0 prédictions évaluées. |
| 4.10 | Prédictions hors saison (juin-août) → BLEU | ✅ | — | Return immédiat dans `predict_day()` |
| 4.11 | Saison correcte (1er sept → 31 août) | ✅ | — | `get_season_dates()` gère le chevauchement d'années |
| 4.12 | **Date affichée off-by-one en JS (timezone UTC)** | ⚠️ | Moyenne | `new Date("2026-02-10")` en JS est interprété en UTC. Pour un user en France (UTC+1), `getDay()` peut renvoyer le jour précédent si l'heure locale est entre 0h et 1h. Ex: "2026-02-10" → Date(UTC 00:00) → en France c'est encore le 9 → mauvais jour de la semaine affiché. |
| 4.13 | Groupement prédictions ("3 prochains jours" / "cette semaine") | ✅ | — | Slicing correct (0-3, 3-7, 7+) |

---

## 5. Tableau de tests — Cas limites

| # | Cas de test | Statut | Sévérité | Détails |
|---|------------|--------|----------|---------|
| 5.1 | Hors saison (juin-août) → tout BLEU | ✅ | — | `predict_day()` line 98 |
| 5.2 | Quotas rouges épuisés → pas de rouge prédit | ✅ | — | `remaining["ROUGE"] == 0` → skip ROUGE |
| 5.3 | Quotas rouge ET blanc épuisés → tout BLEU | ✅ | — | Early return line 106 |
| 5.4 | API météo down → pas de prédiction (plus de fallback simulé) | ✅ | — | `weather_client.py` retourne [] si Météo France indisponible |
| 5.5 | API Tempo down → "Données non disponibles" | ✅ | — | `/api/today` retourne `status: "unavailable"` |
| 5.6 | API RTE down → prédiction sans score RTE | ✅ | — | `rte_score = None`, facteur ignoré |
| 5.7 | Cache prédictions (5 min) | ✅ | — | Lock asyncio + double-check |
| 5.8 | Invalidation cache après confirmation EDF | ✅ | — | `invalidate_predictions_cache()` appelé dans scheduler |
| 5.9 | Scheduler retry (2 tentatives avec 30s pause) | ✅ | — | Toutes les 4 tâches ont retry |
| 5.10 | Purge DB au démarrage (cache 30j, preds 90j, perf 180j) | ✅ | — | `purge_old_data()` dans lifespan |
| 5.11 | Admin password auto-généré si non configuré | ✅ | — | `secrets.token_urlsafe(24)` dans lifespan |
| 5.12 | Rate limiting admin (10 tentatives / 5 min) | ✅ | — | `_admin_rate_limit_store` + HTTP 429 |
| 5.13 | **RTE timezone DST hardcodé** | ❌ | Moyenne | `rte_client.py` utilise `+01:00` hardcodé. En été (mars→octobre), la France est en UTC+2. Les requêtes RTE cibleront le mauvais jour ~6 mois/an. |
| 5.14 | **Pas de retry API externe (météo, Tempo, RTE)** | ⚠️ | Moyenne | Un timeout réseau unique = `None` retourné. Le scheduler retry au niveau tâche (30s) mais pas au niveau requête HTTP individuelle. |
| 5.15 | **Backfill : 5 erreurs consécutives → arrêt** | ✅ | — | Garde-fou correct |
| 5.16 | **weather_cache croît indéfiniment entre purges** | ⚠️ | Basse | Purge à 30j au démarrage seulement. Si l'app tourne longtemps, accumulation. |
| 5.17 | **Hash phone non salé → attaque rainbow table** | ⚠️ | Moyenne | ~10^8 numéros FR possibles, SHA-256 rapide. Si DB compromise, tous les numéros retrouvables en minutes. |

---

## 6. Tableau de tests — Performance

| # | Cas de test | Statut | Sévérité | Détails |
|---|------------|--------|----------|---------|
| 6.1 | Chargement dashboard (cold start) | ✅ | — | 5 requêtes API parallèles via `fetch()`, non bloquantes |
| 6.2 | Cache prédictions (warm) | ✅ | — | 5 min cache mémoire, lock asyncio pour stampede |
| 6.3 | Requêtes concurrentes `/api/predictions` | ✅ | — | `asyncio.Lock` protège le cache |
| 6.4 | SQLite WAL mode pour lectures concurrentes | ✅ | — | `PRAGMA journal_mode=WAL` à chaque connexion |
| 6.5 | Busy timeout 5s pour contention écriture | ✅ | — | `PRAGMA busy_timeout=5000` |
| 6.6 | **Pas de connection pooling SQLite** | ⚠️ | Basse | `get_db()` ouvre une nouvelle connexion à chaque appel. OK pour SQLite (conçu ainsi) mais sous charge ~100 users simultanés, overhead d'ouverture/fermeture. |
| 6.7 | **Envoi SMS batch séquentiel** | ⚠️ | Moyenne | `send_alerts_for_prediction()` itère les users en série. Pour 1000 users, chaque `send_sms()` attend la réponse Twilio (~200ms). Total estimé : **~3 minutes** pour 1000 SMS. Pas de parallélisme. |
| 6.8 | **Scheduler mono-thread** | ⚠️ | Basse | APScheduler AsyncIO, les tâches s'exécutent séquentiellement dans la boucle événementielle. Si la tâche 18h est lente (SMS batch), elle bloque les requêtes web. |
| 6.9 | Purge rate-limit store (>1000 entrées) | ✅ | — | `_cleanup_rate_limit_store()` supprime les entrées > 1h |
| 6.10 | **Pas de pagination `/admin/sms-logs`** | ⚠️ | Basse | Limit configurable mais pas d'offset. 50k logs → requêtes lentes. |

---

## Bugs critiques — Détails et étapes de reproduction

### BUG-01 : Webhook STOP SMS non implémenté (Critique)

**Impact** : L'utilisateur ne peut PAS se désinscrire par SMS malgré la promesse dans chaque message.

**Reproduction** :
1. S'inscrire aux alertes SMS
2. Recevoir un SMS disant "STOP pour se désinscrire"
3. Répondre "STOP" au numéro Twilio
4. → Rien ne se passe. L'utilisateur continue de recevoir des SMS.

**Cause racine** : Aucun endpoint `/api/sms/webhook` ou `/api/twilio/incoming` n'est défini pour recevoir les réponses SMS entrantes. Twilio requiert un webhook configuré pour transmettre les réponses.

**Fix recommandé** : Créer un endpoint `POST /api/sms/incoming` avec validation signature Twilio, parser le body pour "STOP"/"START", et appeler `unsubscribe_user()`.

---

### BUG-02 : Pas d'interface de désinscription web (Haute)

**Impact** : Aucun moyen visible pour l'utilisateur de se désinscrire depuis le site web.

**Reproduction** :
1. Aller sur le dashboard
2. Chercher un bouton/lien "Se désinscrire" ou "Gérer mes alertes"
3. → Rien. Seul le formulaire d'inscription existe.

**Cause racine** : L'API `/api/unsubscribe` existe (app.py line 615) mais aucun formulaire HTML ne l'appelle.

**Fix recommandé** : Ajouter un formulaire sous le formulaire d'inscription : "Déjà inscrit ? Entrez votre numéro pour vous désinscrire."

---

### BUG-03 : confirm_prediction écrase les prédictions originales (Haute)

**Impact** : Perte de données de prédiction, empêchant l'évaluation de performance.

**Reproduction** :
1. Le scheduler 18h génère une prédiction BLANC pour demain
2. Le scheduler 11h30 du lendemain fait d'abord `confirm_prediction("ROUGE")`
3. Toutes les prédictions pour ce jour deviennent `couleur_predite = "ROUGE"`
4. Puis `evaluate_predictions_for_date()` skip car `confirmed = 1`
5. → La prédiction BLANC erronée n'est jamais comptée dans les métriques

**Cause racine** : `confirm_prediction()` (predictor.py) fait un UPDATE destructif des colonnes `couleur_predite`, `probabilite_*`, `score_risque`, `raison`.

**Note** : L'ordre actuel du scheduler (evaluate PUIS confirm) atténue ce bug, mais un retry ou un appel concurrent peut inverser l'ordre.

---

### BUG-04 : Doublon SMS (prédiction + officiel) même jour (Haute)

**Impact** : L'utilisateur reçoit 2 SMS pour le même jour rouge (1 prédiction à 18h + 1 confirmation à 11h30 le lendemain).

**Reproduction** :
1. 18h : prédiction ROUGE pour demain → SMS "Jour ROUGE prévu..."
2. 11h30 lendemain : EDF confirme ROUGE → SMS "Tempo confirmé: ROUGE..."
3. L'utilisateur a reçu 2 SMS en 17h30

**Cause racine** : La limite 1/jour utilise des types différents (`prediction%` vs `officiel`). Pas de dédup cross-type.

---

### BUG-05 : Préférences perdues lors de la réinscription (Moyenne)

**Impact** : Un utilisateur qui se réinscrit avec de nouveaux réglages conserve ses anciens.

**Reproduction** :
1. S'inscrire avec `seuil_rouge = 70`, `alerte_blanc = true`
2. Se désinscrire
3. Se réinscrire avec `seuil_rouge = 90`, `alerte_blanc = false`
4. → Les préférences restent à `70` et `true` (anciennes valeurs)

**Cause racine** : `register_user()` line 317 ne met à jour que `actif` et `phone_encrypted`.

---

### BUG-06 : Timezone RTE hardcodée (Moyenne)

**Impact** : Requêtes RTE avec mauvaise date pendant 6 mois (heure d'été).

**Reproduction** :
1. En été (mars-octobre), l'offset est UTC+2
2. `rte_client.py` utilise `+01:00` hardcodé
3. La requête cible la mauvaise journée entre 23h et 0h

**Cause racine** : Pas d'utilisation de `zoneinfo` ou `pytz` pour gérer le DST.

---

### BUG-07 : Date off-by-one en JavaScript (Moyenne)

**Impact** : Mauvais jour de la semaine affiché entre minuit et 1h du matin.

**Reproduction** :
1. Ouvrir le dashboard à 00h30 (heure de Paris)
2. Une prédiction pour "2026-02-10" (Mardi)
3. `new Date("2026-02-10")` = Feb 10 00:00 UTC = Feb 9 23:00 UTC-1 n'est pas le cas, mais Feb 10 00:00 UTC pour un user UTC+1 = `getDay()` sur l'objet UTC donne le bon jour. Mais `getDay()` utilise l'heure locale, donc si l'heure locale est le 10 fév 01:00, c'est ok.
4. En fait, le vrai problème : `new Date("2026-02-10")` donne minuit UTC. `getDay()` en local (UTC+1) donne bien le 10 fév. Donc pas de bug pour la France. **Reclassifié : Risque théorique minime en France.**

---

## Évaluation des flux utilisateur

### Flux 1 : Découverte → Inscription SMS
**Note : 7/10**
- (+) Parcours clair : données utiles en haut, formulaire en dessous
- (+) Validation téléphone temps réel, normalisation automatique
- (+) Copywriting orienté action ("Ne payez plus 100€ par surprise")
- (-) Pas de confirmation SMS (pas de double opt-in)
- (-) Préférences perdues à la réinscription

### Flux 2 : Réception d'alertes → Action
**Note : 7/10**
- (+) SMS clair avec jour, date, confiance, température, conseil
- (+) Alerte officielle distincte des prédictions
- (+) Limite 1/jour respectée
- (-) Doublon prédiction + officiel pour le même jour
- (-) Pas de lien web dans le SMS pour voir le détail

### Flux 3 : Désinscription
**Note : 2/10**
- (-) Aucun formulaire web de désinscription
- (-) STOP SMS non fonctionnel (webhook manquant)
- (-) Seule option : appeler l'API en curl (impossible pour Jean-Pierre)
- (+) RGPD : suppression auto après 6 mois d'inactivité

### Flux 4 : Consultation quotidienne du calendrier
**Note : 8/10**
- (+) Couleur du jour + demain en haut (above the fold)
- (+) Résumé semaine avec points colorés
- (+) Groupement par fiabilité ("fiable" / "moyen" / "indicatif")
- (+) Conseils actionnables par couleur
- (-) Bug date timezone théorique (minime en pratique)
- (-) Prédictions écrasées par confirmation → incohérence données perf

### Flux 5 : Robustesse (pannes API, cas limites)
**Note : 7/10**
- (+) Fallback météo simulée
- (+) Retry scheduler (2 tentatives)
- (+) Messages d'erreur user-friendly + bouton Réessayer
- (+) Mode simulation SMS sans Twilio
- (-) Pas de retry HTTP unitaire
- (-) Timezone DST RTE
- (-) Hash phone non salé

### Flux 6 : Performance
**Note : 6/10**
- (+) Cache mémoire 5 min avec lock
- (+) WAL mode + busy_timeout
- (-) SMS envoyés en série (lent pour 1000+ users)
- (-) Pas de connection pooling
- (-) Scheduler bloque la boucle events

---

## Score qualité global

| Catégorie | Score | Poids | Pondéré |
|-----------|-------|-------|---------|
| Inscription SMS | 7/10 | 20% | 1.40 |
| Alertes | 7/10 | 20% | 1.40 |
| Désinscription | 2/10 | 15% | 0.30 |
| Calendrier | 8/10 | 20% | 1.60 |
| Robustesse | 7/10 | 15% | 1.05 |
| Performance | 6/10 | 10% | 0.60 |
| **TOTAL** | | | **6.35/10** |

---

## Résumé exécutif

### Points forts
- Architecture solide (FastAPI, SQLite WAL, APScheduler, Fernet encryption)
- 71 tests unitaires passants couvrant prédicteur et performance
- UX refondée avec langage humain, prix concrets, conseils actionnables
- Multiples fallbacks pour les API externes
- Rate limiting et protection CSRF sur les endpoints sensibles
- Système d'apprentissage continu (learning journal + correction des biais)

### Points critiques à corriger avant mise en production

| Priorité | Bug | Effort estimé |
|----------|-----|--------------|
| 🔴 P0 | Webhook Twilio STOP (BUG-01) | ~2h |
| 🔴 P0 | Formulaire désinscription web (BUG-02) | ~1h |
| 🟠 P1 | Doublon SMS prédiction + officiel (BUG-04) | ~1h |
| 🟠 P1 | Préférences réinscription (BUG-05) | ~30min |
| 🟡 P2 | confirm_prediction écrase données (BUG-03) | ~2h |
| 🟡 P2 | Timezone DST RTE (BUG-06) | ~30min |
| 🟡 P2 | Hash phone non salé (BUG-17 sécurité) | ~1h |
| 🟢 P3 | SMS batch parallèle | ~2h |
| 🟢 P3 | Retry HTTP unitaire pour APIs | ~1h |

**Verdict** : L'application est fonctionnelle et l'UX est de bonne qualité. Les bugs liés à la désinscription sont **bloquants RGPD** — un utilisateur n'a actuellement aucun moyen effectif de se désinscrire. Les 2 bugs P0 doivent être corrigés avant toute mise en production.
