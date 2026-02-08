# Algorithme de Prediction Tempo EDF — Documentation technique

## Vue d'ensemble

L'algorithme TempoForecast predit la couleur Tempo EDF (BLEU, BLANC, ROUGE) pour chaque jour des 15 prochains jours. Il combine 6 facteurs de scoring ponderes, un bonus "vague de froid" hors-poids, et une conversion en probabilites via sigmoide logistique.

**Fichier source** : `predictor.py` (~740 lignes)
**Version** : v2.1 (post-audit complet)

---

## 1. Architecture du scoring

Chaque jour recoit un **score de risque de 0 a 100** calcule comme suit :

```
score_risque = (temp * 0.30) + (budget * 0.20) + (gradient * 0.15)
             + (rte * 0.15) + (clustering * 0.10) + (dow * 0.10)
             + bonus_vague_de_froid
```

Les poids (0.30, 0.20, etc.) sont **ajustables mensuellement** par regression logistique via le module `performance_tracker.py`. Les poids par defaut sont stockes dans `config.py:DEFAULT_WEIGHTS`.

---

## 2. Les 6 facteurs de scoring

### 2.1 Temperature nationale ponderee (poids: 30%)

**Fonction** : `_score_temperature_v2(temp_moy_nationale)`

La temperature moyenne est calculee a partir de **9 villes francaises** ponderees par population, parc de chauffage electrique et impact climatique sur Tempo :

| Ville            | Poids | Justification                                      |
|------------------|-------|----------------------------------------------------|
| Paris            | 20%   | Plus grande agglomeration, forte consommation       |
| Lille             | 14%   | Climat froid, fort parc chauffage electrique        |
| Lyon             | 13%   | Grand bassin, hivers froids                         |
| Strasbourg       | 12%   | Climat continental froid                            |
| Nantes           | 10%   | Facade atlantique, climat modere                    |
| Toulouse         | 9%    | Sud-Ouest, climat intermediaire                     |
| Bordeaux         | 8%    | Climat oceanique doux                               |
| Marseille        | 7%    | Climat mediterraneen, faible impact Tempo           |
| Clermont-Ferrand | 7%    | Massif Central, representatif de l'interieur froid  |

**Changements v3** : Marseille reduit de 12% a 7% (climat doux qui baisait la moyenne), Lille et Strasbourg augmentes (regions froides = plus d'impact Tempo), Clermont-Ferrand ajoute (representatif du Massif Central).

**Grille de scoring** :

| Temp. moy. nationale | Score |
|----------------------|-------|
| < -5 C               | 98    |
| -5 a -2 C            | 90    |
| -2 a 0 C             | 80    |
| 0 a 2 C              | 68    |
| 2 a 4 C              | 55    |
| 4 a 6 C              | 40    |
| 6 a 8 C              | 25    |
| 8 a 10 C             | 15    |
| 10 a 14 C            | 8     |
| > 14 C               | 3     |

**Logique** : Les jours rouges EDF tombent quasi exclusivement quand la temperature nationale descend sous 5 C. La zone critique 0-5 C est celle ou se prennent la majorite des decisions. En dessous de -5 C, c'est quasi certain rouge.

---

### 2.2 Pression budgetaire (poids: 20%)

**Fonction** : `_score_budget_v2(remaining, d_left, target_date)`

EDF doit placer exactement **22 jours rouges** et **43 jours blancs** par saison (1er sept. - 31 mai). Ce facteur mesure l'urgence de placement.

**Profil mensuel historique** (base sur 20 saisons) :

| Mois       | % des 22 jours rouges |
|------------|----------------------|
| Septembre  | 0%                   |
| Octobre    | 0%                   |
| Novembre   | 7% (1-2 jours)       |
| Decembre   | 18% (3-5 jours)      |
| Janvier    | 35% (6-9 jours)      |
| Fevrier    | 23% (4-6 jours)      |
| Mars       | 12% (1-3 jours)      |
| Avril      | 4% (0-1 jour)        |
| Mai        | 1% (0-1 jour)        |

**Calcul** :
1. Compare le nombre de jours rouges restants au nombre attendu a cette periode
2. Si ratio > 2.0 : forte pression (+50 pts)
3. Si ratio > 1.5 : pression moderee (+35 pts)
4. Booste pour les mois historiquement charges (janvier +25, dec/fev +15)
5. Urgence fin de saison : <30 jours restants avec >3 rouges a placer (+25)

---

### 2.3 Gradient thermique (poids: 15%)

**Fonction** : `_score_gradient(forecasts, target_idx)`

Mesure la **chute de temperature entre J-1 et J**. Une baisse brutale est un signal fort car EDF reagit a la demande croissante.

| Chute temp. (J-1 -> J) | Score |
|-------------------------|-------|
| >= 8 C                  | 90    |
| 5-8 C                   | 70    |
| 3-5 C                   | 50    |
| 1-3 C                   | 35    |
| 0-1 C (stable)          | 25    |
| Rechauffement -3 a 0 C  | 15    |
| Fort rechauffement      | 5     |

---

### 2.4 Consommation RTE eco2mix (poids: 15%)

**Fonction** : Score fourni par `rte_client.py:get_consumption_score()`

Utilise les donnees de prevision de consommation electrique de RTE et la disponibilite du parc nucleaire.

**Seuils de consommation nationale** :
- > 80 GW : risque rouge tres eleve
- > 70 GW : risque rouge
- > 60 GW : risque blanc

**Disponibilite nucleaire** :
- < 70% de la capacite (61.37 GW) : risque accru (+15 pts)
- > 85% : parc en forme, risque reduit (-10 pts)

**Attenuation par horizon** :
- J+1 : score RTE complet (100%)
- J+2 a J+3 : attenuation lineaire vers neutre (50)
- J+4+ : score RTE ignore (non pertinent a cet horizon)

---

### 2.5 Clustering / continuite (poids: 10%)

**Fonction** : `_score_clustering(target_date, forecasts, target_idx)`

Les jours rouges EDF arrivent souvent **en serie** (2-4 jours consecutifs quand le froid persiste).

| Situation                                       | Score |
|-------------------------------------------------|-------|
| Hier etait rouge + temp. < 2 C                  | 90    |
| Hier etait rouge + temp. < 5 C                  | 70    |
| Hier etait rouge + radoucissement               | 40    |
| J-1 prevu froid (<2 C) ET J aussi froid (<2 C)  | 60    |
| J-1 et J frais (<4 C)                           | 40    |
| Pas de continuite detectee                       | 20    |

Le systeme utilise un cache des couleurs reelles des 7 derniers jours pour eviter les requetes DB a chaque appel.

---

### 2.6 Jour de la semaine / feries (poids: 10%)

**Fonction** : `_score_weekday_v2(target_date)`

Sur 20 ans d'historique, **0 jour rouge n'a jamais ete place un jour ferie**.

| Jour              | Score |
|-------------------|-------|
| Jour ferie        | 5     |
| Samedi / Dimanche | 8     |
| Lundi             | 50    |
| Mardi             | 65    |
| Mercredi          | 70    |
| Jeudi             | 65    |
| Vendredi          | 45    |

Les 12 jours feries francais sont calcules dynamiquement (incluant Paques via l'algorithme de Meeus) et mis en cache LRU.

---

## 3. Bonus vague de froid (hors-poids)

**Fonction** : `_detect_cold_wave(forecasts, target_idx)`

Detecte 3+ jours consecutifs avec temp. moy. < 2 C dans une fenetre de 5 jours centree sur la date cible.

| Condition                           | Bonus |
|-------------------------------------|-------|
| 100% de la fenetre froide (3+ j.)   | +25   |
| 4 jours froids dans la fenetre      | +20   |
| 3 jours froids                      | +15   |
| 2 jours froids                      | +8    |
| 0-1 jour froid                      | +0    |

Ce bonus s'ajoute **apres** le score pondere, ce qui peut pousser le score au-dela des seuils meme si les facteurs individuels sont moyens.

---

## 4. Decision de couleur

Le score final (0-100) est compare aux seuils :

```
Si score >= 65 ET quota ROUGE > 0  => ROUGE
Si score >= 35 ET quota BLANC > 0  => BLANC
Sinon                              => BLEU
```

Seuils definis dans `config.py` :
- `SEUIL_ROUGE = 65` (abaisse de 70 pour meilleur recall)
- `SEUIL_BLANC = 35` (abaisse de 40)

---

## 5. Calcul des probabilites

**Fonction** : `_compute_probabilities(score, remaining)`

Les probabilites sont calculees via **sigmoide logistique** centree sur chaque seuil :

```
P(rouge) = sigmoid(score, centre=65, pente=0.12)
P(blanc) = sigmoid(score, centre=35, pente=0.08) * (1 - P(rouge))
P(bleu)  = 1 - P(rouge) - P(blanc)
```

La pente (steepness) controle la transition : 0.12 donne une transition douce sur ~20 points autour du seuil (environ 50% de probabilite au seuil exact).

Les quotas epuises forcent la probabilite a 0 (si les 22 rouges ont deja ete poses, P(rouge) = 0 quel que soit le score).

Les probabilites sont normalisees pour sommer a 1.0.

---

## 6. Gestion des quotas dans predict_range

Quand on predit une serie de 15 jours, le systeme **decremente un quota simule** :

1. Charge les quotas reels depuis l'API Tempo (`get_remaining_days()`)
2. Copie les quotas pour simulation
3. Pour chaque jour predit ROUGE : decremente `sim_remaining["ROUGE"]`
4. Pour chaque jour predit BLANC : decremente `sim_remaining["BLANC"]`

Cela evite de predire 25 jours rouges quand il n'en reste que 5.

---

## 7. Couleurs officielles (override)

Si EDF a deja annonce la couleur pour J+1 (via l'API Tempo a 11h), le systeme **n'execute pas l'algorithme** pour cette date :

1. `_load_future_actuals()` charge les couleurs connues depuis la table `actuals`
2. Si une couleur existe pour la date, `_result_confirmed()` cree un resultat avec probabilite = 100% et le flag `confirmed = True`
3. L'algorithme est bypasse, mais le quota est quand meme decremente

---

## 8. Auto-apprentissage

Le systeme se recalibre mensuellement (1er du mois a 2h00) via `performance_tracker.py:recalculate_weights()` :

1. Charge les predictions des 90 derniers jours avec leur resultat reel
2. Entraine une regression logistique multinomiale (scikit-learn) sur les 6 sub-scores
3. Extrait les coefficients comme nouveaux poids
4. Normalise les poids pour sommer a 1.0
5. Verifie que la precision sur les donnees d'entrainement est >= precision actuelle
6. Si oui, stocke les nouveaux poids dans `weights_history`

Les sub-scores (score_temperature, score_budget, etc.) sont stockes en DB avec chaque prediction pour permettre cet apprentissage.

---

## 9. Workflow complet

```
18h00 (scheduler)
  |
  +-- Fetch meteo 16 jours (9 villes ponderees, Open-Meteo gratuit)
  +-- Fetch consommation RTE (J+1)
  +-- predict_range(forecasts, rte_score)
  |     |
  |     +-- Pour chaque jour:
  |     |     +-- Couleur officielle connue? -> _result_confirmed()
  |     |     +-- Sinon -> predict_day() avec 6 facteurs
  |     |     +-- Decrementer quota simule
  |     |
  |     +-- Retourne 16 predictions
  |
  +-- store_prediction() pour chaque prediction
  |     +-- Detecte changements vs cycle precedent
  |     +-- Stocke en DB (source unique de verite)
  |
  +-- Alertes SMS (J+1 a J+3, rouge/blanc seulement)
  |
  API /api/predictions -> lit la DB (pas de recalcul)
```

---

## 10. Sources de donnees

| Source                  | API                                      | Usage                        |
|-------------------------|------------------------------------------|------------------------------|
| Couleurs officielles    | api-couleur-tempo.fr                     | Verification, override J+1   |
| Meteo                   | Open-Meteo (9 villes, 16j gratuit)       | Temperature, vent, precipitations |
| Consommation electrique | RTE eco2mix (OAuth2)                     | Prevision pointe, nucleaire   |
| Jours feries            | Calcul interne (algorithme de Meeus)     | Scoring jour semaine          |

---

## 11. Qualite des donnees meteo

Chaque jour de prevision porte un champ `forecast_quality` :

| Qualite        | Source                     | Attenuation du score    | Horizon typique |
|---------------|----------------------------|-------------------------|-----------------|
| `api`          | Open-Meteo (modeles NWP)  | Aucune (100% confiance) | J+0 a J+16     |
| `simulated`    | Moyennes saisonnieres      | 50% vers neutre (50)   | Si Open-Meteo indisponible |

**Open-Meteo** : API gratuite sans cle, fournit 16 jours de previsions journalieres basees sur les modeles meteorologiques nationaux (ICON, GFS, etc.). Toutes les donnees sont des previsions modele fiables, pas d'extrapolation necessaire.

---

## 12. Limites connues

1. **Horizon J+8 a J+16** : Les modeles meteorologiques perdent en fiabilite au-dela de 7-8 jours. Les predictions a cet horizon sont indicatives.
2. **Donnees RTE** : Seulement fiables pour J+1, attenuees pour J+2/J+3, ignorees au-dela.
3. **Profil mensuel** : Base sur un historique de 20 saisons. Un changement de politique EDF invaliderait ce profil.
4. **Pas de donnees infra-journalieres** : Le scoring utilise des moyennes journalieres, pas les pointes horaires.
5. **Meteo fallback** : Si Open-Meteo est indisponible, le systeme utilise des moyennes saisonnieres (flag `simulated`, attenuation 50%).
