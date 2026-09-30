# Migration Replit → Cloudflare

Runbook technique pour les sessions Claude Code qui exécutent la migration (décidée le
2026-09-29 : le fondateur quitte Replit). Tout se lance depuis la session : le fondateur
n'a plus à utiliser le Shell Replit. Sa seule action pendant la bascule : arrêter le
déploiement Replit.

## Architecture

| Élément | Replit (actuel) | Cloudflare (cible) |
|---|---|---|
| App FastAPI | process Replit (autoscale) | conteneur Cloudflare (même `Dockerfile`), 1 instance `basic` |
| Frontal | proxy Replit | Worker `calendrier-tempo` (`worker.ts`) |
| Base | PostgreSQL Replit | Neon PostgreSQL (même code, `DATABASE_URL`) |
| Planificateur | APScheduler dans le process | APScheduler dans le conteneur, maintenu éveillé par un cron Worker `*/5` |
| Secrets | Secrets Replit | secrets du Worker, transmis au conteneur (`APP_ENV_KEYS`) |

Invariants à ne jamais casser :
- `max_instances: 1` : deux conteneurs = deux schedulers = alertes WhatsApp en double.
- Le Worker écrase `X-Forwarded-For` avec `CF-Connecting-IP` ; le conteneur fait
  confiance à ces en-têtes (`FORWARDED_ALLOW_IPS=*`, lu par uvicorn). Sans ça, le rate
  limiting de `/api/subscribe` voit une seule IP pour tous les visiteurs.
- `PHONE_ENCRYPTION_KEY` doit être IDENTIQUE à Replit, sinon les numéros des abonnés
  sont illisibles.
- `*.workers.dev` répond avec `X-Robots-Tag: noindex` (pas de contenu dupliqué).
- Le domaine nu redirige en 301 vers `www`.

## Fichiers

- `wrangler.jsonc` : Worker + conteneur + Durable Object + cron.
- `worker.ts` : routage, en-têtes, keep-alive.
- `check_access.py` : vérifie accès et secrets, et surtout que la clé relit les numéros des abonnés.
- `copy_database.py` : copie complète Replit (`REPLIT_DATABASE_URL`) → Neon (`NEON_DATABASE_URL`).
- `push_secrets.py` : secrets de l'app (environnement de la session) → Worker.
- `merge_users.py` : abonnés modifiés sur Replit pendant la transition DNS → Neon.
- `scripts/docker-proxy-ca.sh` : build Docker derrière le proxy TLS des sessions Claude.

## Déployer depuis une session Claude Code

Prérequis (réglages de l'environnement Claude, posés par le fondateur) :
- Variables : `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `NEON_DATABASE_URL`,
  `REPLIT_DATABASE_URL` (base de PRODUCTION Replit) et tous les Secrets Replit tels quels
  (`PHONE_ENCRYPTION_KEY`, `ADMIN_PASSWORD`, `WHATSAPP_*`, `RTE_*`, `METEOFRANCE_*`, `ANTHROPIC_API_KEY`...).
  `tests/conftest.py` retire les variables sensibles : les tests ne touchent jamais ces bases ni WhatsApp.
- Réseau autorisé : `api.cloudflare.com`, `registry.cloudflare.com`, `*.workers.dev`, `*.neon.tech`,
  `calendrier-tempo.fr`, `www.calendrier-tempo.fr`, et l'hôte de `REPLIT_DATABASE_URL` s'il n'est pas en neon.tech.

Constats du 2026-09-30 (session avec les secrets du fondateur) :
- **Le port PostgreSQL 5432 est bloqué** depuis les sessions (seul le HTTPS sort). Les deux bases
  sont chez Neon (Replit = `ep-…us-east-1.aws.neon.tech`) : passer par l'API SQL HTTPS de Neon
  (`POST https://<hôte>/sql`, en-tête `Neon-Connection-String`), testée en lecture sur Replit.
  Les scripts (check, copie, fusion) doivent utiliser ce transport. Le schéma de la cible est créé
  par l'app elle-même au premier démarrage du conteneur (init_db), pas depuis la session.
- Base Replit en **schéma v23** : copie compatible (migrations v24-v25 additives).
- Noms refusés par l'environnement de la session (ex. `ANTHROPIC_API_KEY`, que Claude Code prendrait
  pour lui) : le fondateur les range sous `TEMPO_<NOM>` ; `push_secrets.py` les renvoie au Worker
  sous leur vrai nom.
- Jeton du 2026-09-30 : lecture Workers OK, **conteneurs 403** (droit Containers manquant ou
  forfait Workers Paid inactif), sous-domaine workers.dev 403.
- Domaine : registrar IONOS ; traité **à la fin**. Pas de clé API IONOS : le fondateur envoie une
  capture de la page DNS IONOS du domaine (données publiques). Claude la recoupe avec le DNS public
  (DNS-over-HTTPS) pour ne rien oublier (MX, SPF, DKIM, DMARC, vérifications), crée la zone et
  les enregistrements à l'identique chez Cloudflare via l'API, puis donne au fondateur les 2 serveurs
  DNS à saisir chez IONOS (sa seule action sur le domaine).
- Jeton Cloudflare = jeton **utilisateur** (Profil > API Tokens), variable `CLOUDFLARE_Token_Value`
  à renommer `CLOUDFLARE_API_TOKEN` (sinon : lire `CLOUDFLARE_Token_Value`).

Toujours commencer par `python3 cloudflare/check_access.py` (dépendances : `pip install -r requirements.txt`).

```bash
# Démon Docker + image de base (Docker Hub limite les pulls anonymes : miroir Google)
(dockerd > /tmp/dockerd.log 2>&1 &) ; sleep 5
docker pull mirror.gcr.io/library/python:3.12-slim
docker tag mirror.gcr.io/library/python:3.12-slim python:3.12-slim

cd cloudflare && npm ci && npx tsc --noEmit
WRANGLER_DOCKER_BIN=$PWD/scripts/docker-proxy-ca.sh npx wrangler deploy
```

Test local complet (sans Cloudflare) : `docker build --secret id=proxy_ca,src=/root/.ccr/ca-bundle.crt -t tempo-local .`
puis lancer avec un Postgres (`mirror.gcr.io/library/postgres:16-alpine`) ; vérifié le 2026-09-29 :
toutes les pages en 200, IP réelle transmise, canonicals en https, scheduler démarré (10 tâches).

## Phase 1 : test (sans impact abonnés, Replit continue de servir le site)

1. `check_access.py` : tout OK sauf éventuellement la zone (« pending » tant que les serveurs DNS
   ne sont pas changés chez le registrar ; bloquant pour la phase 2 seulement). Le point 5
   (numéros relus) est bloquant : sans lui, aucune copie ni bascule.
   Le point 3 doit montrer une prédiction du jour : sinon `REPLIT_DATABASE_URL` est la base de dev.
2. `wrangler deploy` (crée le Worker), `copy_database.py`, puis `push_secrets.py` (sans `--prod`).
3. Sans `WHATSAPP_TOKEN`, l'app est en mode simulation (`statut = simulated` dans `sms_logs`) ;
   sans `ANTHROPIC_API_KEY`, les agents SEO/backlinks sont ignorés.
4. Vérifier sur `https://calendrier-tempo.<sous-domaine>.workers.dev` : pages, `/admin`,
   logs (`npx wrangler tail`), `[RTE twin]` actif, prédictions générées.
5. Abaisser à 60 s le TTL des enregistrements DNS `www` et apex (toujours vers Replit) : la
   bascule se propagera en une minute au lieu de cinq.

## Phase 2 : bascule SANS COUPURE (fenêtre 13h-17h, pas un mardi ni un dimanche)

Décision fondateur : le site ne doit jamais être hors ligne. Principe : Replit continue de
servir pendant toute la bascule ; on n'arrête Replit qu'une fois le trafic passé sur Cloudflare.
Entre 13h et 17h aucune tâche n'envoie d'alerte (7h30, 18h, dimanche 20h) : les deux
serveurs peuvent coexister quelques minutes sans doublon.

Prérequis : zone `active` ; TTL des enregistrements `www` et apex abaissé à 60 s (fait en phase 1,
au moins 1 h avant).
1. Claude : `copy_database.py` (Replit toujours en ligne), puis `push_secrets.py --prod`. Le Worker
   détecte le changement de secrets et redémarre le conteneur (empreinte dans `worker.ts`).
   Vérifier sur `*.workers.dev` : `GET /admin/whatsapp-diagnostic` (Bearer `ADMIN_PASSWORD`)
   renvoie `token_set: true`, pages et `/admin` OK. Au moindre doute : on s'arrête là, rien n'a changé
   pour les visiteurs.
2. Claude : remplacer les enregistrements DNS `www` et apex (Replit) par les deux `custom_domain`
   de `wrangler.jsonc`, `wrangler deploy`. Pendant la propagation (quelques minutes), une partie
   des visiteurs voit encore Replit, les autres Cloudflare : le site répond toujours.
3. Claude : vérifier que `www.calendrier-tempo.fr` répond depuis Cloudflare (`/health`, home,
   `/admin`, message « RECAP » de test au webhook WhatsApp). L'URL du webhook Meta ne change pas.
4. Après 10 minutes, avant 17h30 : fondateur, Replit → Deployments → **Stop**. Indispensable :
   sinon le scheduler Replit enverrait aussi les alertes de 18h (doublons).
5. Claude : `merge_users.py` rapatrie les inscriptions, désinscriptions et changements de
   préférences arrivés sur Replit pendant la transition (clé `phone_hash`, le plus récent gagne).

Retour arrière (avant l'étape 4) : remettre les enregistrements DNS vers Replit, qui n'a jamais
été arrêté. Après l'étape 4 : redémarrer le déploiement Replit, remettre le DNS, et recopier
Neon → Replit avec `copy_database.py` en inversant les deux variables.

Après 7 jours sans incident : le fondateur peut résilier Replit (garder un export avant).

## Limites connues (à traiter après la bascule)

- Un redémarrage du conteneur pile à l'heure d'une tâche la fait sauter (APScheduler sans
  `misfire_grace_time`) ; la tâche post-démarrage relance prédictions et backfill.
