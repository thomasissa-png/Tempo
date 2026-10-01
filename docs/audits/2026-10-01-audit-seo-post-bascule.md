# Audit SEO technique post-bascule Cloudflare (2026-10-01)

Périmètre : https://www.calendrier-tempo.fr en ligne, mesuré le 2026-10-01 entre 05h21 et 05h40 GMT (7h21 à 7h40 Paris), soit environ 20 minutes après la bascule de 7h02. Méthode : curl réel (HTTP/2, `Accept-Encoding: br, gzip`), crawl des 127 URL du sitemap, parsing HTML et JSON-LD. Aucun fichier du dépôt modifié, rien déployé. Données volume/difficulté de mots-clés non utilisées (audit technique, pas de keyword research).

Réserves de méthode : `project-context.md` est absent du dépôt (commandement 1). Le contexte vient du `CLAUDE.md` du projet et du brief. Pas de mesure Replit d'avant la bascule : toute comparaison "avant/après" est une hypothèse, marquée comme telle. Les mesures de temps passent par le proxy de la session (latence de base d'environ 0,17 s sur les ressources statiques).

## Verdict

Aucun P0. L'indexation n'est menacée par rien : 127/127 URL du sitemap en 200 sans redirection, canonicals, metas, JSON-LD, robots.txt, sitemap et X-Robots-Tag corrects, aucune trace de `workers.dev` ou `replit` dans le contenu public. Quatre points à traiter vite (P1) : temps de réponse serveur des pages dynamiques (1,2 à 2,8 s), Content-Type des polices, balises de vérification Google/Bing absentes, chaîne de redirections du domaine nu en http.

## 1. Tableau OK / KO avec preuves

| # | Contrôle | Résultat | Preuve |
|---|---|---|---|
| 1.1 | 127 URL du sitemap : code HTTP | OK | 127 x `200`, 0 redirection, 0 erreur curl (crawl complet, `num_redirects=0` partout) |
| 1.2 | Liens internes (145 URL internes distinctes trouvées dans les 127 pages) | OK | Les 18 non listées au sitemap répondent toutes 200 (6 `/api/*`, 10 mois 2026-11 à 2027-08, `historique-previsions.csv`, `llms.txt`) |
| 1.3 | http://www → https://www | OK | `301` en 1 saut vers `https://www.calendrier-tempo.fr/` |
| 1.4 | https://apex → www | OK | `301` en 1 saut, `location: https://www.calendrier-tempo.fr/` (Worker) |
| 1.5 | http://apex → https://www sans chaîne | KO | 2 sauts : `http://calendrier-tempo.fr/` → 301 `https://calendrier-tempo.fr/` → 301 `https://www...`. Avec `/calendrier?month=11&year=2026` : 3 sauts |
| 1.6 | Slash final `/calendrier/` | OK | `301` vers `/calendrier` (1 saut) |
| 1.7 | Slash final des autres pages | KO (mineur) | `/tarif-tempo-edf/`, `/alertes/`, `/methodologie/`, `/a-propos/`, `/blog/<slug>/`, `/calendrier/2026-11/`... : `307` (temporaire) au lieu de 301. Hérité du comportement par défaut de FastAPI, pas de la bascule |
| 1.8 | Anciennes URL `?month=&year=` | OK | `?month=11&year=2026` → `301 /calendrier/2026-11` ; `?month=3&year=2027` → `301 /calendrier/2027-03`. Mois courant (`?month=10&year=2026`) : 200 avec canonical `/calendrier` (voulu, `app.py:1002-1008`) |
| 1.9 | `/blog` et `/blog/` | KO (mineur) | Les deux répondent 200, canonical commun `/blog/`. Duplication neutralisée par le canonical, pas par une redirection |
| 1.10 | 404 | OK | `/index.html`, `/calendrier/2027-09`, `/calendrier/2026-13`, `/Calendrier` : `404`, `x-robots-tag: noindex`, `<meta robots noindex, follow>`, `cache-control: no-store` |
| 2.1 | Canonical absolu, https://www, autoréférent | OK | Présent sur 127/127, 1 seul par page, égal à l'URL du sitemap partout. `/calendrier/2026-10` → canonical `/calendrier` (volontaire). Paramètres `?utm_source=...`, `?foo=bar`, `?page=2` : canonical propre |
| 2.2 | Title : présent, 30 à 65 car., unique | OK | 127/127 présents, 0 doublon, 0 hors fourchette |
| 2.3 | Meta description : présente, 70 à 160 car., unique | KO (mineur) | 127/127 présentes, 0 doublon. `/alertes` : 165 caractères (`templates/alertes.html:8`) |
| 2.4 | Un seul H1 | OK | 127/127 pages avec exactement 1 `<h1>` |
| 2.5 | hreflang | OK (absent) | 0 balise hreflang, `<html lang="fr">` sur 127/127, `content-language: fr` |
| 2.6 | Open Graph + Twitter | OK | `og:title/description/image/url/type` et `twitter:card` sur 127/127 ; `og:url` = URL de la page ; `og-image.png` = PNG 1200x630 (vérifié avec `file`), 200 |
| 2.7 | Balises de vérification Google (`google-site-verification`) et Bing (`msvalidate.01`) | KO (à vérifier) | 0 occurrence sur 127 pages. Le gabarit les rend si `GOOGLE_SITE_VERIFICATION` / `BING_SITE_VERIFICATION` existent (`app.py:308-309`, `templates/_base.html:9-10`). L'audit du 2026-09-29 les listait comme présentes. `/BingSiteAuth.xml` : 404 |
| 3.1 | robots.txt : 200, sitemap déclaré, aucun blocage involontaire | OK | 26 groupes, `Sitemap: https://www.calendrier-tempo.fr/sitemap.xml`, aucun `Disallow: /` ; seuls `/admin`, `/api/`, `/manage/` bloqués ; identique à l'instantané |
| 3.2 | Bots de recherche IA et d'entraînement non bloqués | OK | OAI-SearchBot, ChatGPT-User, Claude-SearchBot, Claude-User, PerplexityBot, Applebot, bingbot, GPTBot, ClaudeBot, Google-Extended... tous `Allow: /`. User-agents réels testés (Googlebot, bingbot, GPTBot, ClaudeBot, PerplexityBot, facebookexternalhit, Twitterbot, LinkedInBot) : 200 sur `/`, `/robots.txt`, `/sitemap.xml`, pas de blocage Cloudflare |
| 3.3 | X-Robots-Tag / meta robots sur pages publiques | OK | 0 `X-Robots-Tag` et 0 `<meta robots>` sur les 127 URL du sitemap ; présents uniquement sur 404, `/admin`, `/manage/*` (meta) et `*.workers.dev` |
| 3.4 | URL de test `calendrier-tempo.thomas-issa.workers.dev` | OK | `x-robots-tag: noindex, nofollow` sur `/`, `/robots.txt`, `/sitemap.xml`, canonical vers www (`cloudflare/worker.ts`, bloc `.workers.dev` de `fetch`) |
| 4.1 | sitemap.xml valide, toutes URL en https://www | OK | XML valide (minidom), 127 `<loc>` uniques, 0 hors `https://www.calendrier-tempo.fr`, 127/127 en 200 |
| 4.2 | lastmod réalistes et stables (exigence Bing) | OK | Valeurs : 2026-09-29 (23 : `/blog/` et les 22 articles), 2026-09-30 (99 : 7 pages statiques, 92 pages mois/saisons), 2026-10-01 (5 : `/`, `/calendrier`, `/couleur-tempo-demain`, `/historique-previsions`). Pas de régénération à "aujourd'hui" par défaut (`app.py:1566-1612`). Réserve : les pages mensuelles et saisons partagent `LLMS_CONTENT_DATE` (2026-09-30) quand aucune confirmation EDF plus récente n'existe, donc valeur stable mais pas propre à chaque page |
| 4.3 | Cohérence sitemap / site | KO (mineur) | Les mois 2026-11 à 2027-08 existent (200, canonical autoréférent, liens depuis `/calendrier/2026-2027`) mais ne sont pas au sitemap |
| 5.1 | JSON-LD : JSON valide partout | OK | 0 JSON invalide sur 127 pages |
| 5.2 | Types attendus | OK avec écart de doc | `/` : WebSite, SoftwareApplication, Organization, HowTo, ItemList, FAQPage. `/calendrier` : Dataset, BreadcrumbList, FAQPage. Articles : Article + BreadcrumbList (22/22). Voir écart CLAUDE.md en P2 |
| 5.3 | FAQ visible = FAQPage | OK | 109 FAQPage, questions et réponses retrouvées dans le HTML visible (comparaison normalisée) ; home 12 questions, `/calendrier` 6 |
| 5.4 | BreadcrumbList avec `item` | OK | 126 listes, 0 élément sans `item`, 0 URL hors `https://www.calendrier-tempo.fr` |
| 5.5 | Organization.logo en home | OK | `ImageObject` `/static/favicon-192.png` 192x192 (200) |
| 5.6 | URL `http://` ou externes parasites dans les JSON-LD | OK | 0 `http://` ; seules URL externes : licence creativecommons.org |
| 6.1 | Compression pages | OK | `content-encoding: br` sur 127/127 pages HTML, `vary: Accept-Encoding` |
| 6.2 | Compression assets | OK | gzip sur CSS, JS, robots, sitemap, feed, llms, manifest, ICO |
| 6.3 | Content-Type et charset pages et fichiers texte | OK | `text/html; charset=utf-8`, `application/xml; charset=utf-8`, `text/plain; charset=utf-8`, `application/rss+xml; charset=utf-8` |
| 6.4 | Content-Type des polices | KO | `/static/fonts/inter-latin-wght-normal.woff2` : `content-type: text/plain; charset=utf-8` + `content-encoding: gzip` (48 254 octets) au lieu de `font/woff2` sans compression |
| 6.5 | Cache-Control pages | OK | `/` 300 s + swr 600 ; `/calendrier` 600 s ; `/couleur-tempo-demain` 120 s ; `/tarif-tempo-edf` 3600 s ; 404 `no-store` |
| 6.6 | Cache-Control assets | OK (améliorable) | `/static/*` : `max-age=3600, swr=86400`, ETag + Last-Modified ; `/robots.txt`, `llms*.txt`, `manifest.json` : 86400 |
| 6.7 | En-têtes de sécurité | OK | HSTS (1 an, includeSubDomains), nosniff, X-Frame-Options SAMEORIGIN, Referrer-Policy, Permissions-Policy, HTTP/3 annoncé |
| 6.8 | Temps de réponse serveur (TTFB) | KO | Mesuré 2 à 3 fois par page, base réseau 0,17 s : `/` 1,24 à 2,83 s ; `/calendrier` 1,6 à 1,7 s ; `/couleur-tempo-demain` 2,3 s ; `/calendrier/2026-11` 1,87 s ; `/tarif-tempo-edf` et `/blog/` 0,17 s puis 1,06 s. Statique 0,17 à 0,20 s. Aucun `cf-cache-status` : Cloudflare ne met pas le HTML en cache |
| 7.1 | Mots-clés cibles : `/` | OK | Title "Tempo EDF : couleur du jour, demain et prévisions EDF Tempo" ; H1 "Couleur Tempo EDF aujourd'hui, demain et prévisions à 15 jours" ; 1er paragraphe "Calendrier Tempo EDF" ; "tempo edf" x14, "edf tempo" x9 |
| 7.2 | `/calendrier` | OK | Title "Calendrier Tempo EDF 2026-2027 : jours rouges, blancs et bleus" ; H1 "Calendrier Tempo EDF 2026-2027" ; 1er paragraphe "Le calendrier Tempo EDF 2026-2027". "edf tempo" x1 seulement |
| 7.3 | `/couleur-tempo-demain` | OK | Title "Couleur Tempo demain : EDF Tempo, couleur du lendemain" ; H1 "Couleur Tempo EDF de demain" ; 1er paragraphe donne la couleur de demain. "couleur du jour" x0 |
| 7.4 | `/tarif-tempo-edf` | OK | Title "Tarif Tempo EDF 2026 : prix du kWh rouge, blanc et bleu" ; H1 "Tarif Tempo EDF : prix du kWh au 1er août 2026" ; 1er paragraphe répond avec les 3 prix. "edf tempo" x0 |
| 8.1 | URL `workers.dev` / `replit` / `localhost` / http dans HTML, sitemap, robots, feed, llms | OK | 0 occurrence sur 127 pages et 5 fichiers texte |
| 8.2 | feed.xml, llms.txt, llms-full.txt | OK | feed.xml XML valide, 20 items, `atom:link` en https://www ; llms*.txt 200, 0 URL hors www |
| 8.3 | IndexNow | Non vérifiable | `INDEXNOW_KEY` est dans `cloudflare/push_secrets.py:44` ; la clé par défaut `/calendrier-tempo-indexnow-key.txt` répond 404, ce qui est cohérent avec une clé personnalisée en secret. Le fichier clé ne peut pas être testé sans la clé |
| 8.4 | IP réelle / en-têtes proxy | OK | Les 307 renvoient des `Location` en `https://www...` (schéma et hôte corrects derrière le Worker) ; canonicals construits depuis `SITE_URL`, pas depuis l'hôte reçu |
| 8.5 | Stabilité | À surveiller | 1 requête sur environ 200 a expiré à 20 s (`/calendrier-tempo-indexnow-key.txt`, non reproduit sur 6 essais suivants). Probable à-coup du conteneur ou du proxy |

## 2. Corrections priorisées

Aucun P0 (rien ne bloque l'indexation ni ne casse le référencement acquis). Les lignes citées sont celles de la version commitée (HEAD `3fb8d63`).

### P1 (à traiter cette semaine, la saison rouge démarre le 1er novembre)

| # | Problème | Où | Correction proposée |
|---|---|---|---|
| P1-1 | TTFB de 1,2 à 2,8 s sur les pages SSR dynamiques (`/`, `/calendrier`, `/couleur-tempo-demain`, mois), contre 0,17 s sur les ressources statiques. Seuil Google "bon" : 800 ms. Cloudflare ne met pas le HTML en cache (aucun `cf-cache-status`), donc chaque visite et chaque passage de bot interroge le conteneur puis Neon. [HYPOTHÈSE : plusieurs requêtes SQL séquentielles vers Neon par rendu, latence réseau conteneur/Neon supérieure à celle de Replit ; pas de mesure d'avant bascule pour le confirmer.] | `app.py:503` (`_get_ssr_data`), `app.py:685-687` (home), `app.py:1121` (demain), `app.py:780` (`_get_calendrier_data`) ; `cloudflare/worker.ts` | 1) Cache mémoire TTL 60 s des données SSR et du HTML rendu côté Python. 2) Cache de bord dans le Worker en respectant les `Cache-Control` déjà posés (`app.py:358-402`). Le working tree contient déjà une version non commitée de `worker.ts` avec `serveWithEdgeCache` : elle n'est pas déployée (mesure faite à 05h40 GMT, aucun en-tête de cache). Précautions : clé de cache sans query string, jamais de cache des 4xx/5xx ni de `/api/*` ni de `/admin`, `/manage`, purge ou TTL court sur `/couleur-tempo-demain` (change vers 12 h). Re-mesurer ensuite avec les mêmes URL |
| P1-2 | Polices servies en `text/plain` + gzip. Cause probable : l'image `python:3.12-slim` n'a pas de `/etc/mime.types`, `.woff2` est inconnu. Spécifique à la migration (Replit avait un système avec table MIME) | `app.py:304` (`app.mount("/static", StaticFiles...)`), `app.py:303` (GZip), `main.py:181` (`mimetypes.guess_type` du proxy de démarrage, renverrait `application/octet-stream`), `Dockerfile:1` | Avant le montage statique : `mimetypes.add_type("font/woff2", ".woff2")` (idem `.webmanifest`/`.svg` si besoin) dans `app.py` et `main.py`. Exclure `font/woff2` de la compression. Ajouter `Cache-Control: public, max-age=31536000, immutable` pour `/static/fonts/` (nom de fichier versionné par le contenu) |
| P1-3 | Balises de vérification Google et Bing absentes des 127 pages. Si les propriétés Search Console / Bing Webmaster Tools reposent sur la balise meta, elles seront dé-vérifiées au prochain contrôle (perte de suivi du sitemap, de la couverture et de IndexNow côté Bing) | Variables `GOOGLE_SITE_VERIFICATION`, `BING_SITE_VERIFICATION` (`app.py:308-309`, `templates/_base.html:9-10`), `cloudflare/push_secrets.py:44` | Vérifier aujourd'hui dans Search Console et Bing Webmaster Tools que les deux propriétés sont toujours "vérifiées". Si elles tiennent par balise : renseigner les deux secrets puis `push_secrets.py --prod`. Mieux : vérification par enregistrement DNS TXT dans la zone Cloudflare (indépendante de l'hébergement). Resoumettre ensuite `sitemap.xml` dans les deux outils |
| P1-4 | Chaîne de redirections du domaine nu en http : `http://apex` → `https://apex` → `https://www` (2 sauts, 3 avec `?month=&year=`). Les liens externes et anciennes backlinks vers l'apex en http en subissent la perte de vitesse de crawl | `cloudflare/worker.ts` (bloc « Domaine nu → www », lignes 94-97 de HEAD), réglage "Always Use HTTPS" de la zone | Règle de redirection Cloudflare unique (Redirect Rule) : `http(s)://calendrier-tempo.fr/*` → `https://www.calendrier-tempo.fr/$1`, 301, en conservant la query string, évaluée avant "Always Use HTTPS" ; ou forcer `url.protocol = "https:"` dans le Worker et désactiver le saut HTTPS pour l'apex. Retester : `curl -sIL http://calendrier-tempo.fr/` doit montrer 1 seul `301` |

### P2 (finitions et confort)

| # | Problème | Où | Correction proposée |
|---|---|---|---|
| P2-1 | Slash final : 307 au lieu de 301 sur toutes les pages sauf `/calendrier/` | `app.py:1049-1052` (seul cas traité), routes FastAPI par défaut | Middleware ou `redirect_slashes` personnalisé : 301 vers l'URL sans slash (hors `/blog/`, voir P2-2) |
| P2-2 | `/blog` et `/blog/` répondent tous deux 200 (canonical `/blog/`) | `app.py:1267-1273` | Rediriger `/blog` en 301 vers `/blog/` (URL du sitemap et du canonical) |
| P2-3 | Meta description de `/alertes` : 165 caractères (coupée dans la SERP) | `templates/alertes.html:8` | Ramener à 150-155 caractères en gardant "Alertes Tempo EDF gratuites par WhatsApp" au début |
| P2-4 | Pages mensuelles futures (2026-11 à 2027-08) : 200, indexables, liées, absentes du sitemap, pages "pas encore prévu" quasi vides pour la plupart | `app.py:1606-1611` (boucle des mois jusqu'au mois en cours) | Ajouter novembre à mars 2027 au sitemap dès que des prévisions existent (le pic de recherche est le 1er novembre) ; sinon `noindex` sur les mois sans donnée, pour éviter le contenu mince |
| P2-5 | Googlebot est dans le groupe `User-agent: *`, qui bloque tout `/api/`. Le JS de la home appelle `/api/today` etc., et le `Dataset` de `/calendrier` déclare `/api/history?days=365` comme `contentUrl`. Le SSR garantit le contenu, mais le rendu Google ne verra pas les mises à jour JS et la distribution du Dataset est inaccessible à Google | `app.py:1480-1481` (groupe `*`) ; `templates/calendrier.html:64` | Vérifier une fois dans Search Console, Inspection d'URL, que le rendu de `/` montre le SSR. Option : ajouter les 6 `Allow` des endpoints publics au groupe `*` (cohérent avec CLAUDE.md, qui annonce l'exception pour tous les groupes), ou remplacer `contentUrl` par un fichier crawlable (CSV `/historique-previsions.csv`) |
| P2-6 | Aucun `ETag` ni `Last-Modified` sur le HTML : pas de réponse 304 pour Googlebot/Bingbot (crawl plus coûteux, mauvais signal de fraîcheur Bing) | Middleware `app.py:405-430` | ETag faible calculé sur le corps rendu, 304 sur `If-None-Match` ; en lien avec P1-1 |
| P2-7 | Cache des assets statiques : 1 h seulement. `style.min.css` et `app.min.js` sont versionnés par `?v=` | `app.py:363` | `max-age=31536000, immutable` pour les URL avec `?v=` ; 1 h pour le reste |
| P2-8 | lastmod des 92 pages mois/saisons = date de révision du site (2026-09-30) faute de confirmation plus récente : stable mais pas propre à chaque page | `app.py:1596-1611`, `site_facts.py:137` | Acceptable. Ne pas passer à "date du jour". À affiner quand les pages de mois gagneront des données propres |
| P2-9 | Écart de documentation : CLAUDE.md annonce `AggregateRating` et `BreadcrumbList` en home. En réalité absents (volontairement : l'audit du 2026-09-29 a retiré un `AggregateRating` factice, et un fil d'Ariane sur l'accueil n'a pas de sens) | `CLAUDE.md` (section Structured Data) | Mettre CLAUDE.md à jour. Ne pas réintroduire `AggregateRating` sans avis réels |
| P2-10 | Hors bascule, à noter : auteur des 22 articles = `Organization` (pas de `Person`, signal E-E-A-T faible) ; variantes exactes "edf tempo calendrier" et "tempo edf couleur du jour" absentes de `/calendrier` et de `/` ; `Dataset` sans `license` sur 11 des 97 pages | `templates/blog_article.html`, `templates/calendrier.html`, `templates/dashboard.html` | Backlog contenu (coordination @copywriter) |

### Points à surveiller dans les 7 jours (sans action de code)
- Search Console et Bing Webmaster : erreurs de couverture, "Introuvable (404)", "Erreur serveur (5xx)", temps de réponse moyen de l'exploration, état de la propriété (P1-3).
- Un délai d'expiration isolé de 20 s observé sur 1 requête (non reproduit) : surveiller les journaux du Worker (`[keepalive]`) et le redémarrage du conteneur.
- IndexNow : fichier clé non testable sans la valeur du secret ; confirmer dans Bing Webmaster que les soumissions sont acceptées.

## 3. Handoff

**Handoff → @fullstack (puis @orchestrator)**
- Fichier produit : `/home/user/Tempo/docs/audits/2026-10-01-audit-seo-post-bascule.md` (aucun autre fichier touché, rien déployé).
- Décisions : migration saine pour l'indexation (127/127 URL en 200, canonicals, JSON-LD, robots, sitemap OK, workers.dev en noindex) ; 0 P0, 4 P1 (TTFB 1,2 à 2,8 s, MIME des polices, balises de vérification Google/Bing, chaîne http apex), 10 P2.
- Points d'attention : `worker.ts` a un diff non commité (edge cache) non déployé au moment de la mesure ; vérifier Search Console/Bing aujourd'hui ; pages à double enjeu SEO+GEO : `/`, `/calendrier`, `/api-tempo` (robots `/api/`) à coordonner avec @geo ; référence SERP concurrentes non consultée (audit technique uniquement).
