# Design : météo dans les prévisions des 15 prochains jours (/calendrier)

Date : 2026-09-30. Auteur : @design. Demande fondateur : « pour les prévisions des 15 prochains jours, faisons comme l'historique et affichons la météo ». Périmètre : le rendu visuel du composant « jour de prévision » (`createForecastCard` dans `static/js/app.js`, styles dans `static/css/style.css`, bloc `#forecast-container` de `templates/calendrier.html`). Le contenu, le libellé et le parcours sont traités par @ux (`docs/audits/2026-09-30-ux-meteo-15-jours.md`, pas encore publié à l'écriture de ce document : les textes proposés ici sont marqués « à arbitrer @ux »). Aucun code modifié : ce document est la liste de travail de @fullstack.

## 0. Base et limites (dites franchement)

- Lu : `renderForecast` n'existe pas sous ce nom, le rendu est `loadPredictions` + `createForecastCard` (app.js l.203-306 et l.557-665), tout le CSS `.forecast-*`, `.fc-*`, `.proba-*`, `.week-dot*` (style.css), `calendrier.html` en entier, `_historique_grille.html`, la grille de `/historique-previsions` (capture v2), les captures v3 (`calendrier-*`, `pli-*`) et `captures/local/calendrier_1366.png` et `calendrier_390.png` (seules captures montrant la zone des prévisions, en rendu local).
- Non vérifié : (1) le nom exact du champ température moyenne dans la réponse de `/api/predictions` (le code actuel lit `temp_min_prevue` et `temp_max_prevue`, la colonne `temp_moy_prevue` existe en base depuis la v18). **[À VÉRIFIER @fullstack]** : si l'API n'expose pas la moyenne pondérée 9 villes, l'ajouter côté API, ne JAMAIS la remplacer par (min+max)/2 (ce n'est pas la même grandeur que l'historique). (2) Aucune capture à 768 px. (3) Les rendus décrits pour les états « froid » et « météo indisponible » sont des spécifications non rendues : à valider par captures après implémentation (aucune validation des 10 critères sans preuve visuelle, voir section 8). (4) Contrastes calculés à la main, à confirmer avec un outil. (5) Le rendu de `tabular-nums` avec la police Inter auto-hébergée est à confirmer visuellement.
- Données : jamais de valeur fabriquée pour les captures de contrôle. Pour les états « froid » et « gel » (inexistants en septembre), relever de vraies valeurs de l'hiver précédent (colonne `temp_moy_prevue`, `db_dump.json` si présentes).

## 1. Constats sur le rendu actuel (captures locales 1366 et 390)

1. **Température actuelle incohérente avec l'historique.** Les cartes affichent `23.4° / 23.4°` (min / max, point décimal, sans unité), alors que l'historique affiche `15,3°` (moyenne, virgule). Sur les jours confirmés min = max, ce qui paraît cassé. Le fondateur a demandé « comme l'historique » : une moyenne, virgule française.
2. **Les jours lointains sont illisibles par construction.** `.forecast-grid-far { opacity: 0.7 }` (style.css, bloc « UX audit #9 ») : le texte gris `--text-secondary` sur fond `--bleu-bg` tombe à environ 3,4:1 (AA exige 4,5:1 pour ce corps de texte de 11 à 14 px), et la pastille blanche sur bleu à 70 % n'atteint pas 4,5:1. Ajouter la température dans cette zone sans corriger l'opacité, ce serait ajouter du texte illisible. Correctif en P0.
3. **Orphelins de grille à 1366 px.** Le groupe lointain de 9 cartes se répartit 7 + 2 (auto-fill à 140 px), le groupe moyen de 4 cartes laisse 3 cases vides. Les trois groupes n'ont pas de logique de colonnes commune.
4. **Mobile 390 px : la zone des prévisions fait environ 1 400 px de haut (mesure approximative sur la capture locale réduite).** Groupe fiable : 3 cartes pleine largeur ; groupe moyen : 2 colonnes ; groupe lointain : 9 cartes en 2 colonnes (5 rangées, une carte orpheline). La température doit s'ajouter sans allonger encore la page.
5. **Semi-accessibilité de la barre de probabilités.** `<div class="fc-proba-bar" aria-label="...">` n'a pas de rôle : un `aria-label` sur un `div` sans rôle n'est pas annoncé par les lecteurs d'écran. Les pourcentages ne sont donc dits que par les petits labels visuels.
6. **Signaux hors palette** (sans gravité pour cette mission, P2) : `rgba(244,67,54,…)` (ombre rouge), `#FFB74D` (bordure badge hésitation), `#2E7D32`, `&#9888;` (⚠ rendu en emoji sur certains systèmes, contraire à « pas d'emoji »), survol `translateY(-4px)` sur une carte non cliquable.

## 2. Décisions de conception

| # | Décision | Justification |
|---|----------|---------------|
| D1 | La température est **une ligne sous la pastille de couleur** (et sous « Confirmé par EDF » quand présent), à l'emplacement actuel de `.fc-temp`. Pas de pastille ronde, pas de colonne. | C'est exactement le motif de l'historique (pastille, température dessous) et des cartes actuelles : moindre changement, cohérence immédiate. Les grandes pastilles Aujourd'hui/Demain de l'accueil n'ont pas de température : on ne les touche pas (P2). |
| D2 | **Valeur = moyenne pondérée 9 villes**, format `14,1 °C` : virgule, une décimale pour les 7 premiers jours, **entier** au-delà (`14 °C`). Espace insécable avant `°C`. Vrai signe moins `−` (U+2212). | Une décimale à J+10 est une fausse précision (règle « zéro fausse promesse » du fondateur) ; les 7 premiers jours suivent l'historique (une décimale, virgule). L'unité `°C` est écrite : public de 35 à 65 ans, pas de repère à deviner. |
| D3 | **Min/max : uniquement sur les 3 grandes cartes** (groupe fiable, 1 colonne en mobile donc la place existe), en ligne secondaire `min 10° · max 18°`, affichée seulement si les deux valeurs arrondies diffèrent. | Évite le `23,4 / 23,4` actuel et la surcharge des petites cartes (149 px utiles à 390 px). |
| D4 | **Pas d'icône de météo** sur la ligne de température (ni soleil, ni nuage, ni thermomètre). | L'historique n'en a pas ; le nombre suivi de `°C` est sans ambiguïté ; aucune donnée « ciel » n'existe (inventer un pictogramme de ciel à partir de la température serait une invention). Une seule icône inline, le flocon, est réservée au repère de froid (D5). |
| D5 | **Repère de froid (P1), discret, à deux niveaux** : « Froid » (moyenne ≤ 5 °C) et « Gel » (≤ 0 °C), sous forme de pastille **avec texte** et flocon SVG inline. Couleur : famille cyan (`--froid` #0E7490), **jamais** le bleu Tempo (#2563EB) ni le rouge. Niveau « Gel » = pastille pleine, niveau « Froid » = pastille à contour : la différence ne repose pas sur la couleur seule. **[HYPOTHÈSE : seuils 5 °C et 0 °C, à valider par @ux et le fondateur]** (le seuil de 5 °C reprend l'ordre de grandeur du contexte « jours moyennement froids 5-7 °C » du moteur, sans être un seuil du moteur). | Le froid est la cause des jours rouges : le repérer aide à comprendre une prévision Rouge/Blanc. Un repère bleu se confondrait avec « jour bleu » (le contresens le plus coûteux ici). Texte + forme + couleur : WCAG 1.4.1 tenu. |
| D6 | **État sans donnée : « Météo indisponible »** en italique gris, sur la même ligne réservée (`min-height`), jamais un tiret seul ni `0 °C`. Si **aucun** jour n'a de température : pas de répétition, une seule note au-dessus de la grille. | Zéro donnée inventée, zéro écran cassé (préférences fondateur n°18). La ligne réservée évite que les cartes d'une même rangée aient des hauteurs qui sautent. |
| D7 | **Ne plus utiliser l'opacité pour marquer l'incertitude** des jours lointains (P0) : pastille en contour pointillé + texte de la couleur (même langage que `.cal-day.future` : pointillé = prévision). Le fond et le liseré gauche de la carte restent pleins (un rouge lointain doit rester visible : le rappel des jours rouges est la priorité n°1). | Corrige le contraste (constat 2) sans perdre le signal « tendance indicative ». Cohérent avec la grille du mois. |
| D8 | **Grille à colonnes cohérentes** (P1) : groupe fiable 3 colonnes, groupe moyen 4, groupe lointain 5 sur ordinateur ; plus la carte est proche, plus elle est grande. Mobile : le groupe lointain passe en **tuile compacte 3 colonnes** (3 x 3 pour 9 jours). | Supprime 7 + 2 et l'orphelin mobile ; gain d'environ 500 px de hauteur à 390 px (estimé sur la capture, à mesurer après implémentation). |

## 3. HTML cible du composant (généré par `createForecastCard`)

Les classes existantes sont conservées telles quelles (tests et CSS en dépendent). Ajouts : `.fc-temp-val`, `.fc-temp-unit`, `.fc-temp-range`, `.fc-temp-nd`, `.fc-froid`, `.fc-hesi-colors`, `.fc-compact`. La température remplace le bloc `tempHtml` actuel, même position dans `card.innerHTML`.

```html
<!-- carte standard, température présente, 3 grandes cartes (showRange) -->
<div class="forecast-card color-BLEU confirmed fc-animate">
  <div class="fc-day">Mer</div>
  <div class="fc-date">30 sep</div>
  <span class="fc-couleur BLEU" role="img" aria-label="Couleur prédite : BLEU">BLEU</span>
  <div class="fc-confirmed">Confirmé par EDF</div>
  <div class="fc-temp" title="Température moyenne prévue (moyenne de 9 villes)">
    <span class="sr-only">Température moyenne prévue : </span><span class="fc-temp-val">14,1</span><span class="fc-temp-unit">&nbsp;°C</span>
    <span class="fc-temp-range">min 10° · max 18°</span>   <!-- grandes cartes seulement, si valeurs arrondies différentes -->
  </div>
  <div class="fc-froid"><svg aria-hidden="true" …></svg>Froid</div>   <!-- P1, seulement si moyenne ≤ 5 -->
  <div class="fc-confidence">Très probable</div>
  …badge hésitation, barre de probabilités, « Était X » : inchangés
</div>

<!-- sans donnée -->
<div class="fc-temp fc-temp-nd">Météo indisponible</div>

<!-- groupe lointain : pastille pointillée (CSS) + classe fc-compact sur la carte (P1) -->
<div class="forecast-card color-ROUGE fc-compact fc-animate"> … </div>

<!-- badge hésitation : le détail des couleurs est isolé pour pouvoir le masquer visuellement en tuile compacte -->
<div class="fc-uncertain-badge"><span class="uncertain-icon" aria-hidden="true">&#9888;</span>Hésitation<span class="fc-hesi-colors"> Rouge/Blanc</span></div>

<!-- barre de probabilités : rôle ajouté (P1) -->
<div class="fc-proba-bar" role="img" aria-label="Probabilités : bleu 97%, blanc 3%, rouge 0%">…</div>
```

Accessibilité : le `title` du bloc est un complément (non lu sur mobile) ; le préfixe `sr-only` porte le sens pour les lecteurs d'écran. La valeur est lue « 14,1 degrés Celsius » grâce à `°C`.

## 4. JavaScript (app.js) : fonctions à ajouter et appels à modifier

À placer avant `createForecastCard`. Texte en UTF-8 littéral. Le nom du champ `temp_moy_prevue` est à confirmer (section 0).

```js
// Météo des prévisions : moyenne pondérée 9 villes (même grandeur que /historique-previsions)
const FROID_SEUIL = 5;   // °C, moyenne ≤ 5 : repère « Froid ». [HYPOTHÈSE à valider @ux / fondateur]
const GEL_SEUIL = 0;     // °C, moyenne ≤ 0 : repère « Gel »
const ICON_FROID = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9"/></svg>';

// Nombre arrondi, virgule française, vrai signe moins ; null si absent ou non numérique
function roundTemp(v, decimals) {
    if (v === null || v === undefined || v === '') return null;
    const n = Number(v);
    if (!Number.isFinite(n)) return null;
    const k = Math.pow(10, decimals);
    const r = Math.round(n * k) / k;
    return r === 0 ? 0 : r;            // évite « -0,0 »
}
function fmtTemp(r, decimals) {
    return r.toLocaleString('fr-FR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).replace('-', '−');
}

// opts : { decimals: 1 | 0, showRange: bool }
function meteoHtml(pred, opts) {
    const r = roundTemp(pred.temp_moy_prevue, opts.decimals);
    if (r === null) return '<div class="fc-temp fc-temp-nd">Météo indisponible</div>';
    let range = '';
    if (opts.showRange) {
        const lo = roundTemp(pred.temp_min_prevue, 0), hi = roundTemp(pred.temp_max_prevue, 0);
        if (lo !== null && hi !== null && hi > lo) {
            range = `<span class="fc-temp-range">min ${fmtTemp(lo, 0)}° · max ${fmtTemp(hi, 0)}°</span>`;
        }
    }
    let html = '<div class="fc-temp" title="Température moyenne prévue (moyenne de 9 villes)">'
        + '<span class="sr-only">Température moyenne prévue : </span>'
        + `<span class="fc-temp-val">${fmtTemp(r, opts.decimals)}</span><span class="fc-temp-unit"> °C</span>${range}</div>`;
    if (r <= GEL_SEUIL) {
        html += `<div class="fc-froid fc-froid-gel" title="Température moyenne prévue de ${fmtTemp(GEL_SEUIL, 0)} °C ou moins">${ICON_FROID}Gel</div>`;
    } else if (r <= FROID_SEUIL) {
        html += `<div class="fc-froid" title="Température moyenne prévue de ${fmtTemp(FROID_SEUIL, 0)} °C ou moins">${ICON_FROID}Froid</div>`;
    }
    return html;
}
```

Appels à modifier :

- `createForecastCard(pred)` devient `createForecastCard(pred, opts = {})` avec `opts = { decimals: 1, showRange: false, compact: false }` par défaut. Remplacer tout le bloc `let tempHtml = …` par `const tempHtml = meteoHtml(pred, { decimals: opts.decimals ?? 1, showRange: !!opts.showRange });` (le reste du gabarit `card.innerHTML` est inchangé, `${tempHtml}` reste entre `${confirmedHtml}` et `fc-confidence`).
- `if (opts.compact) card.classList.add('fc-compact');`
- Badge hésitation : `Hésitation ${A}/${B}` devient `Hésitation<span class="fc-hesi-colors"> ${A}/${B}</span>` (l'espace est dans le span).
- Barre de probabilités : ajouter `role="img"` au `div.fc-proba-bar`.
- `loadPredictions` : groupe fiable `createForecastCard(pred, { decimals: 1, showRange: true })` ; groupe moyen `{ decimals: 1 }` ; groupe lointain `{ decimals: 0, compact: true }`.
- Aucune température sur aucun des jours : avant les groupes, si `preds.every(p => roundTemp(p.temp_moy_prevue, 1) === null)`, insérer `<p class="fc-meteo-note">Les températures prévues ne sont pas disponibles pour le moment.</p>` (texte à arbitrer @ux) et passer `{ noTemp: true }` pour ne pas répéter « Météo indisponible » 16 fois (dans `meteoHtml`, si `opts.noTemp` retourner `''`).
- Régénérer `static/js/app.min.js` et incrémenter `facts.ASSET_V` (les templates lisent déjà `?v={{ facts.ASSET_V }}`).

## 5. CSS prêt à coller

### 5.1 Jetons (dans le bloc `:root` existant, après `--info-bg`)

```css
    /* Repère de froid (météo des prévisions) : famille cyan, distincte du bleu Tempo */
    --froid: #0E7490;       /* texte sur --froid-bg : 5,1:1 ; sur surface : 5,4:1 */
    --froid-bg: #ECFEFF;
    --froid-fort: #155E75;  /* fond du niveau « Gel », texte blanc : 7,3:1 */
```

### 5.2 Modifications de règles existantes

- Supprimer `.forecast-grid-far { opacity: 0.7; }` (bloc « UX audit #9 »).
- Remplacer `.forecast-card .fc-temp { font-size: 0.85rem; color: var(--text-secondary); margin-top: 6px; }` par le bloc 5.3 (la règle ci-dessous la remplace).

### 5.3 Nouveau bloc (fin de `style.css`, après « Sommaire compact » ; même bloc à reporter dans `style.min.css`)

```css
/* === Météo des prévisions 15 jours (audit design 2026-09-30) === */

/* Ligne de température : espace réservé pour que les cartes d'une rangée gardent la même hauteur */
.forecast-card .fc-temp { margin: 4px 0 0; min-height: 1.6em; line-height: 1.6; font-size: 0.8rem; color: var(--text-secondary); font-variant-numeric: tabular-nums; }
.fc-temp-val { font-size: 1rem; font-weight: 600; color: var(--text); }
.fc-temp-unit { font-weight: 400; }
.fc-temp-range { display: block; font-size: 0.8rem; line-height: 1.4; }
.fc-temp-nd { font-style: italic; }
.forecast-grid-primary .fc-temp-val { font-size: 1.15rem; }
.fc-meteo-note { margin: 0 0 12px; font-size: 0.85rem; color: var(--text-secondary); }

/* Repère de froid (P1) : texte + flocon + forme ; « Gel » = pastille pleine */
.fc-froid { display: inline-flex; align-items: center; gap: 4px; margin-top: 6px; padding: 2px 8px; border: 1px solid var(--froid); border-radius: 10px; background: var(--froid-bg); color: var(--froid); font-size: 0.75rem; font-weight: 600; line-height: 1.4; }
.fc-froid svg { width: 12px; height: 12px; flex: none; }
.fc-froid.fc-froid-gel { background: var(--froid-fort); border-color: var(--froid-fort); color: #fff; font-weight: 700; }

/* Jours lointains : plus d'opacité (contraste AA), l'incertitude passe par la pastille pointillée */
.forecast-grid-far .forecast-card .fc-couleur { background: transparent; border: 1.5px dashed currentColor; padding: 1px 9px; }
.forecast-grid-far .fc-couleur.BLEU { color: var(--info); }
.forecast-grid-far .fc-couleur.BLANC { color: var(--blanc); }
.forecast-grid-far .fc-couleur.ROUGE { color: #B91C1C; }

/* Badge hésitation : jamais de débordement dans une carte étroite */
.fc-uncertain-badge { max-width: 100%; justify-content: center; line-height: 1.3; text-align: center; border-color: var(--warn-border); }

/* Colonnes cohérentes : plus le jour est proche, plus la carte est grande (P1) */
@media (min-width: 769px) {
    .forecast-grid:not(.forecast-grid-primary) { grid-template-columns: repeat(4, 1fr); }
    .forecast-grid.forecast-grid-far { grid-template-columns: repeat(5, 1fr); }
}
@media (min-width: 601px) and (max-width: 768px) {
    .forecast-grid:not(.forecast-grid-primary) { grid-template-columns: repeat(4, 1fr); }
    .forecast-grid.forecast-grid-far { grid-template-columns: repeat(3, 1fr); }
}
@media (min-width: 481px) and (max-width: 600px) {
    .forecast-grid:not(.forecast-grid-primary) { grid-template-columns: repeat(2, 1fr); }
    .forecast-grid.forecast-grid-far { grid-template-columns: repeat(3, 1fr); }
}

/* Tuile compacte des jours lointains en mobile (P1) : 3 x 3, la barre garde les probabilités */
@media (max-width: 480px) {
    .forecast-grid.forecast-grid-far { grid-template-columns: repeat(3, 1fr); gap: 8px; }
    .forecast-card.fc-compact { padding: 12px 6px; }
    .fc-compact .fc-date { font-size: 0.95rem; }
    .fc-compact .fc-couleur { font-size: 0.75rem; padding: 1px 7px; }
    .fc-compact .fc-temp-val { font-size: 0.95rem; }
    .fc-compact .fc-confidence, .fc-compact .fc-proba-labels { display: none; }
    .fc-compact .fc-uncertain-badge { font-size: 0.7rem; padding: 2px 6px; margin-top: 4px; }
    .fc-compact .fc-hesi-colors { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
}
```

Vérifications de spécificité : `.forecast-grid-far .forecast-card .fc-couleur` (0,3,0) bat `.fc-couleur.BLEU` (0,2,0) et égale `.forecast-card.color-ROUGE .fc-couleur` (0,3,0), placée après donc gagnante. `.forecast-grid:not(.forecast-grid-primary)` (0,2,0) est placée après les règles `@media (max-width: 768px)` et `(max-width: 480px)` existantes ; les plages `min-width` ne se recouvrent pas, et le cas ≤ 480 px reste à 2 colonnes pour le groupe moyen (règle existante `repeat(2, 1fr)`).

## 6. États, variantes et comportement par largeur

### 6.1 États du composant

La carte n'est pas interactive : pas d'état focus, désactivé ou actif à spécifier. États réels :

| État | Rendu | Note |
|------|-------|------|
| Normal | pastille, `14,1 °C` (1 décimale, J+0 à J+6) ou `14 °C` (J+7 et plus) | `--text` 600 pour la valeur, `--text-secondary` 400 pour `°C` |
| Confirmé par EDF | identique, sous « Confirmé par EDF » ; `✓` en haut à droite conservé | la température reste une **prévision** (libellé « prévue » dans le préfixe lecteur d'écran et le `title`) |
| Hésitation | badge orange sous la température, avant la barre | ordre inchangé : pastille, confirmé, température, repère froid, confiance, hésitation, barre |
| Froid (P1) | pastille cyan à contour « Froid » + flocon, sous la température | seuil 5 °C, à valider |
| Gel (P1) | pastille cyan pleine « Gel » + flocon, texte blanc | seuil 0 °C, à valider |
| Sans température | `Météo indisponible` en italique gris, ligne de hauteur réservée | pas de repère froid, le reste de la carte est inchangé |
| Aucune température sur 16 jours | une note unique au-dessus de la grille, aucune ligne dans les cartes | évite 16 répétitions |
| Jour lointain | pastille en contour pointillé, carte pleine opacité | remplace `opacity: 0.7` |
| Changé (« Était BLEU ») | inchangé | |
| Chargement | `.loader` existant, `#forecast-container` garde ses `min-height` | |
| Erreur API | message et bouton « Réessayer » existants | |

### 6.2 Largeurs

| Largeur | Groupe fiable (J+0 à J+2) | Groupe moyen (4 jours) | Groupe lointain (9 jours) |
|---------|----------------|----------------|-----------------|
| ≥ 1024 px (vérifié 1366) | 3 colonnes, grandes cartes, température 1,15 rem + min/max | 4 colonnes | 5 colonnes (5 + 4) |
| 769 à 1023 px | 3 colonnes | 4 colonnes | 5 colonnes |
| 601 à 768 px (**non capturé**) | 3 colonnes | 4 colonnes | 3 colonnes (3 x 3) |
| 481 à 600 px | 3 colonnes | 2 colonnes | 3 colonnes |
| ≤ 480 px (vérifié 390) | 1 colonne (décision L-01 QA conservée) | 2 colonnes | **3 colonnes, tuiles compactes** |

À 390 px : conteneur de 366 px ; groupe moyen, carte de 177 px, contenu utile de 149 px : `14,1 °C` (environ 60 px), `Météo indisponible` (environ 125 px), pastille « Froid » ou « Gel » (moins de 70 px) tiennent sans retour à la ligne. Groupe lointain compact : carte de 114 px, contenu utile de 94 px : `14 °C`, « Froid » ou « Gel », `BLEU` tiennent ; « Hésitation » est visible seul, le détail des couleurs est conservé pour les lecteurs d'écran. **Aucun débordement horizontal attendu** : à vérifier par capture et par `document.documentElement.scrollWidth <= 390`.

## 7. Intégration dans la page (`templates/calendrier.html`)

- Texte d'appui (à arbitrer @ux / @copywriter) : dans `.cal-forecast-intro`, ajouter sous « Plus le jour est proche… » une ligne `<span class="update-freq">Sous chaque couleur : la température moyenne prévue (moyenne de 9 grandes villes de France).</span>`. Une seule phrase, pas de légende graphique : le nombre suivi de `°C` se suffit.
- Aucune légende pour le repère de froid : la pastille porte son texte (« Froid », « Gel ») et le seuil est dans l'attribut `title`.
- Ne pas toucher aux styles inline de la page : tout le nouveau CSS va dans `style.css` (et `style.min.css`).
- Aucune modification de `_historique_grille.html` : la grille de l'historique reste la référence. Cohérence de format vérifiée : virgule, `tabular-nums`, une décimale. Écart volontaire : l'historique écrit `15,3°` sans unité, les cartes écrivent `15,3 °C` (P2 : ajouter « en °C » à la légende de la grille pour aligner).

## 8. Critères visuels (auto-évaluation prévisionnelle, **non validée**)

Aucun rendu de la spécification n'existe encore : les 10 critères ne peuvent pas être déclarés PASS. Points d'attention pour la validation sur captures 390 / 768 / 1366 après implémentation :

1. PRO, 3 BRAND-ALIGNED, 4 MÊME IDENTITÉ : cyan uniquement sur le repère de froid (3 jetons), tout le reste réutilise les jetons existants.
2. 5 PROPRE, 6 ALIGNÉ : lignes de température à hauteur réservée, colonnes 3 / 4 / 5 sans orphelin (à contrôler à 768 px, non capturé).
3. 7 AÉRÉ : tuiles compactes 3 x 3 (mobile) et carte sans ligne ajoutée hors cas de froid ou de min/max.
4. 8 CONVERSION / 9 HIÉRARCHIE : la pastille de couleur reste l'élément dominant de chaque carte (température en 1 rem, 600, `--text`, plus petite que la date et que la pastille à 1,3 rem) ; le CTA de la page (inscription aux alertes) n'est pas touché.
5. 10 ACCESSIBLE : retrait de l'opacité, `°C` lu par les lecteurs d'écran, barre de probabilités avec `role="img"`, froid = texte + forme + couleur, aucune information portée par la seule couleur. Contrastes à confirmer avec un outil : `--froid` sur `--froid-bg` ≈ 5,1:1, blanc sur `--froid-fort` ≈ 7,3:1, `--info` sur `--bleu-bg` ≈ 6:1, `--blanc` sur `--blanc-bg` ≈ 4,6:1, `#B91C1C` sur `--rouge-bg` ≈ 5,8:1.

## 9. Priorités

**P0 (à livrer avec la fonctionnalité)**
1. Ligne de température moyenne sur chaque carte : `roundTemp`, `fmtTemp`, `meteoHtml`, CSS 5.3 (blocs « Ligne de température »), remplacement de l'ancien `.fc-temp` min/max. Format FR, `°C` écrit, 1 décimale jusqu'à J+6 puis entier.
2. État « Météo indisponible » et note unique quand aucune température n'existe.
3. Suppression de `.forecast-grid-far { opacity: 0.7 }` et remplacement par la pastille pointillée (CSS 5.3, bloc « Jours lointains »).
4. **[À VÉRIFIER]** Confirmer que `/api/predictions` expose la moyenne pondérée 9 villes (`temp_moy_prevue`) ; sinon l'ajouter côté API. Jamais de moyenne dérivée de (min+max)/2.
5. Régénérer `app.min.js` et `style.min.css`, incrémenter `facts.ASSET_V`, exécuter la procédure de test du dépôt (les tests de gabarits peuvent référencer `fc-temp`).
6. Captures de contrôle à 390, 768 et 1366 px avec de **vraies** données, remises à @design pour la validation (section 8).

**P1 (même passe)**
7. Repère de froid (« Froid », « Gel »), jetons `--froid*`, seuils à valider par @ux et le fondateur.
8. Colonnes cohérentes 3 / 4 / 5 et tuile compacte mobile du groupe lointain.
9. `role="img"` sur `.fc-proba-bar` ; badge hésitation sans débordement (`.fc-hesi-colors`).
10. Phrase d'appui dans l'intro (section 7), texte arbitré par @ux.
11. Min/max sur les 3 grandes cartes (si @ux le retient ; sinon ne pas appeler `showRange`).

**P2 (finitions, à faire aussi : les P2 ne sont pas optionnels)**
12. Retirer le survol `translateY(-4px)` des `.forecast-card` (carte non cliquable, fausse affordance) et son override `prefers-reduced-motion`.
13. Entrée en cascade : `animation-delay: calc(var(--i) * 40ms)` avec `--i` posé par le JS (16 cartes x 40 ms = 0,64 s maximum), le motif par défaut du système est 100 ms trop long pour 16 éléments.
14. Jetons au lieu de valeurs en dur : `rgba(244,67,54,…)` (ombre rouge), `#FFB74D`, `#2E7D32` / `#E8F5E9` (vers `--success*`), `#B91C1C` (jeton `--rouge-texte`).
15. Remplacer `&#9888;` (⚠) par un SVG inline (plus d'emoji rendu selon le système).
16. `.proba-label` à 0,75 rem (0,7 rem = 11 px, sous le confort de lecture du public cible).
17. Légende de l'historique : « températures en °C » pour aligner sur les cartes. Option : reprendre `fmtTemp` pour les grandes pastilles Aujourd'hui/Demain de l'accueil (décision @ux, hors demande actuelle).
18. Liste sémantique : `<ol>` / `<li>` pour chaque groupe de jours (annonce « liste de 4 éléments » par les lecteurs d'écran).

## 10. Tests suggérés (@qa / @fullstack)

- Présence de `fmtTemp`, `roundTemp`, `meteoHtml` dans `app.js` et de `.fc-temp-val` dans `style.css`.
- `fmtTemp(14.06, 1)` donne `14,1`, `fmtTemp(-0.04, 1)` donne `0,0` (jamais `-0,0`), `fmtTemp(-3.2, 1)` donne `−3,2`, `null`, `undefined`, `''`, `'abc'` donnent « Météo indisponible ».
- Absence de `opacity: 0.7` sur `.forecast-grid-far` dans `style.css` et `style.min.css`.
- Aucun tiret cadratin dans les textes ajoutés (règle client-facing).

## Handoff

- Destinataire : @fullstack (via orchestrateur). Fichier produit : `/home/user/Tempo/docs/audits/2026-09-30-design-meteo-15-jours.md`.
- Décisions : température moyenne 9 villes sous la pastille, format `14,1 °C` (entier à partir de J+7), pas d'icône de ciel, repère de froid cyan en pastille avec texte (seuils 5 et 0 °C en hypothèse), état « Météo indisponible », fin de l'opacité sur les jours lointains, colonnes 3 / 4 / 5 et tuile compacte mobile.
- Points d'attention : nom du champ API à confirmer, seuils de froid et textes à arbitrer avec @ux, aucune capture de validation à ce stade (à refaire à 390 / 768 / 1366 px), fichiers minifiés à resynchroniser, décisions fondateur non touchées (Selectra, « 2 500 foyers », menu Historique, badge « En test »).

