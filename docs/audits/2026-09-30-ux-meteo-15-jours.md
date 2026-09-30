# UX : météo sur les prévisions 15 jours et cases « pas de prévision » de l'historique

Date : 2026-09-30. Auteur : @ux. Destinataires : @fullstack (application), @design (visuel, en parallèle : `docs/audits/2026-09-30-design-meteo-15-jours.md`, non encore présent à l'écriture de ce document).
Demande fondateur : « pour les prévisions des 15 prochains jours, faisons comme l'historique et affichons la météo ». Second sujet : expliquer honnêtement les cases « pas de prévision ».
Ce document ne contient aucun code. Chaque correction : fichier, élément, changement, justification. Priorité P0/P1/P2 (sert à ordonner, tout est à faire).

---

## 0. Décisions en bref

| Question | Décision |
|---|---|
| Quelle météo par jour ? | Une seule valeur : la température moyenne prévue (moyenne pondérée 9 villes, `predictions.temp_moy_prevue`), même format que l'historique : une décimale, virgule, signe degré (« 16,1° »). Pas de min/max. |
| Pourquoi pas min/max ? | Un min/max « moyenné sur 9 villes » ne correspond à aucune météo que le visiteur connaît ; l'historique affiche la moyenne (deux chiffres différents pour le même jour seraient incohérents) ; c'est la moyenne qui alimente le score (température = 38 % des poids). |
| Où ? | Dans chaque carte de `#forecast-container`, directement sous la pastille couleur (et sous « Confirmé par EDF » si présent), à la place de la ligne `fc-temp` actuelle. Mobile d'abord. |
| Explication | Une ligne, une seule fois, en tête du conteneur : « Sous chaque jour : température moyenne prévue en France, pondérée sur 9 villes. » + lien « méthode » vers `/methodologie`. |
| Repère « froid » | Oui, mais P2 et sous condition de validation sur données réelles (section 3.6). Jamais une icône seule, jamais les couleurs Tempo. |
| Sans donnée | Ligne « Météo indisponible » en gris sur la carte concernée ; la ligne d'explication n'apparaît que si au moins une carte a une température. |
| Accueil | Température seulement sous les deux grandes pastilles Aujourd'hui / Demain (P2). Pas sur les 8 petites pastilles. |
| API | `/api/predictions` ne renvoie PAS `temp_moy_prevue` (vérifié dans `app.py`, `SELECT` de `api_predictions`). @fullstack doit l'ajouter : P0. |
| Cases « pas de prévision » | Légende honnête et neutre, liste datée des jours sans calcul, infobulle par case, et surtout : séparer « erronées » de « sans prévision » dans « En bref » (aujourd'hui l'écart 78 % contre 100 % est entièrement dû aux prévisions absentes, pas à des erreurs). |

---

## 1. Constat (vérifié) et limites

Lu : `static/js/app.js` (`loadPredictions`, `createForecastCard`, `renderWeekSummary`, `weekDotHtml`), `app.py` (`api_predictions`, lignes 2103 à 2317), `templates/calendrier.html` (section prévisions), `static/css/style.css` (`.forecast-*`, `.fc-*`, responsive), `prediction_history.py`, `templates/_historique_grille.html`, `_historique_bref.html`, `historique_previsions.html`, captures `v3/calendrier-*.png` et `v2/historique-previsions-ordinateur.png`, audit du jour `2026-09-30-audit-ux-pages.md`.

Faits :
1. **L'API ne renvoie pas la moyenne.** Le `SELECT` de `api_predictions` liste `temp_min_prevue, temp_max_prevue, pression_prevue…` mais pas `temp_moy_prevue`. Les deux dictionnaires construits ensuite (branche « couleur officielle » et branche « prévision ») n'ont donc que min/max. Le chemin de repli « premier lancement » renvoie directement les dictionnaires de `predict_range` (clé météo à vérifier par @fullstack, celle que `store_prediction` lit pour écrire `temp_moy_prevue`).
2. **Les cartes ont déjà une ligne température** (`createForecastCard`, `fc-temp`, « min° / max° ») affichée seulement si min ET max sont non nuls. Je n'ai pas pu lire la base : [À VÉRIFIER par @fullstack] si `temp_min_prevue` / `temp_max_prevue` sont NULL sur les lignes récentes (cela expliquerait que le fondateur ne voie « pas de météo » aujourd'hui) ou si elles sont renseignées avec une autre définition que la moyenne 9 villes.
3. **Les captures fournies ne montrent pas la section prévisions** (elles s'arrêtent à la grille du mois : desktop y≈768 px, mobile y≈844 px). Je n'ai donc aucune preuve visuelle de l'état actuel des cartes. Captures à produire après application : section prévisions sur 3 appareils (390, 768, 1366 px) dans `tests/screenshots/`.
4. **Grille responsive actuelle** (`style.css` lignes 642 à 665, 1537 à 1561) : groupe « 3 prochains jours » en 3 colonnes (desktop et tablette), **1 colonne sous 480 px** ; les autres groupes en `auto-fill minmax(140px)` (desktop), `minmax(120px)` (≤768 px), **2 colonnes sous 480 px** (≈ 177 px de large à 390 px). La carte de 2 colonnes est donc le cas limite : « -12,3° » et un libellé court doivent y tenir.
5. **Le groupe « semaine prochaine et au-delà » a `opacity: 0.7`** (`.forecast-grid-far`). Appliquée au texte gris secondaire (#4B5563), elle le fait passer sous 4,5:1 (estimation ≈ 3,5:1 sur fond clair) : toute température ajoutée dans ce groupe échouerait WCAG 2.2 AA (correction M15-06).
6. **Historique : le chiffre « 94 sur 120 (78 %) » de « En bref » cache une précision capitale.** Dans le bilan, pour chaque délai de 2 à 5 jours : 23 justes + 7 sans prévision = 30 jours, 24 + 6 = 30. Il y a donc **0 prévision erronée** ; tout l'écart avec le repère « toujours bleu » (100 %) vient des jours sans prévision. Le bloc « En bref » ne le dit pas (seul le bilan affiche « dont N sans prévision » en petit). Un visiteur lit « 78 % » comme « on se trompe une fois sur cinq », ce qui est faux et, dans l'autre sens, cache aussi que le service a été interrompu.
7. **La colonne « La veille » a 15 jours sans prévision sur 30**, contre 6 ou 7 pour les délais 2 à 15. Les interruptions du calcul de 18 h (qui touchent tous les délais du même jour d'émission) ne suffisent pas à l'expliquer. [HYPOTHÈSE à vérifier par @fullstack : à la confirmation EDF de 11 h 30, `confirm_prediction` écrase la couleur et, si `couleur_originale` n'a pas été conservée, `_load_preds` renvoie `None` pour la prévision de la veille.] Conséquence de conception : **ne jamais étiqueter toutes les cases vides « service interrompu »** (règle INT-03).

---

## 2. Persona, parcours et walkthrough

Persona : abonné Tempo (ou futur abonné, 35 à 65 ans, sur mobile), qui veut savoir quand décaler lessive, sèche-linge, recharge VE. Il connaît la météo (« il va faire froid »), pas le vocabulaire de modèle. `personas.md`, `kpi-framework.md`, `brand-platform.md` n'ont pas été fournis : travail depuis le contexte du dépôt et l'audit du jour.

But sur cet écran : « vais-je avoir un jour rouge dans les 15 jours, et est-ce crédible ? ». La météo répond à la seconde moitié (« il fera 2 degrés, c'est logique »).

**Cognitive walkthrough, /calendrier, mobile 390 px, premier passage**

| Étape | Sait-il quoi faire ? | Action visible ? | Lien but/action clair ? | Retour immédiat ? |
|---|---|---|---|---|
| 1. Arrive sur la section « Prévisions détaillées des 15 prochains jours » | Oui (titre) | Oui | Oui | Oui (cartes) |
| 2. Lit une carte (jour, pastille couleur) | Oui | Oui | Oui | Oui |
| 3. Voit « 4,2° » sous la pastille | NON si aucune explication : `[FRICTION H2]` à l'étape 3, le first-time user ne sait pas de quelle température il s'agit (sa ville ? minimale ? maximale ?). Solution : ligne d'explication en tête du conteneur (M15-03). | Oui | Partiel | Oui |
| 4. Se demande pourquoi « Rouge » un jour à 4,2° et « Bleu » un autre à 4,0° | NON : `[FRICTION H10]` la température n'est pas le seul critère. Solution : une phrase dans l'explication repliable (3.6) et la barre de probabilités déjà présente ; ne pas laisser croire « froid = rouge ». | n/a | Non | n/a |
| 5. Veut vérifier la fiabilité | Oui (lien « Voir l'historique » déjà sous le conteneur) | Oui | Oui | Oui |

## 3. Partie A : météo sur les cartes de prévision

### 3.1 Spécification par carte

Ordre des éléments d'une carte (inchangé, sauf la ligne température) : jour (LUN) > date (2 oct) > pastille couleur > « Confirmé par EDF » (si confirmé) > **température** > libellé de confiance > badge hésitation > barre de probabilités > « Était X ».
La température reste juste sous la couleur, comme dans la grille de l'historique (couleur puis température dessous) : même lecture dans les deux pages.

- **Format** : `16,1°`. Une décimale, virgule, `°` sans « C » (identique à `_temp_label` de `prediction_history.py`). Négatif : signe moins typographique (U+2212) dans le texte visible, `-0,0` interdit (une valeur arrondie à zéro s'affiche `0,0°`). Même fonction côté JS que côté Python (mêmes arrondis), sinon la carte et l'historique peuvent afficher 16,1 et 16,0 pour la même valeur.
- **Hiérarchie** : la pastille couleur reste l'élément dominant. Température : 0,95 rem, graisse 600, couleur texte secondaire (≥ 4,5:1 sur les 3 fonds de carte). Sur les cartes du groupe « 3 prochains jours » (pleine largeur sous 480 px) : 1,05 rem. Jamais plus grosse que la date.
- **Texte pour lecteur d'écran** : `<span class="sr-only">température moyenne prévue 16,1 degrés Celsius</span>` (le texte visible est `aria-hidden`, comme dans la grille de l'historique). Signe moins lu « moins ».
- **Pas de min/max** sur la carte. Les champs `temp_min_prevue` / `temp_max_prevue` restent dans l'API (ne rien casser pour les utilisateurs de l'API documentée sur `/api-tempo`).
- **Jours confirmés par EDF** (Aujourd'hui, Demain) : la température reste affichée (contexte utile : « 2° : normal pour un jour rouge »). C'est la prévision du dernier calcul, pas une mesure.

### 3.2 Wireframes (pattern de layout)

**Mobile < 480 px (390 px de large, conteneur 366 px).**
Pattern : pile verticale ; groupe 1 en 1 colonne pleine largeur (3 cartes), groupes 2 et 3 en grille 2 colonnes de 177 px (gap 12 px).
Ordre : titre H2 > intro 1 ligne > légende couleurs > **ligne d'explication météo** > étiquette « Les 3 prochains jours » > 3 cartes > étiquette « Cette semaine » > grille 2 colonnes > étiquette « Semaine prochaine » + note > grille 2 colonnes (texte de la carte non estompé, voir M15-06) > lien « Voir l'historique » > CTA.

Carte pleine largeur (groupe 1) :
```
| MER                         ✓ |
| 1er oct                       |
|        [ BLEU ]               |
|   Confirmé par EDF            |
|          16,1°                |
|  (libellé de confiance)       |
```
Carte 177 px (groupes 2 et 3) : mêmes lignes, centrées ; « 16,1° » tient (6 caractères à 0,95 rem ≈ 45 px). Cas le plus long : « −12,3° » (7 caractères, ≈ 55 px) : tient.

**Tablette 481 à 768 px** : groupe 1 en 3 colonnes ; autres groupes `auto-fill minmax(120px)` (4 à 5 colonnes) ; même carte.
**Desktop > 768 px** : groupe 1 en 3 colonnes ; autres groupes `auto-fill minmax(140px)` (environ 7 colonnes à 1100 px) ; ligne d'explication alignée à gauche au-dessus du premier groupe, pas centrée.

Interaction : aucune (les cartes ne sont pas cliquables ; le survol actuel `translateY(-4px)` reste). Pas d'infobulle comme seule source d'information (inutilisable sur mobile).

### 3.3 Explication en une ligne

- Texte exact : « Sous chaque jour : température moyenne prévue en France, pondérée sur 9 villes. » suivi du lien « Méthode » vers `/methodologie`.
- Élément : `<p class="forecast-temp-note">`, premier enfant de `#forecast-container` rendu par `loadPredictions` (il disparaît avec les cartes en cas d'erreur, pas d'orphelin). 0,8 rem, couleur texte secondaire, sur 2 lignes maximum à 390 px.
- Ajouter, dans la même ligne ou juste après, une seconde phrase courte : « Mise à jour à chaque recalcul. » (la carte montre la dernière prévision, l'historique montre la prévision figée du jour d'émission : c'est normal que les deux diffèrent, la phrase évite la question).
- Ne pas écrire « pondérée par la population » : la nature de la pondération n'est pas documentée dans ce que j'ai lu (règle anti-invention). Utiliser exactement « pondérée sur 9 villes », comme `/methodologie` et l'historique.
- Si aucune carte n'a de température : ne pas afficher la ligne.

### 3.4 États

| État | Comportement |
|---|---|
| Chargement | Inchangé (loader + texte) ; hauteur réservée (CAL-05 de l'audit du jour). |
| Température présente | Ligne `fc-temp` formatée. |
| Température absente sur une carte | `Météo indisponible` (0,8 rem, gris, non italique), même hauteur de ligne que la température pour garder l'alignement des lignes suivantes ; sr-only « température prévue indisponible ». Pas de « n/d » (jargon), pas de tiret seul. |
| Température absente sur toutes les cartes | Aucune ligne d'explication ; chaque carte garde « Météo indisponible ». Jamais de valeur par défaut ni de valeur recopiée du jour voisin. |
| Erreur API / maintenance | Inchangé (bloc maintenance + « Réessayer »). |
| Connexion lente | Cartes affichées dès que `/api/predictions` répond (le champ est dans la même réponse, pas d'appel supplémentaire). |
| Retour après 30 jours | Aucun état spécifique : les données sont rechargées à chaque visite ; le cache de 5 minutes ne change rien. |
| Valeur négative ou nulle | `−2,4°` ; `0,0°` (jamais `−0,0°`). |
| Valeur aberrante (hors −30 à 45 °C) | Traiter comme absente et le signaler dans les logs (ne pas afficher une météo improbable). |

### 3.5 Cohérence avec l'historique et l'accueil

- **Historique** : même nombre (une décimale, virgule, `°`), même libellé (« température moyenne prévue »), même place (sous la couleur). Différence assumée et expliquée : la carte montre la prévision actuelle ; la grille montre celle d'il y a N jours (figée).
- **Accueil** : les 8 petites pastilles (34 px, colonne de 60 px max) restent sans température (densité, et la réponse à l'intention est la couleur). Sous les deux grandes pastilles Aujourd'hui / Demain, une ligne `16,2°` sous « Confirmé » ou « Prévision 99 % » (P2, M15-10). Attention : le rendu existe en double (macro `week_dot` dans `templates/dashboard.html` et `weekDotHtml` dans `app.js`, « même HTML »), plus `_get_ssr_data` pour les données : les trois à modifier ensemble, tests `tests/test_home_week_card.py` à mettre à jour. Ne pas allonger le premier écran mobile (ACC-02 de l'audit du jour) : une ligne de 16 px maximum.
- **/couleur-tempo-demain** : le tableau « Les prochains jours » peut recevoir une colonne « Température prévue » (P2, M15-11), mêmes règles de format.

### 3.6 Lien avec la couleur : repère « froid »

Question du fondateur : le froid fait les jours rouges, faut-il un repère visuel ?
Réponse : oui, pour aider à comprendre, mais avec trois garde-fous.
1. **Ne pas promettre.** La température pèse 38 % du score ; le reste vient du nombre de jours rouges restants, de la consommation prévue (RTE) et des règles EDF. Un jour froid peut être bleu ou blanc. Un repère « froid » placé sur une carte bleue en plein hiver doit donc être testé avant d'être montré.
2. **Seuil issu des données, pas inventé.** [HYPOTHÈSE : seuil « froid » à fixer par @data-analyst à partir des jours rouges réels des 7 saisons (table `actuals` croisée avec `weather_cache.temp_moy`), par exemple le 75e centile des températures des jours rouges.] Les notes du dépôt indiquent que des jours rouges tombent à 5 à 7 °C, donc un seuil à 0 ou 2 °C serait trompeur.
3. **Critère de non-diffusion** : si, sur les 7 saisons, plus de la moitié des jours sous le seuil, de novembre à mars, sont bleus, le repère induit en erreur : ne pas le diffuser.

Si validé (M15-09, P2) : pour un jour non confirmé, du 1er novembre au 31 mars, température sous le seuil : la température passe en graisse 700 et le mot « froid » est ajouté après la valeur (« 2,4° · froid »). Le mot est obligatoire (pas d'information par la couleur ou une icône seule). Couleur : neutre foncé ; **interdit** d'utiliser bleu, gris-blanc ou rouge Tempo (risque de confusion avec les couleurs du jour ; @design arbitre le token). Une phrase sous la ligne d'explication : « « Froid » : température sous X °C. Le froid fait monter la consommation d'électricité, donc les jours rouges tombent surtout par temps froid ; mais d'autres critères comptent (jours rouges restants, consommation prévue), tous les jours froids ne sont pas rouges. » (X renseigné par la constante validée, dans `site_facts.py`, jamais en dur dans le JS ou le template.)

### 3.7 Corrections actionnables, partie A

| ID | Prio | Fichier | Élément | Changement | Justification |
|---|---|---|---|---|---|
| M15-01 | P0 | `app.py`, `api_predictions` (l. 2136 à 2214 et chemin de repli l. 2283 à 2317) | `SELECT` + 2 dictionnaires `pred` | Ajouter `temp_moy_prevue` au `SELECT` ; l'inclure dans les deux dictionnaires (valeur arrondie à 1 décimale, `null` si absente ou hors −30 à 45) ; dans le chemin de repli, renvoyer la même clé (retrouver celle que lit `store_prediction`). Ne pas retirer `temp_min_prevue` / `temp_max_prevue`. Documenter le champ dans `templates/api_tempo.html` (M15-07). | Sans ce champ, rien à afficher. La ligne retenue par la requête est la dernière émission (ou la confirmée) : c'est bien la prévision la plus récente. |
| M15-02 | P0 | `static/js/app.js`, `createForecastCard` | bloc `tempHtml` | Remplacer « min° / max° » par la moyenne : `<div class="fc-temp"><span aria-hidden="true">16,1°</span><span class="sr-only">température moyenne prévue 16,1 degrés Celsius</span></div>` ; si absente : `<div class="fc-temp fc-temp-nd">Météo indisponible</div>`. Créer une fonction de formatage unique (une décimale, virgule, U+2212, pas de `−0,0`) et l'utiliser aussi pour l'accueil (M15-10). | Section 3.1 et 3.4. Même format que l'historique. |
| M15-03 | P0 | `static/js/app.js`, `loadPredictions` | début du contenu de `container` | Insérer en premier enfant `<p class="forecast-temp-note">Sous chaque jour : température moyenne prévue en France, pondérée sur 9 villes. Mise à jour à chaque recalcul. <a href="/methodologie">Méthode</a></p>` seulement si au moins une prévision a `temp_moy_prevue` non nul. | Walkthrough étape 3 (`[FRICTION H2]`). Texte sans tiret cadratin. |
| M15-04 | P0 | `static/css/style.css` (+ régénérer `style.min.css`, changer `?v=`) | `.forecast-card .fc-temp`, `.forecast-temp-note`, `.fc-temp-nd` | `.fc-temp` : 0,95 rem, graisse 600, couleur secondaire, marge haute 6 px, hauteur de ligne fixe (alignement avec le cas « Météo indisponible ») ; dans `.forecast-grid-primary` : 1,05 rem. `.forecast-temp-note` : 0,8 rem, marge basse 12 px. `.fc-temp-nd` : 0,8 rem, graisse 400. Vérifier qu'une valeur de 7 caractères ne déborde pas d'une carte de 177 px à 390 px. | Sections 3.1 et 3.2. |
| M15-05 | P0 | `tests/test_qa_fixes.py`, `tests/test_history_page.py`, `tests/test_home_week_card.py`, `tests/test_audit_pages_2026_09_30.py` | assertions sur `fc-temp`, `temp_min_prevue`, structure de l'API | Mettre à jour (min/max remplacé par la moyenne ; nouveau champ de l'API ; état « Météo indisponible » ; format de température identique entre JS et `_fr_temp`). Suivre la procédure de test du `CLAUDE.md` avant tout push. | Règle du dépôt : tests à jour, tous verts avant `git push`. |
| M15-06 | P1 | `static/css/style.css`, `.forecast-grid-far` | `opacity: 0.7` | Retirer l'opacité sur la grille ; estomper uniquement le fond et la bordure de la carte (fond plus pâle) en gardant le texte à sa couleur pleine ; vérifier ≥ 4,5:1 (WCAG 2.2 AA) sur les trois fonds (bleu, blanc, rouge pâles). | Fait 5 : la température du groupe « au-delà » serait illisible. Le message « tendance indicative » est déjà porté par l'étiquette et la note du groupe. |
| M15-07 | P1 | `templates/api_tempo.html` (+ `site_facts.py` si la liste des champs y est) et `static/llms*` si le champ y est décrit | description de `/api/predictions` | Ajouter `temp_moy_prevue` (°C, moyenne pondérée 9 villes, `null` si absente). | L'API est publique et documentée (robots.txt l'autorise) : tout nouveau champ doit l'être. |
| M15-08 | P1 | `templates/calendrier.html` (intro `cal-forecast-intro`) | texte d'introduction | Ne pas ajouter de phrase ici (l'explication vit dans le conteneur, M15-03) ; conserver l'intro courte décidée en CAL-02. | Éviter la répétition et l'allongement d'un bloc déjà trop long sur mobile. |
| M15-09 | P2 (bloqué par la validation des données, 3.6) | `site_facts.py` (constante de seuil), `app.js`, `style.css` | repère « froid » | Implémenter seulement après validation du seuil et du critère de non-diffusion par @data-analyst. Sinon ne pas livrer. | Section 3.6. |
| M15-10 | P2 | `templates/dashboard.html` (macro `week_dot`), `static/js/app.js` (`weekDotHtml`), `app.py` (`_get_ssr_data`) | grandes pastilles Aujourd'hui / Demain | Ajouter sous la ligne d'info une ligne `16,2°` (même fonction de format), uniquement pour `hero`. Aucune température sur les petites pastilles. Premier écran mobile non allongé de plus de 16 px. | Section 3.5. |
| M15-11 | P2 | `templates/couleur_demain.html` | tableau « Les prochains jours » | Colonne « Température prévue » (mêmes règles, « Météo indisponible » si absente). | Cohérence. |
| M15-12 | P2 | `tests/screenshots/` | preuve visuelle | Captures de la section prévisions, 390 / 768 / 1366 px, avec : température positive, négative, absente, valeur longue. | G_PROOF ; aucune capture de l'état actuel n'existe. |

---

## 4. Partie B : cases « pas de prévision » dans /historique-previsions

### 4.1 Ce que le visiteur voit aujourd'hui

- Cercle pointillé gris, légende courte « pas de prévision », légende complète repliée « aucune prévision émise ce jour-là (comptée comme manquée) ». Aucune cause.
- Une interruption du calcul de 18 h un jour E supprime la prévision de E pour chaque jour cible E+1 à E+15, donc **une case par ligne sur 15 lignes, en diagonale** (visible sur la capture : pointillés dispersés sur J-15, J-13, J-9…). Marquer « les lignes concernées » n'a donc pas de sens : on marque les cases et on liste les jours d'émission.
- « En bref » ne dit pas que l'écart vient de ces absences (fait 6).

### 4.2 Principes

1. Dire le fait, une fois, sans s'excuser longuement ni dramatiser : pas de bandeau rouge ou orange, pas de titre dédié, pas de ton d'alerte. Le pointillé gris reste le seul signal dans la grille.
2. Ne jamais recréer ni estimer une prévision après coup (déjà dit dans la méthode : « prévision figée »).
3. Séparer trois choses que le visiteur confond si on les mélange : prévision **juste**, prévision **erronée**, **pas de prévision**.
4. N'attribuer la cause « service interrompu » qu'aux jours pour lesquels aucun calcul n'est enregistré (règle INT-03). Les autres absences gardent le libellé neutre.

### 4.3 Textes proposés (sans tiret cadratin)

- Légende courte (visible, 2 lignes sur mobile) : « ⦿ juste · ⊗ erronée · ◌ pas de prévision ce jour-là » (mêmes pictogrammes CSS que l'existant : `hg-ok`, `hg-ko`, `hg-none`).
- Légende complète (dans le `<details>` existant), remplacer la ligne « aucune prévision émise ce jour-là (comptée comme manquée) » par : « **Pas de prévision** : le calcul de 18 h n'a pas pu aller au bout ce jour-là (interruption technique du service). Nous ne recréons jamais une prévision après coup : le jour compte comme manqué dans nos pourcentages. »
- Sous la légende, uniquement si la saison affichée compte au moins un jour sans calcul : `<details><summary>Jours sans calcul de prévision ({{ n }})</summary>` avec la liste « 15, 17, 23, 26 et 29 septembre 2026 » (forme : dates d'émission, en clair). Phrase : « Un jour sans calcul retire une case à chacun des 15 jours suivants : c'est pourquoi les cases vides forment des diagonales. »
- Infobulle et texte lecteur d'écran d'une case absente :
  - émission E sans aucune prévision : « {N jours avant} : pas de prévision, service interrompu le {date E}. »
  - autre absence : « {N jours avant} : aucune prévision enregistrée. » (libellé actuel « émise » reste acceptable).
- « En bref » (voir INT-01) : « Prévisions justes, toutes couleurs » devient une ligne à trois valeurs : « **94** justes, **0** erronée, **26** sans prévision (sur 120) ». Le pourcentage actuel reste affiché avec sa règle de seuil (`pct_min`). Les 26 (chiffre de l'exemple actuel) viennent de `bref` : ne rien calculer à la main dans le template.
- Méthode : la puce « Une prévision absente compte comme un jour manqué » est conservée et complétée : « … Cela arrive quand le service est interrompu au moment du calcul. »

### 4.4 Corrections actionnables, partie B

| ID | Prio | Fichier | Élément | Changement | Justification |
|---|---|---|---|---|---|
| INT-01 | P0 | `prediction_history.py` (`_bref`, `_horizon_stats`) + `templates/_historique_bref.html` (ligne « Prévisions justes, toutes couleurs ») | bloc « En bref » | Cumuler `sans_prevision` et `erronees` (= jours − justes − sans_prevision) dans `_bref` ; afficher « X justes, Y erronées, Z sans prévision (sur N) ». Pourcentage et repère « toujours bleu » inchangés (l'absence compte toujours comme manquée). Ajouter un test sur les trois effectifs. | Fait 6 : sans cela, 78 % se lit comme des erreurs alors qu'il n'y en a aucune ; le visiteur est trompé dans un sens et la transparence sur les interruptions est absente. |
| INT-02 | P0 | `templates/_historique_grille.html` (légende courte et `<details>` « Légende complète ») | textes de légende | Remplacer les textes par ceux de 4.3. Libellé court : « pas de prévision ce jour-là ». | Explication à l'endroit où la question se pose. |
| INT-03 | P0 | `prediction_history.py` (`_build`, `_grid_cell`, `page_context`) | calcul des jours sans calcul | Calculer, pour chaque saison, l'ensemble des jours d'émission sans aucune prévision : `E = date cible − N` pour toutes les clés de `preds` donnent les jours d'émission PRÉSENTS ; les jours ABSENTS sont ceux de l'intervalle [première émission connue, hier] absents de cet ensemble (exclure aujourd'hui tant que le calcul de 18 h n'a pas eu lieu, exclure les jours avant `PREDICTION_START_DATE`). Exposer `h.jours_sans_calcul` (liste de dates formatées) et, par case absente, un drapeau `interruption: True` seulement si `date cible − N` appartient à cet ensemble. Les cases absentes hors de cet ensemble gardent l'état `absente` sans cause. | Fait 7 : la colonne « La veille » montre 15 absences sur 30, dont une partie n'est pas due aux interruptions (hypothèse `couleur_originale` perdue). Ne jamais étiqueter à tort. |
| INT-04 | P1 | `templates/_historique_grille.html` | `title` et `sr-only` des cases `absente` | Utiliser les deux libellés de 4.3 selon le drapeau `interruption`. | Accessibilité et précision ; mobile : la légende et la liste couvrent l'absence de survol. |
| INT-05 | P1 | `templates/_historique_grille.html` | liste datée | Ajouter le `<details>` « Jours sans calcul de prévision (N) » sous la légende, affiché seulement si N > 0, avec la phrase sur les diagonales. Ne rien ajouter visuellement dans la grille. | Aucune ligne n'est « concernée » (diagonales) : la liste est la seule information juste. |
| INT-06 | P1 | `templates/historique_previsions.html` (section Méthode) | puce « Prévision figée » | Compléter la phrase comme en 4.3. Légende et méthode alignées avec `/methodologie` (MET-01 de l'audit du jour). | Une seule version du message. |
| INT-07 | P1 | `prediction_history.py` (`_load_preds`) + enquête | colonne « La veille » (15 absences sur 30) | Vérifier sur les données réelles la cause des absences du délai 1 (hypothèse `couleur_originale` non conservée à la confirmation de 11 h 30). Si la couleur émise est retrouvable dans la table `performance` (évaluation figée), la restaurer depuis cette source réelle (déjà prévu par la boucle 1 de `_load_preds`) ; sinon laisser « aucune prévision enregistrée ». Interdit de reconstituer une couleur. | Une partie des « sans prévision » de la veille est peut-être un défaut d'affichage, pas une absence réelle ; à trancher sur les données avant de publier la légende. |
| INT-08 | P2 | `CSV` (`prediction_history.to_csv`) et `templates/methodologie.html` | export et texte | Documenter dans la méthode que les cellules vides du CSV signifient « pas de prévision enregistrée » (déjà le comportement) ; ne pas ajouter de colonne cause. | Cohérence page / CSV. |
| INT-09 | P2 | Hors périmètre UX, à transmettre à @fullstack / @infrastructure | robustesse du calcul de 18 h | Après la bascule sur Cloudflare, revérifier le nombre de jours sans calcul ; si des interruptions subsistent, un rattrapage le soir même (avant minuit, horodatage réel) serait légitime, contrairement à une recréation après coup. La légende disparaît d'elle-même quand la liste est vide. | Le message est conçu pour s'effacer quand le problème disparaît. |

[À CONFIRMER par la session principale] : que tous les jours sans calcul, depuis `Config.PREDICTION_START_DATE`, ont bien la cause « interruption technique » (19 jours établis depuis février). Sinon, garder « aucun calcul enregistré ce jour-là » sans cause pour les autres.

---

## 5. Audit heuristique Nielsen (prévisions 15 jours avec météo, et historique corrigé)

| # | Heuristique | Verdict | Évidence |
|---|---|---|---|
| 1 | Visibilité de l'état | FAIL avant correction, PASS après | Aucun indice du périmètre de la température (M15-03) ; absences de l'historique sans cause (INT-02, INT-05). |
| 2 | Vocabulaire du persona | PASS après | « Température moyenne prévue en France », « Météo indisponible » ; pas de « n/d » ni « pondération » seule. |
| 3 | Contrôle et annulation | PASS | Lecture seule ; `<details>` repliables ; erreur avec « Réessayer ». |
| 4 | Cohérence | FAIL avant, PASS après | Min/max sur les cartes et moyenne dans l'historique ; format identique à imposer (M15-02). |
| 5 | Prévention des erreurs | PASS après | Valeurs aberrantes traitées comme absentes ; `−0,0` interdit ; aucune cause affirmée sans preuve (INT-03). |
| 6 | Reconnaissance plutôt que rappel | PASS | Température toujours sous la couleur, mêmes pictogrammes que l'historique. |
| 7 | Raccourcis experts | PASS | CSV en lien discret, champs min/max conservés dans l'API. |
| 8 | Minimalisme | PASS sous condition | Une seule valeur par carte, une seule ligne d'explication ; repère « froid » seulement si validé ; pas de min/max. |
| 9 | Messages d'erreur humains | PASS | « Météo indisponible » dit quoi, sans jargon ; blocs d'erreur existants avec solution. |
| 10 | Aide dans le flux | FAIL avant, PASS après | Ligne d'explication dans le conteneur ; légende contextuelle dans la grille. |

## 6. Tests UX (bloc obligatoire)

| Test | Critère | Résultat attendu |
|---|---|---|
| Parcours persona sans aide | Un abonné comprend de quelle température il s'agit sans ouvrir la méthode | Frictions H2 et H10 corrigées par M15-03 et la phrase de 3.6. À rejouer par un testeur (section 8). |
| Charge cognitive | ≤ 3 actions principales par écran | Aucune nouvelle action : 1 valeur, 1 ligne d'explication. |
| Time-to-value | Couleur et météo d'un jour en ≤ 3 étapes | 0 étape : visibles à l'arrivée sur la section. |
| Edge cases | Vide, erreur, chargement, connexion lente, retour après 30 jours | Section 3.4. |
| Accessibilité WCAG 2.2 AA | Clavier, focus, cibles ≥ 44 px, contrastes | Aucun élément interactif ajouté hormis le lien « Méthode » (cible ≥ 44 px de haut à prévoir : `padding` vertical) et les `<details>` (`summary` ≥ 44 px) ; contraste de la température ≥ 4,5:1 (M15-06) ; texte lecteur d'écran complet ; information jamais portée par la couleur seule. |

**HEART (météo et historique)**

| Dimension | Signal observable | Cible | Mesure |
|---|---|---|---|
| Task success | Part des visiteurs de `/calendrier` qui défilent jusqu'au conteneur de prévisions | [HYPOTHÈSE : à fixer après 2 semaines de mesure, aucune base actuelle] | Umami, profondeur de défilement |
| Engagement | Ouverture du `<details>` « Jours sans calcul » et clics « Voir l'historique » depuis `/calendrier` | [HYPOTHÈSE : à fixer après mesure] | Événements Umami à ajouter : `history_gaps_open`, `forecast_to_history_click` |
| Happiness | Aucun CSAT mesuré aujourd'hui | Non applicable sans enquête | n/a |

Aucune cible chiffrée inventée : les seuils génériques (activation ≥ 60 %, parcours ≥ 90 %, CSAT ≥ 8/10) ne s'appliquent pas sans mesure de base.

## 7. Garde-fous pour @fullstack

- Zéro donnée inventée : jamais de température par défaut, jamais de prévision recréée. Une valeur absente s'affiche comme absente.
- Zéro tiret cadratin dans tout texte ajouté (légendes, note, infobulles). Zéro nom de concurrent. Lien Selectra et « Plus de 2 500 foyers alertés » non touchés.
- Un seul `<h1>` par page ; ne pas ajouter de titre de niveau 2 pour la note ou la liste des jours sans calcul (`summary` de `<details>`).
- `static/js/app.min.js` et `static/css/style.min.css` à régénérer et à synchroniser avec les sources, puis changer la version `?v=` (règle du dépôt).
- Ordre d'exécution : M15-01 > M15-02, M15-03, M15-04 > M15-05 (tests) > INT-01, INT-02, INT-03 > INT-04 à INT-07 > M15-06, M15-07 > P2.
- Boucle visuelle obligatoire avant de rendre la main : captures 390 / 768 / 1366 px de la section prévisions de `/calendrier` et de la grille de `/historique-previsions` (tests/screenshots/), puis ré-invocation de @ux pour `docs/ux/ux-review.md`.
- Arbitrage avec @design : la structure (ordre des éléments, une valeur, ligne d'explication en tête du conteneur, libellés) est fonctionnelle et prime ; @design règle tailles, couleurs, espacement et le token du repère « froid » (hors couleurs Tempo).

## 8. Questions ouvertes (pour Thomas ou la session principale)

1. Seuil « froid » : à déterminer sur les jours rouges réels des 7 saisons (@data-analyst). Sans validation, le repère n'est pas livré.
2. Confirmer la cause « interruption technique » pour tous les jours sans calcul affichés (19 depuis février).
3. Origine des 15 absences sur 30 dans la colonne « La veille » (INT-07) : si c'est un défaut d'affichage, les chiffres publiés changeront à la correction ; prévenir Thomas avant déploiement.
4. Les colonnes `temp_min_prevue` / `temp_max_prevue` : remplies ou NULL en base ? (fait 2).

## 9. Agents à envisager (handoff @agent-factory)

| Agent | Type | Rôle | Justification | Priorité |
|---|---|---|---|---|
| testeur-abonné-tempo | testeur-persona | Simule un abonné Tempo sur mobile : lit les cartes, comprend la température, compare avec l'historique et interprète les cases vides | Les frictions H2 et H10 et la lecture de « 0 erronée, 26 sans prévision » doivent être rejouées sur captures réelles | Haute (déjà proposé dans l'audit du jour) |

---

**Handoff -> @fullstack (application), @design (visuel, en parallèle)**
- Fichier produit : `/home/user/Tempo/docs/audits/2026-09-30-ux-meteo-15-jours.md`
- Décisions prises : moyenne 9 villes seule (pas de min/max), une décimale comme l'historique, température sous la couleur, une ligne d'explication en tête du conteneur, « Météo indisponible » sans donnée, repère « froid » P2 conditionné par les données, température sur l'accueil seulement sous les deux grandes pastilles ; historique : séparer erronées et sans prévision dans « En bref », liste datée des jours sans calcul, cause affichée seulement quand aucun calcul n'est enregistré.
- Points d'attention : `/api/predictions` ne renvoie pas `temp_moy_prevue` (M15-01, bloquant) ; `.forecast-grid-far { opacity: .7 }` rend la température illisible (M15-06) ; 0 prévision erronée derrière le « 78 % » de l'historique (INT-01) ; colonne « La veille » à 15 absences sur 30 (INT-07) ; aucune capture de la section prévisions avant modification, à produire après ; tests et fichiers `.min` à régénérer.
