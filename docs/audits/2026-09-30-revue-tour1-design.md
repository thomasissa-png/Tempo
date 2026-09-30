# Revue notée, tour 1 : design des pages en ligne (ordinateur 1366 px, mobile 390 px)

Date : 2026-09-30. Auteur : @design. Base : captures du site EN LIGNE `captures/tour1/` (13 pages x 2 formats), `captures/v6/pli-*` et `historique-grille.png`, référence `captures/avant/`. Code relu : `static/css/style.css` (en entier), `_base.html`, `_header.html`, `calendrier.html`, `alertes.html`, `tarif_tempo.html`, `historique_previsions.html`, `_historique_bilan.html`, `fonts.css`, `.dockerignore`. Aucun code modifié : ce document est la liste de travail de @fullstack.

## 1. Tableau récapitulatif

Barème : 10 = produit soigné haut de gamme, rien d'amateur. Demi-points autorisés. Les notes « prov. » sont plafonnées par la lisibilité des captures (section 2).

| Page | Ordinateur /10 | Mobile /10 | Points bloquants (vers le 10) |
|---|---|---|---|
| Accueil | 8,5 | 8 | Texte de 11 px dans les compteurs (mobile) ; texte centré sur 6 à 9 lignes dans les 3 cartes « Comment économiser » ; barres de compteur vides sans sens lisible ; 2 boutons pleins bleus dans les 2 premiers écrans ; pas de hiérarchie H1 (1,5 rem) / H2 (1,15 rem) assez marquée |
| Calendrier (mois) | 7,5 | 7,5 | 3 largeurs de colonne (1100 / 800 / 700) et bloc « Jours rouges officiels » orphelin sous le CTA ; zone prévisions 15 jours jamais chargée dans les captures (loader + vide 150 px) ; légende prix isolée au-dessus d'un vide ; police de repli visible (T1) |
| Saison | 8,5 | 8,5 | Chapeau plus petit et plus gris que le paragraphe suivant (hiérarchie inversée) ; colonne étroite décalée de 120 px par rapport à l'en-tête ; pastilles « À venir » de 28 px ; police de repli visible (T1) |
| Historique | 8 | 7 | 3 cadres imbriqués dans « En bref » ; grille 14 x 15 de pastilles avec anneau + coche = trop dense ; tableau « Bilan » : colonnes séparées par un grand vide (ordinateur) et 3e colonne coupée (mobile) ; H2 à 4 tailles différentes |
| Tarif | 8,5 | 8,5 | Mur de texte « Combien coûte un jour rouge » (6 chiffres sans relief) ; lien isolé entre chapeau et H2 ; chapeau de 15 px sous le corps de 16 px ; police de repli visible (T1) |
| Couleur de demain | 8,5 | 7 | Mobile : tableau coupé (colonne Température invisible, « mercredi 30 septembre » sur 3 lignes) ; colonne Statut non traduite en composant `.source-label` ; ordinateur : fin de page sans relief |
| Méthodologie | 9 | 8,5 | Pastilles du sommaire flottantes sous le chapeau ; section 5 très dense (8 paragraphes) ; ligne « Voir aussi » orpheline ; mobile : longueur |
| API | 8,5 | 7 | Mobile : bouton « Copier » recouvre la 1re ligne du code ; chips `code` sur 6 à 10 mots par paragraphe = bruit ; lien + chip dans le tableau (double traitement) |
| Blog (index) | 8 | 8 | Cellule vide dans la grille à 2 colonnes (5 groupes sur 5 en ont une) ; titres des 3 guides essentiels plus petits que ceux des cartes normales ; même date répétée 22 fois ; 3 lignes de titre tronquées |
| Article de blog | 8 prov. | 8 prov. | Non vérifiable en détail (captures de 8 128 et 12 637 px réduites à 336 et 62 px de large) ; tableau comparatif et sommaire à contrôler à l'échelle 1 |
| Alertes | 7,5 | 8 | 3 bords gauches différents (fil d'Ariane, « Comment ça marche ? », bulle) ; H1 à 1,8 rem en ligne au lieu de 2 rem ; H2 à 24 px non stylé ; page « nue » face aux cartes de À propos ; 2 CTA identiques à 700 px d'écart |
| À propos | 9 | 9 | Espace bas des cartes « Transparence » et « API » trop grand ; 2 boutons de fin de page au poids visuel trop proche ; police de repli visible (T1) |
| Mentions légales | 8 | 7,5 prov. | Corps en `--text-secondary` 0,9 rem sur 6 000 px de lecture ; hiérarchie 4.1 à 4.8 / a à f à resserrer ; mobile non vérifiable en détail (10 753 px réduits à 73 px de large) |

Moyenne : ordinateur 8,3 ; mobile 7,9. Exigence fondateur (9/10 minimum) : NON atteinte sur 11 pages sur 13 à l'ordinateur et 12 sur 13 en mobile. Écart moyen au 9 : 0,7 pt ordinateur, 1,1 pt mobile. Bonne nouvelle : les corrections sont concentrées (5 chantiers, section 6) et aucun chantier ne demande de nouveau composant hors `.data-table` déjà créé.

## 2. Base, limites, et ce qui n'a PAS pu être vérifié (dit franchement)

1. Aucun outil de recadrage : les captures pleines pages sont affichées réduites (article 336 px de large, mentions mobile 73 px). Pour l'article, l'historique mobile et les mentions légales mobile je n'ai pu juger que la structure. À refaire au tour 2 en tranches de 1 400 px de haut à l'échelle 1 (voir section 7).
2. **Zone « prévisions 15 jours » du calendrier jamais rendue** : les deux captures montrent le loader « Chargement des prévisions… » et un vide de 150 à 190 px. Toute ma spécification `2026-09-30-design-meteo-15-jours.md` (température, pastilles pointillées, grille 3/4/5 colonnes, tuile compacte mobile, repère froid) est donc **non vérifiée en ligne**. Aucune note ne la récompense ni ne la pénalise, mais elle est bloquante pour le 10 sur /calendrier. La capture doit attendre `#forecast-container .forecast-card`.
3. Saison en cours = septembre : les états d'hiver (jours rouges/blancs, bilan à 7 colonnes `rb.v`, pastilles rouges, listes `.season-dates`) n'existent dans aucune capture. Le tableau « Bilan » à 7 colonnes (min-width 720 px) est le cas d'usage réel de novembre à mars et n'a jamais été vu.
4. Pas de capture à 768 px, ni de la modale d'inscription en ligne (bottom sheet G9), ni de l'admin. Non notés.
5. Contrastes : ratios recalculés à la main (formule WCAG) pour les couples listés en section 4 (T4), à confirmer avec un outil.

## 3. Écarts entre mes spécifications et ce qui est en ligne

Appliqué et conforme : G1 (liens de contenu), G2 (titres), G3 (`.data-table`, montants alignés à droite : tarif, demain, saison, méthodologie, API, historique, OK), G4 (boutons), G5 (pied de page 3 colonnes aligné sur l'en-tête à 153 px), G7 (FAQ), G10 (pastilles de saison), G11, G12, A1 (encadré réponse sur /couleur-tempo-demain), C1 (compteurs 3 colonnes en mobile), S1/S2, T1/T2 (tarif), L1/L3/L4 (mentions), H1 (colonne collante).

Non appliqué ou appliqué à moitié :

| Spéc. | Constat en ligne | Reprise |
|---|---|---|
| C4 (calendrier : 2 largeurs, pas 3) | Toujours 1100 (légende prix, CTA, prévisions) / 800 (FAQ, saisons, texte SEO, centrés) / 700 (grille, résumé du mois, note source, centrés) | T2 |
| T3 (repères chiffrés du tarif en `.counter-card`) | Mur de texte inchangé | P1 page Tarif |
| D3 (statut demain en `.source-label`) | `.color-tag` posé dans Couleur, mais Statut reste en texte brut gras ou normal | P1 page Couleur de demain |
| B1 (méta blog) | Une date s'affiche bien (« Publié » OU « Mis à jour »), mais c'est 29/09/2026 sur les 22 cartes : bruit pur | P1 page Blog |
| G8 (aucun texte utile sous 12 px) | `.hg-temp` 11,2 px, `.counter-help` 11,2 px en mobile, `.week-dot-info` 11,2 px, `.cal-today-label` 9,6 à 10,4 px, `.fc-proba-labels` 11,2 px | T4 |
| G6 (pied de page collé en bas) | OK | sans reprise |
| Police Inter (G0 implicite : CSS `body`) | Sur 4 pages des captures (tarif, calendrier, saison, à propos) le texte est rendu en Arial / Liberation Sans ; demain, API, méthodologie, alertes, accueil sont bien en Inter. Les 5 pages partagent le même `<head>` (`fonts.css`, preload) | T1 |

## 4. Corrections transversales (à faire une seule fois, dans `static/css/style.css` puis `style.min.css` + `facts.ASSET_V`)

### T1. Police de repli visible sur 4 pages (P0, à vérifier puis corriger)

Constat : sur `tarif`, `calendrier`, `saison` et `a-propos`, le H1, le corps et les cellules de tableau sont en Arial (chiffres « 1er », « g », « R » caractéristiques), alors que l'en-tête et les pages demain/API/méthodologie sont en Inter. Même `<head>`, même `fonts.css`, woff2 bien présents dans `static/fonts/` et non exclus par `.dockerignore` : cause la plus probable = capture prise avant la fin du chargement du woff2 (`font-display: swap`). Mais un visiteur sur mobile 4G verra la même bascule, et Arial (plus étroit) fait sauter la mise en page au remplacement.

Actions (dans cet ordre) :
1. Refaire les 4 captures après `await page.evaluate(() => document.fonts.ready)` et vérifier `document.fonts.check('16px Inter')` = true ; si false sur le site en ligne, contrôler `GET /static/fonts/inter-latin-wght-normal.woff2` (200, `content-type: font/woff2`, pas de redirection du Worker) et `GET /static/css/fonts.css`.
2. Dans `static/css/fonts.css`, ajouter une police de repli aux métriques d'Inter (supprime le saut de mise en page) : `@font-face { font-family: 'Inter Fallback'; src: local('Arial'); size-adjust: 107%; ascent-override: 90%; descent-override: 22%; line-gap-override: 0%; }` (valeurs usuelles publiées pour Inter sur Arial, à confirmer en comparant deux captures superposées).
3. Dans `static/css/style.css` ligne 51, pile : `font-family: 'Inter', 'Inter Fallback', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;`.

### T2. Quatre bords gauches différents à l'ordinateur (P0 cohérence)

Bord gauche du contenu à 1366 px : en-tête et pied de page 153 ; accueil, calendrier (titre), historique 153 ; blog (index) 223 (`.blog-index-main` 960 px) ; tarif, demain, API, saison, à propos, méthodologie, mentions 273 (`.page-content` 860 px) ; alertes 303 (`<main style="max-width:800px">`). Un produit haut de gamme en a deux au plus.

Règle cible : **2 gabarits**. Lecture = `.page-content` (860 px). Données/listes = `.page-content.page-wide` (1100 px, bord à 153, aligné sur l'en-tête).
- `templates/alertes.html` l.84 : remplacer `class="container" role="main" style="max-width:800px"` par `class="container page-content"` (et retirer le `style`).
- `templates/blog_index.html` + `style.css` ligne `.blog-index-main { max-width: 960px; }` : passer en `page-wide` (la grille 3 colonnes des guides y gagne 140 px).
- `templates/calendrier.html` (bloc `<style>` l.100-104 et 109) : `.cal-seo-content { max-width: 860px; margin: 0; padding: 32px 0; }` (texte aligné à gauche sur le bord du titre, plus centré avec 70 px de retrait) ; `.cal-month-summary { max-width: none; margin: 0 0 24px; }` ; `.cal-source-note` reste centré sous la grille (c'est un composant). La grille (700) et les compteurs (500) restent des composants centrés : acceptable, ce sont des objets, pas des blocs de texte.
- Justification : le zigzag actuel (CTA 1100, FAQ 800 centrée, saisons 800, texte 800, résumé 700 centré) est la cause principale du 7,5 de /calendrier.

### T3. Échelle typographique unique (P0 cohérence)

Tailles réelles en ligne : H1 = 2 rem (défaut `main h1`), 1,8 rem (alertes inline, article), 1,5 rem (accueil `.home-title`) ; H2 = 24 px (navigateur : tarif, demain, API, saison, alertes), 22,4 px (calendrier `.cal-seo-content h2`), 20 px (`.legal-section h2`), 19,2 px (`.blog-pillars h2`), 18,4 px (`.section-title` accueil). Un visiteur qui passe de /tarif-tempo-edf à /historique-previsions voit des titres de hiérarchie différente.

Tokens à ajouter dans `:root` : `--fs-h1: 2rem; --fs-h2: 1.4rem; --fs-h2-card: 1.25rem; --fs-h3: 1.1rem; --fs-lead: 1.1rem; --fs-body: 1rem; --fs-small: 0.875rem; --fs-min: 0.75rem;`
- `main h2 { font-size: var(--fs-h2); line-height: 1.3; }` ; `.legal-section h2, .history-bref h2, .blog-pillars h2, .blog-group h2 { font-size: var(--fs-h2-card); }` ; `.section-title { font-size: var(--fs-h2-card); }` (accueil : 1,15 vers 1,25 rem pour un vrai écart avec le chapeau) ; supprimer `.cal-seo-content h2 { font-size: 1.4rem }` (hérite de `main h2`) ; `main h3 { font-size: var(--fs-h3); }`.
- Exception documentée : `.home-title` (accueil, 1,5 rem) et `.blog-article-header h1` (1,8 rem). Supprimer `.alertes-hero h1 { font-size: 1.8rem }` (le H1 hérite de 2 rem / 1,6 rem mobile).
- **Chapeau** : aujourd'hui `.section-desc` = 0,95 rem gris alors que le paragraphe suivant = 1 rem sombre (tarif, saison, API : le chapeau est plus petit que le corps). Règle : `main h1 + .section-desc, main h1 + p { font-size: var(--fs-lead); line-height: 1.6; color: var(--text-secondary); }` et tous les autres `<p>` courants en `var(--fs-body)` / `var(--text)`. Sur saison, le 2e paragraphe (« Jusqu'au jeudi 1er octobre ») reste en corps, il n'a plus à être plus gros que le chapeau.

### T4. Plancher de 12 px pour tout texte porteur de sens (P1)

Passer à `var(--fs-min)` (0,75 rem) au moins : `.hg-temp` (0,7 rem, historique : 14 x 15 valeurs de température), `.counter-help` en ≤ 768 (0,75 rem) et ≤ 480 (0,7 rem), `.week-dot-info`, `.week-dot-proba`, `.fc-proba-labels .proba-label`, `.hesi-separator` (0,6 rem), `.cal-today-label` (0,65 rem et 0,6 rem en mobile ; garder blanc sur `--text`, mais 0,75 rem et hauteur de pastille 18 px), `.cal-legend-item` en ≤ 600 (0,74 rem). Contrastes recalculés : `--text-secondary` #4B5563 sur blanc 7,6:1 ; sur `--bg` 7,2:1 ; sur `--bleu-bg` 6,9:1 ; `--blanc` #6B7280 sur blanc 4,83:1 (tout juste AA : ne pas l'utiliser sous 14 px sur `--bg`, où il tombe à 4,6:1) ; blanc sur `--bleu` 5,17:1 ; blanc sur `--rouge` 4,83:1 ; `--bleu` sur `--bleu-bg` 4,77:1 ; `#2E7D32` sur `#E8F5E9` 4,56:1 (à la limite : assombrir `--success` en #1B5E20, 7:1, si le texte reste sous 14 px gras) ; pied de page #9CA3AF sur #0F2137 6,3:1. Aucun échec AA relevé sur les captures ; la marge est mince sur 3 couples.

### T5. Pied de page trop haut à l'ordinateur (P2)

`.footer-links a { min-height: 44px; padding: 10px 0 }` donne un pas de 44 px entre deux liens de 14 px : 4 rangées = 180 px de liens. La cible de 44 px n'a de sens qu'au doigt. Ajouter `@media (min-width: 769px) { .footer-links a { min-height: 0; padding: 6px 0; } }` (pas de 36 px). Garder 44 px en ≤ 768.

### T6. Cartes de contenu : marge basse parasite (P2)

Sur À propos (cartes « Transparence et limites », « API publique gratuite ») l'espace sous le dernier élément est supérieur au padding haut (24 px + marge du `ul` 12 px + `li` 6 px). `style.css`, ajouter : `.legal-section > :last-child, .legal-section li:last-child { margin-bottom: 0; }` et `.legal-section .history-details:last-child { margin-bottom: 0; }`.

### T7. Zone tactile des pastilles « À venir » (P2)

`.season-upcoming a { min-height: 28px }` : sous la règle 44 px du système. WCAG 2.2 AA (2.5.8) exige 24 px, donc conforme, mais c'est une exception silencieuse au contrat de design. Soit `min-height: 36px; padding: 6px 12px` (compromis à documenter), soit les convertir en `.cal-seasons a`.

## 5. Détail par page : ce qui empêche le 10 (priorité P0 bloquant, P1 à faire dans la passe, P2 finition)

Légende : « T# » renvoie aux corrections transversales de la section 4. Les composants réutilisés existent déjà (`.counters`, `.counter-card`, `.today-answer-links`, `.history-seasons`, `.legal-section`, `.data-table`, `.source-label`, `.hb-sub`, `.btn-outline`).

### 5.1 Accueil (8,5 ordinateur / 8 mobile)

Acquis : retour « c'est très chargé » traité (plus de bande grise, plus de liens bruts, une carte, un bouton, pastilles nommées). Les deux premiers écrans sont propres et hiérarchisés.
- **P1 A1. Texte de 11 px dans les compteurs en mobile** (`style.css`, `.counter-help` 0,7 rem sous 480 px, 4 lignes de 11 px dans chaque carte de 110 px) : `.counter-help { font-size: 0.75rem; line-height: 1.35; }` dans les 3 media queries et `.counter-card { padding: 12px 6px; }` sous 480 px (T4).
- **P1 A2. Cartes « Comment économiser »** : corps centré sur 6 à 9 lignes (la carte 3 en a 9). Le texte long centré se lit mal. `.step-card { text-align: left; align-items: flex-start; }`, `.step-number { margin: 0 0 12px; }`, `.step-card h3 { font-size: 1.05rem; }`, `.step-card p { font-size: 0.9rem; }`. Garder la hauteur égale (déjà en flex colonne).
- **P1 A3. H1 / H2** : H1 1,5 rem contre H2 1,15 rem (ratio 1,3). Avec T3 : `.section-title` passe à 1,25 rem, ratio H1/H2 1,2 mais H2 en gras 700 avec marge haute 32 px ; vérifier en capture que la hiérarchie tient.
- **P2 A4. Barres des compteurs** (`.counter-progress`, 6 px, gris plein à 0 %) : sur les 3 cartes la barre est vide alors que le chiffre dit « 22 restants ». Une barre vide contredit le chiffre. Soit remplir à `restants / total` (pleine en début de saison, qui se vide), soit masquer la barre tant que rien n'est consommé. **[À arbitrer @ux]**, ne pas changer la sémantique sans décision.
- **P2 A5.** Le bouton plein « Voir les 15 prochains jours » et la carte WhatsApp plus bas sont deux actions primaires de même poids ; le fondateur a demandé « un seul bouton » dans la carte : ne rien changer, à surveiller si la conversion alertes est l'objectif.
- Mobile : pile hero (2 grandes pastilles + grille 4 x 2) propre ; CTA pleine largeur ; FAQ à 44 px. Aucun débordement.

### 5.2 Calendrier du mois (7,5 / 7,5)

- **P0 C1. Vérifier la zone prévisions** (section 2, point 2). Tant qu'aucune capture ne montre les cartes chargées, /calendrier ne peut pas dépasser 7,5. Capture à attendre : `#forecast-container .forecast-card`.
- **P0 C2. Trois largeurs** : T2 (`calendrier.html` `<style>` l.100 `.cal-seo-content`, l.104 `.cal-month-summary`). Sans cela, le zigzag 1100 / 800 centré / 700 centré subsiste.
- **P1 C3. Vide de chargement** : `#forecast-container { min-height: 320px }` avec un loader de 20 px collé en haut laisse environ 250 px de blanc (puis un saut de contenu). Ajouter `#forecast-container .loading-state { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: inherit; }` ; option P2 : 3 gabarits gris (`.forecast-skeleton`, 3 colonnes, 140 px de haut, `background: var(--incertain-bg); border-radius: var(--radius-sm)`).
- **P1 C4. Résumé du mois orphelin** : « Jours rouges officiels en septembre 2026 : aucun. / Jours blancs officiels : aucun. » arrive après le CTA, à 173 px de bord gauche, sans lien visuel avec la grille qu'il commente. Le déplacer juste sous `.cal-source-note` (template l.262-273 avant le bloc `{% if is_current_month %}` l.229) et le poser en composant : `.cal-month-summary { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 14px 18px; }`. **[Placement à valider @ux]**.
- **P1 C5. Légende prix seule** : le cadre `.legend.legend-inline` (Bleu / Blanc / Rouge + prix) est au-dessus du loader : tant que les cartes ne sont pas là, il flotte. Une fois les cartes chargées il est logique ; à revalider sur capture.
- **P2 C6.** `.cal-today-label` 9,6 à 10,4 px (T4). `.cal-legend` : « Italique = prévision » mêle un style de texte à des pastilles : acceptable.
- Mobile : compteurs 3 colonnes, navigation de mois en 1 ligne, grille 7 colonnes : propres. Le point 2 s'applique aussi (écran vide sous la note de source).

### 5.3 Saison (8,5 / 8,5)

Acquis : `.data-table` propre (montants à droite), H1 coupé après les deux-points, pastilles de saisons, carte CTA.
- **P1 S1. Chapeau inversé** : 1er paragraphe en 0,95 rem gris, 2e en 1 rem sombre : T3 (`main h1 + .section-desc`).
- **P1 S2. Un chiffre qui parle** : la page ne contient aucun grand nombre ; « 0 jour rouge, 0 jour blanc » est noyé dans une phrase. Réutiliser `.cal-stats` (déjà dans `calendrier.html` ; le déplacer dans `style.css`, section C5 de l'audit précédent) au-dessus du tableau : 3 cartes `0 / 22`, `0 / 43`, `30 bleus` (valeurs du template, pas en dur).
- **P2 S3.** « À venir : » (pastilles de 28 px, gris) : T7. Les deux systèmes de pastilles sur la même page (grises 0,8 rem / noires 0,9 rem gras 44 px) sont voulus (hiérarchie), à garder.
- **P2 S4.** Police de repli sur les captures : T1.

### 5.4 Historique (8 / 7)

Le composant le plus abouti du site et le plus exigeant. Acquis : puces de saison, légende repliable, colonne de date collante, `tabular-nums`, « Afficher toute la saison ».
- **P0 H1. Trois cadres imbriqués dans « En bref »** : `.history-bref` (carte, bord bleu 5 px) contient `.history-bref-ref` (fond `--info-bg` + bord 3 px) et `.history-note--info` (2e fond bleu). C'est exactement le « cadre dans le cadre » refusé par le fondateur sur l'accueil. `style.css` : `.history-bref-ref { background: none; border-left: 0; padding: 0; margin: 0 0 12px; border-radius: 0; }` et `.history-note--info { background: none; padding: 0; margin-top: 8px; border-radius: 0; }`. Seule la carte extérieure garde son bord gauche. Même correction pour `.history-note--info` dans `_historique_bilan.html` (l.12).
- **P0 H2. Mobile : le tableau Bilan est coupé** : la colonne « Repère : toujours dire « bleu » » est tronquée (« toujours dir ») alors que le tableau est en `is-compact` (3 colonnes). `style.css` : `.history-bilan-scroll.is-compact .history-table { table-layout: fixed; } .history-bilan-scroll.is-compact th:first-child { width: 32%; } .history-bilan-scroll.is-compact th.num { width: 34%; white-space: normal; }`, et sous 600 px `.history-table th, .history-table td { padding: 8px 6px; font-size: 0.8rem; }` ; `.history-bilan .hb-sub { white-space: normal; }`. À vérifier à 390 px : aucun défilement horizontal dans le cas à 3 colonnes.
- **P1 H3. Écart de 300 px entre colonnes à l'ordinateur** (cas 3 colonnes) : mêmes règles de largeur (32 % / 34 % / 34 %) + `max-width: none`, le vide disparaît.
- **P1 H4. Grille : anneau vert + coche sur 210 cellules** (14 lignes x 15) : le motif « tout juste » crée un mur de 210 anneaux verts qui masque l'information utile (les erreurs). Proposition : cellule juste = pastille simple avec coche, sans anneau (`.hg-dot.hg-ok { box-shadow: none; }`, garder `::after` coche) ; cellule erronée = anneau rouge + croix (inchangé). L'œil va aux erreurs, cohérent avec « y compris quand nous nous sommes trompés ». Mettre à jour les 2 échantillons de légende (`.history-legend`). La coche reste, donc pas de signal par la couleur seule.
- **P1 H5. H2** : T3 (`.history-page h2` hérite `main h2` 1,4 rem ; `.history-bref h2` 1,25 rem au lieu de 1,15).
- **P1 H6. Températures de 11 px** (`.hg-temp`) : T4 (12 px).
- **P2 H7.** Cas d'hiver non vu (7 colonnes, min-width 720 px, défilement avec ombre) : à capturer avec de vraies données de la saison 2025-2026 (`/historique-previsions/2025-2026`).
- Mobile : grille à défilement, colonne de date collante, 14 lignes : lisible. Longueur 5 955 px : acceptable pour ce type de page, mais H2 + H4 la raccourcissent.

### 5.5 Tarif (8,5 / 8,5)

- **P1 T1. Mur de texte « Combien coûte un jour rouge »** : 6 chiffres (4,4 fois ; 0,5641 € ; 25 à 35 kWh ; 14 à 20 € ; 22 jours ; 310 €) dans un paragraphe de 5 lignes, sans aucun relief. Réutiliser `.counters` / `.counter-card rouge|rouge|bleu` au-dessus du paragraphe : « 4,4 fois », « 14 à 20 € de plus », « 310 € d'économie » (valeurs issues de `facts`, jamais en dur). Le paragraphe est conservé dessous.
- **P1 T2. Lien isolé** (`tarif_tempo.html` l.33 `<p><a href="/">Voir la couleur Tempo d'aujourd'hui et de demain</a></p>`) : entre le chapeau et le H2, il ressemble à un reste. L'envelopper dans `<div class="today-answer-links">` (pastille existante, 44 px).
- **P1 T3. Chapeau** : T3 (chapeau 0,95 rem gris sous le corps de 1 rem).
- **P2 T4.** Les deux `.form-hint` sous le tableau (0,85 rem) : les regrouper dans un seul bloc `.history-note` (une marge de 12 px au lieu de deux de 8 px).
- Mobile : le tableau tient à 390 px (libellés en 2 lignes via `.hb-sub`), montants à droite, pastilles lisibles. Bon.
- Police de repli visible : T1.

### 5.6 Couleur de demain (8,5 / 7)

- **P0 D1. Mobile : tableau coupé** : 4 colonnes (Jour, Couleur, Statut, Température prévue) dans 358 px : la colonne Température est hors écran (« Température prév… », valeurs « 21 », « 19 » tronquées), « mercredi 30 septembre » prend 3 lignes, rien n'indique qu'on peut défiler (`.table-scroll-hint` n'est pas posé). Correctif dans `couleur_demain.html` : (a) date double `<span class="d-long">mercredi 30 septembre</span><span class="d-short" aria-hidden="true">mer. 30 sept.</span>` avec `.d-short { display: none }` et, sous 600 px, `.d-long { display: none } .d-short { display: inline; white-space: nowrap }` ; (b) le statut passe sous la pastille de couleur en mobile : dans la cellule Couleur ajouter `<span class="hb-sub d-mobile-only">Confirmé par EDF</span>` (classe `.hb-sub` existante) et masquer la colonne Statut sous 600 px (`.days-table .col-statut { display: none }`, `.d-mobile-only { display: none }` au-dessus de 600 px). Résultat : 3 colonnes (Jour, Couleur + statut, Température), environ 300 px, aucune perte d'information officielle/prévue.
- **P1 D2. Statut en composant** : en ordinateur, `<span class="source-label label-officiel">Confirmé par EDF</span>` et `label-prediction` pour « Prévu à 99 % » (D3 de l'audit précédent, non appliqué). Ajouter `.data-table .source-label { text-transform: none; letter-spacing: 0; font-size: 0.8rem; }` (les capitales dégraderaient la lecture de « Prévu à 99 % »).
- **P2 D3.** Bas de page : liste des couleurs possibles avec pastilles : bon ; H2 en 24 px : T3.

### 5.7 Méthodologie (9 / 8,5)

Acquis : cartes numérotées, tableau des poids propre (à droite, 38 % etc.), sommaire en pastilles, CTA final.
- **P1 M1. Le chiffre clé de la page est noyé** : « 90 % … 640 prévisions justes sur 714 … 22 sur 50 rouges … 16 sur 49 blancs » est au milieu de la carte 5 en texte gras. M3 de l'audit précédent (non appliqué) : en tête de la carte 5, `.counters` avec 3 `.counter-card` (90 %, bleu ; 22 sur 50, rouge ; 16 sur 49, gris), valeurs du template.
- **P2 M2.** Sommaire : espacement chapeau / pastilles / 1re carte 16 / 20 px : passer à 20 / 24 (`.page-toc { margin: 16px 0 24px; }`). « Voir aussi » en fin : le poser en `<nav class="history-seasons">` (pastilles existantes) au lieu d'une ligne de liens.
- Mobile 8,5 : longueur et carte 5 dense ; chiffres clés en cartes (M1) résolvent l'essentiel.

### 5.8 API Tempo (8,5 / 7)

- **P0 Ap1. Mobile : « Copier » recouvre le code** : sur les deux blocs `<pre>`, le bouton flottant est posé sur la 1re ligne (`$ curl https://www.calendrier-tempo.fr/ap…` coupée sous le bouton). Dans le `<style>` d'`api_tempo.html` (`.api-doc pre` et le bouton de copie) : `.api-doc pre { padding: 44px 16px 14px; }` et bouton `position: absolute; top: 8px; right: 8px; min-height: 32px;` (une bande d'outils au-dessus du code, à toutes les largeurs). Le code garde son défilement horizontal, `tabindex="0"` conservé.
- **P1 Ap2. Bruit des chips `code`** : jusqu'à 10 fragments à fond gris par paragraphe (`status`, `ok`, `unavailable`, `date`, `couleur`...). `style.css` : `main p code, main li code { background: none; padding: 0; font-weight: 600; }` (police mono conservée), chips gardés dans le tableau et dans `.api-doc pre`.
- **P2 Ap3.** Tableau : 1re colonne = lien bleu + chip gris (double traitement). `.data-table td:first-child code { background: none; padding: 0; }` (le lien souligné suffit).
- Mobile : le tableau devient une liste de lignes avec « Cache : 2 min » en sous-ligne : bon choix, à conserver.

### 5.9 Blog, index (8 / 8)

- **P1 B1. Cellule vide** : grille à 2 colonnes, groupes de 3 cartes (Comprendre, Calendrier, Préparer) : une case vide à droite sur 3 groupes sur 5. `style.css` : avec `page-wide` (T2), `.blog-group .blog-list { grid-template-columns: repeat(3, 1fr); }` à partir de 1024 px (3, 3, 3, 6 cartes remplissent ; le groupe de 4 garde une dernière carte : `.blog-group .blog-list > :last-child:nth-child(3n+1) { grid-column: 1 / -1; }`) ; entre 601 et 1023 px, 2 colonnes et `.blog-group .blog-list > :last-child:nth-child(odd) { grid-column: 1 / -1; }`.
- **P1 B2. Hiérarchie inversée** : `.blog-card--pillar h3 { font-size: 1.1rem }` est plus petit que `.blog-card h3 { 1.25rem }` : les « guides essentiels » ont les titres les plus petits. Passer `.blog-card--pillar h3` à 1,3 rem (et laisser le titre sur 3 lignes, sans troncature).
- **P1 B3. Même date 22 fois** : « Mis à jour le 29/09/2026 » sur 19 cartes, « Publié le 29/09/2026 » sur 3. Bruit, pas information. Retirer la date des cartes d'index (garder durée de lecture), la conserver dans l'en-tête d'article, le flux et le JSON-LD. **[À arbitrer @ux / @seo : fraîcheur affichée]**. Si la date reste : style atténué `.blog-card-meta time { color: var(--text-secondary); font-size: 0.8rem; }`.
- **P2 B4.** Les 2 lignes de résumé sont tronquées (`line-clamp: 2`) avec points de suspension au milieu d'un mot ou d'un chiffre (« budget de 22… ») : acceptable, mais privilégier une phrase d'accroche de 110 caractères fournie par le frontmatter (`description`).

### 5.10 Article de blog (8 prov. / 8 prov.)

Captures trop réduites pour un jugement fin. Constats certains : carte unique propre, H2 à filet, sommaire replié, carte CTA, « À lire aussi ».
- **P1 Ar1. Longueur de ligne à confirmer** : carte de 860 px, padding 32 px, corps 1,05 rem / 1,8 : environ 90 caractères par ligne, au-dessus des 75 conseillés pour un texte long. À mesurer sur capture 1:1 ; si confirmé : `@media (min-width: 769px) { .blog-article { padding: 40px 56px; } }` (environ 75 caractères).
- **P1 Ar2.** Tableaux du corps (comparatif à 4 colonnes) : vérifier à 390 px la présence de `.table-scroll` et de l'ombre de défilement ; appliquer `.blog-article-body th, .blog-article-body td { padding: 10px 12px; }` seulement dans la carte, avec `.num` pour les montants (`Ar4` de l'audit précédent).
- **P2 Ar3.** T3 : H1 d'article en 1,8 rem = exception documentée.

### 5.11 Alertes (7,5 / 8)

- **P0 Al1. Trois bords gauches** : fil d'Ariane à 303 px (container 800), « Comment ça marche ? » à 383 px (`.alertes-how` 600 px), bulle WhatsApp à 483 px (400 px centrée), plus un H1 et des boutons centrés. T2 : `<main class="container page-content">` (860 px, fil d'Ariane à 273 px comme les autres pages de lecture).
- **P1 Al2. Section « Comment ça marche ? » sans cadre** : H2 de 24 px non stylé, liste numérotée du navigateur, sur fond de page, alors que À propos / Méthodologie utilisent des cartes. `alertes.html` `<style>` : `.alertes-how { max-width: 600px; margin: 0 auto 32px; background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow); padding: 24px; }`, `.alertes-how h2 { font-size: var(--fs-h2-card); margin: 0 0 12px; }`, `.alertes-how ol { padding-left: 20px; }`, `.alertes-how-cta { margin-top: 20px; }`. Le 2e bouton reste pleine largeur de la carte sur mobile.
- **P1 Al3. H1** : `.alertes-hero h1 { font-size: 1.8rem }` (inline) contredit `main h1` (2 rem) : supprimer (T3) ; `.alertes-hero { padding: 8px 0 24px; }` (aujourd'hui 32 px en haut + fil d'Ariane : 100 px de blanc avant le H1).
- **P2 Al4.** Emojis de la bulle rendus par le système (Noto sur Linux, Apple sur iOS) : attendu, c'est l'aperçu d'un vrai message WhatsApp ; ne pas les remplacer. La puce « 100 % gratuit · Sans application… » (0,9 rem gris) : bon contraste (7,6:1).
- Mobile 8 : propre, bulle pleine largeur, CTA pleine largeur ; gagne 0,5 avec Al2.

### 5.12 À propos (9 / 9)

- **P1 Ap-A1. Hiérarchie des boutons de fin** : « Voir la couleur du jour » est plein, « S'inscrire aux alertes » est en contour. L'action de conversion (inscription) doit être la dominante : inverser (`btn-outline` sur « Voir la couleur du jour », plein sur « S'inscrire aux alertes »). Le même choix vaut pour la 404 si elle existe avec les mêmes boutons.
- **P2 Ap-A2.** Marge basse des cartes (T6), police de repli (T1).
- Mobile : pastilles « Méthodologie détaillée » / « Historique de nos prévisions » pleine largeur, accordéons à 44 px : très bon.

### 5.13 Mentions légales (8 / 7,5 prov.)

Acquis : sommaire compact (« Se désinscrire » visible + « Sommaire » replié), cartes numérotées, hébergeur à jour (Cloudflare, Neon), hiérarchie 4.1 à 4.8 visible.
- **P1 L1. Confort de lecture sur 6 000 px** : `.legal-text` 0,9 rem (14,4 px) gris secondaire. Passer à 0,95 rem (`.legal-text, .legal-section li { font-size: 0.95rem; }`), garder la couleur (7,6:1).
- **P2 L2. Séparer les blocs 4.x** : `.legal-section h3:not(:first-child) { border-top: 1px solid var(--border); padding-top: 16px; }` (les 8 sous-parties de la section 4 s'enchaînent sans filet).
- **P2 L3.** Mobile à contrôler à l'échelle 1 (10 753 px de haut) : dépliage du sommaire compact (`.page-toc-more ul { position: absolute }` : vérifier qu'il ne déborde pas à 390 px).

## 6. Les 5 corrections qui font gagner le plus de points

| # | Correction | Pages touchées | Gain estimé |
|---|---|---|---|
| 1 | **Tableaux de données lisibles en mobile** : demain (D1), Bilan de l'historique (H2), bloc `pre` de l'API (Ap1) | 3 pages mobile | +1 à +1,5 sur chacune (ce sont les 3 notes mobile à 7, les plus basses avec /calendrier) |
| 2 | **Police : vérifier le repli Arial, ajouter `Inter Fallback`** (T1) | tarif, calendrier, saison, à propos (x 2 formats) | +0,5 à +1 sur 8 notes si confirmé en réel ; sinon correction de la procédure de capture |
| 3 | **Une seule grille de pages + une seule échelle de titres** (T2, T3) : 2 gabarits (860 / 1100), H2 en 3 niveaux, chapeau au-dessus du corps | calendrier, blog, alertes, tarif, saison, API, historique | +0,5 sur 8 pages ; soulève /calendrier (C2) et /alertes (Al1) de 0,5 à 1 |
| 4 | **Historique : supprimer les cadres imbriqués et l'anneau sur les cellules justes** (H1, H4) | historique x 2 | +1 ordinateur, +0,5 mobile |
| 5 | **Calendrier : vérifier la zone prévisions chargée, ranger le bloc « Jours rouges officiels » sous la grille, loader pleine hauteur** (C1, C3, C4) | calendrier x 2 | +1 à +1,5 (la note la plus basse du site) |

Suivent, par ordre de gain : grille du blog en 3 colonnes (B1, B2), compteurs chiffrés tarif et méthodologie (T1 tarif, M1), carte « Comment ça marche ? » des alertes (Al2), chips `code` de l'API (Ap2), plancher de 12 px (T4).

Ordre d'exécution pour @fullstack : (1) `style.css` : T1, T3, T4, T5, T6, T7, H1, H2/H3, H4, B1, B2, Al2, Ap2, Ap3, L1, L2, D2 en une seule passe, régénérer `style.min.css`, incrémenter `facts.ASSET_V` ; (2) gabarits : T2 (`alertes.html`, `blog_index.html`, `calendrier.html`), D1 (`couleur_demain.html`), Ap1 (`api_tempo.html`), C3, C4, T1/T2 tarif, M1, Ap-A1 ; (3) nouvelles captures (section 7) ; (4) procédure de test du repo avant tout push (les gabarits changent : classes `d-long`, `d-short`, `.counters`).

## 7. Captures à fournir pour le tour 2 (sans elles, pas de validation)

1. Toutes les pages après `document.fonts.ready` ET, pour `/calendrier`, après l'apparition de `.forecast-card` (vérifier la spécification météo 15 jours : températures, pastilles pointillées, 3/4/5 colonnes, tuile compacte mobile 3 x 3).
2. En tranches de 1 400 px de haut à l'échelle 1 (pas de pleine page réduite) : article de blog (ordinateur et mobile), mentions légales mobile, historique mobile (grille, En bref, Bilan), API mobile (blocs `pre`).
3. Historique saison 2025-2026 : bilan à 7 colonnes (défilement, ombre, colonne collante) en ordinateur et en mobile.
4. `/couleur-tempo-demain` à 390 px après D1 ; `/historique-previsions` à 390 px après H2 (aucun défilement horizontal sur le cas à 3 colonnes).
5. Modale d'inscription à 390 px (bottom sheet G9) et à 1366 px ; 404 ordinateur et mobile ; 768 px sur accueil, calendrier, historique, blog.

## Handoff

- Destinataire : @orchestrator, puis @fullstack. Fichier produit : `/home/user/Tempo/docs/audits/2026-09-30-revue-tour1-design.md`.
- Décisions : aucun nouveau composant ; 2 gabarits de page (860 / 1100) ; 3 niveaux de H2 ; un seul style de chapeau ; le composant `.data-table` doit aussi tenir à 390 px sans défilement caché ; décisions fondateur intactes (Selectra, « 2 500 foyers », menu Historique, badge « En test » non touchés).
- Points d'attention : notes mobile plafonnées par des captures trop réduites (article, mentions, historique mobile) ; spécification météo 15 jours non vérifiée en ligne ; police de repli à confirmer avant correction ; contrastes calculés à la main (marge mince sur `--blanc` sur `--bg`, `--success` sur `--success-bg`) ; aucune mention de concurrent, aucun tiret cadratin repéré sur les captures.

