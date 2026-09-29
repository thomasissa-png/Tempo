# Jumeau de l'algorithme RTE et correctif du double décompte du quota (2026-09-29)

Données : uniquement les données RÉELLES du dépôt (couleurs EDF non synthétiques de `db_dump.json`, météo observée `weather_cache`, fichiers eCO2mix RTE 2019 à 2024). Aucune donnée inventée ni simulée. Protocole de rejeu : prédiction émise à 18 h le jour D, cibles J+2 à J+5, couleurs EDF connues jusqu'à D+1, météo OBSERVÉE à la place des prévisions (borne optimiste, aucune archive de prévisions dans le dépôt). Outils versionnés : `tools/replay/`.

## 1. Correctif du double décompte du quota (tâche 1)

**Bug.** `tempo_client.get_remaining_days()` compte toutes les couleurs EDF publiées de la saison, J0 et J+1 compris. `predict_range` les décrémentait une seconde fois (bloc « Decrementer le quota meme pour les confirmees »). Le quota vu par J+2 était donc trop bas d'une unité par ROUGE ou BLANC publié à J0/J+1, ce qui baissait la pression budgétaire.

**Correctif (minimal).** Suppression de ces 4 lignes dans `predictor.py`. Le décrément des jours PRÉDITS (Fix #2) est inchangé. Test : `tests/test_quota_double_count.py` (échoue avant : quota ROUGE vu à J+2 = 20 au lieu de 21 ; passe après ; second test qui garantit le décrément des jours prédits).

**Rejeu AVANT / APRÈS** (`tools/replay/replay_pipeline.py`, vrai `predict_range`, 7 saisons, J+2..J+5 cumulés, VP/FP/FN). Le rejeu AVANT reproduit exactement le chiffre de référence de l'audit ML (497/342/47).

| Configuration | ROUGE avant | ROUGE après | Écart VP / FP | BLANC rappel / préc. avant → après |
|---|---|---|---|---|
| Poids défaut, ML hors saison 0,19 (référence audit) | 497/342/47 (91,4 % / 59,2 %) | 509/351/35 (93,6 % / 59,2 %) | **+12 / +9** | 56,7 / 54,1 → 56,8 / 54,2 |
| Poids prod, ML livré (config prod réelle) | 504/369/40 (92,7 %) | 517/370/27 (95,0 %) | **+13 / +1** | 67,5 / 54,1 → 67,8 / 53,9 |
| Poids défaut, ML livré | 502/305/42 | 515/314/29 | +13 / +9 | 60,4 / 56,6 → 60,2 / 56,5 |
| Sans ML | 444/260/100 | 461/276/83 | +17 / +16 | 63,8 / 53,2 → 63,4 / 53,3 |

Par saison (référence audit), écart VP / FP ROUGE : 2019-20 +0/−3, 2020-21 +3/+3, 2021-22 +6/−2, 2022-23 +1/+5, 2023-24 +0/+5, 2024-25 +2/−5, 2025-26 +0/+6. Aucune saison ne perd de ROUGE attrapé. Par horizon : rappel J+2 88,2 → 91,9 %, J+3 90,4 → 93,4 %, J+4 91,9 → 94,1 %, J+5 inchangé (94,8 %). BLANC : ±6 VP par saison au plus, +1 VP / −2 FP au total.

**Décision : correctif appliqué.** Gain annoncé confirmé (+12 à +13 ROUGE attrapés, +1 à +9 fausses alertes selon la configuration), sans dégradation BLANC.

## 2. Critères d'acceptation du jumeau (fixés AVANT les résultats)

Écrits le 2026-09-29, avant tout rejeu du jumeau en prévision, sur consigne du fondateur. Référence « actuel » = pipeline de production AVEC le correctif du quota (section 1), rejoué par `tools/replay/replay_pipeline.py` avec les poids de production (dernière ligne non rejetée de `weights_history`) et le ML hors saison (un modèle LOSO par saison, seuils 0,19 / 0,20 du modèle livré ; le modèle livré a vu ces saisons à l'entraînement, il flatterait l'actuel). Comparaison sur exactement les mêmes couples (horizon, date cible) que la variante. Une variante n'est branchée que si elle passe TOUS les critères.

| # | Critère | Mesure |
|---|---|---|
| C1 | Rappel ROUGE poolé J+2..J+5 ≥ actuel | VP / (VP + FN), toutes saisons évaluables |
| C2 | Fausses alertes ROUGE ≤ actuel ; tolérance +5 % seulement si le rappel ROUGE gagne ≥ 3 points | FP ROUGE poolés |
| C3 | Rappel ROUGE dégradé sur au plus 1 saison | par saison, J+2..J+5 |
| C4 | Rappel BLANC ≥ actuel − 3 points | poolé J+2..J+5 |
| R1 | Stabilité par horizon : à chaque horizon J+2, J+3, J+4, J+5, rappel ROUGE ≥ actuel − 2 points | par horizon |
| R2 | Bootstrap par saison (2 000 tirages de saisons avec remise) : borne basse de l'IC 95 % de Δrappel ROUGE ≥ −1 point, ET amélioration établie sur un axe ROUGE (borne basse de Δrappel > 0 ou borne haute de ΔFP < 0) | IC 95 % |

Choix fixés en même temps (pour ne pas les ajuster après coup) :
- Quantiles de normalisation : calculés sur l'année Tempo PRÉCÉDENTE (1er septembre à 31 août), jamais sur la saison testée. La saison 2019-2020 n'a pas d'année précédente dans le dépôt (météo à partir du 2019-09-01) : elle est exclue de l'évaluation du jumeau, et l'actuel est comparé sur les mêmes saisons.
- Modèle de C_nette : appris en saison exclue (LOSO), uniquement sur des variables fournies par `weather_client` (températures, humidité, vent, pression) et le calendrier.
- Hybrides évalués : H1 « filet » (le pipeline passe en ROUGE quand le jumeau dit ROUGE, règles EDF et quota respectés), H2 « filet + veto » (H1, plus un ROUGE du pipeline redescend en BLANC quand le jumeau ne franchit aucun seuil).

## 3. Jumeau RTE : méthode (`rte_twin.py`)

Transcription de la note RTE (indice 2 du 7/01/2025), testée formule par formule (`tests/test_rte_twin.py`) :
- **C_nette** = consommation nationale − éolien − photovoltaïque, moyenne des 48 demi-heures de 6 h (jour j) à 5 h 30 (j+1).
- **Normalisation** : C_std = (C_nette − q_conso,0.4) / ((q_conso,0.8 − q_conso,0.4) · exp(−γ (q_temp,0.3 − κ))), γ = −0,1176, κ = 8,3042 °C. Quantiles par interpolation linéaire (convention de RTE non publiée).
- **Seuils** : Blanc+Rouge = 4,00 − 0,015·JourTempo − 0,026·Stock(B+R) ; Rouge = 3,15 − 0,010·JourTempo − 0,031·Stock(R), JourTempo = 1 au 1er septembre.
- **Placement** : ROUGE du 1er novembre au 31 mars, hors week-end et jours fériés, 5 consécutifs au plus ; BLANC hors dimanche ; écoulement : si le stock égale les jours éligibles restants, le jour est placé même sous le seuil. Stock décrémenté jour après jour.
- **C_nette estimée** (prévision) : régression ridge sur 24 variables disponibles à J+h dans `weather_client` (degrés-jours base 16 du jour, de la veille et de l'avant-veille, température mini, amplitude, vent et vent², humidité, pression) et le calendrier (jour de semaine, férié, harmoniques annuelles, Noël, août). Coefficients lisibles dans `rte_twin_model.json` : +1 830 MW par degré sous 16 °C, −690 MW par km/h de vent, +6 000 MW un jour ouvré contre un dimanche. Erreur hors saison (RMSE) : 3,2 à 4,6 GW selon la saison, pour une échelle de normalisation de 12 à 18 GW.

## 4. Résultats a) C_nette réelle connue (borne haute)

C_nette réelle eCO2mix, saisons 2020-2021 à 2023-2024 complètes et 2024-2025 de septembre à décembre (fin des fichiers eCO2mix au 2024-12-30), 1 582 jours, décision le jour même. « Stock réel » : stock et ROUGE consécutifs réels de la veille ; « stock simulé » : le jumeau place seul ses 22/43 jours sur toute la saison.

| C_nette | Normalisation | Stock | ROUGE VP/FP/FN | Rappel / préc. ROUGE | Rappel / préc. BLANC |
|---|---|---|---|---|---|
| Réalisée | année précédente | simulé | 78/21/18 | 81 / 79 % | 75 / 75 % |
| Réalisée | année précédente | réel | 87/26/9 | 91 / 77 % | 74 / 77 % |
| Réalisée | 365 j glissants | réel | 92/20/4 | 96 / 82 % | 84 / 82 % |
| Prévision J-1 de conso − éolien − solaire réalisés | 365 j glissants | simulé | 88/10/8 | 92 / 90 % | 86 / 86 % |
| Prévision J-1 de conso − éolien − solaire réalisés | 365 j glissants | réel | **95/16/1** | **99 / 86 %** | 84 / 89 % |

Meilleure variante par saison (ROUGE VP/FP/FN) : 2020-21 22/1/0, 2021-22 22/1/0, 2022-23 22/8/0, 2023-24 21/3/1, 2024-25 (sept.-déc.) 8/3/0. **Le jumeau reproduit l'algorithme RTE** : avec la C_nette que RTE voit la veille (prévision J-1), il retrouve 95 ROUGE sur 96. Les écarts restants viennent des quantiles exacts (fenêtre, convention), de la température France de RTE (ici moyenne 9 villes) et des prévisions éolien/solaire de RTE (ici le réalisé).

## 5. Résultats b) C_nette estimée depuis la météo, comparaison au pipeline

Rejeu J+2..J+5, 6 saisons (2020-2021 à 2025-2026, 7 932 couples horizon × date), modèle de C_nette appris hors saison, quantiles de l'année Tempo précédente (C_nette estimée depuis la météo observée). « Actuel » = pipeline avec correctif du quota, poids prod, ML hors saison.

| Variante | ROUGE VP/FP/FN | Rappel / préc. ROUGE | BLANC VP/FP/FN | Rappel / préc. BLANC |
|---|---|---|---|---|
| Actuel | 440/304/32 | 93,2 / 59,1 % | 636/591/337 | 65,4 / 51,8 % |
| Jumeau seul | 420/86/52 | 89,0 / **83,0 %** | 762/358/211 | **78,3 / 68,0 %** |
| H1 filet | 456/323/16 | 96,6 / 58,5 % | 617/578/356 | 63,4 / 51,6 % |
| H2 filet + veto (calcul hors ligne) | 456/299/16 | 96,6 / 60,4 % | 620/599/353 | 63,7 / 50,9 % |
| **H2 branché dans `predictor.py` (vrai code)** | **456/299/16** | **96,6 / 60,4 %** | 620/599/353 | 63,7 / 50,9 % |

Rappel ROUGE par saison, actuel → jumeau seul → H2 branché : 2020-21 95,5 → 90,9 → 100 ; 2021-22 93,2 → 100 → 100 ; 2022-23 95,5 → 95,5 → 95,5 ; 2023-24 87,5 → **59,1** → 90,9 ; 2024-25 92,0 → 95,5 → 95,5 ; 2025-26 100 → 100 → 100. Fausses alertes ROUGE H2 − actuel : +8, +0, +0, −1, +5, −17.

Par horizon (rappel ROUGE actuel → H2 branché) : J+2 91,5 → 97,5 ; J+3 93,2 → 97,5 ; J+4 94,1 → 95,8 ; J+5 94,1 → 95,8.

**Critères (section 2)**, outil `tools/replay/criteria.py` :

| Critère | Jumeau seul | H1 | H2 branché |
|---|---|---|---|
| C1 rappel ROUGE ≥ actuel | ÉCHEC (89,0 < 93,2) | PASS | PASS (96,6) |
| C2 FP ROUGE | PASS (86) | ÉCHEC (323 = +6,3 %) | PASS (299 ≤ 304) |
| C3 au plus 1 saison dégradée | ÉCHEC (2020-21, 2023-24) | PASS | PASS (aucune) |
| C4 rappel BLANC ≥ −3 pts | PASS (+12,9) | PASS (−2,0) | PASS (−1,7) |
| R1 horizons ≥ −2 pts | ÉCHEC (J+4 −6,0) | PASS | PASS |
| R2 bootstrap (IC 95 %) | ÉCHEC (Δrappel [−16,1 ; +3,8]) | PASS | PASS (Δrappel [+1,4 ; +5,1] pts, ΔFP [−48 ; +27]) |

Sensibilité (H2 branché contre actuel, 6 saisons, tous critères passés dans chaque cas) : poids par défaut + ML hors saison 441/263/31 → 456/262/16 ; poids prod + ML livré 447/273/25 → 463/271/9 ; sans ML 397/189/75 → 448/194/24. Sur 7 saisons (2019-2020 inchangée, jumeau inactif faute d'année précédente) : 510/404/34 → 526/399/18. Seuls 59 couples sur 9 396 changent : 8 jours ROUGE anticipés en plus (2021-02-08/09/10, 2021-12-13/15, 2024-03-29, 2024-12-16, 2025-01-31), aucun perdu ; 5 jours de fausse alerte en plus, 8 en moins.

## 6. Décision et recommandation

**H2 branché, `Config.RTE_TWIN_ENABLED = True`.** C'est la seule variante qui passe les 6 critères. Branchement contenu : `predictor._apply_rte_twin`, appelé une fois en fin de `predict_range`, après la cohérence thermique, sur les seuls jours non confirmés de J+2 à J+5. Filet : jumeau ROUGE → ROUGE si cela ne crée pas plus de 5 ROUGE consécutifs. Veto : pipeline ROUGE et jumeau BLEU → BLANC. Probabilités remises en cohérence. Toute erreur ou donnée manquante laisse les prédictions inchangées. Interrupteur coupé : rejeu identique à l'octet près à l'actuel (vérifié) et test dédié. Gain attendu : environ +3,4 points de rappel ROUGE J+2..J+5 (93 → 97 %), fausses alertes ROUGE stables (−5 sur 6 saisons), rappel BLANC −1,7 point.

**Après novembre, à étudier :** le jumeau seul a une précision ROUGE de 83 % (contre 59 %) et un BLANC nettement meilleur (78/68 % contre 65/52 %). Il échoue seulement parce qu'il rate 2023-2024 (59 % de rappel), la saison où la C_nette réelle s'éloigne le plus du modèle météo (RMSE 4,2 GW). Pour le rendre autonome, il faut ancrer la C_nette sur les données RTE réelles :
- C_nette réalisée 2025-2026 : absente du dépôt (eCO2mix s'arrête au 2024-12-30 ; `rte_daily` 2025-2026 n'a que la consommation, sans éolien ni solaire). Source : exports eCO2mix « En-cours consolidé / temps réel » ou API RTE (production réalisée par filière).
- Prévisions à J+h : consommation (API RTE Consumption, déjà appelée par `rte_client.py`), éolien et solaire (API RTE Generation Forecast déjà appelée en observabilité ; horizons disponibles à vérifier) ou, à défaut, vent à 100 m et rayonnement Open-Meteo.
- Archive des prévisions météo J+2..J+5 (`weather_forecast_log`) pour mesurer la perte réelle due à la prévision.

## 7. Limites

- **Météo observée à la place des prévisions** : tous les chiffres sont des bornes hautes, pour l'actuel comme pour le jumeau. Le modèle de C_nette sera moins précis avec le vent prévu à J+5.
- **2019-2020 non évaluable** pour le jumeau (pas de météo avant le 2019-09-01). En production, le jumeau exige au moins 330 jours de `weather_cache` sur l'année Tempo précédente (2025-09-01 → 2026-08-31 pour la saison 2026-2027). Sinon il reste inactif (log `[RTE twin] N jours de météo`) et le comportement est celui d'avant. À vérifier en prod avant le 1er novembre ; complément possible avec `import_history.fetch_historical_weather`.
- Quantiles RTE exacts, température France de RTE et prévisions éolien/solaire de RTE inconnus ; la note précise que la version publiée n'est pas l'algorithme réel.
- 6 saisons, 16 ROUGE-horizons gagnés : effet net mais petit en volume ; l'IC bootstrap exclut zéro sur le rappel, pas sur les fausses alertes.
- Suite de tests complète non relancée après l'activation de l'interrupteur (exécution refusée par l'environnement) : à lancer avant tout commit.
