# Audit UX des pages publiques : AVANT (prod Replit) contre APRÈS (Cloudflare)

Date : 2026-09-30. Auteur : @ux. Destinataire : @fullstack (application), @design (visuel, en parallèle).
Périmètre : accueil, calendrier (mois), calendrier saison, historique-previsions, tarif-tempo-edf, couleur-tempo-demain, methodologie, api-tempo, blog index, article de blog, alertes, a-propos, mentions-legales, 404, fenêtre d'inscription.
Ce document ne contient aucun code. Chaque correction est actionnable : fichier, élément, changement, justification.

---

## 0. Résumé : les P0 (à faire avant de valider la bascule)

| ID | Page | Correction en une ligne |
|---|---|---|
| ACC-01 | Accueil | Supprimer le bloc « today-answer » comme carte séparée (décision fondateur : pas de bloc aujourd'hui/demain hors du résumé 10 jours) et replier son texte SEO dans la carte du résumé. |
| ACC-02 | Accueil | Compacter l'en-tête (H1 + phrase 4,4 fois + barre « Dernière mise à jour ») pour que les 10 pastilles redeviennent visibles sans scroller (mobile : le titre du résumé passe de y≈225 px à y≈470 px). |
| HIS-01 | Historique | Retirer le bouton « Télécharger le CSV » du haut de page, le remplacer par un lien texte discret en bas (mesuré avec Umami). |
| HIS-02 | Historique | Mettre la réponse (« puis-je me fier aux prévisions ? ») avant le tableau : bloc « En bref » en tête, grille limitée à 14 jours par défaut, bilan avant la méthode. Aujourd'hui le bilan commence à ≈2450 px de profondeur. |
| HIS-03 | Historique | Rendre le bilan et la grille lisibles sur mobile (colonnes coupées sans indice de défilement, légende de 10 lignes). |
| GLO-01 | Global (404, pages `.legal-content`) | Les `.btn` sont soulignés et les `.btn-outline` s'affichent pleins : corriger le CSS (probable source du ressenti « pas forcément propres » du fondateur). |

Les P1 et P2 sont détaillés page par page. Tout est à corriger (les P2 ne sont pas optionnels : la priorité sert à ordonner).

---

## 1. Méthode et limites

- Lu : 14 captures desktop APRÈS (`apres_verif/`), captures AVANT desktop et mobile, captures mobile APRÈS (`apres/`), templates Jinja (`dashboard`, `calendrier`, `historique_previsions` + 2 partials, `blog_index`, `blog_article`, `404`, `_base`, `_footer`, `_subscribe_modal`). Non relus en code : `tarif_tempo`, `couleur_demain`, `methodologie`, `api_tempo`, `calendrier_saison`, `alertes`, `a_propos`, `legal` (audités sur captures desktop ; les sélecteurs à modifier sont à retrouver par le texte cité).
- Non vérifié faute d'outil : `static/css/style.css` (je n'ai pas pu chercher dedans). Quand un correctif CSS dépend d'un état actuel du CSS, c'est écrit « d'après la capture, à vérifier ».
- Les captures mobile APRÈS montrent « Chargement des prévisions… » et des « ? » sur les compteurs : c'est l'absence de données/JS au moment de la capture (artefact), pas un défaut audité. Les captures desktop `apres_verif` affichent les vraies données SSR.
- Persona de référence : abonné Tempo (ou futur abonné) qui veut la couleur d'aujourd'hui et de demain, voir venir les jours rouges, savoir combien il en reste, puis s'inscrire aux alertes. Les visiteurs « je découvre Tempo » (SEO) sont servis par la FAQ, le tarif et le blog, en dessous du pli.

## 2. Règles de hiérarchie appliquées (valent pour toutes les pages)

1. Le premier écran répond à l'intention de la page : une réponse, pas trois blocs d'introduction.
2. Ce que 1 visiteur sur 100 utilise (export CSV, JSON, méthode détaillée, légende exhaustive) va en bas, en lien texte, jamais en bouton d'en-tête.
3. Un seul CTA principal par écran de hauteur (alertes), placé après la preuve de valeur (les données), pas avant.
4. Une information = un endroit. Ce qui est répété (méthode, sources, API, tarifs) devient un résumé de 2 à 3 lignes avec lien vers la page de référence.
5. Le contenu SEO est déplacé ou rendu discret, jamais supprimé : H1 unique, FAQ issues de `site_facts`, JSON-LD, liens internes, textes mots-clés restent dans le DOM.

## 3. Ce que l'APRÈS apporte réellement (à conserver)

- Compteurs et tarifs alimentés par `site_facts` (une seule source), tarifs datés au 1er août 2026.
- Historique de prévisions transparent (prévision figée, jours ratés visibles) : différenciateur de confiance, à garder, mais en second plan.
- Pages `couleur-tempo-demain`, `tarif-tempo-edf`, `calendrier/AAAA-AAAA` : bon contenu SEO, réponses directes.
- Exemple de message d'alerte honnête sur `/alertes` (« couleurs fictives »), récap 7 jours cohérent avec la promesse.
- Calendrier : cases grisées « non publié » (jamais de bleu par défaut), navigation mois par liens HTML.

## 4. Décisions fondateur respectées (non modifiées)

Lien Selectra (FAQ « Combien coûte réellement un jour rouge ? ») et « Plus de 2 500 foyers alertés » : non touchés. Aucune correction ci-dessous ne les modifie, déplace ou signale.

---

## 5. Corrections transverses (GLO)

- **GLO-01 (P0)** `static/css/style.css` (+ régénérer `style.min.css`), règles `.legal-content a`, `.btn`, `.btn-outline`. D'après la capture 404 : tous les boutons apparaissent pleins bleus avec un texte blanc souligné (sauf le bouton « Alertes gratuites », non souligné). Attendu : `.legal-content a.btn` sans soulignement, couleur de texte propre au bouton, y compris au survol et au focus ; `.btn-outline` réellement en contour (fond blanc, bordure et texte bleus). Vérifier aussi les `.btn` dans `historique_previsions`, `methodologie`, `tarif_tempo`. Justification : défaut visuel le plus visible des pages sans en-tête riche (la 404 de l'AVANT avait déjà des boutons soulignés, donc à corriger aussi, pas seulement à cause de la migration).
- **GLO-02 (P1)** créer `templates/_header.html` (logo + nav + CTA + bouton hamburger) et l'inclure dans `_base.html`, `dashboard.html`, `calendrier.html`, `blog_index.html`, `blog_article.html` (l'en-tête est aujourd'hui copié 5 fois). Paramètre `nav_active` : accueil, calendrier, tarifs, blog. Corriger au passage `calendrier.html` (classe `active` posée en dur, y compris sur les pages de mois) et `blog_*` (pas de `aria-current`). Supprimer les 2 copies du script hamburger dans `blog_index.html` et `blog_article.html` si `app.min.js` couvre déjà le cas (sinon le mettre dans un seul endroit). Justification : sans cela, chaque changement de navigation (GLO-03) est à répéter 5 fois et diverge.
- **GLO-03 (P1)** navigation d'en-tête : passer de 5 liens + CTA à 4 liens + CTA : Accueil, Calendrier, Tarifs, Blog, « Alertes gratuites ». Retirer « Historique » de l'en-tête (il reste dans le pied de page, et il est relié depuis la FAQ « Vos prévisions sont-elles fiables ? », depuis la section prévisions de `/calendrier` et depuis `/methodologie`). L'AVANT avait 3 liens + CTA ; on garde « Tarifs » parce qu'il porte le mot-clé « tarif tempo edf ». Justification : l'historique est une page de preuve pour les indécis, pas une destination quotidienne. À confirmer par Thomas (section 24).
- **GLO-04 (P1)** `templates/_footer.html` : les 12 liens en un seul flux qui se replie sur deux lignes, dont `llms.txt` (sans sens pour un visiteur), donnent un pied de page « soupe ». Passer à 3 groupes de liens avec titre : « Suivre les couleurs » (Accueil, Couleur Tempo demain, Calendrier Tempo, Alertes), « Comprendre » (Tarif Tempo EDF, Blog, Méthodologie, Historique des prévisions), « Le site » (À propos, API Tempo, Mentions légales). 3 colonnes desktop (grille), 2 colonnes mobile puis 1 colonne sous 380 px. Déplacer `llms.txt` en petit lien gris dans la ligne `.footer-legal`. Les 12 liens restent présents (maillage interne conservé). Justification : lisibilité, et l'AVANT avait 6 liens sobres.
- **GLO-05 (P1)** `style.css`, pages `.legal-content` (tarif, méthodologie, api, saison, historique, à propos) : le H1 est collé au premier paragraphe (captures tarif, méthodologie, API) et, quand il passe sur 2 lignes, l'interligne est trop grand. Attendu : `h1 { line-height: 1.2; margin-bottom: 12px }`, marge haute nulle sur le paragraphe qui suit. Justification : propreté (« pas forcément propres »).
- **GLO-06 (P1)** règle CTA : au plus un CTA principal « S'inscrire » visible par hauteur d'écran. Concerne l'accueil (ACC-04), l'article (déjà 1), l'alerte. Les CTA secondaires sont des liens texte.
- **GLO-07 (P2)** le SVG WhatsApp (chemin de plusieurs centaines de caractères) est recopié dans 8 templates : créer `templates/_icon_whatsapp.html` (ou un `<symbol>` SVG unique dans `_base`) et l'inclure. Justification : poids HTML, cohérence.
- **GLO-08 (P2)** versions d'assets `?v=20260929b` et `?v=20260929` en dur dans chaque template : les passer dans une variable Jinja globale unique (`asset_v`). Justification : un seul endroit à changer quand `style.min.css` / `app.min.js` sont régénérés (les fichiers minifiés doivent rester synchronisés avec les sources).
- **GLO-09 (P2)** styles en ligne récurrents (`style="margin-top:.75rem;width:100%"`, `style="margin: 32px 0"`, 404, modal) : les passer en classes utilitaires. Justification : maintenabilité, et permet à @design d'ajuster sans toucher au HTML.
- **GLO-10 (P2)** contraste : `.cal-day.future { opacity: .6 }` et les textes gris secondaires sur fond clair. Vérifier le ratio ≥ 4,5:1 (WCAG 2.2 AA) ; remplacer l'opacité par italique + bordure pointillée si besoin.

---

## 6. Accueil (`templates/dashboard.html`)

Intention : connaître la couleur d'aujourd'hui et de demain, voir les jours rouges à venir, savoir combien il en reste, s'inscrire.
AVANT : phrase « Un jour rouge coûte 5x plus cher », résumé 10 jours, 3 étapes, compteurs, CTA WhatsApp, FAQ. Le titre du résumé était à y≈150 px (desktop) et y≈225 px (mobile).
APRÈS : H1, carte « Couleur Tempo EDF du mercredi… », phrase 4,4 fois, barre « Dernière mise à jour », résumé 10 jours, 3 étapes, compteurs, CTA, FAQ de 12 questions. Le titre du résumé est à y≈395 px (desktop) et y≈470 px (mobile).

- **ACC-01 (P0)** `dashboard.html` lignes 180 à 199, `<section class="today-answer">`. Supprimer la carte comme bloc séparé. Réintégrer sa phrase (couleur du jour, couleur de demain, statut officielle ou prévision) dans `.week-summary-card`, sous les pastilles et au-dessus du bouton, en `<p class="week-summary-answer">` (texte 0,9 rem, couleur secondaire, sans bordure ni ombre). Le contenu (`<time>`, mots « Couleur Tempo EDF du… », « couleur officielle EDF ») reste dans le DOM SSR. Les deux liens « Couleur Tempo de demain · Calendrier de la saison » passent sur la même ligne que le bouton « Voir les prévisions détaillées… » (à sa droite sur desktop, dessous sur mobile). Justification : décision fondateur inscrite dans `CLAUDE.md` (« Do NOT add a separate today/tomorrow block above the 10-day summary »), et redondance avec les pastilles « Aujourd'hui Confirmé / Demain Confirmé ».
- **ACC-02 (P0)** `dashboard.html` lignes 178 à 211. Réduire l'en-tête à : H1 sur une ligne (desktop 1,5 rem, mobile 1,25 rem, max 2 lignes) ; la phrase « Un jour rouge coûte {{ facts.RATIO }} fois plus cher… » devient le sous-titre directement sous le H1 (texte, sans carte ni fond) ; la barre pleine largeur `#last-update-bar` devient un petit texte (0,8 rem) aligné à droite de la ligne du H2 « Résumé des 10 prochains jours ». Conserver les `id` (`welcome-banner`, `last-update-bar`, `last-update-text`) car `app.js` les cible, et le `<time datetime>`. Critère de réussite : à 390 × 844 px la première rangée de pastilles est entièrement visible sans scroller ; à 1366 × 768 les deux rangées le sont. Justification : le résumé 10 jours est la réponse à l'intention, il ne doit pas descendre de 245 px par rapport à l'AVANT.
- **ACC-03 (P1)** ordre des sections : résumé 10 jours, puis « Où en est la saison Tempo ? » (compteurs), puis CTA « Anticipez les 7 prochains jours », puis « Comment économiser… » (3 étapes), puis FAQ. Déplacer le bloc lignes 269 à 317 avant le bloc 246 à 267 (le compteur est plus actionnable pour l'abonné que l'explication générale ; le CTA arrive juste après la preuve). Les ancres `#comment-ca-marche` et `#subscribe` et le JSON-LD HowTo ne changent pas. Justification : « Combien de jours rouges reste-t-il ? » est la 2e question du persona.
- **ACC-04 (P1)** ligne 259 : retirer le bouton « Recevoir les alertes gratuites » de la carte d'étape 2 (il double le CTA du bandeau et celui de l'en-tête). Justification : GLO-06.
- **ACC-05 (P1)** chiffres en dur contraires à la règle « site_facts source unique » : lignes 272 à 273 (« 22 jours rouges », « 43 blancs ») et lignes 282, 288, 294 (« HP 0,73 », « 0,19 », « 0,17 €/kWh »). Utiliser `facts.JOURS_ROUGES`, `facts.JOURS_BLANCS` et `facts.TARIFS.*.hp` avec le même formatage que `calendrier.html` (`|string|replace('.', ',')`). Justification : l'accueil affiche « 0,73 » alors que le calendrier et le tarif affichent « 0,7295 » : incohérence visible.
- **ACC-06 (P1)** lignes 278, 285, 291 : sans données SSR les compteurs affichent « ? ». Afficher « - » (tiret court) avec `aria-label` « en cours de chargement » et garder la hauteur (pas de saut de mise en page) ; l'AVANT affichait déjà un tiret. Justification : « ? » ressemble à une erreur.
- **ACC-07 (P2)** pastilles du résumé : les pourcentages sous chaque jour bleu (99 %, 100 %, 99 %…) sont du bruit. N'afficher le pourcentage que pour un jour non confirmé dont la couleur n'est pas BLEU, ou dont la probabilité est inférieure à 90 %. Le texte `sr-only` garde la valeur. Justification : l'œil doit être attiré par les jours rouges et blancs.
- **ACC-08 (P2)** la FAQ « Vos prévisions sont-elles fiables ? » contient « chargement… » tant que l'appel n'a pas abouti (lignes 340 à 341). Prévoir un repli texte permanent avec lien vers `/historique-previsions` si l'appel échoue. Justification : jamais de texte de chargement figé.
- **ACC-09 (P2)** `id="subscribe"` existe deux fois sur l'accueil (ligne 302 et `<div id="subscribe">` en tête de `_subscribe_modal.html`). Retirer le doublon du partial (l'ancre `/#subscribe` obligatoire dans les articles pointe déjà vers la section). Justification : HTML valide.
- **ACC-10 (P2)** titre H2 « Résumé des 10 prochains jours » alors que le H1 et le bouton parlent de 15 jours : renommer le H2 « Les 10 prochains jours » (ou aligner le résumé sur 15 si @design le permet). Justification : cohérence des promesses.

---

## 7. Calendrier, mois en cours (`templates/calendrier.html`)

Intention : voir la couleur d'un jour précis, voir les prévisions détaillées à venir, repérer les dates rouges.
AVANT : compteurs, navigation mois, légende, grille, prévisions 15 jours, CTA, texte SEO (la grille finit à y≈840 px en équivalent desktop, les prévisions commencent vers y≈1210 px).
APRÈS : identique, avec en plus un paragraphe « Jours rouges officiels en septembre 2026 : aucun… » et une phrase de source entre la grille et les prévisions, des liens de saisons, une FAQ de 6 questions. Les prévisions commencent vers y≈1300 px.

- **CAL-01 (P1)** lignes 222 à 235, `<section class="cal-month-summary">` : déplacer le bloc sous le conteneur `#forecast-container` (et sous le CTA), en 0,9 rem. Sortir la phrase « Couleurs officielles : {{ facts.SOURCE_COULEURS_PHRASE }}… » vers une note de bas de légende (0,8 rem). Justification : entre la grille et les prévisions, « Jours rouges officiels : aucun. Jours blancs officiels : aucun. » ne sert à personne et repousse la valeur de 200 px.
- **CAL-02 (P1)** lignes 243 à 264 : le paragraphe d'intro des prévisions (3 lignes) + la légende à 3 lignes avec prix (`.legend`) précèdent les cartes. Réduire à : une phrase (« Plus le jour est proche, plus la prévision est fiable. ») + « Recalculées {{ facts.CALCUL_PREVISIONS }} » en petit ; légende en une seule ligne de 3 pastilles (Bleu, Blanc, Rouge, avec prix HP) qui passe à la ligne sur mobile ; le conseil « Reportez lessive, sèche-linge, four, recharge VE » reste attaché à la pastille ROUGE. Note tarifs + lien « grille complète » en fin de ligne. Justification : sur mobile ce bloc occupe ≈ 350 px avant la première carte.
- **CAL-03 (P1)** lignes 104 à 108 (media ≤ 600 px) : `.cal-stats { grid-template-columns: 1fr }` empile trois cartes (≈ 250 px) avant la grille. Garder 3 colonnes sur mobile (nombre 1,4 rem, libellé 0,75 rem, padding 10 px). Justification : la grille doit apparaître dans le premier écran mobile.
- **CAL-04 (P1)** ajouter, dans la section prévisions, une ligne lien texte « Nos prévisions sont-elles fiables ? Voir l'historique » vers `/historique-previsions` (suite au retrait de l'entrée d'en-tête, GLO-03). Justification : garder l'accès à la preuve au bon moment.
- **CAL-05 (P2)** l'état « chargement » de `#forecast-container` (loader + texte) doit réserver la hauteur des cartes (min-height ≈ 320 px desktop, 220 px mobile) pour éviter le saut quand les cartes arrivent. Ajouter, si l'appel échoue, un message avec bouton « Réessayer » (jamais une zone vide).
- **CAL-06 (P2)** ordre bas de page : CTA, saisons, FAQ, texte « Comprendre… ». Garder, mais placer le bloc « Dates des jours rouges et blancs par saison » après la FAQ (moins utile que la FAQ) et remplacer `style="margin: 32px 0"` du CTA par une classe.
- **CAL-07 (P2)** case du jour : `box-shadow` bleu conservé, ajouter une étiquette `sr-only` « aujourd'hui » (déjà couvert par `day.label` ? vérifier que le libellé le mentionne).

---

## 8. Calendrier, saison (`templates/calendrier_saison.html`)

Intention : lister les dates rouges et blanches d'une saison (souvent une saison passée, via recherche).
Constat : bon contenu, mais bruyant en saison en cours : 10 lignes « données non disponibles », deux sections vides, un paragraphe d'ouverture trop technique.

- **SAI-01 (P1)** H1 « Jours rouges et blancs Tempo EDF : saison 2026-2027 » se coupe entre « 2026- » et « 2027 » : envelopper « 2026-2027 » dans `<span style="white-space:nowrap">` (trait d'union normal, pour ne pas changer le texte indexé) ; interligne du H1 à 1,2.
- **SAI-02 (P1)** tableau « Répartition par mois » : pour une saison en cours, ne garder en lignes de tableau que les mois avec données ; regrouper les mois futurs dans une ligne « À venir : Novembre 2026, Décembre 2026, … » où chaque mois reste un lien HTML (maillage vers `/calendrier/AAAA-MM` conservé), en gris. Saison terminée : tableau complet inchangé. Justification : 10 lignes « données non disponibles » donnent l'impression d'un site vide.
- **SAI-03 (P1)** deuxième paragraphe (« 0 jours rouges, 0 jours blancs et 31 jours bleus sur les 31 jours disponibles dans notre base… sur 365 jours de saison ») : le remplacer par une phrase de visiteur construite avec les mêmes variables : « Au {date du dernier jour publié} : {r} jour(s) rouge(s) et {b} jour(s) blanc(s) publiés par EDF, sur 22 et 43 par saison. La liste s'allonge à chaque publication d'EDF. Nos prévisions sont sur le calendrier du mois. » (lien conservé). Aucun chiffre nouveau : uniquement des valeurs déjà calculées.
- **SAI-04 (P1)** quand une saison n'a aucun jour rouge ou blanc publié, remplacer les deux H2 vides (« Dates des jours rouges… », « Dates des jours blancs… ») par un seul H2 « Dates des jours rouges et blancs » avec une seule phrase (« Aucun jour rouge ni blanc publié pour l'instant ; les jours rouges sont possibles du 1er novembre au 31 mars. »). Saison avec données : deux H2 comme aujourd'hui.
- **SAI-05 (P2)** liens de la colonne « Mois » : gras bleu souligné lourd. Passer en graisse normale, couleur lien, soulignement conservé. Colonnes chiffrées alignées à droite (déjà le cas).
- **SAI-06 (P2)** ajouter en fin de page, pour la saison en cours seulement, le bloc CTA `.blog-cta` déjà utilisé sur `/calendrier`. La page n'a aujourd'hui aucun chemin vers l'inscription.

---

## 9. Historique des prévisions (`templates/historique_previsions.html`, `_historique_grille.html`, `_historique_bilan.html`)

Intention réelle : « Puis-je me fier à ces prévisions avant de m'inscrire ? » (le visiteur qui va jusqu'ici est déjà à moitié convaincu, il cherche la preuve). Le téléchargement de données concerne 1 visiteur sur 100 (remarque du fondateur).
Constat desktop : ordre actuel = H1, paragraphe, sélecteur de saison, gros bouton bleu « Télécharger le CSV » (le seul CTA visible), légende de 6 items, grille 30 lignes × 15 colonnes (≈ 1800 px), bilan de 7 colonnes (commence à ≈ 2450 px), méthode. La réponse à la question est donc à 2450 px et le seul bouton mis en avant sert à autre chose.

- **HIS-01 (P0)** `historique_previsions.html` lignes 55 à 58 (`<p class="history-download">`) : supprimer le bouton du haut. Ajouter, à la fin de la section `#methode` (après la ligne « Voir aussi »), un paragraphe discret : « Données brutes : <a href="/historique-previsions.csv" download data-umami-event="csv_download">télécharger l'historique (CSV, séparateur point-virgule, compatible Excel)</a>. Autres formats : <a href="/api-tempo">API Tempo</a>. » sans classe `.btn`. La route `/historique-previsions.csv`, le `distribution` du JSON-LD Dataset et la mention CSV de `/methodologie` restent. Méta-description (ligne 19) : retirer « Export CSV » et « Export CSV gratuit » (le CSV n'est plus l'argument de la page). Justification : demande explicite du fondateur, et l'événement Umami permet de vérifier l'usage réel.
- **HIS-02 (P0)** réordonner la page : (1) H1 + intro raccourcie à 2 phrases ; (2) sélecteur de saison ; (3) NOUVEAU bloc « En bref » alimenté par `h.fiables` (zone 2 à 5 jours avant) : « Jours rouges annoncés à l'avance : X sur Y », « Alertes rouges qui étaient justes : A sur B », même chose pour le blanc, et une ligne de repère « Dire « bleu » tous les jours aurait eu raison {n} fois sur {m} » ; sous le seuil `pct_min` ne pas afficher de pourcentage (réutiliser la macro `ratio`) et écrire « pas encore assez de jours rouges cette saison » ; (4) grille jour par jour, 14 lignes visibles par défaut ; (5) bilan par délai ; (6) méthode et lien CSV. Les lignes au-delà de 14 portent la classe `hg-more` (masquée en CSS) avec un bouton « Afficher toute la saison ({{ h.nb_days }} jours) » qui bascule la classe ; `<noscript><style>.hg-more{display:table-row!important}</style></noscript>` pour garder tout le contenu sans JavaScript. Toutes les lignes restent dans le DOM (SEO et accessibilité). Justification : la réponse passe de 2450 px à moins de 700 px et la grille reste la preuve consultable.
- **HIS-03 (P0)** mobile (≤ 600 px). Bilan : le tableau à 7 colonnes est coupé à droite sans indice (capture : colonne « Alertes blanches justes » tronquée). Attendu : première colonne (« Délai ») collante, ombre dégradée sur le bord droit du conteneur `.table-scroll`, et le texte `.table-scroll-hint` (aujourd'hui `aria-hidden` et présent seulement pour la grille) affiché au-dessus du bilan aussi, visible sur mobile. Grille : légende `.history-legend` de 6 items (≈ 10 lignes sur mobile) réduite à 2 lignes : garder « juste / erronée / pas de prévision » et déplacer « service pas encore lancé » et « zone la plus fiable » dans une note repliée `<details><summary>Légende complète</summary>`. La grille reste défilée vers la droite au chargement (J-1 et couleur officielle d'abord), comportement à conserver.
- **HIS-04 (P1)** vocabulaire des en-têtes du bilan (persona = particulier, pas statisticien) : « Jours rouges annoncés » devient « Rouges prévus à l'avance », « Alertes rouges justes » devient « Alertes rouges qui étaient justes », « Prévisions justes » reste, « « Toujours bleu » juste » devient « Repère : dire toujours bleu ». Même logique pour blanc. Les libellés de la macro `ratio` (« aucun », « aucune ») restent.
- **HIS-05 (P1)** tant que la saison n'a ni jour rouge ni jour blanc (état actuel, jusqu'en novembre), les 4 colonnes rouge/blanc du bilan affichent uniquement « aucun/aucune » (24 cellules vides). Masquer ces 4 colonnes et afficher à la place, au-dessus du tableau, « Aucun jour rouge ou blanc depuis le 1er septembre : ces colonnes s'afficheront dès le premier jour concerné. » ; le tableau garde Délai, Prévisions justes, Repère. Justification : évite un tableau vide et rend le mobile lisible sans défilement.
- **HIS-06 (P1)** paragraphes d'introduction de la grille (5 lignes) et du bilan (5 lignes) : réduire à 2 phrases chacun. Conserver l'idée honnête « pas de taux de réussite global » en une phrase : « Pas de taux global : environ {{ facts.JOURS_BLEUS }} jours sur 365 sont bleus, dire « bleu » tous les jours aurait souvent raison ; la dernière colonne le montre. » Justification : minimalisme, la preuve est dans les chiffres.
- **HIS-07 (P1)** supprimer le lien d'en-tête (GLO-03) et ajouter le lien depuis la FAQ fiabilité (accueil, `faq-fiabilite`) et depuis `/methodologie` (déjà présent).
- **HIS-08 (P2)** section « Méthode » : 5 puces de 2 à 3 lignes. Les mettre dans un `<details>` fermé par défaut (résumé « Comment nous mesurons »), placé avant la ligne de téléchargement. Le contenu reste dans le DOM et indexable.
- **HIS-09 (P2)** pages de saisons passées (`is_current` faux) : le bloc « En bref » est calculé sur la saison affichée ; le titre `<title>` et le H1 restent inchangés.

---

## 10. Tarif Tempo EDF (`templates/tarif_tempo.html`)

Intention : connaître le prix du kWh par couleur, comprendre ce que coûte un jour rouge. Page absente de l'AVANT ; utile et bien datée (arrêté du 29 juillet 2026).

- **TAR-01 (P1)** tableau « Grille tarifaire » : il ne fait que 640 px et se replie mal (« Heures pleines (6 h à 22 h) » sur 2 lignes, « Jour bleu (300 jours/saison) » sur 2 lignes). Attendu : tableau à la largeur du contenu ; en-têtes « Heures pleines » et « Heures creuses » avec la plage horaire en `<small>` dessous ; première colonne avec la pastille `.color-tag` (Bleu, Blanc, Rouge, mêmes composants que l'historique) et « (300 jours/saison) » en `<small>` gris. Nombres alignés à droite (déjà le cas). Justification : la table est le contenu principal de la page.
- **TAR-02 (P1)** fin de page : le seul appel aux alertes est une phrase dans le dernier paragraphe. Après la section « Anticiper les jours rouges » (texte conservé pour les liens internes), ajouter le bloc `.blog-cta` utilisé ailleurs (`Anticipez les {{ facts.ALERTES_HORIZON_JOURS }} prochains jours Tempo`). Justification : GLO-06, la page a une intention forte (coût du jour rouge) et aucun chemin visuel vers l'inscription.
- **TAR-03 (P2)** section « Et l'abonnement ? » (un H2 pour dire qu'on ne donne pas l'information) : la transformer en note en petit sous la source du tableau (« L'abonnement dépend de la puissance souscrite : voir votre contrat ou la grille EDF. »). Ne pas inventer de montants. Justification : un H2 sans réponse déçoit le visiteur qui l'ouvre.
- **TAR-04 (P2)** ajouter le lien « Voir la couleur d'aujourd'hui et de demain » en haut de page sous l'intro (retour vers l'intention principale du site).

---

## 11. Couleur Tempo demain (`templates/couleur_demain.html`)

Intention : « Quelle couleur demain, et après ? ». L'encadré de réponse en haut est bon.

- **DEM-01 (P1)** ordre : encadré de réponse, puis tableau « Les prochains jours », puis les sections d'explication (« À quelle heure EDF publie-t-il… », « Quelles couleurs sont possibles demain ? »). Aujourd'hui 2 sections de texte (≈ 450 px) séparent la réponse du tableau qu'on vient chercher. Les deux sections restent, plus bas, avec leurs H2.
- **DEM-02 (P1)** tableau : la colonne « Couleur » est du texte brut (« Bleu »). Utiliser la pastille `.color-tag` comme sur l'historique ; la colonne « Statut » devient « Officielle EDF » ou « Prévision, 99 % » (avec le texte, pas seulement la couleur). Les lignes officielles en gras, les prévisions en texte normal.
- **DEM-03 (P1)** après le tableau, ajouter le bloc `.blog-cta` (inscription alertes) ; conserver les liens texte actuels (calendrier, API Tempo). Justification : c'est la page où l'on comprend qu'on n'a pas envie de revenir chaque jour, l'inscription est la suite logique.
- **DEM-04 (P2)** avant 11 h (couleur de demain pas encore publiée) : l'encadré doit annoncer la prévision avec l'étiquette « prévision, EDF ne l'a pas encore publiée » et l'heure habituelle de publication ; vérifier ce rendu avec une capture réalisée avant 11 h.

---

## 12. Méthodologie (`templates/methodologie.html`)

Intention : vérifier le sérieux avant de s'inscrire. Page longue (≈ 2750 px), propre, tableau des poids clair.

- **MET-01 (P1)** section 5, « En conditions réelles : 100,0 % de prévisions correctes sur les 101 prévisions de J+2 à J+5… » : ce chiffre isolé se lit comme une promesse, alors que l'historique explique qu'un outil qui dit « bleu » chaque jour aurait presque 100 % en septembre. Sous la phrase, ajouter deux lignes issues des mêmes données que `/historique-previsions` : « dont jours rouges ou blancs annoncés : X sur Y » (ou « aucun jour rouge ou blanc pour l'instant ») et « Repère : dire toujours bleu aurait eu Z % », avec lien « Voir le détail » vers `#bilan`. Aucune valeur nouvelle, seulement `h.fiables`. Justification : cohérence avec `PERFORMANCE_POLICY` et avec la page historique.
- **MET-02 (P1)** ajouter, juste après l'intro, un sommaire d'une ligne (`<nav class="page-toc">` : Données, Score, Apprentissage, Règles EDF, Performance, Limites) avec ancres vers des `id` sur les H2 numérotés. Justification : page de 6 sections, accès direct à « Performance » et « Limites » qui sont les plus consultées.
- **MET-03 (P2)** tableau des poids : largeur 100 % du contenu (aujourd'hui ≈ 470 px, cadré à gauche). 
- **MET-04 (P2)** ajouter en fin de page le bloc `.blog-cta`, plus le lien texte vers `/historique-previsions` (remplace la mention « un export CSV » par « les données brutes en CSV » avec lien).

---

## 13. API Tempo (`templates/api_tempo.html`)

Intention : développeurs et utilisateurs Home Assistant, très faible audience. Page conforme : accessible depuis le pied de page uniquement, ce qui est le bon niveau.

- **API-01 (P2)** blocs de code : ajouter un bouton « Copier » (texte, pas icône seule) et, pour `curl /api/today`, un exemple avec une vraie réponse récupérée à l'affichage (pas de valeurs inventées) à la place de `"AAAA-MM-JJ"`. 
- **API-02 (P2)** tableau des endpoints : rendre chaque `GET /api/...` cliquable vers l'URL réelle (nouvel onglet) pour tester en un clic.
- **API-03 (P2)** H1 collé au paragraphe : couvert par GLO-05.

---

## 14. Blog, index (`templates/blog_index.html`)

Intention : trouver un guide utile (économiser, matériel, calendrier). 22 articles en liste plate (≈ 5700 px).

- **BLG-01 (P1)** lignes 98 à 102 : sur presque toutes les cartes s'affiche « mis à jour le 29/09/2026 » en plus de la date de publication : du bruit de mise à jour en masse. N'afficher qu'une seule date par carte : « Mis à jour le … » si `updated_date` existe, sinon « Publié le … ». Le `<time datetime>` reste, les dates dans l'article et le JSON-LD (`dateModified`) ne changent pas. 
- **BLG-02 (P1)** structure : ajouter en tête un bloc « Les guides essentiels » de 3 cartes plus grandes (grille 3 colonnes desktop, 1 colonne mobile) pour les 3 articles piliers (titres actuels : « Tempo EDF 2026-2027 : le guide complet pour les abonnés », « Économiser avec Tempo EDF en 2026-2027 : guide et astuces », « Calendrier Tempo 2026-2027 : dates, règles et couleurs »). Marquer les piliers dans le front-matter (`pillar: true`) plutôt que par une liste de slugs en dur. Puis lister le reste groupé par cluster (`cluster` existe déjà dans `Article`) avec un H2 par cluster (libellés proposés : « Comprendre Tempo », « Jours rouges », « Calendrier et saisons », « Équipements », « Préparer la saison » ; à valider par Thomas) ; les titres de cartes passent en H3. Tout reste en HTML statique, aucun filtre JavaScript nécessaire. Justification : 22 titres alignés sans repère ne permettent pas de choisir.
- **BLG-03 (P2)** description de carte limitée à 2 lignes (`line-clamp`), meta sur une seule ligne (date, temps de lecture).
- **BLG-04 (P2)** phrase d'intro : conserver « De nouveaux articles paraissent régulièrement » (l'ancienne promesse « chaque mardi » a été retirée à juste titre, ne pas la remettre tant que la cadence saisonnière n'est pas hebdomadaire).

---

## 15. Blog, article (`templates/blog_article.html`)

Intention : lire un guide et, à la fin, s'inscrire. Articles de 6000 à 8000 px de haut (l'article « alerte jour rouge » en fait ≈ 8100 sur la capture). Blocs actuels : H1, meta, corps, CTA en fin d'article, « À lire aussi », rangée de 5 liens.

- **ART-01 (P1)** sommaire cliquable sous la meta pour les articles de plus de 1200 mots : liste des H2 générée depuis `content_html`, repliée (`<details>`) sur mobile, ouverte sur desktop. Justification : navigation dans un long texte, sans modifier le contenu SEO.
- **ART-02 (P1)** tableaux du corps de l'article (ex. comparaison des services d'alerte, 4 colonnes) : les envelopper à la génération du HTML (`blog.py`) dans `<div class="table-scroll" tabindex="0">` pour éviter le débordement horizontal sur mobile. À vérifier sur les 3 devices avant de clore.
- **ART-03 (P2)** le commentaire `<!-- CTA mid-article -->` (ligne 111) décrit un CTA au milieu alors qu'il est en fin d'article : garder en fin (parcours « conviction avant conversion »), corriger le commentaire.
- **ART-04 (P2)** la rangée `.blog-nav-articles` (ligne 130, style en ligne) répète le pied de page : garder « Retour au blog » et « Calendrier », retirer « Alertes gratuites » (CTA juste au-dessus) et passer en classe CSS.

---

## 16. Alertes (`templates/alertes.html`)

Intention : décider de s'inscrire (page d'action, le CTA en héros est légitime). L'APRÈS est meilleur que l'AVANT sur le texte (récap 7 jours, exemple « couleurs fictives »).

- **ALE-01 (P1)** doublon : 3 cartes à icônes (« Anticiper les 7 prochains jours », « Via WhatsApp », « Gratuit et sécurisé ») répètent ensuite « Comment ça marche ? » (3 étapes) et le héros. Garder la rangée de réassurance en une ligne sous le CTA du héros (« Gratuit · Sans application · Désinscription en 1 clic »), supprimer les 3 cartes (leur contenu est dans le héros et les étapes), et remonter l'exemple de message juste sous le héros. Résultat : preuve (exemple) avant les explications, page raccourcie d'environ 250 px.
- **ALE-02 (P1)** exemple de message : la page dit « couleurs fictives » avec des jours sans dates ; la fenêtre d'inscription montre le même message avec de vraies dates mais sans cette mention. Aligner : même libellé « (exemple, couleurs fictives) » dans `_subscribe_modal.html` (`.sms-preview-label`).
- **ALE-03 (P2)** icônes emoji (⚠️, 🔒) au rendu variable selon l'appareil : @design remplace par des icônes SVG du site.
- **ALE-04 (P2)** ajouter dans la section « Comment ça marche ? » un lien texte « Détail complet dans notre guide » vers l'article de blog sur les alertes. Justification : maillage utile, pas de doublon de contenu.

---

## 17. À propos (`templates/a_propos.html`)

Intention : savoir qui est derrière et pourquoi lui faire confiance. 7 cartes dont 4 recopient `/methodologie` (score, apprentissage, sources, précision, limites) et 1 recopie `/api-tempo`.

- **APR-01 (P1)** remplacer les cartes « Notre méthodologie de prévision », « Sources de données », « Précision et fiabilité » par une seule carte « Comment nous prévoyons » (4 à 5 lignes : météo de 9 villes, consommation RTE, règles EDF, priorité à ne pas manquer les jours rouges) avec deux liens en évidence : « Méthodologie détaillée » et « Historique de nos prévisions ». Garder « Transparence et limites » (4 puces courtes, bon signal de confiance). La carte « API publique » passe à 2 lignes + lien « Documentation de l'API Tempo ». Justification : une seule version de la méthode (la version de `/methodologie` est déjà plus à jour, par exemple sur le parc nucléaire) ; le SEO de fond reste sur `/methodologie`, les mots-clés de `/a-propos` (« clients EDF au tarif Tempo », « anticiper les jours rouges ») restent dans « Qui sommes-nous ? ».
- **APR-02 (P2)** ajouter une ligne d'identité dans « Qui sommes-nous ? » : « Service édité par ISSA Capital (voir les mentions légales) », reprise des mentions légales déjà publiées. À valider par Thomas (section 24).
- **APR-03 (P2)** adresse de contact `jean-pierre@calendrier-tempo.fr` (aussi dans les mentions légales) : un prénom-persona dans une adresse publique est inhabituel ; vérifier avec Thomas qu'il s'agit bien de l'adresse voulue avant bascule. Aucune modification sans sa réponse.

---

## 18. Mentions légales et RGPD (`templates/legal.html`)

Intention : trouver une information précise (désinscription, durée de conservation, contact DPO). Page de ≈ 6200 px, sans sommaire.

- **LEG-01 (P1)** ajouter en tête un sommaire cliquable (`<nav class="page-toc">`) des 9 sections avec ancres sur les H2, dont « Se désinscrire » (section 9) mis en premier lien. Justification : le visiteur qui veut se désinscrire ne doit pas parcourir 6000 px. Ne rien replier : le contenu légal reste entièrement visible.
- **LEG-02 (P2)** ajouter le fil d'Ariane (« Accueil › Mentions légales ») comme sur les autres pages, et un lien « Retour en haut » flottant sur mobile (le bouton `back-to-top` existe sur l'accueil).
- **LEG-03 (P2)** titres de niveau 3 (`4.1` à `4.8`) : numérotation visible et espacement plus grand avant chaque H3 pour séparer les blocs (aujourd'hui le texte est dense, capture illisible en aperçu).

---

## 19. 404 (`templates/404.html`)

Intention : rebondir vers la bonne page en un clic. Contenu OK (H1, 6 destinations, `noindex`).

- **404-01 (P0)** couvert par GLO-01 (boutons soulignés).
- **404-02 (P1)** les 6 boutons se répartissent sur 2 lignes de largeurs inégales (3 puis 3, tailles différentes). Passer à une grille de 3 colonnes desktop, 2 colonnes mobile, boutons de même largeur ; « Couleur Tempo du jour » en bouton principal, les 5 autres en `.btn-outline`. Retirer les styles en ligne (`style="…"` lignes 7, 9, 10) au profit d'une classe `.notfound-links`.
- **404-03 (P2)** vérifier que la page renvoie bien le statut HTTP 404 (pas 200) avec `noindex, follow`, et que la page s'affiche correctement quand l'URL demandée est sous `/blog/` (chemin relatif des assets). Libellés : « Couleur Tempo du jour » mène à `/` : le renommer « Couleur du jour et de demain » pour décrire la page réelle.

---

## 20. Fenêtre d'inscription (`templates/_subscribe_modal.html`)

Conforme aux préférences fondateur : popup fermable (croix, clic dehors, Échap), en-tête et pied de page visibles derrière, champ unique, focus rendu à l'élément déclencheur. Corrections :

- **MOD-01 (P1)** focus trap (lignes 205 à 213) : `focusable = m.querySelectorAll('button, input, a, …')` inclut des éléments non visibles (formulaire « gérer mon abonnement » masqué, champ honeypot, champ caché `modal-ts`). Quand le formulaire de gestion est replié, le dernier « focusable » n'est pas atteignable et la touche Tab sort de la fenêtre. Filtrer : ne garder que les éléments visibles (`offsetParent !== null`), sans `tabindex="-1"` ni `type="hidden"`. Test : Tab depuis le dernier bouton visible revient à la croix.
- **MOD-02 (P1)** confirmation : la fenêtre se ferme seule après 4 s (ligne 287), alors que le message « Vérifiez votre WhatsApp ! » est l'étape suivante essentielle. Remplacer par un état de succès persistant : icône coche, message, phrase « Si rien n'arrive, vérifiez le numéro saisi » et bouton « Fermer ». Pas de fermeture automatique. Événement Umami `modal_submit` conservé.
- **MOD-03 (P1)** signal de confiance contradictoire : titre avec badge « En test » et mention « Nouvelle fonctionnalité : des perturbations ponctuelles sont possibles » au-dessus du champ, juste avant le message « Déjà plus de 2 500 foyers alertés ». Décision de Thomas : si l'envoi est stable, retirer le badge et la mention ; sinon les garder mais en note de bas de fenêtre (0,8 rem, sous le bouton). Ne pas toucher à la ligne « 2 500 foyers ».
- **MOD-04 (P1)** libellé de l'exemple : ajouter « couleurs fictives » (voir ALE-02).
- **MOD-05 (P2)** cases à cocher : cibles de 24 px de haut (libellés 0,85 rem) : porter à 44 px (padding vertical) ; sortir les styles en ligne vers des classes.
- **MOD-06 (P2)** mobile : la préférence fondateur est le bottom sheet (`align-items:flex-end`, `100dvh`, marge `safe-area`). Vérifier dans `style.css` (`.subscribe-modal-overlay`) que c'est bien le comportement livré, sur une capture réelle iOS Safari ; je n'ai pas pu lire ce fichier.
- **MOD-07 (P2)** message d'erreur numéro (« Numéro non reconnu. Formats acceptés : 06/07, +33, +32, +41, +352, +49 ») : correct ; ajouter le focus sur le champ après l'erreur.

---

## 21. Audit heuristique Nielsen (site public, APRÈS actuel) et parcours

| # | Heuristique | Verdict | Évidence |
|---|---|---|---|
| 1 | Visibilité de l'état | PASS partiel | Mise à jour datée, « Confirmé » sur les pastilles ; FAIL sur « ? » des compteurs et les états de chargement sans hauteur réservée (ACC-06, CAL-05). |
| 2 | Vocabulaire du persona | FAIL partiel | Bilan d'historique : « Alertes blanches justes », « Toujours bleu juste » (HIS-04). |
| 3 | Contrôle et annulation | FAIL | Fenêtre d'inscription qui se ferme seule après 4 s (MOD-02) ; focus qui sort du dialogue (MOD-01). |
| 4 | Cohérence | FAIL | 0,73 sur l'accueil contre 0,7295 ailleurs (ACC-05) ; boutons soulignés ou non selon la page (GLO-01) ; en-tête copié 5 fois (GLO-02). |
| 5 | Prévention des erreurs | PASS | Validation du numéro en direct, honeypot, message explicite. |
| 6 | Reconnaissance plutôt que rappel | PASS | Pastilles colorées avec texte, légendes ; à alléger sur l'historique (HIS-03). |
| 7 | Raccourcis experts | PASS | API, CSV, liens saison. Le CSV n'a pas à être un raccourci mis en avant (HIS-01). |
| 8 | Minimalisme | FAIL | Accueil : 3 blocs avant le résumé (ACC-01, 02) ; historique : grille de 1800 px avant la réponse (HIS-02) ; à propos : méthode recopiée (APR-01). |
| 9 | Messages d'erreur humains | PASS | Numéro non reconnu avec formats acceptés, lien de gestion déjà inscrit. Ajouter repli « Réessayer » aux zones de prévision (CAL-05). |
| 10 | Aide dans le flux | PASS partiel | Exemple de message dans la fenêtre ; manque la mention « fictives » (MOD-04). |

**Cognitive walkthrough, parcours A : « quelle couleur demain, et que reste-t-il ? » (accueil, mobile 390 px).** Sait-il quoi faire ? Oui, le résumé est la réponse. Action visible ? Non au premier écran (le titre du résumé est à y≈470 px) : `[FRICTION H8]` à l'étape 1, le first-time user voit trois blocs de texte avant les pastilles. Solution : ACC-01 et ACC-02. Feedback immédiat ? Oui (SSR). Compteurs restants : visibles mais sous les 3 étapes `[FRICTION H8]` ; solution : ACC-03.

**Parcours B : « puis-je me fier à ces prévisions ? » (historique).** Le seul bouton mis en avant est « Télécharger le CSV » : `[FRICTION H2]` le lien entre but (juger la fiabilité) et action visible est faux. La réponse est à 2450 px : `[FRICTION H8]`. Solutions : HIS-01, HIS-02, HIS-05. Sur mobile, colonnes du bilan coupées : `[FRICTION H1]` (HIS-03).

**Parcours C : inscription (fenêtre).** Ouverture depuis l'en-tête ou un CTA : OK. Saisie : OK. Confirmation : `[FRICTION H3]` fermeture automatique, le visiteur peut rater l'instruction « Vérifiez votre WhatsApp » (MOD-02).

## 22. Mesure (HEART) et événements Umami

| Flux | Dimension | Signal observable | Cible | Méthode |
|---|---|---|---|---|
| Accueil, couleur visible sans scroll | Task success | Part des visites avec scroll < 1 écran avant clic ou sortie (proxy) | [HYPOTHÈSE : à fixer après 2 semaines de mesure, aucune base actuelle] | Umami, événement `home_view` + profondeur |
| Inscription | Adoption | `modal_submit` / `modal_open` par page | [HYPOTHÈSE : ≥ 45 %, à valider avec @data-analyst sur données réelles] | Événements existants |
| Historique | Engagement | `csv_download` (nouveau), `history_expand_all` (nouveau) | Vérifier l'hypothèse fondateur « 1 visiteur sur 100 » | `data-umami-event` sur le lien CSV et le bouton « Afficher toute la saison » |
| Navigation | Happiness | Clics par lien d'en-tête avant/après GLO-03 | Pas de baisse des clics Calendrier et Tarifs | Umami, pages vues entrantes |

Événements à ajouter : `csv_download`, `history_expand_all`, `cta_alertes_click` avec propriété `page` (pour appliquer GLO-06). Seuils génériques (activation ≥ 60 %, parcours critiques ≥ 90 %, CSAT ≥ 8/10) non applicables sans enquête : aucun CSAT n'est mesuré aujourd'hui.

## 23. Ordre d'exécution pour @fullstack et garde-fous

Ordre : (1) GLO-01, GLO-05 (CSS de base) ; (2) GLO-02, GLO-03, GLO-04 (en-tête et pied partagés) ; (3) ACC-01 à ACC-06 ; (4) HIS-01 à HIS-06 ; (5) modal MOD-01 à MOD-04 ; (6) pages restantes par ordre P1 puis P2 ; (7) régénérer `style.min.css` et `app.min.js`, changer la version d'assets.

Garde-fous SEO et qualité (à vérifier avant de rendre la main) :
- un seul `<h1>` par page ; le H1 n'est jamais déplacé hors de `<main>` ;
- FAQ visibles et JSON-LD `FAQPage` toujours issus de la même liste (`site_facts`), test d'égalité inchangé ; JSON-LD (Dataset, HowTo, SoftwareApplication, BreadcrumbList) non modifiés ;
- aucun texte mot-clé supprimé : uniquement déplacé, replié dans `<details>` fermé (contenu dans le DOM) ou raccourci quand il est recopié ailleurs (APR-01) ;
- le lien Selectra et « Plus de 2 500 foyers alertés » ne bougent pas ;
- zéro tiret cadratin dans tout texte ajouté ou reformulé, zéro nom de concurrent ;
- mettre à jour `tests/test_qa_fixes.py` (certains tests peuvent chercher « Télécharger le CSV », la classe `history-download`, la légende de grille ou l'ordre des sections) et suivre la procédure de test du `CLAUDE.md` avant tout push ;
- boucle visuelle obligatoire après application : captures desktop 1366 px, mobile 390 px et tablette 768 px de chaque page modifiée dans `tests/screenshots/`, puis ré-invocation de @ux pour la revue (`docs/ux/ux-review.md`).

## 24. Décisions à confirmer par Thomas (ne pas trancher sans lui)

1. Retirer « Historique » de l'en-tête (GLO-03) : le lien reste dans le pied de page, la FAQ fiabilité et `/calendrier`.
2. Badge « En test » et mention « perturbations ponctuelles » de la fenêtre d'inscription (MOD-03) : retirer, ou garder discrètement ?
3. Libellés de clusters du blog (BLG-02) et choix des 3 articles piliers.
4. Mention « Service édité par ISSA Capital » sur `/a-propos` (APR-02).
5. Adresse `jean-pierre@calendrier-tempo.fr` (APR-03) : est-ce bien l'adresse à publier ?

## 25. Agents à envisager (handoff @agent-factory)

| Agent | Type | Rôle | Justification | Priorité |
|---|---|---|---|---|
| testeur-abonné-tempo | testeur-persona | Simule un abonné Tempo sur mobile : couleur de demain, jours rouges restants, inscription | Les 3 parcours A/B/C de la section 21 doivent être rejoués après corrections, sur captures réelles | Haute |

---

**Handoff → @fullstack (application), @design (en parallèle : tokens, boutons, espacements, icônes)**
- Fichier produit : `/home/user/Tempo/docs/audits/2026-09-30-audit-ux-pages.md`
- Décisions prises : accueil sans bloc séparé aujourd'hui/demain (décision fondateur respectée) ; historique : réponse avant preuve, CSV en lien discret ; en-tête à 4 liens + CTA ; pied de page en 3 groupes ; méthode dédupliquée entre `/a-propos` et `/methodologie`.
- Points d'attention : boutons soulignés (GLO-01), focus trap de la fenêtre (MOD-01), chiffres en dur sur l'accueil (ACC-05), tests à mettre à jour, `.min` à régénérer, 5 décisions à confirmer par Thomas (section 24).

