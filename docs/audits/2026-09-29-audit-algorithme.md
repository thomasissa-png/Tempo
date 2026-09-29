# Audit de l'algorithme de prédiction Tempo (2026-09-29)

Audit indépendant, en lecture seule. Périmètre : `predictor.py`, `ml_scorer.py`, `confusion_zone_ml.py`, `config.py`, `performance_tracker.py`, backtests du dépôt, données réelles de `db_dump.json` (exporté le 2026-02-23 : couleurs EDF 2019-09-01 → 2026-02-20, météo observée Open-Meteo Archive, `rte_daily`). Aucune donnée inventée ni simulée. Scripts d'analyse : `/tmp/claude-0/-home-user-Tempo/2b9dc741-054e-5751-8851-bbfc6791b022/scratchpad/algo/` (`common.py`, `loso.py`, `loso_budget.py`, `pipeline.py`).

> **Suite donnée le 2026-09-29** : les chiffres de backtest (F1 83,1 %, etc.) ont été retirés de toutes les pages publiques, de llms.txt, des articles et des prompts d'agents. Seule la performance mesurée en conditions réelles (J+2 à J+5) sera publiée (`site_facts.PERFORMANCE_POLICY`).

## Synthèse

**Note : 4/10.** Le moteur de règles EDF et la logique de quota sont solides. Mais les chiffres affichés publiquement ne mesurent pas ce qu'ils prétendent mesurer. Le modèle ML livré est périmé, et la seule chose qui compte (J+2 à J+5 avec de vraies prévisions météo) n'a jamais été mesurée.

**5 constats majeurs**

1. **Les chiffres annoncés (F1 83,1 %, précision 85,4 %, rappel 81,0 %, exactitude 94,1 %) sont surtout mesurés sur des données d'entraînement.** Ils portent sur 2 364 jours, dont 1 827 (77 %) ont servi à entraîner le modèle. Le seuil 0,19 a été choisi sur ce même jeu, avec la météo observée et l'information disponible à J+1. Ces chiffres sont publiés via `site_facts.py:104-116` (`/methodologie`, `llms.txt`).
2. **`ml_model.pkl` a été entraîné sur 2019-09 → 2024-08 seulement**, alors que les métadonnées annoncent « 2019-2026 ». Sur ses 5 saisons d'entraînement, il retrouve 105 ROUGE sur 106 : c'est de la mémorisation. Sur les 2 saisons qu'il n'a jamais vues (2024-2026), il n'en retrouve que **6 sur 30 (20 %)**, même avec une météo parfaite. Sur 2025-2026, il en retrouve 0 sur 8.
3. **Mesure honnête avec une météo parfaite** (validation en laissant une saison de côté, avec l'information réellement disponible à J+h) :
   - ML seul avec variables de quota : rappel ROUGE **86 % à J+2, 83 % à J+5**, précision de 65 à 66 %.
   - Règle de température seule : 68 % de rappel, 69 % de précision.
   - Pipeline complet rejoué : 93 à 96 % de rappel pour 56 à 63 % de précision. C'est une borne haute, car ses seuils manuels ont été calibrés sur ce même historique.
4. **L'effet de l'erreur météo n'est pas mesuré.** `weather_forecast_log` est vide et aucune prévision n'est archivée. En production, les données disponibles se limitent à 14 évaluations J-2 à J-5, sur 5 dates, avec **un seul jour ROUGE**. Rien de concluant.
5. **La configuration de production n'est pas celle qui a été testée, et les correctifs s'empilent.**
   - Les poids auto-recalibrés du dump donnent `jour_semaine` à 35 % au lieu de 8 %. La pression n'y figure plus, donc la somme des poids vaut 1,07.
   - Les poids de début de saison ne s'activent jamais.
   - Le micro-ML est entraîné sur les sorties du backtest.
   - 11 commits de réglage, dont 4 retours arrière, entre le 11 et le 17 mars.
   - `backtest_results.json` a été produit avec une météo **synthétique**.

**Verdict.** L'algorithme ne doit pas être réécrit avant novembre. Il faut en revanche : figer une configuration testée, réentraîner ou encadrer le ML, retirer les chiffres trompeurs et **mesurer enfin J+2 à J+5 en conditions réelles** (journal des prévisions et backtest sur prévisions archivées). Sans ça, personne (ni nous, ni les utilisateurs) ne sait si le rappel ROUGE réel à J+4 vaut 90 % ou 50 %.

## 1. Architecture réelle

**Pipeline** (`predictor.py:343-833`, appelé par `predict_range` à `predictor.py:1051`) :

1. **Sept sous-scores pondérés** (`predictor.py:393-489`) : température avec refroidissement éolien, budget, jour de semaine, gradient, clustering, C_nette estimée à partir de la météo, pression. S'y ajoutent des bonus hors pondération : vague de froid jusqu'à +25 (`:432`) et vigilance jusqu'à +20 (`:439`).
2. **Plusieurs ajustements du score** avant la décision :
   - plafond du budget au-dessus de 6 °C (`:472`) ;
   - atténuation selon la source météo (`:495`) ;
   - bonus d'urgence multiplicatif (`:504`) ;
   - corrections apprises (`:512`).
3. **Décision.** Deux seuils dynamiques, ROUGE et BLANC, calculés à partir de la température (`:539-560`), plus une modulation saisonnière (`:551`). Viennent ensuite :
   - micro-ML de la zone 50-70 (`:571`) ;
   - 3 surcharges par le ML (`:603-633`) ;
   - forçage par densité critique ou progressive, pour ROUGE (`:660-707`) et pour BLANC (`:709-739`) ;
   - règles R1 à R4 (`:746-776`) ;
   - échange a posteriori pour la cohérence thermique (`:840`).

Au total, **4 étages de décision peuvent se contredire** : score, micro-ML, ML, densité. Dans `config.py` (l. 100-330), j'ai compté plus de 70 constantes numériques, sans celles codées en dur dans `predictor.py`. Les commentaires indiquent qu'elles ont été « calibrés sur le backtest 2365 jours » (`config.py:184`, `predictor.py:537`), avec seulement 137 jours ROUGE dans cet historique.

**Bugs et incohérences constatés**

| # | Emplacement | Constat | Gravité |
|---|---|---|---|
| B1 | `performance_tracker.py:2075-2098` + `predictor.py:380` | Le recalcul produit 6 poids, sans `pression`. `predict_day` retombe alors sur 0,07, et la somme des poids vaut 1,07. | Haute |
| B2 | `predictor.py:372` | Les poids de début de saison ne s'appliquent que si `weights == DEFAULT_WEIGHTS`. Après un seul recalcul, novembre et décembre n'utilisent plus jamais `WEIGHTS_EARLY_SEASON`. | Haute |
| B3 | dump `weights_history` | 18 recalculs acceptés et 5 rejetés en 3 jours (12 au 14 février), sur les mêmes 597 à 600 lignes, qui sont des prédictions de **backtest**. Les poids ont dérivé jusqu'à `jour_semaine` = 35,5 % et `consommation_rte` = 5 %. `get_current_weights()` sert ces poids en production. | Haute |
| B4 | `performance_tracker.py:2060-2098` | Le « poids » appris est une importance par permutation d'une régression logistique, lissée avec l'ancien poids. Ce n'est pas le coefficient optimal d'une somme linéaire : le recalcul n'optimise pas le score qu'il modifie. | Moyenne |
| B5 | `ml_scorer.py:6-11` vs `:104-106` vs métadonnées | La docstring annonce un rappel de 23,3 %, le commentaire 81,0 %, et les métadonnées indiquent `date_range 2019-09-01 to 2026-02-12` pour `train_size 1827` (qui s'arrête en réalité au 2024-08-31). | Moyenne |
| B6 | `ml_scorer.py:108` vs `predictor.py:603` | La surcharge « ML fort » se déclenche à P(ROUGE) ≥ 15 %, indépendamment du seuil 0,19 qui a fait l'objet du commit d1e90aa. Seule la branche 1b utilise 0,19. | Moyenne |
| B7 | `ml_scorer.py:223, 266-289` | Décalage entre entraînement et production. À J+2 et au-delà, le lag RTE de la veille n'existe pas : le modèle reçoit des médianes et `has_rte=0`, valeurs quasi absentes de l'entraînement. Au-delà de J+2, la couleur de la veille vaut « BLEU » par défaut. | Haute |
| B8 | `confusion_zone_ml.py:11,58` | Le micro-ML est entraîné sur `backtest_predictor_results.json`, c'est-à-dire sur les sorties du pipeline lui-même, avec la météo observée. Circularité : tout backtest ultérieur est en échantillon. Son F1 en validation croisée est de 0,374 ± 0,128 (pkl). | Moyenne |
| B9 | `predictor.py:588` puis `:636` | La raison « CZ-ML » est écrasée par `raison_ml = ""`. L'admin ne voit jamais quand le micro-ML a décidé. | Faible |
| B10 | `predictor.py:765` | R4 (5 ROUGE consécutifs maximum) n'examine que les couleurs réelles, pas les ROUGE prédits dans la même série. | Faible |
| B11 | `predictor.py:513`, `:360` | L'horizon des corrections et `d_left` sont calculés depuis `date.today()` : faux dans tout backtest (sans effet en production). | Faible |
| B12 | `backtest_full.py:7,137-179` | Il génère une **météo synthétique** (climatologie + bruit par hachage) : c'est l'origine de `backtest_results.json`. Cela viole la règle absolue du projet. | Haute (fiabilité) |

**Historique.** Le dépôt ne conserve que l'historique depuis le 2026-03-09. Entre le 11 et le 17 mars, 11 commits ont touché `predictor.py`, dont 4 retours arrière. Le fil est le suivant :

- seuil thermique de densité passé à 7 °C, puis ramené à 10 °C ;
- passage de « slack ≤ 1 » à « slack ≤ 0 » ;
- retour à la version du 8 mars (commit 151db9b), après avoir raté les ROUGE des 16, 17 et 18 mars.

Chaque réglage répondait à 1 à 3 jours observés : c'est le signe type d'un sur-ajustement en direct.

## 2. Validité des chiffres annoncés

**Les 4 « backtests » ne mesurent pas la même chose, d'où leurs contradictions :**

| Source | Script | Conditions | ROUGE rappel / précision | Nature réelle |
|---|---|---|---|---|
| `backtest_results.json` | `backtest_full.py` | Météo **synthétique**, 712 jours (2023-2026) | 46,2 % / 27,9 % | Invalide : à supprimer |
| `backtest_predictor_results.json` | `backtest_predictor.py` | Météo observée, 2 365 jours, config de l'époque | 95,6 % / 61,2 % | Borne haute, voir les fuites ci-dessous |
| `backtest_rte_results.json` | `backtest_rte.py` | Méthode officielle RTE appliquée à la consommation **réalisée** du jour, 488 jours | 80,0 % / 70,6 % | Mesure la méthode RTE avec la réponse connue, pas notre prédicteur |
| Commentaire `ml_scorer.py:104-106`, repris dans `site_facts.py` | Aucun script dans le dépôt | 2 364 jours, ML seul, seuil 0,19 | 81,0 % / 85,4 % (F1 83,1 %) | 77 % des jours ont servi à l'entraînement ; seuil choisi sur ce jeu |
| Métadonnées `ml_model.pkl` | Aucun script | Test sur 530 jours (2024-2026), hors échantillon | **23,3 % / 46,7 %** | Seul chiffre honnête du ML, jamais publié |

**Fuites de données identifiées**

- **Météo observée utilisée comme « prévision »** dans tous les backtests. Les docstrings le reconnaissent (`backtest_predictor.py:14-17`).
- **Modèle évalué sur ses données d'entraînement**, avec un seuil sélectionné sur le même jeu (commit d1e90aa). Ce commit a en plus **baissé le rappel ROUGE** (de 83,2 % à 81,0 %) pour gagner en précision, à l'inverse de la priorité n°1 du projet.
- **Information J+1 présentée comme J+2.** `backtest_predictor.py` se dit « J+2 », mais `actuals_cache[ds]` est rempli chaque jour (`:291`). La couleur réelle de la veille alimente donc le clustering, et le ML lit le RTE réel de la veille dans `rte_daily`. En production à J+3 et au-delà, aucune de ces deux informations n'existe.
- **Paramètres manuels calibrés sur les jours évalués** (`config.py:184`, `predictor.py:537`), et micro-ML entraîné sur les sorties du backtest (B8).
- **Budget : pas de fuite.** Les quotas restants sont calculés à partir des couleurs passées, et 22/43 est une règle connue. En revanche, l'hypothèse « EDF place toujours ses 22 ROUGE » est fausse pour 2019-2020, qui n'en compte que 18 dans les données réelles. La pression de densité peut donc forcer des ROUGE qui ne viendront pas.

## 3. Mesure hors échantillon sur données réelles

**Protocole.** Validation « leave-one-season-out » sur 7 saisons (2019-2020 à 2025-2026, arrêtée au 2026-02-20), soit 2 356 jours dont 136 ROUGE et 291 BLANC.

- Les 33 variables sont reconstituées à l'identique de `ml_scorer._build_features` (`common.py`), avec les mêmes hyperparamètres et pondérations (25/3/1) et les seuils 0,19/0,20.
- L'**information disponible à J+h** est reproduite : prédiction émise à 18 h le jour D = T-h, couleur EDF connue jusqu'à D+1, RTE observé jusqu'à D. Au-delà, on applique les valeurs de repli du code.
- Les règles R1 à R3 sont appliquées à tous les modèles.
- Le **pipeline complet** est rejoué comme dans `predict_range`, avec quotas simulés, clustering prospectif et cohérence thermique (`pipeline.py`).
- Commande : `DATABASE_PATH=<scratch>/x.db /root/.local/share/uv/tools/pytest/bin/python loso.py` (puis `loso_budget.py`, `pipeline.py default|prod shipped|loso|none`).

**Résultats, toutes saisons (météo observée = « météo parfaite »)**

| Modèle | Horizon | Exactitude | ROUGE VP/FP/FN | Rappel ROUGE | Précision ROUGE | Rappel / préc. BLANC |
|---|---|---|---|---|---|---|
| Toujours BLEU | - | 81,9 % | 0/0/136 | 0 % | - | 0 % / - |
| Climatologie mois × jour de semaine | - | 82,8 % | 92/141/44 | 68 % | 39 % | 25 % / 46 % |
| Règle de température (seuils appris hors saison) | - | 88,0 % | 93/41/43 | 68 % | 69 % | 68 % / 52 % |
| ML livré (`ml_model.pkl`) | J+1 | 95,0 % | 111/14/25 | 82 % | 89 % | 85 % / 78 % |
| ML livré | J+2 / J+5 | 92,0 / 90,0 % | 92/7/44 ; 60/4/76 | **68 % ; 44 %** | 93 % ; 94 % | 59 % / 76 % |
| ML ré-entraîné LOSO | J+1 | 90,6 % | 109/54/27 | 80 % | 67 % | 57 % / 65 % |
| ML LOSO | J+2 / J+5 | 89,8 / 89,2 % | 108/55/28 ; 95/38/41 | 79 % ; 70 % | 66 % ; 71 % | 43 % / 66 % |
| ML LOSO + variables de quota | J+2 / J+5 | 90,5 / 90,0 % | 117/63/19 ; 113/59/23 | **86 % ; 83 %** | 65 % ; 66 % | 58 % / 66 % |
| Pipeline complet, poids par défaut, ML livré | J+2 / J+5 | 88,3 / 88,1 % | 126/85/10 ; 129/75/7 | 93 % ; 95 % | 60 % ; 63 % | 55 % / 56 % |
| Pipeline, ML et micro-ML désactivés | J+2 / J+5 | 88,4 / 87,5 % | 118/73/18 ; 110/68/26 | 87 % ; 81 % | 62 % ; 62 % | 61 % / 55 % |
| Pipeline, ML LOSO à la place du ML livré | J+2 / J+5 | 88,1 / 87,9 % | 128/93/8 ; 128/78/8 | 94 % ; 94 % | 58 % ; 62 % | 53 % / 56 % |
| Pipeline, poids de production du dump | J+2 / J+5 | 87,3 / 86,9 % | 129/100/7 ; 130/95/6 | 95 % ; 96 % | 56 % ; 58 % | 63 % / 52 % |

Lecture :

1. **« Toujours BLEU » obtient 81,9 % d'exactitude.** L'exactitude globale ne doit jamais servir d'argument.
2. **Le ML livré s'effondre hors de ses saisons d'entraînement.** À J+1, il retrouve 105 ROUGE sur 106 sur 2019-2024, contre 6 sur 30 sur 2024-2026. Ré-entraîné honnêtement, il tient 79 % à J+2 et 70 % à J+5.
3. **Les quotas sont le signal le plus rentable.** Ajoutés au ML LOSO, ils font gagner 7 à 13 points de rappel.
4. **Le pipeline complet** atteint 93 à 96 % de rappel, pour environ 13 fausses alertes ROUGE par saison. Mais sa partie manuelle est calibrée sur ces mêmes jours. L'écart avec l'estimation honnête (ML + quotas, 83 à 86 %) donne l'ordre de grandeur du sur-ajustement probable, soit 5 à 10 points.

**Par saison, pipeline par défaut à J+2** (VP/FP/FN ROUGE) :

| 2019-20 | 2020-21 | 2021-22 | 2022-23 | 2023-24 | 2024-25 | 2025-26 (jusqu'au 20 fév.) |
|---|---|---|---|---|---|---|
| 17/22/1 | 20/4/2 | 20/7/2 | 22/17/0 | 20/9/2 | 19/7/3 | **8/19/0** |

La saison en cours au moment de l'export compte 19 fausses alertes ROUGE pour 8 vraies (précision de 30 %). L'hiver doux combiné à la pression de densité produit exactement le travers corrigé puis dé-corrigé en mars.

**Matrice de confusion, pipeline par défaut, J+2** (lignes = réel ; colonnes = BLEU / BLANC / ROUGE) :

- BLEU : 1 791 / 119 / 15
- BLANC : 60 / 160 / 70
- ROUGE : 3 / 7 / 126

**Limite essentielle.** Tous ces chiffres supposent une météo parfaite. L'horizon n'y change que l'information sur les couleurs et le RTE, pas l'erreur de prévision de température, alors que le signal principal est à ±1 à 2 °C du seuil. La dégradation réelle de J+2 à J+5 est **non mesurable avec les données du dépôt** : `weather_forecast_log` est vide et aucune prévision n'est archivée. Je ne chiffre donc aucune dégradation. L'API Open-Meteo « Previous Runs » fournit des prévisions réelles archivées à 1-7 jours d'échéance, depuis janvier 2024 pour la plupart des modèles et mars 2021 pour la température GFS. Elle est bloquée par le proxy de cet environnement (HTTP 403) : l'essai est à refaire depuis Replit.

## 4. Performance réelle en production

Le dump ne contient que **19 évaluations réelles**, portant sur les jours du 13 au 18 février 2026 et émises entre le 12 et le 17 février (table `performance`, contexte « Couleur officielle EDF »). Les 711 autres lignes sont des lignes de backtest.

| Horizon | Évaluations | Correctes | Détail |
|---|---|---|---|
| J-2 | 5 | 3 | Faux BLANC le 16/02 ; ROUGE prédit le 18/02, réel BLANC |
| J-3 | 4 | 3 | ROUGE prédit le 18/02, réel BLANC |
| J-4 | 3 | 2 | ROUGE prédit le 18/02, réel BLANC |
| J-5 | 2 | 0 | ROUGE du 17/02 manqué (prédit BLEU) ; BLANC du 18/02 manqué |
| **J-2 à J-5** | **14** | **8 (57 %)** | Un seul jour ROUGE (17/02) : attrapé à J-2, J-3 et J-4, manqué à J-5 |

**Pourquoi ce n'est pas concluant**

- Il y a un seul jour ROUGE : l'intervalle de confiance à 95 % d'un rappel de 3 sur 4 va de 30 à 95 % (Wilson).
- Le code a changé 3 fois pendant ces 6 jours (`config.py` `TOOL_UPDATE_DATES` : 15, 19 et 20 février).
- Les prévisions météo de production passaient déjà par le repli Open-Meteo (`weather_cache` : description « open-meteo » pour les dates futures).
- Surtout, **la saison réelle (fin février à mars 2026, dont les ROUGE des 16 au 18 mars) n'est que dans la base de production Replit**, absente du dépôt.

**Ce qu'il faut pour conclure**

1. Exporter la base de production : tables `predictions`, `performance`, `actuals`, `weather_cache`, `weights_history` et `weather_forecast_log` jusqu'au 31 mars 2026.
2. Vérifier que `weather_forecast_log` est réellement alimenté en production.
3. À partir de ces données, calculer par horizon le rappel et la précision ROUGE, en isolant la contribution de l'erreur météo : comparer la température prévue à J-N avec la température observée.

## 5. Recommandations pour la saison 2026-2027

Classées par impact attendu sur le rappel ROUGE J+2 à J+5, puis par risque.

**À faire avant le 1er novembre**

| # | Action | Impact rappel ROUGE J+2 à J+5 | Risque | Effort |
|---|---|---|---|---|
| 1 | **Mesurer en conditions réelles.** Exporter la base de prod 2025-2026 et analyser mars 2026. Vérifier l'écriture de `weather_forecast_log`. Lancer depuis Replit un backtest J+2 à J+5 sur **prévisions archivées réelles** (Open-Meteo Previous Runs, 9 villes, saisons 2024-2025 et 2025-2026) avec `pipeline.py`. | Indirect mais décisif : c'est la seule façon de savoir si le rappel J+4 est de 90 % ou de 50 %, et de régler les seuils sur la bonne réalité | Nul | 1 à 2 j |
| 2 | **Remplacer `ml_model.pkl`** par un modèle ré-entraîné sur les 7 saisons, avec les variables telles qu'elles existent à J+h : pas de faux lag RTE (médianes), couleur de la veille connue seulement à J+2, variables de quota ajoutées. Validation LOSO obligatoire et métadonnées exactes. | Élevé : le ML livré retrouve 20 % des ROUGE hors échantillon ; le ML LOSO avec quotas en retrouve 83 à 86 % | Moyen, à valider en LOSO avant bascule | 1 j |
| 3 | **Figer une configuration testée.** Désactiver le recalcul bimensuel des poids (B1 à B4) tant qu'il n'apprend pas sur de vraies prédictions de production. Réinitialiser `weights_history` en prod. Corriger B2 (poids de novembre et décembre). Désactiver les `learnings` non testés. | Moyen : la configuration de prod diffère de la configuration testée. En météo parfaite, 95 % de rappel mais 16 FP ROUGE par saison au lieu de 13 | Faible | 0,5 j |
| 4 | **Choisir les seuils ML sur l'objectif réel**, par LOSO et à horizon J+2 à J+5 : rappel ROUGE ≥ 90 % sous contrainte de précision, et non F1 maximal. Revenir sur la hausse 0,10 → 0,19, qui a sacrifié du rappel. Unifier B6 (15 % contre 0,19). | Moyen, surtout en fin d'horizon | Faible : plus de fausses alertes, jugées moins coûteuses | 0,5 j |
| 5 | **Encadrer la densité en hiver doux.** 2025-2026 : 19 FP pour 8 VP à J+2. Ne recalibrer qu'après l'action 1, pour ne pas refaire l'aller-retour de mars. Garder la version « slack ≤ 1 », qui a attrapé les 16 à 18 mars. | Protège le rappel de fin de saison | Élevé si on règle à l'aveugle | 1 j après l'action 1 |
| 6 | **Assainir.** Supprimer `backtest_full.py` et `backtest_results.json` (météo synthétique). Remplacer `BACKTEST_ML` dans `site_facts.py` par un chiffre hors échantillon, en précisant « météo observée ». Corriger les docstrings (B5) et B9. | Nul sur le rappel ; élevé sur la crédibilité | Nul | 0,5 j |

**Plus tard (saison en cours ou été 2027)**

- **Simplifier la décision.** Remplacer les 4 étages qui se contredisent (score, micro-ML, ML, densité) par un seul modèle probabiliste calibré : météo prévue, quotas, calendrier. Les règles EDF resteraient des contraintes dures.
- **Propager l'incertitude météo.** Utiliser les ensembles de prévision : proportion de membres sous le seuil plutôt qu'une température moyenne.
- **Calibrer les probabilités affichées** (diagramme de fiabilité par horizon).
- **Corriger B10** (R4 sur les ROUGE prédits).
- **Supprimer le micro-ML**, circulaire, ou le ré-entraîner hors échantillon.
- **Remplacer le recalcul des poids** par un ré-entraînement annuel en septembre, sur la saison écoulée, avec validation LOSO.

## 6. Faut-il publier l'historique de nos prédictions ?

**Avis : oui, à partir du 1er novembre 2026, à condition d'être intégral et de montrer des effectifs avant des pourcentages.** C'est le seul moyen crédible de remplacer les chiffres de backtest, et une différence réelle face aux sites qui n'affichent que des prévisions sans bilan. Mais il faut l'assumer : les premiers chiffres seront modestes et bruités.

**Métriques montrables honnêtement**, par horizon J-2, J-3, J-4, J-5, séparément :

- **Rappel ROUGE** : « X jours rouges sur Y annoncés N jours avant ».
- **Précision ROUGE** : « X alertes rouges sur Z confirmées ».
- Même paire pour BLANC.
- Matrice de confusion 3 × 3.
- Référence de comparaison affichée : « toujours BLEU » et la règle de température.
- **Pas d'exactitude globale en titre** : « toujours BLEU » obtient 82 %.

**Taille d'échantillon.** Intervalles de confiance à 95 % (Wilson) pour un rappel observé de 80 % :

| Jours ROUGE évalués | Intervalle de confiance à 95 % |
|---|---|
| 10 | 49 à 94 % |
| 22 (une saison) | 61 à 93 % |
| 44 (deux saisons) | 65 à 89 % |
| 88 | 70 à 87 % |

Règles d'affichage proposées :

- En dessous de **20 jours ROUGE évalués par horizon**, afficher les effectifs bruts et la mention « échantillon insuffisant ». Aucun pourcentage ROUGE.
- Même règle pour BLANC, à 20 jours.
- Le seuil actuel de 10 prédictions vérifiées (`/methodologie`) est trop bas pour le ROUGE.
- Une saison complète ne donne qu'environ ±16 points de précision statistique : le dire.

**Risques**

- **Novembre et décembre comptent peu de ROUGE**, voire aucun : des cases vides ou des 0/1 visibles tôt.
- **Hiver doux** : le type 2025-2026 donnerait une précision ROUGE autour de 30 %, même en météo parfaite.
- **Comparaison défavorable** avec les 81-85 % affichés aujourd'hui, qui sont à retirer avant (recommandation 6).
- **Tentation du cherry-picking** : ne montrer que J-2, ou que les versions récentes.
- **Changement de version en cours de saison** : l'indiquer, sans jamais filtrer les jours.

**Contenu de la page**

1. **Tableau jour par jour**, sur tous les jours ouvrés de la saison sans exception : date, couleur EDF réelle, puis les couleurs prédites à J-5, J-4, J-3 et J-2.
   - Chaque prédiction est **figée à son émission** (horodatage et version du code), jamais recalculée après coup.
   - Règle unique : la dernière prédiction émise avant 18 h 30 le jour J-N.
2. **Synthèse par horizon** : effectifs et pourcentages avec intervalle de confiance, plus la référence « toujours BLEU ».
3. **Téléchargement CSV** de l'historique complet.
4. **Lien vers la méthode** précisant que les prévisions météo s'y dégradent avec l'horizon.
5. **Aucune suppression** de lignes, y compris lors des pannes : un jour sans prédiction compte comme une prédiction manquée.

Le tableau récapitulatif de `/admin` (grille J-15 à J-1) en est déjà la base technique.

## Limites de l'étude

- **Météo parfaite.** Tous les résultats utilisent la météo observée (Open-Meteo Archive, `weather_cache`). Ils sont donc optimistes pour J+2 à J+5 dans une proportion **non mesurable ici**.
- **Pipeline complet en échantillon.** Ses constantes manuelles et son micro-ML ont été calibrés sur ces mêmes jours ; seule la partie ML a pu être validée hors échantillon.
- **Corrections d'apprentissage non rejouées.** Les `learnings` et la vigilance ne sont pas dans le dump ; le RTE temps réel J+1 est sans objet à J+2.
- **Saison 2025-2026 tronquée au 20 février 2026.** Les ROUGE de mars ne sont connus que par un message de commit.
- **Poids de production.** Les « poids de production » sont ceux de l'export du 2026-02-23, pas forcément ceux de la base Replit actuelle.
- **Variables ML.** Elles sont reconstituées depuis le code de `_build_features`. Le script d'entraînement d'origine n'est pas dans le dépôt : de petits écarts de construction sont possibles, par exemple sur `cold_streak` ou les moyennes 3 et 7 jours. En revanche, la rupture nette entre 2023-2024 (95 % de rappel ROUGE à J+1) et 2024-2025 (27 %) confirme la date de fin d'entraînement.
- **Prévisions archivées inaccessibles.** L'API a été testée depuis cet environnement : bloquée (HTTP 403).

Sources externes : [Open-Meteo Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api), [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api).
