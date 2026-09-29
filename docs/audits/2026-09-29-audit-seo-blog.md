# Audit SEO technique, on-page et blog : calendrier-tempo.fr

> **Décisions fondateur du 2026-09-29, prioritaires sur cet audit** : lien Selectra de la home conservé tel quel en dofollow (accord Selectra) ; chiffre « 2 500 foyers » conservé ; Replit reste l'hébergement pour la prochaine mise à jour.

Date : 2026-09-29. Périmètre : repo uniquement (site live non joignable, aucun outil de volumes). Aucune donnée de trafic, position, volume ou backlink n'est avancée ici : tout ce qui exige le live est en section « À vérifier en ligne ». Aucun fichier du repo n'a été modifié, hors ce livrable.

## 1. Synthèse

**Note : 5,5 / 10.** Fondations techniques saines (SSR complet, canonicals absolus, robots par bot, IndexNow, Bing/Google verification, gzip, HSTS, 404 noindex, aucun AggregateRating factice). Ce qui plombe la note : un contenu de home factuellement faux sur un point de règle, des données structurées incohérentes ou invalides, un calendrier éditorial qui s'arrête au 13/10 alors que le pic (1er novembre) arrive, un pipeline d'agent SEO qui ne peut pas persister ses articles, et l'absence de pages pour plusieurs requêtes cibles.

**5 priorités (dans l'ordre) :**
1. **Avant le 1er novembre** : corriger la FAQ de la home (samedi « peut être rouge », faux selon R2 et selon le reste du site) et aligner FAQ visible et JSON-LD (une seule source Python).
2. **Publier maintenant l'article `calendrier-tempo-2026-2027-dates`** (programmé au 13/10), le lier depuis home, `/calendrier` et 4 articles, et corriger ses affirmations contredites par les .ics du repo (tableau mensuel, « blancs en octobre »).
3. **Réparer le pipeline éditorial** : plus rien de planifié après le 13/10, aucun article autonome depuis le 26/05, et l'agent écrit sur un disque éphémère. Écrire dans un stockage durable (PR GitHub ou table Postgres) + ping IndexNow, et charger le calendrier de la section 6.
4. **Créer les pages manquantes** : `/tarif-tempo-edf`, `/calendrier/<saison>` (SSR, historique réel depuis `actuals`) et pages mois crawlables, `/couleur-tempo-demain`. Différencier les titles de `/` et `/calendrier` (cannibalisation).
5. **Corriger le JSON-LD blog** (`|tojson`, 7 FAQ invalides), le sitemap (lastmod réels, `updated_date`, retirer `feed.xml`) et le maillage (6 orphelins, pas de bloc « articles liés »).

## 2. Constats [CRITIQUE]

**C1. FAQ home fausse sur les week-ends et les jours blancs.**
Preuve : `templates/dashboard.html:473` « les samedis peuvent l'être (c'est rare mais ça arrive 1 à 2 fois par saison) » et `:474` « Les jours blancs, eux, peuvent tomber n'importe quand ». Contredit `:519` (même page, « jamais le week-end »), la règle R2/R3 de CLAUDE.md et les articles (`jours-rouges-tempo-guide.md:33`, `bilan-saison-tempo-2025-2026.md:34`). Un LLM ou un lecteur qui cite cette FAQ diffuse une règle fausse, sur la page la plus exposée.
Correctif : remplacer par « Non. Jamais le samedi, le dimanche ni un jour férié (R2). Le blanc est possible du lundi au samedi, jamais le dimanche (R3). » Ajouter un test dans `tests/test_qa_fixes.py` qui interdit « samedis peuvent » dans les templates.

**C2. Pipeline éditorial à l'arrêt au début du pic, et non persistant.**
Preuves :
- `articles/_calendrier_editorial.yaml` : dernier item au 2026-10-13, « Dernière mise à jour : 2026-05-26 ». `_publication_log.md` : dernière entrée le 2026-05-26. Les 2 REFRESH prévus (07/07, 18/08) sont restés « à rédiger ». `_seo_rules.yaml` : `saison_courante: "2025-2026"` alors que l'étape « septembre » du prompt aurait dû la passer à 2026-2027. Conclusion : l'agent n'a laissé aucune trace exploitable depuis 4 mois.
- `.claude/seo-agent-prompt.md:273-282` : publication par `git add/commit/push` depuis le conteneur. `.replit` : `deploymentTarget = "cloudrun"` (autoscale, disque éphémère, bientôt conteneur Cloudflare). Impacts SEO : (a) un article écrit sur une instance n'existe que sur elle, donc 404 intermittent d'une requête à l'autre selon l'instance servie (mauvais signal de fiabilité de crawl, surtout pour Bing) ; (b) `blog.py` relit le disque local, donc `sitemap.xml`, `feed.xml` et `llms.txt` divergent selon l'instance ; (c) le redémarrage efface article + calendrier + journal ; (d) sans credentials git dans le conteneur, le push échoue en silence (le prompt prévoit seulement de « loguer l'erreur ») ; (e) aucun ping IndexNow à la publication (`scheduler.py:586-616` ne notifie que `/` et `/calendrier`).
Correctif (au choix, par ordre de préférence) : (1) l'agent ouvre une PR via l'API GitHub, validation du fondateur sur les 5 premiers articles, merge = redéploiement, puis IndexNow sur l'URL ; (2) stocker les articles en table Postgres (déjà en prod) et faire lire `blog.py` en base ; (3) volume persistant. Dans tous les cas : `blog.py` avec cache invalidé par mtime, `calendrier_editorial` et `publication_log` hors disque local, alerte si le dernier article a plus de 10 jours en saison.

**C3. Article `calendrier-tempo-2026-2027-dates` : publié trop tard, orphelin, partiellement contredit par les données.**
Preuves : `publish_date: 2026-10-13`, 0 lien entrant (analyse du maillage), alors que la requête « calendrier tempo 2026-2027 » se joue de septembre à novembre. Contenu : tableau mensuel « Nov 2 à 4, Déc 4 à 6, Jan 5 à 8, Fév 3 à 5, Mars 1 à 3 » présenté comme « moyennes historiques ». Les .ics du repo (données réelles) donnent 2023-24 : nov 1, déc 3, jan 9, fév 3, mars 6 ; 2024-25 : nov 0, déc 8, jan 13, fév 1, mars 0 ; 2025-26 (partiel au 12/02) : déc 2, jan 6. Aucun mois ne colle aux fourchettes. Même article : « on observe quelques jours blancs en octobre » ; or les 3 .ics n'ont aucun blanc en septembre ni octobre (premiers blancs en novembre). Enfin « respecté à l'unité près chaque saison » est vrai pour 2023-24 et 2024-25 (22 rouges) mais 2025-26 est atypique (8 rouges au 12/02).
Correctif : passer `publish_date` à 2026-09-30, remplacer le tableau par les valeurs réelles par saison avec la source (fichiers .ics, ou requête `actuals`), reformuler l'octobre, nuancer « à l'unité près ». Lier depuis la home (FAQ), `/calendrier` (bloc « saison précédente ») et depuis `rentree`, `preparer`, `jours-rouges-tempo-guide`, `calendrier-tempo-2025-2026-dates`.

## 3. Constats [MAJEUR]

**M1. JSON-LD FAQPage de la home différent de la FAQ visible.** `dashboard.html:148-218` (9 questions) vs `:424-534` (10 questions). Absentes du visible : « C'est quoi l'offre Tempo EDF ? », « Comment sont calculés les jours rouges ? » ; reformulées : « Que faire lors d'un jour rouge », « fiabilité ». Absentes du JSON-LD : « Combien coûte réellement un jour rouge », « À quelle heure commence… », « Les jours fériés… ». Google exige que le balisage reflète le contenu visible. Note : depuis 2023 les rich results FAQ sont limités aux sites institutionnels ou de santé, le gain SERP est nul ; le balisage ne sert plus qu'aux moteurs IA, donc il doit être exact. Correctif : une liste `FAQ_HOME` en Python rendue deux fois (HTML et `tojson`). Même contrôle pour `/calendrier` : JSON-LD dit « 5 prochains jours » (`calendrier.html:119`), la page et la modale disent 7.

**M2. JSON-LD des articles invalide ou dégradé.** `blog_article.html:53-54,107,110` interpolent `{{ ... }}` dans des chaînes JSON avec l'autoescape HTML. Conséquences : (a) 7 articles ont une réponse FAQ multi-lignes (`fin-saison-rouge-tempo-bilan`, `linky-tempo-suivre-consommation`, `pompe-a-chaleur-tempo-edf`, `preparer-saison-tempo-2026-2027`, `rentree-tempo-2026-2027`, `souscrire-tempo-edf-guide`, `tempo-edf-avis-rentabilite`) : saut de ligne brut dans une chaîne JSON, donc script invalide et ignoré ; (b) toute apostrophe devient `&#39;` dans `headline` et FAQ ; (c) `economiser-tempo-edf.md` a `title: "…"` entre guillemets, que `blog.py:_parse_frontmatter` ne retire pas : les guillemets s'affichent dans le `<title>`, le H1 et le JSON-LD. Correctif : `{{ article.title | tojson }}`, idem description et FAQ, et retirer les guillemets dans `_parse_frontmatter`. `bilan-saison-tempo-2025-2026` a « ## Foire aux questions » que `validate_article.py` accepte mais que `blog.py:_FAQ_SECTION_RE` ignore : pas de FAQ JSON-LD. Aligner les deux regex.

**M3. Cannibalisation `/` et `/calendrier`, et home sans les requêtes cibles.** `dashboard.html:6` title « Calendrier Tempo EDF » et H1 identique (`:261`, H1 posé dans le lien de marque) ; `calendrier.html:6` title « Calendrier Tempo EDF {saison} ». Les deux visent « calendrier tempo edf ». La home ne contient « EDF Tempo » ni dans le title ni dans le H1, alors que ~40 % du volume vise cet ordre (`_seo_rules.yaml`). Correctif : home = « Tempo EDF : couleur du jour, de demain et prévisions à 15 jours » (H1 propre, marque en `<span>`), `/calendrier` garde « Calendrier Tempo EDF 2026-2027 : jours rouges, blancs et bleus ». Mot-clé exact en title + H1 + premier paragraphe (exigence Bing).

**M4. Pages manquantes pour les requêtes cibles (CLAUDE.md).**

| Requête cible | Couverture actuelle | Action |
|---|---|---|
| tarif tempo edf | FAQ home + blog | page `/tarif-tempo-edf` (grille du 01/08/2026 en tableau, source arrêté, mise à jour auto depuis `_seo_rules.yaml`) |
| edf tempo couleur du jour et du lendemain des 12h / couleur tempo demain | home (FAQ) | pages `/couleur-tempo-aujourdhui` et `/couleur-tempo-demain` SSR, title daté, heure de publication EDF |
| calendrier tempo 2025 2026 / 2026 2027 (dates) | 2 articles de blog | `/calendrier/2025-2026`, `/calendrier/2026-2027` : liste complète des rouges/blancs depuis `actuals` |
| jours rouges tempo (par mois) | aucune | `/calendrier/2026-2027/novembre` etc. (SSR, liens prev/next, cocon) |
| tempo edf 2027 (calendrier, jours rouges) | aucune | intégrer « 2027 » aux titles/H2 des pages saison |

Saisonnalité : les requêtes « 2025 / 2026 » décroissent, la demande « 2026-2027 / 2027 » monte jusqu'en novembre-janvier. [HYPOTHÈSE : à confirmer dans Search Console].

**M5. Calendrier non crawlable et jours sans donnée affichés en BLEU.** `static/js/app.js` (`navigateCalendar`) charge les mois via `/api/calendrier-data`, or `/api/` est en `Disallow` (`app.py:875`) : un seul mois est indexable. `app.py:677` `colors_map.get(d_str, "BLEU")` colore en bleu tout jour sans prédiction (au-delà de J+15), donc un novembre entièrement « bleu » est servi au crawler. Contraire à la règle « ne jamais inventer » : afficher « non prévu » (gris) et ne mettre en avant que les couleurs connues. Correctif : routes SSR par mois (voir M4), navigation par liens `<a>`.

**M6. Sitemap : signaux de fraîcheur peu fiables.** `app.py:1107,1113` : `/` et `/calendrier` toujours `lastmod = today` (Bing traite ça comme du bruit, mais le contenu change réellement chaque jour : à ne garder que si l'HTML change ; sinon date du dernier cycle de prédictions). `:1103` `_STATIC_LASTMOD = "2026-03-15"` figé alors que les templates ont bougé (tarifs 09/08). `:1155-1163` les articles utilisent `publish_date` : les 16 articles rafraîchis le 09/08 gardent leur date d'origine. `:1136` `feed.xml` n'est pas une page, à retirer du sitemap. Correctif : `lastmod = updated_date or publish_date`, dates statiques issues du mtime git du template, feed hors sitemap.

**M7. Lien Selectra sans `rel`.** `dashboard.html:433` lien « cette page de Selectra » en dofollow, alors que `docs/strategy/selectra-partnership.md` documente un échange de liens avec 4 sites (risque « link scheme » évalué modéré à fort). Correctif : `rel="sponsored nofollow noopener"` ou supprimer.

**M8. Allégations non vérifiables ou incohérentes (confiance, E-E-A-T, citations IA).** « Déjà plus de 2 500 foyers » (`dashboard.html:401`, `alertes.html:123`, `_subscribe_modal.html:50`) : à relier à `/api/users/stats` ou à retirer. « Seul site en France » et « Précision mesurée : 83 % sur J+2 à J+5 » (`app.py:1197-1198,1239,1328`) : CLAUDE.md attribue 83,1 % au F1 (précision 85,4 %, exactitude 94,1 %), donc le libellé est faux ; le chiffre sera repris tel quel par les LLM. « 5x plus cher » (`:290`) vs « 4,4 fois » (`:428`) ; « jusqu'à 65 € par jour rouge » (`:354`) vs 14 € dans le HowTo (`:137`) et « 10 à 20 € » (`:431`). Correctif : une constante par chiffre, calculée depuis la grille (0,7295 / 0,1654 = 4,41).

**M9. E-E-A-T faible.** Auteur des articles = `Organization` (`blog_article.html:57`), aucune personne, aucun `Person`. Les 22 articles ont 0 lien externe (aucune source officielle citée alors que la règle interne en exige 2). `a_propos` ne nomme personne. `legal.html` (§1) n'identifie ni éditeur ni hébergeur (obligation LCEN, à valider par @legal). Correctif : page auteur avec `Person` (ou, à défaut, éditeur identifié), lien vers l'arrêté et la page CRE/EDF dans chaque article tarifaire, `sameAs` dans Organization.

**M10. Maillage : 6 orphelins, clusters déséquilibrés.** 0 lien entrant depuis un autre article : `ballon-eau-chaude-tempo`, `climatisation-tempo-edf-ete`, `premiers-jours-blancs-tempo`, `recharge-vehicule-electrique-tempo`, `rentree-tempo-2026-2027`, `souscrire-tempo-edf-guide` (et `calendrier-tempo-2026-2027-dates`, non publié). Cluster `preparation` = 1 article (viole « pilier ≥ 3 clusters »), `equipements` = 6 articles sans pilier (« à créer » dans le prompt), 3 slugs cités dans `.claude/seo-agent-prompt.md` n'existent pas. `blog_article.html:184-189` n'offre que 3 liens génériques : pas de bloc « pilier + articles du cluster ». Correctif : bloc `related` par cluster dans le template (pilier en premier), liste de l'index groupée par cluster, fusionner `preparation` dans `tempo-guide`, désigner un pilier `equipements` (créer `domotique-tempo-...`).

**M11. Fraîcheur artificielle.** 16 articles portent `updated_date: 2026-08-09` (commit tarifaire du jour) ; `jours-rouges-tempo-guide` a encore « guide complet 2025-2026 » dans le title. Une date de modification sans changement de fond est un risque. Correctif : ne bumper que sur modification substantielle, et passer le title de `jours-rouges-tempo-guide` en « 2026-2027 » et revoir les mentions « 2025-2026 » restées dans le corps de `alerte-jour-rouge-tempo`, `premiers-jours-blancs-tempo` et `tempo-edf-2026-guide-complet` (2 occurrences chacun).

**M12. Incohérence promesse d'alerte : « 5 » vs « 7 jours ».** `blog_article.html:178`, `blog_index.html:117`, `alertes.html:170`, `calendrier.html:119,292,321`. La modale annonce 7 jours. Aligner (CLAUDE.md le prévoit).

## 4. Constats [MINEUR]

- `blog_index.html` : « Un nouvel article chaque mardi » est faux (bimensuel hors saison). Liste plate de 22 articles, sans regroupement ni pagination.
- Redirections : `/calendrier/` redirige en 307 (redirect_slashes de Starlette) au lieu de 301 ; aucun redirect apex vers `www` ni http vers https dans le code (dépend de l'infra, voir section 5) ; `/blog` et `/blog/` servent tous deux 200, canonical correct (`blog_index.html:12`).
- `@app.get` ne répond pas à HEAD (405 probable) : `/health` seul l'accepte (`app.py:1442`). Ajouter `api_route(methods=["GET","HEAD"])` sur les pages publiques.
- `dashboard.html:58` : `SearchAction` vers `/blog/?q=`, mais aucune recherche n'existe (`app.py:772-781`) : supprimer. `BreadcrumbList` à 1 seul élément sur la home : supprimer.
- `hreflang` fr + x-default auto-référencés sur un site mono-langue (toutes les pages) : inutile, à retirer pour alléger.
- `Organization.logo` = favicon 192 px (`dashboard.html:89`) : fournir un logo carré dédié (Knowledge Panel).
- Une seule image OG (`og-image.png`, 1200x630, valide) pour toutes les pages : générer une carte par article.
- Doublon Article : microdata `itemscope` (`blog_article.html:156`) en plus du JSON-LD, sans `author` : retirer les microdata.
- `blog.py` : `lru_cache` importé (l. 16) mais non utilisé ; chaque requête sur sitemap, llms.txt, llms-full.txt, feed.xml relit et convertit les 22 fichiers Markdown. Cache par mtime.
- `feed.xml` : `pubDate` fixé à `00:00:00 +0100` (`app.py:1358`), fuseau non tenu l'été. `date.today()` en fuseau serveur dans `blog.py` (publication décalée de 1 à 2 h par rapport à Paris).
- Polices : Google Fonts Inter 5 graisses en CSS externe bloquant (toutes les pages). Auto-héberger un woff2 variable. `/static/` en cache 1 h sans hash de version : ajouter `?v=` ou hash de contenu. CSS 38,8 Ko et JS 30,7 Ko minifiés : corrects. Aucune image de contenu, donc pas de lazy-loading à traiter.
- `/api/indexnow/ping` (`app.py:1395`) est public, sans authentification, et la clé par défaut est en clair dans le repo : protéger par `verify_admin`, définir `INDEXNOW_KEY` en secret.
- Meta descriptions : 8 sur 22 sous 140 caractères (111 à 133), la règle interne vise 140-155 ; `souscrire-tempo-edf-guide` en fait 159.
- `validate_article.py` ne contrôle ni les liens cassés, ni la présence de sources externes, ni l'année dans le title, ni la longueur du title (50-65) et de la description (140-155 selon CLAUDE.md, 50-160 dans le script), ni l'incohérence tarifaire. Les 22 articles passent avec 0 erreur ; 11 articles seulement portent sur le mot-clé principal absent du title ou de la description (`alerte-jour-rouge-tempo`, `calendrier-tempo-2025-2026-dates`, `fin-saison-rouge-tempo-bilan`, `linky-tempo-suivre-consommation`, `pompe-a-chaleur-tempo-edf`, `preparer-saison-tempo-2026-2027`, etc.).
- `premiers-jours-blancs-tempo.md:26` contient une phrase confuse (« sauf si c'est l'un des rares jours bleus déjà prévus, ce qui revient au même »). Réécrire.
- CLAUDE.md est périmé sur ce point : 22 articles (et non 8), pas d'AggregateRating (retiré, test `test_qa_fixes.py:1139`), FAQ home à 9 questions et non 7.

## 5. À vérifier en ligne (méthode)

1. **Indexation** : Search Console > Pages : statut des 22 articles, de `/calendrier`, et absence de `/blog` vs `/blog/` en doublon. Bing Webmaster Tools : sitemap soumis, URL Inspection sur `/` et un article.
2. **Requêtes réelles** : Search Console > Performances, filtre requête contient « tempo » : positions et impressions pour les 16 requêtes cibles, séparément mobile/desktop, sur 3 mois ; comparer « edf tempo » vs « tempo edf ». Aucun chiffre n'est avancé ici.
3. **Cohérence entre instances** : `curl -s https://www.calendrier-tempo.fr/sitemap.xml | grep -c '<loc>'` répété 10 fois (instances différentes ?) et comparer avec `ls articles/*.md` publiés.
4. **Redirections** : `curl -sI http://calendrier-tempo.fr/`, `https://calendrier-tempo.fr/`, `http://www…`, `https://www…/calendrier/`, l'URL `*.replit.app` : attendu 301 vers `https://www.calendrier-tempo.fr/…` ; `curl -sI` sur une page publique pour vérifier HEAD. Prévoir la même règle dans Cloudflare après migration.
5. **Robots et en-têtes** : `curl -s /robots.txt` (pas de cache périmé), `Cache-Control` et `Content-Language` sur `/blog/<slug>`, absence de `X-Robots-Tag` parasite.
6. **Données structurées** : Rich Results Test et validator.schema.org sur `/`, `/calendrier` et 3 articles dont `fin-saison-rouge-tempo-bilan` (FAQ multi-lignes).
7. **Core Web Vitals réels** : Search Console > Signaux Web essentiels et PageSpeed Insights sur `/`, `/calendrier` et un article (mobile) : LCP avec chargement Google Fonts, CLS du bloc SSR/JS (compteurs, « chargement… » du taux de fiabilité).
8. **Facebook Debugger et LinkedIn Inspector** sur `/` et un article (og:image 1200x630).
9. **Preuves de la home** : nombre réel d'abonnés actifs (base) avant de garder « 2 500 » ; précision J+2 à J+5 par la route `/api/performance` avant de citer 83 %.
10. **Tarifs** : `docs`/FAQ affirment « arrêté du 29/07/2026, JO du 31/07/2026 » et l'abonnement 189,60 €/an marqué « non confirmé » dans `_seo_rules.yaml` : vérifier sur Légifrance/EDF, ne pas publier l'abonnement sans source.
11. **Backlinks et lien Selectra** : Ahrefs/Bing Webmaster > Backlinks pour évaluer le risque avant de retirer le lien.
12. **Pipeline prod** : logs `[Agent SEO]` des mardis de septembre (clé présente ? push réussi ?), et `GET /admin/agent-reports`.

## 6. Calendrier éditorial proposé (octobre 2026 à mars 2027)

Règles : un mardi = un item ; chaque article neuf embarque un élément first-hand tiré des données du projet (`actuals`, `predictions`, `/api/performance`, .ics du repo) : c'est le garde-fou « scaled content abuse ». Les articles « bilan » se rédigent le jour même avec les chiffres réels du mois, jamais avant. Passer `SEO_SEASON_SCHEDULE` d'octobre en `weekly` (`config.py:258-271`). Réserve : cadence de 22 articles neufs ou refresh sur 26 semaines, justifiée par la saison et non par la capacité ; sauter un item si le sujet est déjà couvert.

| Mardi | Slug | Titre proposé | Mot-clé | Cluster | Type / first-hand |
|---|---|---|---|---|---|
| 2026-09-30 (hors mardi) | calendrier-tempo-2026-2027-dates | (existant) Calendrier Tempo 2026-2027 : toutes les dates et couleurs | calendrier tempo 2026-2027 | calendrier | avancer + corriger (C3) |
| 2026-10-06 | tempo-edf-2026-guide-complet | REFRESH : Tempo EDF 2026-2027 : le guide complet pour les abonnés | tempo edf | tempo-guide (pilier) | refresh, sources officielles |
| 2026-10-13 | jours-rouges-tempo-guide | REFRESH : Jours rouges Tempo EDF : le guide complet 2026-2027 | jours rouges tempo | jours-rouges (pilier) | refresh, répartition réelle 3 saisons |
| 2026-10-20 | premier-jour-rouge-tempo-date | Premier jour rouge Tempo : à quelle date arrive-t-il ? | premier jour rouge tempo | jours-rouges | dates réelles des 3 saisons (.ics) |
| 2026-10-27 | preparer-saison-tempo-2026-2027 | REFRESH : checklist avant le 1er novembre | préparer saison tempo | tempo-guide | refresh, liens vers les nouveaux articles |
| 2026-11-03 | comment-edf-choisit-jours-rouges-tempo | Comment sont choisis les jours rouges Tempo : consommation et météo | comment sont choisis les jours tempo | jours-rouges | méthode du site (`ALGORITHME_PREDICTION.md`) |
| 2026-11-10 | temperature-jour-rouge-tempo | À quelle température tombe un jour rouge Tempo ? | température jour rouge tempo | jours-rouges | températures des jours rouges passés (DB) |
| 2026-11-17 | domotique-tempo-home-assistant | Domotique et Tempo : automatiser sa maison selon la couleur du jour | tempo home assistant | equipements (pilier) | exemple avec l'API du site, testé |
| 2026-11-24 | jours-rouges-consecutifs-tempo | Jours rouges consécutifs Tempo : jusqu'à 5 d'affilée | jours rouges consécutifs tempo | jours-rouges | séquences réelles (.ics) |
| 2026-12-01 | bilan-tempo-novembre-2026 | Bilan Tempo novembre 2026 : jours rouges et blancs du mois | bilan tempo novembre | calendrier | données `actuals` du mois |
| 2026-12-08 | tempo-noel-nouvel-an | Tempo à Noël et au Nouvel An : quelle couleur les jours de fêtes ? | tempo noël | calendrier | fériés (R2), historique réel |
| 2026-12-15 | cuisiner-jour-rouge-tempo | Cuisiner un jour rouge Tempo : four, plaques, astuces | cuisson jour rouge tempo | equipements | calculs sur la grille du 01/08/2026 |
| 2026-12-22 | tempo-vacances-absence | Partir en vacances avec Tempo : chauffe-eau et chauffage | tempo vacances | equipements | calculs sur la grille |
| 2026-12-29 | fiabilite-previsions-tempo | Prévisions Tempo : bilan chiffré de la fiabilité à J+2 à J+5 | prévision jour rouge tempo | tempo-guide | `/api/performance` réel |
| 2027-01-05 | bilan-tempo-decembre-2026 | Bilan Tempo décembre 2026 | bilan tempo décembre | calendrier | `actuals` |
| 2027-01-12 | panneaux-solaires-tempo | Panneaux solaires et Tempo : autoconsommation, que faire les jours rouges | panneaux solaires tempo | equipements | à sourcer (formules) |
| 2027-01-19 | janvier-mois-rouge-tempo | Pourquoi janvier concentre les jours rouges Tempo | jours rouges janvier | jours-rouges | .ics : 9 et 13 rouges en janvier |
| 2027-01-26 | thermostat-radiateurs-connectes-tempo | Thermostat et radiateurs connectés : piloter selon la couleur Tempo | thermostat connecté tempo | equipements | scénarios chiffrés |
| 2027-02-02 | bilan-tempo-janvier-2027 | Bilan Tempo janvier 2027 | bilan tempo janvier | calendrier | `actuals` |
| 2027-02-09 | economies-tempo-mi-saison | Tempo à mi-saison : combien avez-vous économisé ? | économies tempo | tempo-guide | rouges réels vs simulation |
| 2027-02-16 | jours-rouges-restants-tempo | Combien de jours rouges peut-il rester en février et mars ? | jours rouges restants tempo | jours-rouges | mécanique du budget, compteur réel |
| 2027-02-23 | tarifs-tempo-fevrier-2027 | Tarifs Tempo février 2027 : ce qui change | tarif tempo edf | tempo-guide | uniquement si une révision officielle est publiée, sinon sauter |
| 2027-03-02 | bilan-tempo-fevrier-2027 | Bilan Tempo février 2027 | bilan tempo février | calendrier | `actuals` |
| 2027-03-09 | jours-blancs-fin-hiver-tempo | Jours blancs en fin d'hiver : comment EDF écoule le solde | jours blancs tempo mars | jours-rouges | .ics : 7 blancs en avril 2024 |
| 2027-03-16 | tempo-edf-avis-rentabilite | REFRESH : Tempo EDF avis, rentable ? (bilan de l'hiver) | tempo edf avis | tempo-guide | refresh, données réelles |
| 2027-03-23 | rester-chez-tempo-apres-hiver | (facultatif) Faut-il rester chez Tempo après l'hiver ? | quitter tempo edf | tempo-guide | seulement si le mot-clé n'entre pas en conflit avec la ligne du 16/03 |
| 2027-03-30 | fin-saison-rouge-tempo-bilan | REFRESH : fin de saison rouge 2026-2027 (ce qui change le 1er avril) | fin saison rouge tempo | jours-rouges | refresh, dates réelles |

Après chaque publication : ajouter l'item dans le calendrier YAML, le journal, envoyer IndexNow sur l'URL, lier depuis 2 à 3 articles du même cluster (bloc « articles liés »), valider avec `validate_article.py` étendu (voir M2 et mineurs). Suivi : impressions, positions et présence dans les réponses IA (@geo), pas uniquement les clics (les AI Overviews réduisent le CTR informationnel).

## 7. Handoff

**Handoff → @fullstack** (puis @geo pour les points marqués)
- Fichier produit : `/home/user/Tempo/docs/audits/2026-09-29-audit-seo-blog.md`.
- Décisions : home = « Tempo EDF » (couleur du jour), `/calendrier` = « Calendrier Tempo EDF {saison} » ; pages à créer : tarif, couleur du jour/demain, saisons, mois ; cocon `tempo-guide`, `jours-rouges`, `calendrier`, `equipements` (pilier à créer), `preparation` fusionné.
- Points d'attention : FAQ home visible vs JSON-LD (une source), `|tojson` dans `blog_article.html`, persistance des articles de l'agent, sitemap lastmod réels, `rel` sur le lien Selectra, libellé « 83 % » dans llms.txt (@geo), mentions légales (@legal), constantes chiffrées uniques.
- Références SERP : non consultées (WebSearch non exploité dans cette session), à ajouter avant la rédaction des pages de la section M4.
