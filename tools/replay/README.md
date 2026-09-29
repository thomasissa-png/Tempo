# Rejeu de l'algorithme sur les saisons réelles

Outils pour mesurer TOUT changement de `predictor.py`, `ml_scorer.py`, `config.py` ou `rte_twin.py` avant déploiement. Données : uniquement `db_dump.json` (couleurs EDF non synthétiques, météo observée, `rte_daily`) et les fichiers eCO2mix du dépôt. Aucune donnée inventée ; un jour sans données est sauté. Les sorties (JSON) s'écrivent hors du dépôt.

Protocole : prédiction émise à 18 h le jour D, cibles J+2 à J+5, couleurs EDF connues jusqu'à D+1, RTE réalisé jusqu'à D-1, météo OBSERVÉE à la place des prévisions (borne optimiste). Lancer depuis la racine du dépôt avec un Python qui a les dépendances du projet.

| Script | Rôle |
|---|---|
| `replay_pipeline.py` | Appelle le vrai `predictor.predict_range` jour par jour (7 saisons) ; écrit les couples (date, réel, prédit) par horizon et saison |
| `compare.py` | Tableaux VP/FP/FN, rappel, précision ROUGE et BLANC, par horizon et par saison, et écarts entre rejeux |
| `criteria.py` | Critères d'acceptation pré-enregistrés (C1 à C4, R1, R2 avec bootstrap par saison) d'une variante contre l'actuel |
| `rte_twin_study.py` | Jumeau RTE avec la C_nette RÉELLE eCO2mix (borne haute, 2020-2024) |
| `rte_twin_forecast.py` | Jumeau RTE en prévision, C_nette estimée depuis la météo (LOSO) ; `--export-model` régénère `rte_twin_model.json` |
| `rte_twin_data.py` | Lecture eCO2mix (C_nette 6 h - 6 h) et du dump |

## Rejouer un changement

```bash
# 1. ML hors saison (un modèle par saison exclue), optionnel mais recommandé : le ml_model.pkl livré a vu ces saisons
python ml_train.py --save-folds /tmp/folds.pkl --out /tmp/candidat.pkl
# 2. Rejeu AVANT (code actuel, git stash si besoin) puis APRÈS
python tools/replay/replay_pipeline.py --weights prod --ml folds=/tmp/folds.pkl --thresholds 0.19,0.20 --out /tmp/avant.json
python tools/replay/replay_pipeline.py --weights prod --ml folds=/tmp/folds.pkl --thresholds 0.19,0.20 --out /tmp/apres.json
# 3. Lecture
python tools/replay/compare.py avant=/tmp/avant.json apres=/tmp/apres.json
python tools/replay/criteria.py /tmp/avant.json /tmp/apres.json
```

Options de `replay_pipeline.py` : `--weights default|prod`, `--ml shipped|none|folds=PATH`, `--thresholds R,B`, `--rte-twin off|on` (défaut : `Config.RTE_TWIN_ENABLED`). Durée : environ 20 s par rejeu (1 min avec le jumeau).

Références (2026-09-29, poids prod, ML hors saison 0,19/0,20, jumeau branché) : ROUGE 526/399/18 sur 7 saisons. Toute régression par rapport à ce rejeu doit être justifiée avant déploiement. Détails : `docs/audits/2026-09-29-jumeau-rte.md`.
