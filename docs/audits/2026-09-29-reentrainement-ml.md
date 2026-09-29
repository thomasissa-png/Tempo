# Ré-entraînement du modèle ML, poids du scoring et performance réelle (2026-09-29)

Suite de l'audit `2026-09-29-audit-algorithme.md`. Données exclusivement réelles : `db_dump.json` (exporté le 2026-02-23 : couleurs EDF 2019-09-01 → 2026-02-20, météo observée Open-Meteo Archive, `rte_daily` réalisé jusqu'au 2024-12-31 puis 10 jours de février 2026). Aucune donnée inventée ni simulée.

Scripts : `ml_train.py` (dépôt, reproductible). Les rejeux du pipeline (`pipeline2.py`, `b_compare.py`, `c_prod.py`) sont dans le scratchpad de session, hors dépôt : à versionner si l'on veut rejouer chaque changement de poids ou de seuil.

## Synthèse

| Question | Réponse mesurée |
|---|---|
| Nouveau ML meilleur que l'ancien ? | Oui, hors échantillon : rappel ROUGE J+2 à J+5 de **79 %** contre **12,5 %** sur les 2 saisons inédites pour les deux. **Remplacé**, ancien sauvegardé (`ml_model_2024-08.pkl`). |
| Le pipeline complet se dégrade-t-il ? | Non, sur 2024-2026 : rappel ROUGE 92,5 % contre 89,2 %, précision 51,4 % contre 50,2 %. Seuils d'inférence inchangés (0,19 / 0,20). |
| Poids de prod contre poids par défaut ? | Même rappel ROUGE (92 % contre 91 %), 62 fausses alertes ROUGE de plus sur 7 saisons, +5 points de rappel BLANC. Pas de raison de les changer avant novembre ; geler le recalcul automatique. |
| Performance réelle ? | 22 prédictions J-2 à J-5 en production (14 au 20/02/2026), 1 seul jour ROUGE : non concluant. Meilleure estimation : rejeu hors saison, borne haute, ROUGE 91 à 92 % de rappel pour 55 à 59 % de précision. |

Constat nouveau : `predict_range` décompte deux fois J0 et J+1 dans le quota ROUGE/BLANC, ce qui coûte environ 4 points de rappel ROUGE à J+2 (non corrigé, hors périmètre).

## A. Ré-entraînement du modèle ML

**Méthode (`ml_train.py`).**

- **Variables.** Le script appelle `ml_scorer._build_features` tel quel : une seule source de vérité. Seul ajout, un paramètre optionnel `rte_lag_fn` (défaut `None`, comportement d'inférence inchangé).
- **Information disponible.** Chaque ligne reproduit ce que la production sait à 18 h le jour D pour la cible T = D + h, avec h de 2 à 5 :
  - `forecasts` commence à D, avec `target_idx = h` ;
  - couleurs EDF connues jusqu'à D+1 ;
  - `rte_daily` réalisé connu jusqu'à D-1.
- **Données d'entraînement.** 9 410 lignes, soit 2 354 jours réels, du **2019-09-03 au 2026-02-11**. Les couleurs vont jusqu'au 20/02, mais la météo observée s'arrête au 11/02 : au-delà, le dump ne contient que des prévisions, écartées.
- **Modèle.** Même modèle qu'avant : GradientBoosting, mêmes hyperparamètres, pondération ROUGE 25, BLANC 3, BLEU 1. Mêmes 33 variables, dans le même ordre, et même format de pkl (`model`, `scaler=None`, `metadata`).
- **Validation.** Saison laissée de côté (7 plis). Pour l'objectif « ML seul », les seuils sont choisis en validation imbriquée, sans la saison testée.

**ML seul, hors échantillon, J+2 à J+5 poolés (ROUGE VP/FP/FN).**

| Modèle, seuils | Périmètre | ROUGE | Rappel | Précision |
|---|---|---|---|---|
| Ancien (`ml_model_2024-08.pkl`), 0,19 | 2024-2025 + 2025-2026 (inédites pour lui) | 15/13/105 | **12,5 %** | 54 % |
| Nouveau, 0,19 | idem | 95/70/25 | **79,2 %** | 58 % |
| Nouveau, 0,19 | 7 saisons | 439/254/105 | 80,7 % | 63,4 % |
| Nouveau, objectif « rappel ≥ 90 % » (seuils imbriqués 0,02 à 0,07) | 7 saisons | 482/445/62 | 88,6 % | 52,0 % |

Par horizon, nouveau modèle à 0,19 : rappel ROUGE 82 % (J+2), 80 % (J+3), 79 % (J+4), 81 % (J+5), précision 63 à 64 %. Sur les 7 saisons, l'ancien affiche 51 % de rappel et 92 % de précision, mais c'est un mélange : 5 de ces saisons faisaient partie de son entraînement. Hors échantillon, il ne retrouve plus rien : 0 ROUGE sur 32 en 2025-2026.

**Compromis de l'objectif « 90 % ».** Pour atteindre 90 % de rappel, le ML seul doit descendre à un seuil de 0,03 : précision 52 %, contre 63 % à 0,19. Dans le pipeline complet, ce seuil bas n'apporte que +3 ROUGE sur 7 saisons, pour +55 fausses alertes (tableau suivant). Le pipeline complet dépasse déjà 90 % de rappel quel que soit le seuil ML entre 0,05 et 0,30. Les seuils retenus à l'inférence restent donc **0,19 / 0,20, inchangés**, stockés dans les métadonnées. L'objectif « ML seul » et ses seuils sont conservés pour information (`metadata.ml_objective`).

**Pipeline complet rejoué** (`predict_day` + quotas simulés + cohérence thermique, poids par défaut, modèles hors saison ; J+2 à J+5 cumulés) :

| ML dans le pipeline | 7 saisons ROUGE | Rappel / préc. ROUGE | 2024-2026 ROUGE | Rappel / préc. 2024-2026 | Rappel / préc. BLANC 7 saisons |
|---|---|---|---|---|---|
| Aucun ML | 444/260/100 | 81,6 % / 63,1 % | 97/82/23 | 80,8 % / 54,2 % | 64 % / 53 % |
| Ancien | 495/315/49 | 91,0 % / 61,1 % | 107/106/13 | 89,2 % / 50,2 % | 58 % / 55 % |
| **Nouveau, 0,19** | **497/342/47** | **91,4 % / 59,2 %** | **111/105/9** | **92,5 % / 51,4 %** | 57 % / 54 % |
| Nouveau, seuils « objectif 90 % » | 500/397/44 | 91,9 % / 55,7 % | 114/120/6 | 95,0 % / 48,7 % | 54 % / 54 % |

Avec les poids de production : ancien 500/377/44, nouveau 497/400/47. Sur 2024-2026 : ancien 110/130/10, nouveau 111/124/9.

Balayage du seuil ROUGE du nouveau modèle dans le pipeline : le rappel reste identique de 0,05 à 0,30 (497 VP) et baisse à partir de 0,40 (493). Seules les fausses alertes varient, de 378 à 334. Le seuil BLANC (0,10, 0,20 ou 0,30) n'a aucun effet mesurable. Le choix imbriqué sur le pipeline désigne 0,30, en bord de plateau. On garde 0,19 pour conserver une marge côté rappel, au prix de 8 fausses alertes sur 7 saisons.

**Décision : remplacement effectué.**

- **Critère 1** (rappel ROUGE hors échantillon au moins égal) : largement tenu, 79 % contre 12,5 % sur les saisons inédites.
- **Critère 2** (pipeline non dégradé) : tenu là où la comparaison est loyale. Sur 2024-2026, le rappel passe de 89,2 à 92,5 % et la précision de 50,2 à 51,4 % ; en BLANC, de 172 à 181 VP, avec 115 FP au lieu de 117.
- **Sur les 7 saisons**, le nouveau modèle fait +2 VP et +27 FP (poids par défaut), ou -3 VP et +23 FP (poids de production). L'écart vient entièrement de 2019-2024, que l'ancien modèle connaissait par cœur. La saison 2026-2027 sera inédite pour les deux modèles.
- **Retour arrière** : `cp ml_model_2024-08.pkl ml_model.pkl`.

**Vérifications.**

- `ml_scorer` charge le nouveau pkl sans erreur (`GBM_v4_horizon_J2J5`).
- `compute_ml_score` renvoie les mêmes 5 clés.
- Les seuils lus à l'inférence sont ceux des métadonnées (0,19 / 0,20).
- `tests/test_ml_retrain.py` : 14 tests, dont l'absence de fuite (couleur de la veille connue à J+2 et inconnue à J+3, pas de RTE de la veille). Suite complète : 759 passés, 2 ignorés.
- Aucune modification de `predictor.py`, `confusion_zone_ml.py` ni de son pkl.

**Constat annexe, non corrigé (hors périmètre, `predictor.py`).** `predict_range` compte deux fois les couleurs de J0 et J+1 dans le quota. `get_remaining_days()` les inclut déjà, puis la boucle les décrémente à nouveau (`predictor.py`, bloc « Decrementer le quota meme pour les confirmees »). Rejoué, ce double décompte coûte à J+2 : 124 VP ROUGE sans double décompte, 119 avec. Sur J+2 à J+5 : +13 VP pour +12 FP en le supprimant. Correctif d'une ligne à confier à @fullstack, avec re-rejeu avant déploiement. Il explique aussi l'écart entre le rappel J+2 de l'audit (93 %, protocole sans double décompte) et celui du protocole fidèle (88 %).

## B. Poids du scoring : production contre valeurs par défaut

**Question du fondateur** : « poids dérivés ? Et alors, par exemple dimanche toujours bleu, quel est le souci ? »

**Règle et poids ne jouent pas au même endroit.**

- Une **règle** EDF (R2 : pas de ROUGE le week-end ni les jours fériés ; R3 : pas de BLANC le dimanche) est appliquée **après** le calcul du score. Quel que soit le poids, un dimanche sort BLEU. Le poids n'a donc aucun rôle à jouer pour « dimanche toujours bleu » : c'est déjà garanti.
- Le **poids** `jour_semaine` multiplie un sous-score fixe par jour (`predictor._score_weekday_v2` : lundi 50, mardi 65, mercredi 70, jeudi 65, vendredi 45, week-end 8). Il ajoute donc une constante à **tous les jours ouvrés**, sans regarder la météo. Avec 0,3553 au lieu de 0,08, un mercredi gagne 24,9 points au lieu de 5,6 (+19), un vendredi 16,0 au lieu de 3,6 (+12). En contrepartie, la température (0,346 au lieu de 0,38), la consommation (0,05 au lieu de 0,18) et la pression (absente, repli à 0,07) pèsent moins. Le poids ne « découvre » pas la règle du dimanche ; il pousse les jours ouvrés doux vers les seuils ROUGE et BLANC.

**Mesure.** Rejeu du pipeline complet (`predict_day` + quotas simulés + cohérence thermique) à 18 h le jour D, J+2 à J+5, 7 saisons, météo observée, ML livré. Poids « prod » = dernière ligne non rejetée de `weights_history` (2026-02-14, `jour_semaine` 0,3553, sans `pression`). Poids « défaut » = `Config.DEFAULT_WEIGHTS`. Effectifs cumulés sur J+2 à J+5 (544 jours-horizons ROUGE, 1 161 BLANC).

Protocole de l'audit (`pipeline.py`) :

| Poids | ROUGE VP / FP / FN | Rappel ROUGE | Précision ROUGE | Rappel BLANC | Précision BLANC |
|---|---|---|---|---|---|
| Production | 522 / 384 / 22 | 96 % | 58 % | 63 % | 52 % |
| Défaut | 514 / 320 / 30 | 94 % | 62 % | 57 % | 55 % |

Protocole fidèle à la production (`pipeline2.py`, voir A) :

| Poids | ROUGE VP / FP / FN | Rappel ROUGE | Précision ROUGE | Rappel BLANC | Précision BLANC |
|---|---|---|---|---|---|
| Production | 500 / 377 / 44 | 92 % | 57 % | 63 % | 52 % |
| Défaut | 495 / 315 / 49 | 91 % | 61 % | 58 % | 55 % |

Par saison (protocole fidèle, ROUGE VP/FP/FN cumulés J+2 à J+5) :

| Saison | Production | Défaut |
|---|---|---|
| 2019-2020 | 69/103/3 | 68/90/4 |
| 2020-2021 | 81/12/7 | 81/11/7 |
| 2021-2022 | 76/28/12 | 77/29/11 |
| 2022-2023 | 83/64/5 | 83/58/5 |
| 2023-2024 | 81/40/7 | 79/21/9 |
| 2024-2025 | 78/32/10 | 75/33/13 |
| 2025-2026 (au 20/02) | 32/98/0 | 32/73/0 |

**Lecture.** Les poids de production attrapent **5 jours-horizons ROUGE de plus sur 7 saisons** (moins d'un par saison et par horizon) et font **62 fausses alertes ROUGE de plus** (environ 2 par saison et par horizon, concentrées sur les hivers doux : 2023-2024 et 2025-2026). Côté BLANC : +5 points de rappel, -3 points de précision. Ce n'est ni une catastrophe ni un gain : le rappel ROUGE, priorité n°1, est au même niveau.

**Recommandation (aucun poids modifié).** Ne pas changer les poids avant novembre pour « faire propre » : la mesure ne le justifie pas. En revanche, **geler le recalcul bimensuel** (`bimonthly_weights`) avant le 1er novembre. Ce sont ses dérives successives qui ont produit ces poids, à partir de lignes de backtest (audit B3), et la prochaine dérive ne sera pas testée. Toute évolution de poids passera par ce même rejeu avant déploiement.

## C. Performance réelle

**(1) Production réelle, seule mesure en conditions réelles (prévisions météo réelles).** Prédictions émises entre le 12 et le 20 février 2026, couleur prédite d'origine (`predictions.couleur_originale`, cohérente avec la table `performance`), cibles du 14 au 20 février 2026.

| Horizon | Prédictions | Correctes | ROUGE réels attrapés | Alertes ROUGE confirmées | BLANC réels attrapés |
|---|---|---|---|---|---|
| J-2 | 7 | 4 | 1 sur 1 | 1 sur 2 | 1 sur 2 |
| J-3 | 6 | 3 | 1 sur 1 | 1 sur 3 | 0 sur 2 |
| J-4 | 5 | 2 | 1 sur 1 | 1 sur 4 | 0 sur 2 |
| J-5 | 4 | 0 | 0 sur 1 | 0 sur 2 | 0 sur 2 |
| **Total** | **22** | **9** | **3 sur 4** | **3 sur 11** | **1 sur 8** |

Un seul jour ROUGE (17/02). Le 18, le 19 et le 20 février (BLANC, BLANC, BLEU) ont été annoncés ROUGE à J-3/J-4. Ce n'est **pas une mesure de performance** : 7 dates, 1 ROUGE, 3 versions du code en 6 jours. Aucun pourcentage ne doit en être tiré.

**(2) Rejeu honnête, saison exclue, météo observée (J+2 à J+5).** C'est la meilleure estimation disponible : voir le tableau de la section A (pipeline complet avec le ML hors saison). Il faut le lire comme une **borne haute** : la météo y est parfaite, et les réglages manuels du pipeline ont été calibrés sur ces mêmes saisons.

**Pourquoi l'historique compte bien.** Les 7 saisons servent à entraîner **et** à évaluer, mais jamais sur les mêmes jours. Pour mesurer 2024-2025, le modèle est entraîné sur les 6 autres saisons, puis il prédit 2024-2025 qu'il n'a jamais vue ; on recommence pour chaque saison et on additionne. Les seuils sont choisis de la même façon, sans la saison testée. Chaque jour est donc prédit par un modèle qui ne l'a pas vu : c'est ce qui rend le chiffre honnête, tout en utilisant tout l'historique.

**Données de production qui manquent dans le dépôt.** Le dump s'arrête au 20/02/2026 (export du 23/02). Manquent :

- les couleurs EDF du 21/02 au 31/08/2026 (dont les ROUGE de mars) ;
- toutes les prédictions et évaluations (`predictions`, `performance`) émises après le 20/02/2026, en particulier les J-2 à J-5 de mars ;
- `weather_forecast_log` (vide dans le dump) : sans lui, impossible de séparer l'erreur météo de l'erreur de l'algorithme ;
- `rte_daily` du 01/01/2025 au 10/02/2026 (aucune ligne) ;
- `weights_history` après le 14/02/2026 (poids réellement servis aujourd'hui).

**Comment les obtenir.** Sur la prod Replit : `/admin` → onglet Actions → « Exporter DB → dump », puis commiter `db_dump.json` (tables predictions, actuals, performance, weather_cache, weather_forecast_log, rte_daily, weights_history ; pas de données personnelles). À défaut, export CSV mensuel admin : `GET /api/performance/csv?month=3&year=2026` (en-tête `Authorization: Bearer <mot de passe admin>`), un fichier par mois de février à mai 2026. Dès réception : recalculer le tableau (1) ci-dessus, puis relancer `ml_train.py`, rejouer le pipeline et seulement alors `ml_train.py --install`. Le ré-entraînement n'intègre une date que si `weather_cache` contient sa météo **observée** (description « archive ») : dans le dump actuel, les dates après le 11/02/2026 n'ont que des prévisions.

## Limites

- **Météo parfaite.** Tous les rejeux utilisent la météo observée à la place des prévisions : ce sont des bornes hautes pour J+2 à J+5. L'erreur de prévision n'est pas mesurable avec le dépôt (`weather_forecast_log` vide).
- **Pipeline en échantillon.** Les constantes manuelles de `predictor.py` et le micro-ML ont été réglés sur ces saisons. Seul le ML est validé hors échantillon.
- **Comparaison biaisée en faveur de l'ancien modèle** sur 2019-2024, qu'il a appris par cœur. La comparaison loyale se limite à 2024-2025 et 2025-2026 : 2 saisons, 30 jours ROUGE distincts.
- **RTE.** Aucune donnée `rte_daily` du 01/01/2025 au 10/02/2026 : sur 2025, les variables de consommation prennent leur valeur de repli, à l'entraînement comme à l'évaluation. En production, `rte_daily` ne reçoit que la consommation réalisée de la veille (pas le nucléaire ni le gaz). À J+2 et au-delà, 5 des 10 variables RTE sont donc constantes : le modèle les ignore, sans effet de bord.
- **Choix du seuil 0,19.** Il vient d'un balayage du pipeline sur les 7 saisons, avec des modèles hors saison. La sélection imbriquée préférait 0,30 ; l'écart est de 8 fausses alertes sur 7 saisons.
- **Saison 2025-2026 tronquée** au 20/02/2026 : les ROUGE de mars manquent, à l'entraînement comme à l'évaluation.
- **Corrections apprises et vigilance** : non rejouées (absentes du dump).
