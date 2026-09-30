# Audit design des pages publiques : AVANT (Replit) vs APRÈS (Cloudflare)

Date : 2026-09-30. Auteur : @design. Périmètre : rendu visuel uniquement (la hiérarchie et le placement sont traités par @ux en parallèle). Aucun code modifié : ce document est la liste de travail de @fullstack.

## 0. Base de l'audit et limites

- Captures lues : `avant/*` (prod), `apres_verif/*` (10 pages desktop), `apres/*` (mobile 390 px de toutes les pages, 404 desktop). Code lu : `static/css/style.css` en entier (2 704 lignes), `_base.html`, `_footer.html`, `_subscribe_modal.html`, `dashboard.html`, `calendrier.html`, `calendrier_saison.html`, `tarif_tempo.html`, `couleur_demain.html`, `methodologie.html`, `api_tempo.html`, `legal.html`, `404.html`, `admin.html` (lignes 1-330).
- Limites, dites franchement : (1) les captures mobiles pleine page sont réduites à 117-284 px de large à l'affichage, donc le détail fin de `historique-previsions`, `blog article`, `mentions-legales` en 390 px n'est PAS vérifié visuellement (constats déduits du code et de la structure, marqués `[code]`). (2) Aucune capture à 768-900 px : les constats de cette plage sont marqués `[à vérifier]`. (3) Aucune capture de la modale d'inscription ni de l'admin : audit sur le code. (4) `alertes.html`, `a_propos.html`, `blog_*.html`, `manage.html` : templates non relus, audit sur captures. (5) Contrastes : ratios calculés à la main (estimations), à confirmer avec un outil.
- Le diagnostic « AVANT » confirme que le site que le fondateur apprécie repose sur : cartes blanches à bord `--border`, ombre `--shadow`, rayon 12 px, pastilles arrondies, bleu `--accent` pour tout ce qui est actionnable (liens sans soulignement, boutons pilule), et gros H1 centrés ou sobres avec beaucoup d'air.

## 1. Diagnostic racine (les 4 causes qui expliquent 80 % des écarts)

1. **Aucune règle globale pour les liens.** `style.css` n'a aucun sélecteur `a {}` ni `main a {}`. Chaque composant restyle ses liens (`.breadcrumb a`, `.legal-text a`, `.next-rouge-detail a`...). Tout lien posé dans un composant neuf retombe sur le style navigateur (bleu `#0000EE` souligné, violet une fois visité). C'est exactement le défaut pointé par le fondateur sous le H1 de l'accueil, et on le retrouve sur 9 pages.
2. **Pas de style de titre.** `h1`, `h2`, `h3` n'ont pas de règle globale : taille navigateur (2em / 1.5em) avec le `line-height: 1.7` du `body` et des marges remises à zéro par `* {margin:0}`. Résultat : H1 sur 2 lignes très aérés en interligne mais collés au paragraphe qui suit (tarif, méthodologie, API, saison), et H1 des mentions légales collé sous le header.
3. **Les pages ajoutées les 29-30/09 utilisent des styles inline dupliqués** (5 variantes de tableau : `.season-table`, `.tarif-table`, `.days-table`, `.method-table`, `.api-table`, chacune dans son `<style>`), sans les composants de l'AVANT (`.table-scroll`, `.history-table`, `.legal-section`, `.subscribe-cta-card`, `.history-seasons`).
4. **Classes ou variables inexistantes** : `.btn-outline` (404.html) n'est défini nulle part ; `--primary` (`.loading-spinner`), `--card-bg`, `--text-primary` (`.maintenance-banner`) et `--bg-secondary` (`.last-update-bar`, repli `#f5f5f5` hors palette) n'existent pas dans `:root`. Une seconde palette Material (`#2E7D32`, `#E8F5E9`, `#E65100`, `#FFF3E0`, `#E3F2FD`) cohabite avec les tokens.

## 2. Résumé des P0 (à faire avant la bascule)

| # | P0 | Portée | Section |
|---|----|--------|---------|
| P0-1 | Style global des liens de contenu (fin des liens bruts bleus soulignés) | toutes les pages | G1 |
| P0-2 | Encadré « Couleur Tempo EDF du ... » de l'accueil : refonte visuelle avec les composants de l'AVANT | accueil, couleur-tempo-demain | Accueil A1, G1 |
| P0-3 | Composant tableau unique (carte, en-tête, montants à droite) en remplacement des 5 tableaux inline | saison, tarif, demain, méthodologie, API | G3 |
| P0-4 | Style de titres et respiration haute (H1 collé au header sur mentions légales, H1 collé au texte sur 5 pages) | 8 pages | G2 |
| P0-5 | Boutons : `.btn-outline` inexistant, boutons `<a>` soulignés, `margin-top` parasite | 404, à propos | G4 |
| P0-6 | Pied de page : 12 liens sur 2 lignes avec orphelins, décalage de 20 px avec le header, pas de pied de page collé en bas sur pages courtes | toutes | G5, G6 |
| P0-7 | FAQ accueil : `max-height: 300px` + `overflow: hidden` peut tronquer les réponses longues (12 questions, listes) | accueil, calendrier | G7 |
| P0-8 | Modale d'inscription : pattern bottom sheet mobile absent (`items-center` + `max-height: 90vh`, cassé sur iOS Safari) | toutes (chemin de conversion) | G9 |
| P0-9 | Admin : `fonts.css` non chargée (Inter absente), palette bleu/rouge hors tokens | admin | Admin |
| P0-10 | Contrastes AA insuffisants (nav header, badges orange, badge « En test ») | toutes | G8 |

Hors design mais bloquant pour la bascule (à transmettre) : `templates/legal.html` section 2 « Hébergeur » nomme encore Replit, Inc. Après la bascule DNS, la mention doit nommer Cloudflare (Worker/conteneur) et Neon (base). Obligation légale (LCEN), pas une question de style.

## 3. Correctifs transverses (réutilisés par les pages)

Fichier : `static/css/style.css`, puis régénérer `static/css/style.min.css` et incrémenter `?v=` dans les templates (`_base.html`, `dashboard.html`, `calendrier.html`, `legal.html`, `alertes.html`, `a_propos.html`, `blog_*.html`, `manage.html`, `admin.html` qui n'a pas de `?v=`).

### G1. Liens de contenu (P0)

Problème : liens navigateur bruts (bleu `#0000EE`, souligné, gras dans les cellules de tableau) partout où un composant n'a pas sa propre règle. Correctif, à ajouter après la règle `.container` :

```css
main a { color: var(--accent-hover); text-decoration: underline; text-decoration-color: rgba(37,99,235,.4); text-decoration-thickness: 1px; text-underline-offset: 3px; }
main a:hover { text-decoration-color: currentColor; }
```

- Spécificité voulue (0,0,2) : toute règle de classe existante gagne (`.btn`, `.breadcrumb a`, `.history-seasons a`, `.cal-nav a`, `.blog-card-link`, `.next-rouge-detail a`, `.cta-banner-btn`). Header, footer et modale sont hors de `<main>`, donc non touchés.
- `--accent-hover` (#1D4ED8) sur blanc = 6,7:1, sur `--bg` = 6,4:1 : AA tenu.
- Justification : un seul style de lien pour tout le contenu, cohérent avec `.breadcrumb a` et `.legal-text a` (bleu du site) mais avec soulignement discret conservé (repère non basé sur la seule couleur, WCAG 1.4.1).
- Ne pas mettre de couleur au `:visited` (pas de violet).

### G2. Titres et respiration de page (P0)

Problème : H1/H2/H3 sans règle globale (interligne 1,7 hérité, marge 0), H1 collé au paragraphe suivant, H1 des mentions légales collé au header.

```css
main h1 { font-size: 2rem; line-height: 1.25; margin: 0 0 16px; }
main h2 { line-height: 1.3; }
main h3 { line-height: 1.35; }
main h1 + .section-desc, main h1 + p { margin-top: 0; }
main.page-content { padding-top: 8px; padding-bottom: 24px; }
@media (max-width: 600px) { main h1 { font-size: 1.6rem; } }
```

- Même taille que l'AVANT (2em, graisse 700 navigateur) : on ne change que l'interligne (1,7 vers 1,25) et la marge basse. `.home-title` (1,5rem) et `.blog-article-header h1` gardent leur règle de classe.
- `.section-desc` a `margin: -8px 0 16px` : le `h1 + .section-desc` remet 0 pour ne pas chevaucher.
- `main.page-content` : `_base.html` a déjà cette classe ; `legal.html` doit la prendre (voir Mentions légales).

### G3. Composant tableau unique (P0)

Remplace `.season-table`, `.tarif-table`, `.days-table`, `.method-table`, `.api-table` (5 blocs `<style>` inline à supprimer). Enveloppe = `.table-scroll` existant (bord `--border`, rayon 8 px, fond blanc, défilement horizontal). Nouvelle classe unique `.data-table`, copie allégée de `.history-table` (sans `min-width: 720px`) :

```css
.data-table { width: 100%; border-collapse: collapse; font-size: 0.95rem; }
.data-table th, .data-table td { padding: 10px 14px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: middle; }
.data-table thead th { background: var(--bg); color: var(--text-secondary); font-size: 0.8rem; font-weight: 600; vertical-align: bottom; }
.data-table tbody th { font-weight: 600; }
.data-table tbody tr:last-child th, .data-table tbody tr:last-child td { border-bottom: 0; }
.data-table .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.data-table .muted { color: var(--text-secondary); font-style: italic; }
.data-table tr.rouge th { color: var(--rouge); }
.data-table code { overflow-wrap: anywhere; }
```

Balisage : `<div class="table-scroll"><table class="data-table">` (garder `<caption class="sr-only">` et `scope`). Montants et effectifs : classe `num` sur `th` ET `td` de la colonne (aligné à droite, chiffres tabulaires). Justification : un seul rendu de tableau sur tout le site, identique à celui de l'historique que le fondateur connaît.

### G4. Boutons (P0)

Problèmes : (a) `<a class="btn">` est souligné (pas de `text-decoration: none` dans `.btn`), visible sur 404 et à propos, alors que le `<button>` voisin ne l'est pas ; (b) `.btn-outline` (404.html) n'existe pas, donc 5 boutons identiques pleins ; (c) `.btn { margin-top: 16px }` décale verticalement les boutons d'une rangée.

```css
.btn { text-decoration: none; }
.btn-outline { background: var(--surface); color: var(--accent); border: 2px solid var(--accent); }
.btn-outline:hover { background: var(--bleu-bg); color: var(--accent-hover); box-shadow: none; }
.btn-row { display: flex; flex-wrap: wrap; justify-content: center; gap: 12px; list-style: none; padding: 0; margin: 24px 0; }
.btn-row .btn { margin-top: 0; }
```

`.btn-outline` à placer APRÈS `.btn:hover` (même spécificité). Appliquer `class="btn-row"` à la `<ul>` de 404.html (remplace les styles inline) et au conteneur des 2 CTA de `a_propos.html`.

### G5. Pied de page (P0)

Problèmes (capture `apres_verif`, toutes pages) : 12 liens sur 2 lignes avec 2 orphelins (« Mentions légales », « llms.txt »), interligne de 50 px, bord gauche à x=133 alors que le header est à x=153 (le `.footer-inner` n'a pas le `padding: 0 20px` du `.container`).

```css
footer { padding: 40px 0 24px; }
.footer-inner { max-width: 1100px; margin: 0 auto; padding: 0 20px; }
.footer-links { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0 24px; margin-top: 12px; }
.footer-links a { display: block; min-height: 44px; line-height: 24px; padding: 10px 0; }
@media (max-width: 600px) { .footer-links { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 480px) { .footer-inner { padding: 0 12px; } }
```

Résultat : 3 lignes de 4 liens alignées (2 colonnes en mobile), cibles tactiles de 44 px conservées. Aucune modification de `_footer.html`. Le pied de page de l'AVANT (6 liens, 1 ligne) ne peut pas être conservé : les 12 liens sont du maillage interne SEO à garder.

### G6. Pied de page collé en bas sur pages courtes (P1)

404 : le pied de page flotte au milieu, du blanc en dessous.

```css
body { min-height: 100vh; min-height: 100dvh; display: flex; flex-direction: column; }
main { flex: 1 0 auto; width: 100%; }
```

`width: 100%` est indispensable (sinon `margin: 0 auto` fait rétrécir `<main>` au contenu dans un flex colonne). Vérifier accueil, calendrier, admin après changement.

### G7. FAQ tronquée et focus clavier (P0)

`.faq-item .faq-content { max-height: 0; overflow: hidden }` puis `.faq-item[open] .faq-content { max-height: 300px }` (lignes ~1051-1059) : l'animation ne peut jamais jouer (un `<details>` fermé masque déjà son contenu) et le plafond de 300 px coupe toute réponse plus longue. Les FAQ de l'accueil ont été allongées (12 questions, listes `.faq-list-items`), le risque est réel en 390 px `[code, à confirmer en ouvrant la plus longue réponse]`.

```css
/* supprimer les deux règles max-height et remplacer par : */
.faq-item .faq-content { overflow: visible; }
.faq-item summary { min-height: 44px; }
.faq-item summary:focus-visible { outline: 3px solid var(--bleu); outline-offset: -3px; border-radius: var(--radius-sm); }
```

Aussi : ajouter `summary` à la liste du bloc `a:focus-visible, button:focus-visible...` (ligne ~2117). Le signe « - » du `[open]` peut devenir `'\2212'` (P2).

### G8. Contrastes et tokens (P0 pour les 4 cas en gras, P2 pour le refactor)

Ajouter dans `:root` : `--success:#2E7D32; --success-bg:#E8F5E9; --warn:#C2410C; --warn-bg:#FFF3E0; --warn-border:#FDBA74; --info:#1D4ED8; --info-bg:#E3F2FD;` puis remplacer les valeurs en dur.

| Élément | Fichier / sélecteur | Actuel | Ratio estimé | Correctif |
|---------|---------------------|--------|--------------|-----------|
| **Liens de nav header** | `nav a` | `rgba(255,255,255,.8)` sur `#2563EB` | ~3,7:1 (échec AA, 13,6 px) | `color: #fff` (la pastille `nav a.active` garde la hiérarchie) |
| **Badges orange** | `.fc-uncertain-badge`, `.fc-changed`, `.today-tip.tip-blanc`, `.week-dot-hesitation`, `.hesi-separator`, `.simulated-warning` | `#E65100` sur `#FFF3E0` | ~3,4:1 | `color: var(--warn)` (4,7:1), bordure `var(--warn-border)` |
| **Badge « En test »** de la modale | `.beta-badge` | blanc sur dégradé `#f59e0b`-`#d97706` | 2,1 à 3,2:1 | `background: var(--warn-bg); color: #9A3412; border: 1px solid var(--warn-border);` sans dégradé |
| **Température non disponible** (historique) | `.hg-temp.hg-nd` | `#9CA3AF` sur blanc | 2,5:1 | `color: var(--blanc)` (4,8:1) |
| Bleu sur fond bleu clair | `.today-tip.tip-bleu`, `.label-prediction` | `#2563EB` sur `#E3F2FD` | ~4,4:1 | `color: var(--info)` |
| Vert admin | `.rouge-table .val-good` (admin.html) | `#16A34A` sur blanc | 3,3:1 | `#15803D` |
| Variables absentes | `.loading-spinner`, `.maintenance-banner`, `.last-update-bar` | `--primary`, `--card-bg`, `--text-primary`, `--bg-secondary` | n/a | `var(--accent)`, `var(--surface)`, `var(--text)`, voir Accueil A3 |

Vérifier ensuite tous les couples avec un outil (la mesure ci-dessus est manuelle). Pas de mode sombre dans le périmètre actuel.

### G9. Modale d'inscription : bottom sheet mobile et focus (P0)

Constat `[code]` : `.subscribe-modal-overlay { align-items: center; height: 100% }` + `.subscribe-modal { max-height: 90vh; overflow-y: auto }` (95vh sous 480 px) : pattern cassé sur iOS Safari (barre d'adresse, `vh`), confirmé sur 3 projets. Toucher `static/css/style.css` (section « Subscribe modal ») :

```css
.subscribe-modal-overlay { height: 100vh; height: 100dvh; }
@media (max-width: 640px) {
    .subscribe-modal-overlay { align-items: flex-end; padding: 0; }
    .subscribe-modal { max-width: none; max-height: calc(100dvh - 24px); border-radius: var(--radius) var(--radius) 0 0; padding: 24px 20px calc(20px + env(safe-area-inset-bottom)); overscroll-behavior: contain; animation: sheetUp 0.3s ease; }
}
@keyframes sheetUp { from { transform: translateY(100%); } to { transform: translateY(0); } }
```

- Supprimer la règle `.subscribe-modal { max-height: 95vh }` du bloc `@media (max-width: 480px)` (elle écraserait la nouvelle).
- Ajouter `viewport-fit=cover` à la balise viewport de `_base.html`, `dashboard.html`, `calendrier.html`, `legal.html` (sinon `env(safe-area-inset-bottom)` vaut 0).
- Focus des champs : `.form-group input:focus, .form-group select:focus` fait `outline: none` avec une ombre à 10 % d'opacité, ce qui masque aussi le `:focus-visible` global (spécificité supérieure). Remplacer par `.form-group input:focus-visible, .form-group select:focus-visible { outline: 2px solid var(--bleu); outline-offset: 2px; border-color: var(--bleu); }`.

### G10. Puces de saisons (P1)

`.history-seasons a` (pilules avec `:hover` et `.active`) existe déjà. L'étendre au sélecteur `.cal-seasons a` (calendrier.html et calendrier_saison.html : liens « Saison 2026-2027 (en cours) · Saison 2025-2026... » aujourd'hui en liens bruts) : groupes `.history-seasons a, .cal-seasons a`, idem `:hover` et `.active`. Dans les 2 templates, supprimer les `&middot;` entre liens (le `gap` du flex suffit) et ajouter `class="active" aria-current="page"` à la saison affichée.

### G11. En-tête à 5 liens entre 769 et 960 px (P1, `[à vérifier à 820 px, non capturé]`)

L'AVANT avait 3 liens de nav, la version APRÈS en a 5 plus le CTA : environ 860 px de large nécessaires contre 769 px de seuil du hamburger, donc risque de nav sur 2 lignes. Correctif préventif :

```css
@media (max-width: 960px) {
    nav a { padding: 10px 10px; font-size: 0.82rem; }
    nav .nav-cta { padding: 10px 16px; }
    header[role="banner"] .header-title { font-size: 1.2rem; }
}
```

Si le rendu à 820 px reste sur 2 lignes, remonter le seuil du hamburger à 900 px (déplacer les 3 règles `.hamburger-btn`, `#main-nav`, `#main-nav a` dans un `@media (max-width: 900px)` dédié).

### G12. Cartes de contenu long (`.legal-section`) (P1)

Sert à mentions légales, à propos, méthodologie. Les `<h2>` de ces cartes n'ont aucun style (seul `.legal-section h3` existe, ligne ~1232) et les sous-titres n'ont pas de marge haute (constaté sur à propos : « Combinaison et règles EDF » collé au paragraphe). Remplacer la règle `.legal-section h3` par :

```css
.legal-section > :first-child { margin-top: 0; }
.legal-section h2 { font-size: 1.25rem; font-weight: 700; margin: 0 0 12px; color: var(--text); }
.legal-section h3 { font-size: 1.05rem; font-weight: 700; margin: 20px 0 8px; color: var(--text); }
.legal-section h4 { font-size: 0.95rem; font-weight: 700; margin: 16px 0 6px; color: var(--text); }
.legal-section ul { margin: 8px 0 12px 20px; line-height: 1.7; }
.legal-section li { margin-bottom: 6px; }
```

À placer APRÈS `.page-content h2` (fin de fichier) pour gagner à spécificité égale. Puis supprimer les `style="margin:0.5rem 0 1rem 1.5rem;line-height:1.8"` des `<ul>` de `legal.html`.

### G13. Utilitaires (P2)

`.nowrap { white-space: nowrap; }` (H1 de la saison), `main code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; background: var(--incertain-bg); padding: 1px 6px; border-radius: 4px; }` et `main pre code { background: none; padding: 0; }`.

---

## 4. Audit page par page

Légende : P0 bloquant bascule, P1 à faire dans la même passe, P2 finition. Chaque ligne : fichier, élément, action, justification.

### 4.1 Accueil `/` (`templates/dashboard.html`)

Constat AVANT/APRÈS : le corps de page (cartes « Comment économiser », compteurs, CTA WhatsApp, FAQ) est fidèle à l'AVANT et propre. Le défaut se concentre dans le haut de page : 4 blocs de langages différents empilés (carte blanche sans bord, texte nu centré, bandeau gris, carte à bord bleu).

- **P0 A1. Encadré `.today-answer`** (dashboard.html l.182-198, style.css l.2629-2635). Refaire avec les composants existants : carte à bord gauche 5 px comme `.week-summary-card` / `.subscribe-cta-card`, pastilles `.color-tag` au lieu de `.color-word`, liens en pilules comme `.history-seasons a`.
  ```css
  .today-answer { background: var(--surface); border: 1px solid var(--border); border-left: 5px solid var(--bleu); border-radius: var(--radius); box-shadow: var(--shadow); padding: 16px 20px; margin: 0 0 16px; font-size: 0.95rem; line-height: 1.7; color: var(--text); }
  .today-answer.has-rouge { border-left-color: var(--rouge); }
  .today-answer .today-answer-line { display: block; }
  .today-answer .today-answer-links { display: flex; flex-wrap: wrap; gap: 8px 12px; margin-top: 12px; font-size: 0.9rem; }
  .today-answer-links a { display: inline-flex; align-items: center; min-height: 44px; padding: 8px 16px; border: 1px solid var(--border); border-radius: 50px; background: var(--surface); color: var(--accent); font-weight: 600; text-decoration: none; }
  .today-answer-links a:hover { border-color: var(--bleu); background: var(--bleu-bg); }
  .color-tag { font-weight: 700; }
  ```
  Template : `class="color-word X"` devient `class="color-tag X"` (3 occurrences), envelopper la phrase « Couleur ... du jour » et la phrase « Demain ... » chacune dans `<span class="today-answer-line">` (une ligne par jour), supprimer le `&middot;` entre les deux liens (le `gap` sépare), ajouter `has-rouge` si l'une des couleurs est rouge. Supprimer `.color-word.*` de style.css. Aucun texte retiré. Justification : `.color-word.blanc` (fond blanc + bordure) est invisible sur la carte blanche, `.color-tag` est déjà le composant du bandeau d'accueil et des pastilles du site ; les liens ne sont plus bruts et gardent 44 px tactiles.
- **P1 A2. `.last-update-bar`** (style.css l.449-457) : bande grise `#f5f5f5` (variable `--bg-secondary` inexistante), absente de l'AVANT. Remplacer par `background: none; padding: 0; margin: 0 0 8px; text-align: left; font-size: 0.8rem;`. Elle devient une légende discrète au-dessus du résumé.
- **P1 A3. Prix incohérents** : `.counter-help` affiche « HP 0,73 / 0,19 / 0,17 € » en dur (dashboard.html l.282-294) alors que calendrier et tarif affichent 0,7295 / 0,1921 / 0,1654. Utiliser la macro `prix()` de `facts.TARIFS` (source unique, cf. CLAUDE.md). Le fondateur verra deux prix différents pour la même chose d'une page à l'autre.
- **P2 A4.** `#subscribe` en double (`_subscribe_modal.html` l.2 `<div id="subscribe">` et `<section id="subscribe">` de l'accueil) : id dupliqué invalide, ne conserver que la section sur l'accueil.
- **P2 A5.** `.step-card` : carte 2 (bouton) et carte 3 (lien) de hauteurs de contenu différentes, bouton non aligné en bas : `.step-card { display: flex; flex-direction: column; }` et `.step-card .btn { margin-top: auto; }`.
- **P2 A6.** `.week-summary-card.has-rouge { background: #FFF8F7 }` : créer `--rouge-bg-soft` ou utiliser `--rouge-bg` (G8).
- Vérifié OK : résumé 10 jours, compteurs, CTA, FAQ (hors G7), mobile 390 px des cartes (3 colonnes compactes).

### 4.2 Calendrier du mois `/calendrier` (`templates/calendrier.html`, styles inline l.72-109)

Constat : structure identique à l'AVANT, nouveaux ajouts propres (navigation mois précédent/suivant, légende « non publié »), mais 3 largeurs de colonne (1100 / 800 / 700) et liens de saisons bruts.

- **P1 C1. Compteurs en mobile** (l.104-108) : 3 cartes empilées de 300 px = 500 px avant la grille (capture mobile). Remplacer le bloc `@media (max-width: 600px)` de `.cal-stats` par `grid-template-columns: repeat(3, 1fr); gap: 8px; max-width: none;` et ajouter `.cal-stat { padding: 12px 6px; } .cal-stat .stat-count { font-size: 1.4rem; } .cal-stat .stat-label { font-size: 0.75rem; }`.
- **P1 C2. Saisons** (`.cal-seasons`, section « Dates des jours rouges et blancs par saison ») : voir G10 (pilules).
- **P2 C3.** Légende `.cal-legend-dot` avec `border:1px solid #999` en ligne : `var(--incertain)`; 5 items : ok en desktop, `justify-content: center` déjà présent.
- **P2 C4.** FAQ en 1100 px de large alors que le texte SEO est en 800 px et la grille en 700 px : ajouter `class="cal-seo-content"` à la `<section class="faq-section">` (largeur 800, centrée) pour n'avoir que 2 largeurs.
- **P2 C5.** Déplacer `.cal-nav`, `.cal-grid`, `.cal-day`, `.cal-stats`, `.cal-stat`, `.cal-legend*`, `.cal-seasons` dans `style.css` (réutilisables par la page saison, cf. S3) et régénérer `style.min.css`.

### 4.3 Saison `/calendrier/AAAA-AAAA` (`templates/calendrier_saison.html`, style inline l.20-26)

Constat : page la plus éloignée du site (aucune carte, liens bruts gras dans le tableau, H1 coupé « 2026- / 2027 »).

- **P0 S1. Tableau « Répartition par mois »** : `.table-scroll` + `.data-table` (G3). `th scope=row` avec lien (bleu G1), cellules Rouges/Blancs/Bleus `class="num"`, cellule `colspan="3"` « données non disponibles » en `class="num muted"`. Supprimer `.season-table` du `<style>`.
- **P0 S2. H1** : « Jours rouges et blancs Tempo EDF : saison 2026-2027 » se coupe au tiret. Envelopper `saison {{ s.season_label }}` dans `<span class="nowrap">` (G13). Le texte du H1 est inchangé.
- **P1 S3. Listes de dates** (`.season-dates`, `ol` à puces système) : classes `season-dates season-dates--rouge` / `--blanc`.
  ```css
  .season-dates { list-style: none; margin: 8px 0 24px; padding: 0; columns: 2; column-gap: 32px; }
  .season-dates li { break-inside: avoid; padding: 6px 0; border-bottom: 1px solid var(--border); }
  .season-dates li::before { content: ""; display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 10px; background: var(--rouge); }
  .season-dates--blanc li::before { background: var(--blanc); }
  ```
  (le `columns: 1` sous 600 px existe déjà). Justification : même pastille que `.legend-dot`, lisible et cohérente avec les couleurs.
- **P1 S4. Chiffres de saison** : le paragraphe « 0 jours rouges, 0 jours blancs et 31 jours bleus... » peut être doublé par les 3 cartes `.cal-stats` (C5) placées au-dessus (texte conservé). Optionnel, gain de hiérarchie visuelle.
- **P1 S5. « Autres saisons »** : pilules (G10). **P1** bloc « données réutilisables » : lien G1.

### 4.4 Historique des prévisions `/historique-previsions` (`historique_previsions.html`, `_historique_*.html`, style.css l.2646-2703)

Constat : le composant le plus abouti des pages neuves (puces de saison, légende, `.table-scroll` avec indice de défilement, colonne date collante, chiffres à droite en `tabular-nums`). Il sert de référence pour G3 et G10.

- **P1 H1.** `.history-table tbody th` collant en mobile (le libellé « 2 jours avant » disparaît au défilement horizontal, `min-width: 720px`) : `.history-table tbody th { position: sticky; left: 0; background: var(--surface); z-index: 1; box-shadow: 1px 0 0 var(--border); }` et idem `.history-table thead th:first-child` avec `background: var(--bg)`. `[code, capture mobile trop réduite]`.
- **P1 H2.** `.hg-temp.hg-nd` : contraste (G8).
- **P2 H3.** Grille J-15 à J-1 en 860 px : cellules de 34 px, pastilles de 18 px. Autoriser 1100 px pour cette page seule : bloc `{% block main_class %}page-content{% endblock %}` dans `_base.html`, valeur `container` (sans `page-content`) dans le template de cette page.
- **P2 H4.** Section « Méthode » : la mettre dans une `.legal-section` (G12) comme sur à propos, pour finir la page par une carte et pas du texte brut.
- **P2 H5.** Contrôle visuel 390 px à refaire après G1-G5 avec une capture non réduite (résolution 390 px vue à l'échelle 1).

### 4.5 Tarif `/tarif-tempo-edf` (`templates/tarif_tempo.html`, style inline l.20-27)

Constat : texte brut sur fond de page, tableau sans cadre limité à 640 px, en-têtes qui se cassent sur 3 lignes (« Heures pleines (6 h à 22 h) »).

- **P0 T1. Tableau** : `.table-scroll` + `.data-table` (G3), supprimer `.tarif-table-wrap` et `.tarif-table`. Conserver `class="rouge"` sur la ligne (défini dans G3), `class="num"` sur les `th` et `td` de prix (déjà là, montants alignés à droite). Pleine largeur de carte : les 3 colonnes tiennent sur 1 ligne d'en-tête en desktop.
- **P1 T2. Libellés de ligne** : en 390 px « Jour bleu (300 jours/saison) » prend 4 lignes. Envelopper la parenthèse dans `<span class="hb-sub">(300 jours/saison)</span>` (classe existante : petit, gris, sur sa propre ligne). Texte inchangé.
- **P1 T3. Section « Combien coûte un jour rouge »** : paragraphe dense de 6 chiffres. Reprendre `.counters` / `.counter-card` (existants, bord haut coloré) pour 3 repères : « 4,4 fois » (rouge), « 14 à 20 € de plus » (rouge), « 310 € d'économie » (bleu), le paragraphe étant conservé dessous. Option visuelle, la hiérarchie finale reste à @ux.
- **P2 T4.** `<small>` de la source : `font-size: 0.85rem; color: var(--text-secondary)` (classe `.form-hint` existe déjà, l'utiliser).

### 4.6 Couleur de demain `/couleur-tempo-demain` (`templates/couleur_demain.html`, style inline l.16-20)

Constat : c'est le même encadré réponse que l'accueil, avec une troisième variante de style.

- **P0 D1. `.demain-answer`** : supprimer le style inline et utiliser `class="today-answer today-answer--lead"` (composant A1). Ajouter `.today-answer--lead { font-size: 1.1rem; } .today-answer--lead .color-tag { font-size: 0.95rem; }`. `color-word` devient `color-tag`. Une phrase par `<p>` est déjà le cas (pas de `today-answer-line` nécessaire).
- **P0 D2. `.days-table`** : `.table-scroll` + `.data-table` (G3).
- **P1 D3. Colonnes du tableau** : cellule Couleur = `<span class="color-tag bleu">Bleu</span>` (le mot reste, la forme ne dépend plus de la seule couleur), cellule Statut = `<span class="source-label label-officiel">officielle (EDF)</span>` ou `label-prediction` (classes existantes `.source-label`, P-13). La probabilité « 99 % » reste dans le texte du statut.
- **P2 D4.** Liste « Quelles couleurs sont possibles demain » : mettre `<strong class="color-tag rouge">Rouge</strong>` (idem blanc, bleu) à la place du `<strong>` nu.

### 4.7 Méthodologie `/methodologie` (`templates/methodologie.html`, style inline l.17-21)

Constat : page sœur de « À propos » (qui est en cartes) rendue en texte brut : deux pages du même type qui ne se ressemblent pas.

- **P0 M1. Tableau des poids** : `.table-scroll` + `.data-table`, `class="num"` sur `th` et `td` du poids (G3). Supprimer `.method-table`.
- **P1 M2. Sections 1 à 6** : chaque `<h2>` et son contenu dans `<section class="legal-section">` (G12), comme à propos. Les `<h3>` de la section 5 (« En conditions réelles », « Historique complet », « Ce que nous ne publions pas ») profitent de G12. Aucun texte retiré, aucun H1 touché.
- **P1 M3. Mesure en direct** (« 100,0 % ... ») : passer `<strong>` en carte `.counter-card bleu` (grand chiffre), paragraphe conservé dessous. Option.
- **P2 M4.** H1 : G2 (interligne 1,25).

### 4.8 API Tempo `/api-tempo` (`templates/api_tempo.html`, style inline l.34-41)

- **P0 Ap1. Tableau des endpoints** : `.table-scroll` + `.data-table` (G3), supprimer `.api-table-wrap`/`.api-table`. La 1re colonne `<code>` : G13 (`main code`).
- **P1 Ap2. `.api-doc pre`** : fond `#0F172A` = `--cta-dark` : utiliser `var(--cta-dark)`. Ajouter `tabindex="0"` aux `<pre>` (défilement clavier, WCAG 2.1.1) et `pre:focus-visible { outline: 3px solid var(--bleu); outline-offset: 2px; }`.
- **P2 Ap3.** Les `<h2>` qui sont des routes (`/api/today et /api/tomorrow`) sont à laisser en texte (SEO), ne pas les passer en `<code>`.

### 4.9 Blog index `/blog/` (`templates/blog_index.html`)

Constat : cartes identiques à l'AVANT, conformes. Écart : la ligne de méta affiche 3 éléments (date, « mis à jour le JJ/MM/AAAA », temps de lecture) et répète souvent la même date.

- **P1 B1.** Afficher « mis à jour le » seulement si la date diffère de la date de publication (logique de template). Sinon ligne trop chargée et redondante.
- **P1 B2.** `.blog-card-meta { flex-wrap: wrap; gap: 4px 16px; }` (évite le débordement en 390 px avec 3 items).
- Vérifié OK : CTA final `.blog-cta`, boutons, espacements, H1.

### 4.10 Article de blog `/blog/<slug>` (`templates/blog_article.html`, `.blog-article*`)

Constat limité par la taille des captures `[code]`. Structure conforme (carte blanche unique, H2 à filet, tableau comparatif, CTA inline, FAQ).

- **P1 Ar1. Tableaux** : `.blog-article-body table { display: block; overflow-x: auto; }` (le tableau « Comparaison des services » à 4 colonnes déborde probablement en 390 px).
- **P1 Ar2. `.blog-article-meta { flex-wrap: wrap; }`** (nouvelle mention « Mis à jour le », 3 éléments).
- **P1 Ar3. Bloc « À lire aussi »** (`.blog-related`) : liens en G1, `ul` à puces système : `list-style: none; margin-left: 0;` et `li { padding: 6px 0; border-bottom: 1px solid var(--border); }` pour ressembler à une liste de cartes (comme `.blog-card`), ou `.blog-nav-articles` existant.
- **P2 Ar4. Montants dans les tableaux Markdown** : `blog.py` peut poser `class="num"` sur les `td` dont le contenu commence par un nombre suivi de `€`, `%`, `kWh` ; ajouter `.blog-article-body td.num { text-align: right; font-variant-numeric: tabular-nums; }`. Non vérifié sur les 22 articles.

### 4.11 Alertes `/alertes` (`templates/alertes.html`)

Constat : page presque identique à l'AVANT, propre (cartes, bulle WhatsApp, CTA). Pas de P0.

- **P1 Al1.** Vérifier que le H1 (centré, 2 lignes, interligne 1,7 visible dans les 2 captures) n'est pas déjà stylé en ligne : sinon appliquer G2 (`line-height: 1.25`).
- **P2 Al2.** Bord gauche non aligné : « Comment ça marche ? » (x=383) décalé par rapport aux cartes (x=303) et au fil d'Ariane (x=317). Élargir la liste à la largeur des cartes, non bloquant (déjà présent en AVANT).
- Liens « Gérer ou se désinscrire » : G1.

### 4.12 À propos `/a-propos` (`templates/a_propos.html`)

Constat : cartes `.legal-section` conformes à l'AVANT, nouveaux liens (méthodologie, API) propres sauf lien brut.

- **P0 Ap-A1. Boutons de fin de page** « Voir le calendrier Tempo », « S'inscrire aux alertes » soulignés : G4 (`.btn { text-decoration: none }`), conteneur `class="btn-row"`.
- **P1 Ap-A2.** Sous-titres sans marge (« Scoring multi-critères », « Machine Learning », « Combinaison et règles EDF ») : G12.
- **P2 Ap-A3.** Phrase « Nous répondons généralement sous 48 h » et adresse en gras : inchangé.

### 4.13 Mentions légales `/mentions-legales` (`templates/legal.html`)

Constat : H1 collé sous le header (AVANT identique, à corriger maintenant), page de 6 100 px avec une section 4 très dense (sous-sections 4.1 à 4.8, lettres a à f) rendue sans hiérarchie visible.

- **P0 L1. `<main>`** (l.83) : `class="container" style="max-width:800px"` devient `class="container page-content"` (G2 pose l'air haut, 860 px). Supprimer le `style` inline.
- **P0 L2. Non design, à transmettre** : section 2 « Hébergeur » cite Replit, Inc. À remplacer au moment de la bascule (Cloudflare, Inc. et Neon pour la base) : obligation légale.
- **P1 L3. Hiérarchie des sections** : G12 (`h2` 1,25 rem, `h3` 1,05 rem, `h4` 0,95 rem, marges, listes). Supprimer les `style` inline des `<ul>`.
- **P1 L4. Fil d'Ariane visible** : ajouter `<nav class="breadcrumb">Accueil › Mentions légales</nav>` (le JSON-LD BreadcrumbList est déjà dans le `<head>`, le visible doit le refléter ; aligne la page sur les 10 autres).
- **P2 L5. Formulaire de désinscription** : `.btn-unsub` (rayon 8 px, rouge) contre les pilules du site : `border-radius: 50px; min-height: 44px; font-weight: 700; background: var(--rouge);` et `.unsub-form input { min-height: 44px; }`. Ajouter un `<label class="sr-only">` au champ.
- **P2 L6.** Sommaire en haut (9 liens d'ancre en pilules `.history-seasons`) pour une page de 6 000 px : décision de placement à valider avec @ux.

### 4.14 Page 404 (`templates/404.html`)

- **P0 N1.** `.btn-outline` inexistant, `<a class="btn">` souligné, marge haute parasite : G4. Remplacer `<ul style="...">` par `<ul class="btn-row">`. Résultat : 1 bouton plein (action principale) + 4 pilules à bord bleu + le bouton « Alertes gratuites » au même niveau de rangée.
- **P1 N2.** Pied de page qui flotte : G6.
- **P2 N3.** Supprimer les styles inline (`padding:2rem 0`, `font-size:1.1rem;margin:1.5rem 0`) : `<p class="section-desc">` et `padding-top` par le G2.

### 4.15 Fenêtre d'inscription (`templates/_subscribe_modal.html`, `.subscribe-modal*`)

- **P0 Mo1.** Bottom sheet mobile, focus des champs : G9.
- **P1 Mo2. Styles inline** (l.29-46 et 58-66) : trois `<label style="display:flex...">` deviennent `<label class="form-checkbox">` (classe existante, case de 24 px) ; le titre « Quelles alertes souhaitez-vous ? » devient `<fieldset class="form-group form-choices"><legend>` avec `.form-choices { border: 0; padding: 0; } .form-choices legend { font-size: 0.85rem; font-weight: 600; color: var(--text-secondary); margin-bottom: 6px; }`. Le champ « gérer mon abonnement » : retirer le `style` inline (il hérite de `.form-group input` dans un `.form-group`) et ajouter `<label class="sr-only" for="modal-manage-phone">`.
- **P1 Mo3.** Badge « En test » : G8 (contraste et fin du dégradé).
- **P2 Mo4.** `.sms-bubble { background: var(--info-bg); }` (G8).

### 4.16 Admin `/admin` (`templates/admin.html`)

Constat `[code, lignes 1-330 lues, pas de capture]` : l'admin doit rester une extension du produit, or il s'en écarte sur la police, les couleurs et l'en-tête.

- **P0 Ad1. Police** : `fonts.css` n'est pas chargée (seulement `style.min.css`, sans `?v=`). Inter absente, police système en repli. Ajouter avant la feuille de style : `<link rel="preload" href="/static/fonts/inter-latin-wght-normal.woff2" as="font" type="font/woff2" crossorigin><link rel="stylesheet" href="/static/css/fonts.css?v=20260929">` et `?v=20260929b` sur `style.min.css`.
- **P0 Ad2. Couleurs des pastilles** : `.recap-dot.bleu #2196F3`, `.blanc #B0BEC5`, `.rouge #F44336`, `.recap-actual.*` (bleu `#2196F3`, blanc `#9E9E9E`, rouge `#F44336`) divergent des tokens publics (`#2563EB`, `#6B7280`, `#DC2626`). Utiliser `var(--bleu)`, `var(--blanc)`, `var(--rouge)` (et `.action-btn.green` : `var(--vert)`). Le violet `#7B1FA2` reste réservé aux marqueurs de version (documenté dans CLAUDE.md).
- **P1 Ad3. En-tête** : la nav n'a que Accueil / Calendrier / Blog ; aligner sur `_base.html` (Accueil, Calendrier, Tarifs, Historique, Blog, CTA).
- **P1 Ad4. Collision de classe** : `.section-title` redéfini dans le `<style>` de l'admin (0,92 rem) alors que c'est la classe publique (1,15 rem) : renommer en `.admin-section-title` (admin.html l.30-32 et usages).
- **P1 Ad5. Focus du login** : `.admin-login input:focus { outline: none; box-shadow: 0 0 0 3px var(--accent-shadow) }` donne un indicateur d'environ 1,9:1 : remplacer par `outline: 2px solid var(--accent); outline-offset: 2px;`.
- **P1 Ad6. Texte trop petit en mobile** : `.recap-table` 0,55 à 0,65 rem (9 à 10 px), `.diag-text` 0,58 rem, `.prf-table` 0,65 rem : plancher à 0,7 rem (11 px), le défilement horizontal existe déjà (`.recap-wrapper`).
- **P1 Ad7. Contraste** : `.rouge-table .val-good` : G8 (`#15803D`).
- **P2 Ad8.** `.action-btn` : pilule (`border-radius: 50px`, `min-height: 40px`) comme `.btn`; limiter les couleurs à bleu / vert / gris (supprimer teal et violet des boutons d'action). ~40 styles inline répétitifs (`display:flex;gap:10px;...`) : classe `.toolbar`.
- **P2 Ad9.** Largeur : `main` en 1100 px alors que la grille de récap demande 900-1100 px de `min-width` : élargir à 1400 px pour l'admin seul.
- **Non vérifié** : admin.html au-delà de la ligne 330 (rendus JS, tableaux injectés). Montants : aucune colonne monétaire repérée dans les 330 lignes lues.

### 4.17 Transversal (header, nav, méta)

- **P1 X1.** Nav header : G8 (contraste) et G11 (5 liens).
- **P2 X2.** `<meta name="theme-color" content="#1565C0">` (toutes pages) : le header est `#2563EB`, l'onglet mobile sera d'un autre bleu : `#2563EB`.
- **P2 X3.** `?v=20260929b` écrit en dur dans ~12 templates : variable Jinja globale `asset_v` pour ne plus oublier un fichier à la prochaine modification de CSS.
- **P2 X4.** `style.css` ligne 6 (commentaire « Google Fonts loaded via <link> ») : obsolète (Inter auto-hébergée), à supprimer pour éviter qu'un agent le « corrige ».
- Zéro tiret cadratin : aucun rendu dans les captures APRÈS (les « — » de l'AVANT ont disparu : aperçu WhatsApp, à propos, compteurs). Non vérifié dans le texte des 22 articles Markdown et dans `admin.html` (titre avec « — », usage interne noindex toléré).

---

## 5. Les 10 critères visuels : échecs constatés sur les captures APRÈS

Seuls les FAIL sont listés (tout le reste est PASS). Devices évalués : 1366 px et 390 px ; 768 px non évaluable (aucune capture).

| Page | Critère en échec | Preuve visuelle |
|------|------------------|-----------------|
| Accueil | 1 PRO, 5 PROPRE, 9 HIÉRARCHIE | encadré sous le H1 : liens bleus soulignés navigateur, pastilles « Bleu » de radius différent, interligne des deux phrases identique, bande grise `#f5f5f5` juste dessous |
| Calendrier | 4 MÊME IDENTITÉ | liens de saisons bruts soulignés, 3 largeurs de colonne (1100 / 800 / 700) |
| Saison | 1, 3, 4, 5, 6 | H1 coupé « 2026- / 2027 », tableau à liens gras `#0000EE`, aucune carte, listes à puces système |
| Tarif | 4, 5, 7 AÉRÉ | en-têtes sur 3 lignes, tableau sans cadre collé sous le H1 (écart nul H1/paragraphe) |
| Couleur demain | 4, 5 | 3e variante de l'encadré réponse, tableau nu |
| Méthodologie | 4, 5 | texte brut alors que « À propos » (même nature) est en cartes |
| API | 5 | tableau nu, `<code>` sans fond |
| Mentions légales | 5, 7, 9 | H1 à 0 px sous le header, section 4 sans hiérarchie |
| 404 | 4, 5 | 5 boutons identiques dont 4 soulignés, pied de page flottant |
| Toutes | 6 ALIGNÉ, 10 ACCESSIBLE | pied de page décalé de 20 px par rapport au header ; nav header 3,7:1 ; badges orange 3,4:1 |

## 6. Ordre d'exécution recommandé pour @fullstack

1. `style.css` : G1, G2, G3, G4, G5, G6, G7, G8, G9, G10, G11, G12, G13 (1 passe, 1 seul commit CSS), puis régénérer `style.min.css` et incrémenter `?v=` partout.
2. Templates : accueil A1-A3, demain D1-D3, saison S1-S3, tarif T1-T2, méthodologie M1-M2, API Ap1-Ap2, mentions légales L1/L3/L4, 404 N1, modale Mo2, admin Ad1-Ad5.
3. Reprendre les captures à 390, 768 et 1280 px sur toutes les pages et les redonner à @design pour la validation des 10 critères (aucune validation sans preuve visuelle). Vérifier en particulier : accueil (encadré), modale sur iOS, FAQ ouverte la plus longue en 390 px, nav à 820 px, 404 (pied de page).
4. Lancer la procédure de test du repo avant tout push (`tests/test_qa_fixes.py`, dont les tests de templates et de polices locales) : les gabarits changent (classes, balises `<div class="table-scroll">`, `<fieldset>`).

## Handoff

- Destinataire : @fullstack (via orchestrateur). Fichier produit : `/home/user/Tempo/docs/audits/2026-09-30-audit-design-pages.md`.
- Décisions : pas de nouveau design system. 1 composant nouveau (`.data-table`), 6 corrections globales de CSS (liens, titres, boutons, pied de page, FAQ, modale), tout le reste réutilise les classes existantes (`.table-scroll`, `.history-seasons`, `.color-tag`, `.legal-section`, `.counter-card`, `.source-label`, `.form-checkbox`).
- Points d'attention : décisions fondateur intactes (lien Selectra, « 2 500 foyers » non touchés), aucun contenu SEO retiré (H1, textes, FAQ, JSON-LD, liens internes), `style.min.css` à resynchroniser, contrastes à confirmer par outil, mention Replit dans `legal.html` à changer à la bascule.

