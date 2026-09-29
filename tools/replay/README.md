# Rejeu de l'algorithme sur les saisons réelles

Outils pour mesurer TOUT changement de `predictor.py`, `ml_scorer.py`, `config.py` ou `rte_twin.py` avant déploiement. Données : uniquement `db_dump.json` (couleurs EDF non synthétiques, météo observée, `rte_daily`) et les fichiers eCO2mix du dépôt. Aucune donnée inventée ; un jour sans données est sauté. Les sorties (JSON) s'écrivent hors du dépôt.

Protocole : prédiction émise à 18 h le jour D, cibles J+2 à J+5, couleurs EDF connues jusqu'à D+1, RTE réalisé jusqu'à D-1, météo OBSERVÉE à la place des prévisions (borne optimiste). Lancer depuis la racine du dépôt avec un Python qui a les dépendances du projet.

| Script | Rôle |
|---|---|
| `replay_pipeline.py` | Appelle le vrai `predictor.predict_range` jour par jour (7 saisons) ; écrit les couples (date, réel, prédit) par horizon et saison |
| `compare.py` | Tableaux VP/FP/FN, rappel, précision ROUGE et BLANC, par horizon et par saison, et écarts entre rejeux |
| `criteria.py` | Critères d'acceptation pré-enregistrés (C1 à C4, R1, R2 avec bootstrap par saison) d'une variante contre l'actuel |
| `alert_sim.py` | Alertes rouges REÇUES par les abonnés (seuil 70/80/90 × délai 1/2/3 × matin/soir) : rouges signalés, fausses alertes, par saison ; `--compare` avant/après |
| `calibrate.py` | Calibration des probabilités par horizon : évaluation hors saison (Brier, fiabilité, rejeu calibré pour `alert_sim.py`) et `--export calibration.json` |
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

## Alertes et calibration

```bash
# rejeu détaillé (cycles 7 h 30 et 18 h, J+1..J+5, probabilités) SANS calibration
python tools/replay/replay_pipeline.py --weights prod --ml folds=/tmp/folds.pkl --thresholds 0.19,0.20 \
    --detail --calibration off --out /tmp/brut.json
python tools/replay/alert_sim.py /tmp/brut.json --policy origine --json /tmp/alertes_avant.json
# calibration hors saison (évaluation honnête) puis alertes avec la politique livrée
python tools/replay/calibrate.py /tmp/brut.json --loso /tmp/cal_loso.json
python tools/replay/alert_sim.py /tmp/cal_loso.json --policy code --json /tmp/alertes_apres.json
python tools/replay/alert_sim.py --compare /tmp/alertes_avant.json /tmp/alertes_apres.json
# régénérer calibration.json (toutes saisons) après tout changement de predictor/ml/config/jumeau
python tools/replay/calibrate.py /tmp/brut.json --export calibration.json
```

`calibrate.py` exige un rejeu `--calibration off` (probabilités brutes). `criteria.py` déclare R2 « sans objet » quand aucune couleur ne change (cas de la calibration).

Options de `replay_pipeline.py` : `--weights default|prod`, `--ml shipped|none|folds=PATH`, `--thresholds R,B`, `--rte-twin off|on` (défaut : `Config.RTE_TWIN_ENABLED`), `--calibration off|on` (défaut : `predictor.PROBA_CALIBRATION_ENABLED`), `--detail` (cycles 7 h 30 + détail J+1..J+5, environ deux fois plus long). Durée : environ 20 s par rejeu (1 min avec le jumeau).

Références (2026-09-29, poids prod, ML hors saison 0,19/0,20, jumeau branché) : ROUGE 526/399/18 sur 7 saisons. Toute régression par rapport à ce rejeu doit être justifiée avant déploiement. Détails : `docs/audits/2026-09-29-jumeau-rte.md`.

Références alertes (2026-09-29, même configuration, calibration hors saison) : profil par défaut (70 %, 3 jours, matin) 93 -> 117 rouges signalés sur 136, fausses alertes 85 -> 42 ; Brier ROUGE J+1..J+5 0,0326 -> 0,0194. Détails : `docs/audits/2026-09-29-alertes-calibration.md`.
