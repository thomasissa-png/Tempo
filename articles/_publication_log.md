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

---

## 2026-09-29 : mise à niveau éditoriale complète (audits SEO et GEO du jour)

- **Contexte** : audits SEO/blog et GEO du 2026-09-29. Saison 2026-2027 démarrée le 1er septembre ; premiers rouges possibles à partir du 1er novembre. Aucun article autonome n'avait été publié depuis le 2026-05-26 et 2 refresh restaient « à rédiger ».
- **Publication** : `calendrier-tempo-2026-2027-dates` avancé du 2026-10-13 au 2026-09-29 (publication immédiate) et réécrit : wording « la saison approche » remplacé par l'état réel de la saison, tableau mensuel remplacé par la répartition réelle des rouges et des blancs de 7 saisons (table `actuals` de `db_dump.json`, recoupée avec les 3 fichiers .ics), sources citées.
- **Refresh en retard traités** : `tempo-edf-2026-guide-complet` (prévu le 2026-07-07) et `economiser-tempo-edf` (prévu le 2026-08-18), faits le 2026-09-29 avec `updated_date`. Les deux dates ont été retirées du calendrier.
- **Articles réécrits sur données réelles** : `calendrier-tempo-historique-saisons` (tableau saison par saison, jours de la semaine, séries de rouges, températures, épisodes précédés d'un blanc), `calendrier-tempo-2025-2026-dates` (dates réelles des 9 rouges et 32 blancs au 20 février 2026), `bilan-saison-tempo-2025-2026`, `fin-saison-rouge-tempo-bilan` (rendu evergreen, dernière date de rouge par saison).
- **Autres articles mis à jour** (20 articles au total avec `updated_date: 2026-09-29`, les 2 articles du jour n'en ont pas) : alerte-jour-rouge-tempo, ballon-eau-chaude-tempo, chauffage-jour-rouge-tempo-astuces, climatisation-tempo-edf-ete, jours-rouges-tempo-guide, linky-tempo-suivre-consommation, pompe-a-chaleur-tempo-edf, preparer-saison-tempo-2026-2027, recharge-vehicule-electrique-tempo, rentree-tempo-2026-2027, simulation-tempo-edf-economies, souscrire-tempo-edf-guide, tempo-edf-avis-rentabilite, tempo-vs-heures-creuses-edf-bleu.
- **Chiffres supprimés car non sourcés** : « environ 25 % / 55 % / 20 % » des séries, « 70 % de chances qu'un deuxième rouge suive », « 90 % des rouges sous 5 °C » (remplacé par 78 % calculé), « 2 400 MW par degré », « 80 GW », « 38 millions de Français », « taux d'ouverture > 90 % », « le chauffe-eau représente 15 % », « 200 à 350 euros par an » pour un appartement, « EDF utilise toujours les 22 rouges » (faux : 18 en 2019-2020), « premiers rouges en novembre 2025 » (faux : premier rouge le 29 décembre 2025), « 3 à 5 blancs en octobre » (faux : aucun blanc en septembre ni octobre sur 7 saisons).
- **Harmonisation** : ratio rouge HP / bleu HP = 4,4 (plus de « 5x » ni « quintuple »), alertes WhatsApp sur 7 jours (récapitulatif du dimanche + message avant un rouge probable), annonce EDF de J+1 « vers 11h » (fin de « 17h »), marque « Calendrier Tempo EDF » (fin de « TempoForecast »), AES-256 retiré, critère officiel de choix des couleurs précisé d'après la note technique RTE indice 2 du 07/01/2025 (consommation nette, choix par RTE depuis le 1er novembre 2014).
- **Métadonnées** : 22 titres entre 50 et 65 caractères (guillemets retirés sur `economiser-tempo-edf`), 22 descriptions entre 140 et 155 caractères, mot-clé principal dans titre et description (les 17 avertissements de `validate_article.py` sont traités), `updated_date` posé seulement sur les articles réellement modifiés.
- **Maillage** : 6 orphelins traités, chaque article reçoit au moins 2 liens entrants ; piliers et satellites des 5 clusters liés dans les deux sens ; pilier `equipements` = `chauffage-jour-rouge-tempo-astuces` (section « Aller plus loin par équipement ») ; cluster `preparation` = pilier `preparer-saison-tempo-2026-2027` + `rentree-tempo-2026-2027` + `souscrire-tempo-edf-guide` (déplacés depuis `tempo-guide`) ; liens vers `/tarif-tempo-edf`, `/couleur-tempo-demain`, `/api-tempo` et `/methodologie` (pages créées ce jour par un autre agent, à vérifier en ligne avant déploiement).
- **Sources externes** : pages d'accueil officielles uniquement (edf.fr, cre.fr, rte-france.com, legifrance.gouv.fr), aucune URL profonde.
- **Calendrier** : 24 mardis planifiés du 2026-10-06 au 2027-03-30 (octobre 1er et 3e mardi, novembre à mars chaque mardi), chacun avec cluster, intention, élément first-hand et note anti-cannibalisation ; 2 items conditionnels (tarifs de février, panneaux solaires).
- **Règles SEO** : `saison_courante` 2026-2027, dates de saison, vérification de la grille tarifaire au 2026-09-29 ; ajout des sections `donnees_reelles` et `chiffres_harmonises` (source de vérité pour l'agent).
- **Points ouverts** : les affirmations « seul site en France » (`app.py` llms.txt, `.claude/backlinks-agent-prompt.md`) et « 900 000 foyers » (`app.py` llms.txt, `jours-rouges-tempo-guide.md`) sont laissées telles quelles dans l'attente de la décision du fondateur ; le score « 83 % » n'est cité dans les articles que comme F1 de backtest historique.
- **Total articles** : 22, tous validés par `validate_article.py` (0 erreur, 0 avertissement).
