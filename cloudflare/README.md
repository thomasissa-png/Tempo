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
- `scripts/docker-proxy-ca.sh` : build Docker derrière le proxy TLS des sessions Claude.

## Déployer depuis une session Claude Code

Prérequis (réglages de l'environnement Claude, posés par le fondateur) :
- Variables : `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `NEON_DATABASE_URL`,
  `REPLIT_DATABASE_URL` (base de PRODUCTION Replit) et tous les Secrets Replit tels quels
  (`PHONE_ENCRYPTION_KEY`, `ADMIN_PASSWORD`, `WHATSAPP_*`, `RTE_*`, `METEOFRANCE_*`, `ANTHROPIC_API_KEY`...).
  `tests/conftest.py` retire les variables sensibles : les tests ne touchent jamais ces bases ni WhatsApp.
- Réseau autorisé : `api.cloudflare.com`, `registry.cloudflare.com`, `*.workers.dev`, `*.neon.tech`,
  `calendrier-tempo.fr`, `www.calendrier-tempo.fr`, et l'hôte de `REPLIT_DATABASE_URL` s'il n'est pas en neon.tech.

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

## Phase 2 : bascule (fenêtre 13h-17h, pas un mardi ni un dimanche)

Ordre choisi pour n'avoir ni doublon d'alerte ni perte de données :
1. Fondateur : Replit → Deployments → **Stop** (plus aucune écriture ni alerte côté Replit).
   La base Replit reste lisible après l'arrêt.
2. Claude : `copy_database.py` (état final) puis `push_secrets.py --prod`. Le Worker détecte le
   changement de secrets et redémarre le conteneur (empreinte dans `worker.ts`).
3. Claude : supprimer les enregistrements DNS `www` et apex qui pointent vers Replit, activer
   les deux `custom_domain` dans `wrangler.jsonc`, `wrangler deploy`.
4. Vérifier : `/health`, home, `/admin`, `GET /admin/whatsapp-diagnostic` (Bearer `ADMIN_PASSWORD`)
   doit renvoyer `token_set: true`, webhook WhatsApp (message « RECAP » de test), logs du
   scheduler. L'URL du webhook Meta reste `https://www.calendrier-tempo.fr/...` : rien à changer chez Meta.

Retour arrière : redémarrer le déploiement Replit, restaurer les enregistrements DNS.
Les données écrites côté Neon entre-temps se recopient avec `copy_database.py` en
inversant les deux variables (REPLIT_DATABASE_URL = Neon, NEON_DATABASE_URL = Replit).

Après 7 jours sans incident : le fondateur peut résilier Replit (garder un export avant).

## Limites connues (à traiter après la bascule)

- Un redémarrage du conteneur pile à l'heure d'une tâche la fait sauter (APScheduler sans
  `misfire_grace_time`) ; la tâche post-démarrage relance prédictions et backfill.
