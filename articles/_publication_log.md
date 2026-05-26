# Journal de publication — Blog Calendrier Tempo EDF

> Ce fichier est mis à jour automatiquement par l'agent SEO après chaque publication.

---

## 2026-02-19 — Initialisation
- **Action** : Création du journal de publication
- **Articles existants** : 8 (3 publiés, 5 programmés)
- **Calendrier** : 10 semaines planifiées (mars-juin 2026)
- **Clusters** : 5 définis (tempo-guide, jours-rouges, calendrier, equipements, preparation)

---

## 2026-05-26 — Rattrapage du trou éditorial (avril-mai)

- **Cause du trou** : l'agent SEO autonome n'a jamais tourné depuis le lot initial du 25 février (`ANTHROPIC_API_KEY` absente → `task_seo_agent` ignorée en silence). Le blog vivait sur 8 articles pré-écrits dont le dernier est daté du 24 mars ; sans nouvel article ensuite, blog muet pendant ~2 mois.
- **Action** : publication de 6 articles backdatés pour combler le trou (hebdo jusqu'à mi-avril, puis bimensuel) :
  - 2026-03-31 — `fin-saison-rouge-tempo-bilan` (jours-rouges)
  - 2026-04-07 — `bilan-saison-tempo-2025-2026` (calendrier)
  - 2026-04-14 — `preparer-saison-tempo-2026-2027` (preparation)
  - 2026-04-28 — `tempo-edf-avis-rentabilite` (tempo-guide)
  - 2026-05-12 — `linky-tempo-suivre-consommation` (equipements)
  - 2026-05-26 — `pompe-a-chaleur-tempo-edf` (equipements)
- **Total articles** : 14 (8 + 6). Tous validés par `validate_article.py` (0 erreur).
- **Garde-fou** : le skip silencieux de `task_seo_agent`/`task_backlinks_agent` passe en `warning` (clé absente) et `error` (clé absente un jour de publication prévu) pour rendre la panne visible.
- **À faire (hors code)** : configurer `ANTHROPIC_API_KEY` dans les secrets de prod pour réactiver la génération autonome.

---

## 2026-05-26 — Pré-rédaction de la file estivale (8 articles, agents @copywriter/@seo + revue @reviewer)

- **Contexte** : clé `ANTHROPIC_API_KEY` configurée en prod. Cadence estivale activée (`SEO_SEASON_SCHEDULE` avril→août = bimensuel au lieu de off).
- **Action** : rédaction anticipée des 8 articles « neufs » planifiés (dates de publication futures, parution automatique à échéance) :
  - 2026-06-09 — `recharge-vehicule-electrique-tempo` (equipements)
  - 2026-06-23 — `simulation-tempo-edf-economies` (tempo-guide)
  - 2026-07-21 — `ballon-eau-chaude-tempo` (equipements)
  - 2026-08-04 — `climatisation-tempo-edf-ete` (equipements)
  - 2026-09-01 — `rentree-tempo-2026-2027` (tempo-guide)
  - 2026-09-15 — `souscrire-tempo-edf-guide` (tempo-guide)
  - 2026-09-29 — `premiers-jours-blancs-tempo` (jours-rouges)
  - 2026-10-13 — `calendrier-tempo-2026-2027-dates` (calendrier)
- **Process qualité** : 1 agent rédacteur (@copywriter+@seo) par article → auto-validation `validate_article.py` 0/0 → passe @reviewer indépendante notée /10 sur les gates (G13 zéro donnée inventée, G15 zéro placeholder, G17 spécificité projet) → itération jusqu'à 10/10 réel. Corrections notables : retrait d'une déduction de prix bleu non public, fix arithmétique chauffe-eau, harmonisation mot-clé exact dans titre/description/H2.
- **Zéro donnée inventée** : aucun prix bleu/blanc en €/kWh (non public), aucune date rouge ni tarif 2026-2027 inventés (renvoi grille EDF + `/calendrier` live).
- **Restant planifié** : 2 REFRESH (`tempo-edf-2026-guide-complet` le 2026-07-07, `economiser-tempo-edf` le 2026-08-18) — laissés « à rédiger » pour l'agent autonome.
- **Total articles** : 22 (14 publiés/backfill + 8 pré-rédigés programmés).
