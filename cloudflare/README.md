# Migration Replit → Cloudflare

Runbook technique. Le pas-à-pas côté fondateur est dans le message de session du 2026-09-29 ;
ce fichier sert aux sessions Claude Code qui exécutent la migration.

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
- `copy_database.py` : copie complète Replit → Neon (à lancer dans le Shell Replit).
- `push_secrets.py` : Secrets Replit → Worker (à lancer dans le Shell Replit).
- `scripts/docker-proxy-ca.sh` : build Docker derrière le proxy TLS des sessions Claude.

## Déployer depuis une session Claude Code

Prérequis session : `CLOUDFLARE_API_TOKEN` et `CLOUDFLARE_ACCOUNT_ID` dans l'environnement ;
`api.cloudflare.com` et `registry.cloudflare.com` autorisés dans le réseau.

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

## Phase 1 : test (sans impact abonnés)

1. `wrangler deploy` (crée le Worker).
2. Fondateur, Shell Replit : `python3 cloudflare/copy_database.py` puis `python3 cloudflare/push_secrets.py`.
3. Sans `WHATSAPP_TOKEN`, l'app est en mode simulation (`statut = simulated` dans `sms_logs`) ;
   sans `ANTHROPIC_API_KEY`, les agents SEO/backlinks sont ignorés.
4. Vérifier sur `https://calendrier-tempo.<sous-domaine>.workers.dev` : pages, `/admin`,
   logs (`npx wrangler tail`), exécution des tâches 11h30 et 18h, prédictions générées.

## Phase 2 : bascule (fenêtre 13h-17h, pas un mardi ni un dimanche)

Ordre choisi pour n'avoir ni doublon d'alerte ni perte de données :
1. Fondateur : Replit → Deployments → **Stop** (plus aucune écriture ni alerte côté Replit).
2. Fondateur, Shell Replit : `python3 cloudflare/copy_database.py` (état final) puis
   `python3 cloudflare/push_secrets.py --prod`.
3. Claude : supprimer les enregistrements DNS `www` et apex qui pointent vers Replit, activer
   les deux `custom_domain` dans `wrangler.jsonc`, `wrangler deploy`.
4. Vérifier : `/health`, home, `/admin`, webhook WhatsApp (message « RECAP » de test),
   logs du scheduler. Si l'URL du webhook Meta n'est pas sur `www.calendrier-tempo.fr`, la mettre à jour.

Retour arrière : redémarrer le déploiement Replit, restaurer les enregistrements DNS.
Les données écrites côté Neon entre-temps se recopient avec `copy_database.py` en
inversant les deux variables (DATABASE_URL = Neon, NEON_DATABASE_URL = Replit).

## Limites connues (à traiter après la bascule)

- Les articles écrits par l'agent SEO vont sur le disque du conteneur, éphémère comme sur
  Replit autoscale : à persister (table Postgres ou PR GitHub).
- Un redémarrage du conteneur pile à l'heure d'une tâche la fait sauter (APScheduler sans
  `misfire_grace_time`) ; la tâche post-démarrage relance prédictions et backfill.
