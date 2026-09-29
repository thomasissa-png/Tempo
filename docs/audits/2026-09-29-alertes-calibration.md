# Alertes rouges reçues par les abonnés et calibration des probabilités (2026-09-29)

Données : uniquement les données réelles du dépôt (`db_dump.json` : couleurs EDF, météo observée, `rte_daily` ; eCO2mix pour le jumeau RTE). Aucune donnée inventée. Rejeu du vrai `predictor.predict_range` (outil `tools/replay/`), 7 saisons 2019-2020 à 2025-2026, poids de production, ML hors saison (un modèle par saison exclue, seuils 0,19 / 0,20), jumeau RTE branché, météo OBSERVÉE à la place des prévisions (borne optimiste).

## 1. Critères pré-enregistrés (écrits AVANT tout rejeu « après »)

Écrits après le seul rejeu « avant » (section 2) et avant tout apprentissage de calibration ou toute évaluation de politique d'alerte.

**Référence « actuel »** : code au commit `d74bc19` (jumeau RTE branché), rejoué avec `replay_pipeline.py --weights prod --ml folds=... --thresholds 0.19,0.20 --detail`, alertes simulées par `alert_sim.py --policy origine` (règle d'origine : couleur prédite ROUGE et seuil <= round(probabilite_rouge x 100)).

**Profils mesurés** : seuil 70 / 80 / 90 (valeurs de `templates/manage.html` ; la modale d'inscription envoie 70 ou 0), délai 1 / 2 / 3 (`app.py` borne à 1..3), heure d'envoi matin (7 h 30) ou soir (18 h). Profil par défaut : 70 %, 3 jours, matin (`register_user`).

| # | Critère | Mesure |
|---|---|---|
| A1 | Jours ROUGE signalés au seuil par défaut (70) >= actuel, à chaque délai (1, 2, 3) et pour chaque heure d'envoi | `alert_sim.py`, 7 saisons |
| A2 | Fausses alertes rouges reçues au seuil par défaut <= actuel x 1,05, à chaque délai et heure | idem |
| A3 | Profil par défaut : au plus 1 saison sur 7 où les rouges signalés baissent ou les fausses alertes dépassent actuel x 1,05 + 1 | par saison |
| K1 | Brier ROUGE <= actuel, poolé J+1..J+5 ET à chaque horizon, probabilités affichées (après règles EDF et cohérence couleur), calibration évaluée hors saison (apprise sans la saison testée) | `calibrate.py` |
| K2 | Couleur prédite inchangée : C1 à C4, R1 de `criteria.py`. R2 exige une amélioration ; si les couples (réel, prédit) sont identiques à l'octet près, R2 est déclaré « sans objet » (Δ = 0), ce n'est pas un échec | `criteria.py` + diff des couples |
| K3 | Invariants : couleur prédite = probabilité max ; règles EDF (dimanche, samedi, férié, hors novembre-mars, quota épuisé) -> 0 % ; somme = 1 ; tests existants verts | tests |

**Seuils 80 et 90** : mêmes critères A1 et A2 appliqués à chaque niveau, faute de quoi le niveau garde la règle la plus proche de l'actuel qui les passe (voir décision).

**Politiques candidates fixées à l'avance** (alerte ROUGE seulement si la couleur prédite est ROUGE, comme aujourd'hui) :
- P0 origine : seuil <= round(p_rouge affichée x 100).
- P1 filet : toute prédiction ROUGE non confirmée déclenche l'alerte, quel que soit le seuil.
- P2 calibrée stricte : p_rouge calibrée >= seuil / 100.
- P3 calibrée par niveau : p_rouge calibrée >= t(seuil), t choisi dans la grille {0 ; 0,3 ; 0,4 ; 0,5 ; 0,6 ; 0,7} par la règle : maximiser les rouges signalés sous A1 et A2 à chaque délai et heure ; à égalité, le t le plus haut (moins de fausses alertes). Aucun t qui passe : le niveau garde P0 si les probabilités ne sont pas calibrées, sinon le t qui minimise l'écart aux critères, signalé comme échec.

**Méthode de calibration fixée à l'avance** : régression logistique multinomiale par horizon (delta 1 à 5 ; au-delà, modèle de J+5), variables = probabilités affichées (log), couleur prédite, avis du jumeau RTE, score ML ROUGE, score de risque ; apprise en saison exclue pour l'évaluation (LOSO), puis sur les 7 saisons pour le fichier livré. Puis règles EDF (0 %), renormalisation, cohérence couleur/probabilité existante.

### 1 bis. Addendum pré-enregistré (écrit après l'échec de A3 en première passe, avant le rejeu de la variante V2)

Première passe (section 4) : la politique P3 retenue par la règle (t = 0,5) passe A1 et A2 mais échoue A3 (2 saisons perdent des rouges signalés : 2019-2020 et 2023-2024). Diagnostic, sans regarder d'autre résultat : en 2019-2020 le jumeau RTE est inactif (pas d'année de météo précédente), et le modèle de calibration J+2..J+5, appris hors saison, n'a jamais vu de ligne « jumeau absent » : il extrapole. Le même cas se produira en production si `weather_cache` n'a pas 330 jours de l'année précédente. Correctif structurel V2 : un second modèle par horizon, sans les variables du jumeau, utilisé quand le jumeau n'a pas donné d'avis. Critères inchangés (A1, A2, A3, K1 à K3), même règle de choix de t, même évaluation hors saison. Si V2 échoue encore un critère, la politique d'alerte n'est pas changée. Variante conçue après avoir vu un échec : à lire avec cette réserve.

## 2. Mesure « avant » : ce que reçoivent les abonnés

Simulation fidèle au code d'envoi (`tools/replay/alert_sim.py`) : cycle de 7 h 30 pour les abonnés « matin » (J+1 pas encore publié, délais 1 à 3), cycle de 18 h pour les abonnés « soir » (J+1 déjà officiel, donc délais 2 et 3 seulement), jour d'envoi en novembre-mars, une alerte par (abonné, date), changements de prédiction 7 h 30 -> 18 h modélisés. 136 jours ROUGE réels évaluables sur 7 saisons (2025-2026 : 8 rouges dans le dump).

**Constat principal.** Le pipeline prédit ROUGE au moins une fois dans la fenêtre d'alerte pour 132 des 136 rouges (profil par défaut), mais l'abonné par défaut n'est alerté que pour 93 d'entre eux (68 %). Les 39 autres sont prédits ROUGE avec une probabilité affichée sous 70 %. Or la probabilité affichée ne dit rien de la fiabilité : parmi les jours prédits ROUGE, un jour annoncé à 55 % est rouge 58 % du temps, un jour annoncé à 85 % l'est 46 % du temps (section 3). Le seuil jette donc des rouges presque au hasard, et laisse passer autant de fausses alertes (85, pour 93 rouges signalés).

Autre constat : un abonné « soir » avec délai 1 ne reçoit jamais d'alerte de prévision (à 18 h, J+1 est déjà officiel ; il ne reçoit que l'alerte officielle de 11 h 30). Cela relève de `scheduler.py` (hors périmètre), transmis à @fullstack.

## 3. Calibration

Modèle : régression logistique multinomiale par horizon (J+1 à J+5 ; J+6 et au-delà utilisent J+5), 11 variables toutes présentes dans la prédiction (log des 3 probabilités brutes, couleur prédite, avis du jumeau RTE, score ML ROUGE, score de risque), plus un modèle sans les variables du jumeau quand celui-ci n'a pas donné d'avis (V2). Après calibration : couleurs impossibles (règles EDF, quota épuisé) à 0 %, renormalisation, puis cohérence couleur prédite = probabilité max par nivellement (`_minimal_prob_coherence`). Code : `predictor.calibrate_prediction`, modèle `calibration.json` (7 saisons, 21 141 prédictions), outil `tools/replay/calibrate.py`.

**Brier ROUGE, évaluation hors saison** (chaque saison calibrée par un modèle appris sans elle), toutes prédictions non confirmées des deux cycles :

| Horizon | n | Brier ROUGE avant | après | Jours ouvrés nov.-mars avant | après | Brier 3 classes avant | après |
|---|---|---|---|---|---|---|---|
| J+1 | 2 349 | 0,0351 | 0,0215 | 0,1147 | 0,0704 | 0,2644 | 0,1451 |
| J+2 | 4 698 | 0,0326 | 0,0196 | 0,1064 | 0,0640 | 0,2625 | 0,1346 |
| J+3 | 4 698 | 0,0329 | 0,0189 | 0,1074 | 0,0618 | 0,2625 | 0,1343 |
| J+4 | 4 698 | 0,0318 | 0,0189 | 0,1038 | 0,0619 | 0,2610 | 0,1343 |
| J+5 | 4 698 | 0,0320 | 0,0192 | 0,1044 | 0,0626 | 0,2606 | 0,1351 |
| **J+1..J+5** | 21 141 | **0,0326** | **0,0194** | 0,1065 | 0,0634 | 0,2620 | 0,1358 |

**Fiabilité ROUGE** (J+1..J+5, hors saison) : annoncé moyen -> observé.

| Tranche annoncée | Avant : n, annoncé, observé | Après : n, annoncé, observé |
|---|---|---|
| 0-10 % | 18 514 ; 0,5 % ; 0,2 % | 19 009 ; 0,1 % ; 0,2 % |
| 10-30 % | 498 ; 16,8 % ; 1,4 % | 35 ; 15,2 % ; 5,7 % |
| 30-50 % | 69 ; 40,3 % ; 36,2 % | 808 ; 42,9 % ; 21,0 % |
| 50-60 % | 399 ; 55,9 % ; 57,9 % | 151 ; 54,4 % ; 37,1 % |
| 60-70 % | 454 ; 64,9 % ; 61,5 % | 83 ; 64,5 % ; 62,7 % |
| 70-80 % | 548 ; 75,3 % ; 60,8 % | 126 ; 75,8 % ; 68,3 % |
| 80-90 % | 563 ; 84,7 % ; 46,0 % | 695 ; 86,0 % ; 91,5 % |
| 90-100 % | 96 ; 93,0 % ; 51,0 % | 234 ; 92,5 % ; 76,5 % |

Avant, la probabilité est plate (et même décroissante au-dessus de 80 %). Après, elle sépare les ROUGE prédits en deux groupes : environ 930 « sûrs » (annoncés 80 % et plus, 88 % réels) et environ 800 « contestés » (annoncés 30 à 50 %, 21 % réels). Défaut résiduel assumé : la tranche 30-50 % est surestimée, parce que l'invariant « couleur prédite = probabilité max » oblige à afficher une hésitation (ex. 46 % ROUGE / 45 % BLEU) là où la calibration seule dirait 25 %. La tranche 90-100 % est un peu optimiste (76 % réels).

## 4. Politique d'alerte

**Première passe (V1, sans modèle « jumeau absent »)**, hors saison : P1 filet échoue A2 (fausses alertes 85 -> 117) ; P3 avec t = 0,5 est retenu par la règle (maximum de rouges signalés sous A1 et A2) mais échoue A3 : 2019-2020 (12 -> 11) et 2023-2024 (14 -> 11). D'où l'addendum V2 (section 1 bis).

**V2, hors saison, règle de choix appliquée** : t = 0,5 pour 70 et pour 80 (t = 0,4 échoue A2, t de 0,5 à 0,7 passent, 0,5 signale le plus). Choix stable : la même règle appliquée sur 6 saisons choisit 0,5 pour chacune des 7 saisons exclues. Seuil 90 : aucun t de la grille ne passe, il garde la règle d'origine (seuil <= round(p x 100)), appliquée à la probabilité calibrée.

| Profil (heure, seuil, délai) | Rouges signalés avant | après | Fausses alertes avant | après |
|---|---|---|---|---|
| matin 70 J-1 | 64 (47,1 %) | 101 (74,3 %) | 60 | 26 |
| matin 70 J-2 | 82 (60,3 %) | 116 (85,3 %) | 73 | 41 |
| **matin 70 J-3 (défaut)** | **93 (68,4 %)** | **117 (86,0 %)** | **85** | **42** |
| soir 70 J-1 | 0 | 0 | 0 | 0 |
| soir 70 J-2 | 76 (55,9 %) | 112 (82,4 %) | 60 | 28 |
| soir 70 J-3 | 85 (62,5 %) | 115 (84,6 %) | 74 | 31 |
| matin 80 J-1 / J-2 / J-3 | 39 / 51 / 56 | 101 / 116 / 117 | 38 / 49 / 58 | 26 / 41 / 42 |
| soir 80 J-2 / J-3 | 48 / 53 | 112 / 115 | 37 / 49 | 28 / 31 |
| matin 90 J-1 / J-2 / J-3 | 11 / 18 / 19 | 5 / 24 / 25 | 5 / 9 / 10 | 2 / 10 / 8 |
| soir 90 J-2 / J-3 | 16 / 16 | 26 / 27 | 4 / 5 | 6 / 6 |

Profil par défaut par saison (rouges signalés / rouges, fausses alertes), avant -> après : 2019-20 12/18, 19 -> 12/18, 4 ; 2020-21 16/22, 5 -> 22/22, 5 ; 2021-22 15/22, 10 -> 22/22, 6 ; 2022-23 14/22, 13 -> 22/22, 13 ; **2023-24 14/22, 13 -> 11/22, 2** ; 2024-25 15/22, 7 -> 20/22, 4 ; 2025-26 7/8, 18 -> 8/8, 8. 2023-2024 est la saison où le jumeau RTE se trompe (rappel 59 %) : la calibration lui fait confiance et conteste 3 vrais rouges.

Ce que « 70 % » veut dire maintenant : au profil par défaut, 74 % des jours pour lesquels l'abonné reçoit une alerte rouge sont réellement rouges (117 / 159), contre 52 % avant (93 / 178). Le texte des messages WhatsApp est inchangé ; la probabilité affichée dans l'alerte est la probabilité calibrée (50 % au minimum pour 70 et 80).

## 5. Bug `simulated`

`confirm_prediction` remettait `simulated = 0` sur toutes les lignes de la date : une prédiction simulée devenait « réelle » dès la confirmation EDF, donc visible dans l'historique public (`prediction_history`, filtre `simulated = 0`), dans le récapitulatif admin (`get_daily_recap`) et évaluée par `evaluate_missed_days`. Même effet par `store_prediction` d'une prédiction confirmée sur une ligne simulée existante (`simulated = excluded.simulated`). Correctif : `confirm_prediction` ne touche plus `simulated` ; `store_prediction` d'une ligne confirmée garde le statut de la ligne existante. Tests : `tests/test_alert_calibration.py::TestSimulatedPreservedOnConfirmation` (3 tests).

## 6. Critères et décision

| Critère | Résultat |
|---|---|
| A1 rouges signalés, seuil 70, chaque délai et heure | PASS (6/6 profils, +24 à +37) |
| A2 fausses alertes, seuil 70 | PASS (toutes en baisse, -32 à -43) |
| A3 au plus 1 saison dégradée (défaut) | PASS en V2 (2023-2024 seule) ; ÉCHEC en V1 |
| K1 Brier ROUGE, poolé et par horizon | PASS (0,0326 -> 0,0194, chaque horizon en baisse) |
| K2 couleurs : C1-C4, R1 ; R2 | PASS, couples identiques à l'octet près (9 396), R2 sans objet |
| K3 invariants, tests | PASS (`tests/test_alert_calibration.py` + suite complète verte) |
| Seuil 80 (A1, A2) | PASS |
| Seuil 90 (A1, A2) | ÉCHEC : matin J-1 11 -> 5 rouges ; fausses alertes soir 4 -> 6 et 5 -> 6, matin J-2 9 -> 10. Règle de code inchangée pour 90, mais elle s'applique à une probabilité calibrée, et la probabilité brute n'est pas stockée (schéma de base hors périmètre) : impossible de garder exactement l'ancien comportement. Bilan 90 : 80 -> 107 rouges signalés, 33 -> 32 fausses alertes sur les 6 profils |

**Écarts au protocole, à lire avant de décider :**
1. Cohérence : le pré-enregistrement prévoyait la cohérence existante (max + 5 points). Après la première évaluation de calibration (Brier et fiabilité seulement, aucune alerte évaluée), elle a été remplacée par un nivellement minimal, parce que le « + 5 points » affiche à 75 % des ROUGE que la calibration juge à 30 % (tranche 70-80 % : 28 % de rouges réels). Avec la cohérence existante, K1 passe (0,0268 ; 0,0278 en V2) mais, évaluée en V2, aucune politique de la grille ne passe A1 et A2 (seul t = 0,8, hors grille, les passe).
2. V2 (modèle sans jumeau) : conçu après l'échec de A3, addendum écrit avant son rejeu.
Les critères n'ont jamais été modifiés. Toutes les évaluations sont hors saison.

**Décision : calibration et politique livrées activées** (`predictor.PROBA_CALIBRATION_ENABLED = True`, `alerts.ALERT_CALIBRATED_THRESHOLDS = {70: 0.50, 80: 0.50}`), parce que tous les critères du seuil par défaut passent et que le gain est large (+24 rouges signalés, fausses alertes divisées par deux). Retour arrière complet (probabilités et alertes identiques à avant) : `PROBA_CALIBRATION_ENABLED = False`. À trancher par le fondateur : (a) garder 80 identique à 70 (résultat de la règle) ou le différencier (t = 0,7 : 104 rouges / 26 fausses alertes au profil matin J-3, passe aussi) ; (b) accepter l'échec du niveau 90 à J-1 ou le rediriger vers t = 0,8.

## 7. Limites

- Météo observée à la place des prévisions : probabilités calibrées optimistes à J+3..J+5 en production. Recalibrer sur les prévisions archivées (`weather_forecast_log`, `rte_forecast_log`) dès qu'une saison est disponible.
- J+6 à J+15 utilisent le modèle J+5 (affichage seulement, aucune alerte).
- Simulation : abonnés `alerte_blanc` non modélisés (une alerte blanche envoyée d'abord bloque l'alerte rouge de la même date), relances de 18 h et changements hors cycle 7 h 30 -> 18 h non modélisés.
- 136 rouges, 7 saisons : le seuil 0,5 est choisi sur ces mêmes saisons (calibration hors saison, choix vérifié saison exclue par saison exclue).
