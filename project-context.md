# Contexte projet : Calendrier Tempo EDF (calendrier-tempo.fr)

Fichier de contexte des agents. Il ne contient que des faits déjà validés par le fondateur et
consignés dans `CLAUDE.md` (sections « TempoForecast - Project Memory » et « Décisions fondateur »),
qui reste la référence détaillée. Créé le 2026-10-01 (session principale).

## Produit
- Site gratuit de prévision des couleurs EDF Tempo (bleu, blanc, rouge) jusqu'à 15 jours, avec
  alertes WhatsApp gratuites. Éditeur : ISSA Capital (mentions légales). Fondateur : Thomas Issa.
- Indicateur de succès : justesse des prévisions J+2 à J+5 ; priorité absolue au rappel des jours
  ROUGES (en manquer un coûte le plus cher à l'abonné).
- Règles EDF appliquées : rouge seulement du 1er novembre au 31 mars, jamais le week-end ni un jour
  férié ; blanc jamais le dimanche ; 5 rouges consécutifs au plus ; 22 rouges et 43 blancs par saison
  (1er septembre au 31 août).

## Persona et objectif marketing
- Persona : abonnés existants à l'offre Tempo d'EDF qui veulent anticiper les jours rouges pour
  économiser (CLAUDE.md, « SEO Target Keywords », Audience).
- Objectif : n°1 sur Google, Bing et les LLM pour les requêtes Tempo listées dans CLAUDE.md
  (« tempo edf », « calendrier tempo edf », « couleur du jour », « tarif tempo edf »…).
- Promesse : anticiper les jours rouges jusqu'à 15 jours à l'avance, gratuitement.

## Stack et hébergement
- FastAPI + Jinja2 (rendu serveur), Python ; PostgreSQL Neon (eu-central-1).
- Production sur Cloudflare depuis le 2026-10-01 7h02 : Worker `calendrier-tempo` + conteneur unique
  (scheduler APScheduler interne) ; runbook `cloudflare/README.md`. Replit arrêté.
- Analytics : Umami. Polices auto-hébergées (RGPD).

## Règles qui s'imposent à tous les agents
- Zéro donnée inventée (météo, températures, taux…) ; chiffres publics centralisés dans `site_facts.py`.
- Taux publics = dernière saison complète, calculés sur les prévisions émises.
- Ne pas toucher ni signaler : lien Selectra (dofollow), « Plus de 2 500 foyers alertés ».
- Pas de tiret cadratin dans le texte client ; vouvoiement ; sobriété (le fondateur préfère une page
  propre et aérée à une page chargée : toute revue se fait côte à côte avec la version de référence).
- Pas d'équipe humaine : calibration vélocité IA.
- Demande du fondateur risquée (juridique, SEO, véracité) : ne pas refuser ni seulement objecter ;
  revenir avec les options qui la font passer intelligemment, appliquer la meilleure et dire pourquoi
  (consigne du 2026-10-01, ex. « le seul à ne rien vendre » → « à notre connaissance » + catégorie restreinte).
