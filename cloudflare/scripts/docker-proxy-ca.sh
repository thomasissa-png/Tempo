#!/usr/bin/env bash
# Enveloppe docker pour les sessions Claude Code (proxy TLS) :
#   WRANGLER_DOCKER_BIN=cloudflare/scripts/docker-proxy-ca.sh npx wrangler deploy
# Ajoute le certificat du proxy aux "docker build". Sans proxy : docker inchangé.
CA="/root/.ccr/ca-bundle.crt"
if [ "${1:-}" = "build" ] && [ -f "$CA" ]; then
  shift
  exec docker build --secret "id=proxy_ca,src=$CA" "$@"
fi
exec docker "$@"
