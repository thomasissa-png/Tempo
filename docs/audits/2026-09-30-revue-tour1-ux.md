# Revue UX notée, tour 1 : site en ligne (13 pages, ordinateur et mobile)

Date : 2026-09-30. Auteur : @ux. Destinataire : @fullstack (application), @design (visuel). Exigence fondateur : 9/10 minimum, itérer jusqu'au 10.
Échelle : 10 = rien à redire pour un abonné Tempo pressé sur son téléphone. Notation stricte : une cause de friction réelle (contenu coupé, information contradictoire, preuve manquante) retire au moins 1 point.

## 1. Tableau récapitulatif

| Page | Ordinateur /10 | Mobile /10 | Points bloquants (ce qui empêche 9 ou 10) |
|---|---|---|---|
| Accueil | 9 | 8,5 | Cartes « Comment économiser » en texte centré de 7 à 8 lignes ; barre grise vide sous les compteurs ; compteurs mobile en colonnes de 110 px avec phrase de prix sur 4 lignes ; température absente sous Aujourd'hui / Demain |
| Calendrier (mois) | 8 | 7,5 | Section prévisions vue uniquement en « Chargement… » dans les captures (aucune preuve des cartes avec météo) ; bloc « Jours rouges officiels : aucun » orphelin après le CTA ; note de source de 4 lignes ; 5 000 px de long sur mobile |
| Calendrier (saison) | 9 | 9 | Trois formulations du même « 0 rouge, 0 blanc » ; premier paragraphe trop technique |
| Historique | 8 | 7 | Grille entièrement cochée alors que « En bref » annonce 26 sans prévision (cases complétées non distinguables) ; cause des absences non affichée ; tableau de bilan coupé à droite sur mobile et étalé sur 1060 px sur ordinateur ; introductions encore trop longues |
| Tarif Tempo EDF | 9 | 9 | CTA placé après un H2 redondant ; ligne « jour rouge » du tableau pas mise en avant |
| Couleur de demain | 9 | 7 | Mobile : colonne « Température prévue » coupée (valeurs illisibles) et dates sur 3 lignes ; température en gras sur les jours confirmés (faux signal de certitude) |
| Méthodologie | 8,5 | 8 | Introduction en jargon (« scoring pondéré », « apprentissage automatique ») ; performance et limites en sections 5 et 6 ; paragraphe de 7 lignes en section 2 |
| API Tempo | 9 | 7,5 | Mobile : bouton « Copier » superposé au code, ligne curl tronquée ; exemples JSON avec « AAAA-MM-JJ » ; champs de `/api/predictions` en paragraphe |
| Blog (index) | 8,5 | 8 | Même date « mis à jour le 29/09/2026 » sur 22 cartes (bruit) ; grille 2 colonnes avec 3 cartes orphelines ; 6 700 px sur mobile sans saut de rubrique |
| Blog (article) | 8 | 7,5 | Sommaire fermé sur ordinateur ; un seul CTA à 8 100 px (12 600 px sur mobile) ; tableau mobile non vérifiable sur les captures ; hiérarchie des intertitres peu marquée |
| Alertes | 9 | 9 | Seul l'exemple du récapitulatif est montré (pas l'alerte jour rouge, promesse centrale) ; titre « Comment ça marche ? » aligné à gauche sous un héros centré ; emoji en étiquette |
| À propos | 9 | 9 | Carte « API publique » hors sujet et deux `<details>` qui recopient d'autres pages |
| Mentions légales | 8,5 | 8 | Sommaire fermé par défaut ; 10 700 px sur mobile sans retour au sommaire ; sous-blocs 4.2 a) à f) sans hiérarchie typographique |

Moyenne : ordinateur 8,7 ; mobile 8,1. Aucune page n'est à 10. Pages à 9 des deux côtés : saison, tarif, alertes, à propos.

## 2. Méthode et limites (à lire avant d'appliquer)

- Vu : les 26 captures `tour1/`, `v6/pli-*`, `v6/historique-grille.png`, plus le code de `_header`, `calendrier`, `alertes`, `couleur_demain`, `historique_previsions`, `_historique_bref`, `_historique_grille`, `_historique_bilan` et `prediction_history.py`. Les autres templates et `style.css` n'ont pas été relus : pour eux je cite l'élément vu sur capture, le sélecteur est à retrouver par le texte.
- Les captures pleine page de l'article (8 100 et 12 600 px) et des mentions légales (10 700 px) sont réduites à 62 à 340 px de large : je n'ai pas pu vérifier le débordement des tableaux dans l'article sur mobile (point R3) ni les sous-titres de la page légale. Je ne l'invente pas.
- `/calendrier` : les captures ont été prises avant la fin du chargement (« Chargement des prévisions… »). Je ne peux donc pas attester que les cartes de prévision avec température (audit météo du jour, M15) s'affichent correctement en ligne. C'est une lacune de preuve, à combler avant le tour 2 (point G1).
- Décisions fondateur respectées, non discutées : menu « Historique », badge « En test », cases d'historique complétées, taux de la dernière saison complète, lien Selectra, « 2 500 foyers ». La fenêtre d'inscription n'était pas dans les captures : non notée.

## 3. Écarts entre mes audits et ce qui est en ligne

Appliqué et conforme (à conserver) : accueil sans bloc séparé aujourd'hui/demain, ordre compteurs puis CTA puis étapes, « ? » remplacés par les valeurs, pourcentages retirés des pastilles, H2 « Les 10 prochains jours » ; en-tête partagé et pied de page en 3 groupes ; calendrier : compteurs en 3 colonnes sur mobile, lien vers l'historique, hauteur réservée au chargement ; saison : mois futurs regroupés, phrase de visiteur, H2 unique quand vide, CTA ; historique : CSV en lien discret, « En bref », 14 lignes + bouton, colonnes rouge/blanc masquées tant qu'il n'y en a pas, libellés de bilan en langage courant ; tarif : tableau à pastilles, CTA, note abonnement ; demain : ordre réponse > tableau > CTA, pastilles, colonne température ; méthodologie : sommaire et repère « toujours bleu » ; API : bouton Copier, `temp_moy_prevue` documenté ; blog : piliers et rubriques ; alertes : 3 cartes supprimées, lien guide ; à propos : méthode dédupliquée ; légales : sommaire et « Se désinscrire ».

Écarts (à corriger) :

| Audit | Demandé | En ligne | Point |
|---|---|---|---|
| INT-02, INT-04 (météo 15 jours) | Case sans prévision = pointillé gris, jamais confondue avec une prévision | Cases complétées (couleur officielle ou prévision voisine, décision fondateur) rendues exactement comme une vraie prévision (coche verte) : la légende « pas de prévision » ne s'affiche plus | HIS-T1 |
| INT-05 | Liste datée des jours sans calcul | Non visible dans la capture du jour ; « Couverture : 94 sur 120, 26 sans prévision » sans cause | HIS-T2 |
| HIS-03 | Bilan lisible sur mobile | Hint de défilement présent, mais le tableau compact reste coupé à droite | HIS-T4 |
| HIS-06 | Introductions de 2 phrases | Bilan : 2 paragraphes + 2 notes avant le tableau | HIS-T5 |
| CAL-01 | Résumé mensuel sous les prévisions, 0,9 rem | Déplacé sous le CTA mais resté bloc orphelin, décalé, « aucun » deux fois | CAL-T2 |
| CAL-02 | Intro + légende réduites | Badge « Nos prévisions » + intro + légende + note de prix : 4 éléments avant les cartes | CAL-T5 |
| M15-11 | Colonne température sur la page demain | Présente, mais coupée sur mobile | DEM-T1 |
| M15-10 | Température sous les deux grandes pastilles de l'accueil | Non faite (P2) | ACC-T4 |
| ART-01 | Sommaire ouvert sur ordinateur | Fermé | ART-T1 |
| LEG-01 | Sommaire cliquable | Présent mais fermé | LEG-T1 |
| ALE-03 | Icônes SVG, pas d'emoji d'interface | Emoji en étiquette de l'exemple | ALE-T3 |
| API-01 | Exemple avec vraie réponse | Placeholders « AAAA-MM-JJ » | API-T2 |
| GLO-09 | Styles en ligne vers des classes | Toujours présents (`calendrier.html` l. 231, 234 ; `alertes.html` l. 84, 147) ; `alertes.html` a son propre `<head>` et `<style>` au lieu d'étendre `_base.html` | GLO-T3 |

---

## 4. Détail par page (priorité P0 = bloque le 9, P1 = bloque le 10, P2 = finition ; tout est à faire)

### 4.1 Accueil (`templates/dashboard.html`, `static/js/app.js`)

Constat : premier écran conforme (mobile : H1, phrase, 10 pastilles, phrase de statut et bouton « Voir les 15 prochains jours » tiennent dans 844 px). Réponse à l'intention en moins de 1 scroll. Reste :

- **ACC-T1 (P1, ordinateur et mobile)** `dashboard.html`, cartes d'étapes « Comment économiser » : le texte est centré (`text-align:center`) sur 7 à 8 lignes (étape 1 : 7 lignes, étape 3 : 8 lignes). Passer les paragraphes en `text-align:left`, garder seulement le numéro centré ; raccourcir l'étape 1 à 3 lignes en reprenant les mêmes faits (météo de 9 villes, jours rouges restants, consommation prévue ; recalcul à 18 h et dès qu'EDF publie). Ne changer aucun chiffre (ils viennent de `site_facts`).
- **ACC-T2 (P1)** compteurs « Où en est la saison Tempo ? » : la barre grise sous chaque nombre est vide (22 restants sur 22 : elle devrait être pleine, ou représenter les jours utilisés, donc vide : ambigu) et sans légende. Soit la supprimer, soit la remplir avec la part restante et ajouter `aria-label` « 22 jours rouges restants sur 22 ». Reprendre le même sens que `/calendrier` (« 0 / 22 utilisés »).
- **ACC-T3 (P1, mobile)** mêmes compteurs : 3 colonnes de ≈ 110 px, la phrase « 0,7295 €/kWh en heures pleines, le plus cher » passe sur 4 lignes. Sous 480 px n'afficher que « 0,7295 €/kWh » (le reste dans un `<span class="hide-sm">`), hauteur des 3 cartes alignée.
- **ACC-T4 (P2)** température sous « Aujourd'hui » et « Demain » seulement (M15-10 : macro `week_dot` dans `dashboard.html`, `weekDotHtml` dans `app.js`, `_get_ssr_data` dans `app.py`, les trois ensemble ; une ligne de 16 px maximum, même fonction de format que les cartes du calendrier).
- **ACC-T5 (P2)** FAQ : « Vos prévisions sont-elles fiables ? » est la 12e question sur 12. C'est la question de confiance avant inscription : la remonter en 3e position dans `site_facts.FAQ_HOME` (la FAQ visible et le JSON-LD viennent de la même liste, le test d'égalité reste vert).
- Pour le 10 sur mobile : ACC-T1 à T3 suffisent.

### 4.2 Calendrier, mois en cours (`templates/calendrier.html`)

Constat : grille, compteurs et navigation de mois propres. Le bloc prévisions est la valeur de la page et c'est lui qu'on ne peut pas juger.

- **G1 / CAL-T1 (P0, preuve)** la capture montre « Chargement des prévisions… » avec 150 px vides. Refaire la capture après apparition de `#forecast-container .forecast-card` (attendre ce sélecteur, pas seulement le réseau) à 390, 768 et 1366 px, avec une carte négative, une sans température et la ligne `forecast-temp-note`. Si le chargement reste bloqué en production, c'est un défaut P0 (le JS de `loadPredictions` ne doit jamais laisser un loader infini : bloc d'erreur + « Réessayer » après 10 s).
- **CAL-T2 (P1)** l. 262 à 273, `<section class="cal-month-summary">` : après le CTA, le texte « Jours rouges officiels en septembre 2026 : aucun. Jours blancs officiels : aucun. » est décalé par rapport à la colonne (x≈173 contre 80 sur la capture) et n'apporte rien quand il est vide. N'afficher le bloc que s'il y a au moins un jour rouge, blanc ou une prévision rouge/blanche ; sinon le rendre `sr-only` (le texte reste dans le DOM) ; quand il s'affiche, le mettre à la largeur du contenu, sous les cartes et avant le CTA.
- **CAL-T3 (P1)** l. 225 à 227, `.cal-source-note` : 4 lignes de 0,75 rem. Supprimer « Saison 2026-2027 : du 1er septembre au 31 août. » (déjà dans le H1) et « Un jour sans donnée reste grisé : nous n'affichons jamais de couleur par défaut » (déjà porté par la légende « Non publié ou pas encore prévu »). Garder une phrase : source + « Seule la couleur publiée par EDF fait foi ».
- **CAL-T4 (P1, mobile)** la page fait 4 970 px : les trois H2 « Comprendre le calendrier Tempo EDF » / « Quand tombent les jours rouges ? » / « Comment utiliser ce calendrier ? » (texte de bas de page) passent dans un `<details class="faq-item">` fermé sous 768 px, ouvert au-dessus (contenu toujours dans le DOM). Le bloc « Dates des jours rouges et blancs par saison » passe après la FAQ (CAL-06 de l'audit du jour, toujours valable).
- **CAL-T5 (P1)** l. 234 à 246 : quatre éléments avant les cartes (titre, badge « Nos prévisions », phrase, légende avec prix, note de prix). Supprimer `span.source-label.label-prediction` (redondant avec le H2), fusionner « Plus le jour est proche… » et « Recalculées chaque jour à 18 h » en une seule ligne, garder la légende. Gain attendu : 60 à 80 px avant la première carte.
- **CAL-T6 (P2)** largeurs : grille ≈ 550 px centrée, prévisions à 1060 px, FAQ et textes à 800 px. Fixer deux largeurs seulement (1060 pour prévisions, 800 pour textes) et sortir les styles en ligne l. 231 et 234.

### 4.3 Calendrier, saison (`templates/calendrier_saison.html`)

Constat : propre, lisible, CTA présent, chips des mois à venir efficaces.

- **SAI-T1 (P2)** « 0 jour rouge et 0 jour blanc » est dit trois fois (paragraphe 2, tableau, H2 vide). Quand la saison n'a ni rouge ni blanc, fondre la phrase du H2 « Dates des jours rouges et blancs » dans le paragraphe 2 et supprimer ce H2 (le lien vers les autres saisons reste).
- **SAI-T2 (P2)** premier paragraphe (3 lignes) : raccourcir en « Couleurs officielles publiées par EDF, sans estimation (source : api-couleur-tempo.fr). » ; le détail « service tiers qui diffuse les données Tempo EDF/RTE » est déjà dans la note de `/calendrier`.
- Pour le 10 : ces deux points, rien d'autre.

### 4.4 Historique (`templates/historique_previsions.html`, `_historique_bref.html`, `_historique_grille.html`, `_historique_bilan.html`)

Constat : énorme progrès (réponse en tête, 14 lignes, CSV discret). Mais la page se contredit pour un visiteur attentif.

- **HIS-T1 (P0)** cohérence grille / « En bref ». « En bref » dit « 94 justes, 0 erronée, 26 sans prévision » ; la grille montre 14 lignes sans une seule case vide. Raison (lue dans `prediction_history._couleurs_voisines` et `_historique_grille.html` l. 52 à 58) : les cases complétées (couleur officielle en J-1, ou prévision voisine) sont rendues comme de vraies prévisions. On garde la décision fondateur (cases remplies), mais elles doivent se distinguer d'un coup d'œil : ajouter une classe `hg-fill` sur le `<span class="hg-dot">` quand `c.voisine` est vrai (ou que la cellule vient de `remplies`), style = anneau pointillé gris au lieu de l'anneau vert/rouge, sans coche ni croix ; l'infobulle et le `sr-only` disent déjà la source. Ajouter un 3e élément à la légende courte visible : « couleur complétée, non comptée dans les taux ». Sans cela, le lecteur voit une grille à 100 % et un taux qui ne l'est pas.
- **HIS-T2 (P1)** `_historique_bref.html` l. 20 : « 94 sur 120, 26 sans prévision » sans cause, et `h.jours_sans_calcul` n'apparaît pas. Détailler : « dont X couleur déjà publiée par EDF avant notre calcul (veille), Y jours sans calcul, Z autres ». `prediction_history._build` calcule déjà `causes` (« publiee », « interruption ») : exposer les trois totaux dans `h.bref` et afficher la ligne seulement si la somme est non nulle. Si « Z autres » n'est pas nul, la page doit le dire (« cause non enregistrée »), jamais l'attribuer à une panne.
- **HIS-T3 (P1)** bloc « En bref » : 1 paragraphe de référence + 2 notes + 6 lignes de liste avant la grille. Mettre les deux chiffres qui répondent à « puis-je m'y fier ? » en tête sous forme de 3 tuiles (desktop : 3 colonnes ; mobile : 1 colonne) : « Saison 2025-2026 : 90 % de prévisions justes (640 sur 714) » ; « Saison en cours : 94 justes, 0 erronée, 26 sans prévision » ; « Repère : dire bleu tous les jours : 86 % ». La liste détaillée descend dans un `<details open>` ordinateur / fermé mobile. Aucune valeur nouvelle : tout vient de `reference` et `h.bref`.
- **HIS-T4 (P1)** `_historique_bilan.html` : (a) mobile, le tableau compact à 3 colonnes est coupé (« Repère : toujours dire « bleu » » sort de l'écran à 390 px) : sous 600 px, libeller la colonne « Toujours « bleu » » (texte long en `title` et `sr-only`), ramener « + 7 jours sans prévision » à « + 7 sans prévision » en 0,72 rem, `table-layout: fixed`, première colonne 30 % ; (b) ordinateur, `.is-compact` s'étale sur 1060 px avec 400 px d'écart entre colonnes : `max-width: 720px`, aligné à gauche.
- **HIS-T5 (P1)** l. 6 à 13 : deux paragraphes et deux notes avant le tableau (HIS-06 non terminé). Garder une phrase (« Environ 300 jours sur 365 sont bleus : dire « bleu » tous les jours aurait souvent raison, la dernière colonne le montre. ») et mettre « Pourcentage affiché seulement à partir de N jours… » dans le `<details>` « Comment nous mesurons » (qui le dit déjà, l. 76 de `historique_previsions.html`).
- **HIS-T6 (P2)** mobile : les deux boutons de saison s'empilent en pleine ligne ; les mettre côte à côte (`flex-wrap`, `gap: 8px`) gagne 50 px. Le H1 est sur 2 lignes (acceptable).
- Pour le 10 : HIS-T1 à T5 ; HIS-T1 et HIS-T4 sont indispensables au 9.

### 4.5 Tarif Tempo EDF (`templates/tarif_tempo.html`)

Constat : page très propre (tableau à pastilles, source datée, note abonnement sans chiffre inventé, CTA). Mobile identique en qualité.

- **TAR-T1 (P2)** l'intention forte (coût d'un jour rouge) est servie par la section « Combien coûte un jour rouge Tempo ? » ; le CTA arrive après une section « Anticiper les jours rouges » qui ne fait que lier ailleurs. Placer le CTA `.blog-cta` juste après « Combien coûte un jour rouge » et fondre « Anticiper les jours rouges » en une ligne « Voir aussi : calendrier, couleur de demain, blog » (tous les liens internes restent).
- **TAR-T2 (P2)** dans le tableau, la ligne « Jour rouge » n'est pas distinguée : mettre 0,7295 en graisse 700 et ajouter, sous la pastille, « 4,4 fois le bleu » avec `facts.RATIO`.
- Pour le 10 : ces deux points.

### 4.6 Couleur de demain (`templates/couleur_demain.html`)

Constat : encadré de réponse excellent, tableau clair sur ordinateur, ordre réponse > tableau > CTA > explications respecté.

- **DEM-T1 (P0, mobile)** l. 42 à 52 : à 390 px la colonne « Température prévue » est coupée (on lit « 21 », « 19 », « 17 »…) sans indication de défilement ; les dates occupent 3 lignes (« mercredi / 30 / septembre »). Changer : sous 480 px, date courte (« mer. 30 sept. », ajouter un champ `date_label_court` à `ssr.week_summary` dans `_get_ssr_data` de `app.py`, même format que les pastilles de l'accueil), statut court (« Confirmé » / « Prévu 99 % », le « par EDF » dans un `<span class="hide-sm">`), en-tête « Temp. prévue » (avec `<abbr title="Température prévue">`), cellules `white-space: nowrap`. Le tableau doit tenir en 366 px sans défilement ; à défaut, appliquer `.table-scroll-wrap` + ombre comme sur l'historique.
- **DEM-T2 (P2)** la température des jours confirmés est en gras (`tr.officielle`) : on lit une certitude alors que c'est la prévision du dernier calcul. Mettre en gras le statut seulement.
- **DEM-T3 (P2)** `history-note` « Température moyenne prévue en France… » et le paragraphe suivant sont collés : marge de 12 px entre les deux.
- Pour le 10 : DEM-T1 à T3.

### 4.7 Méthodologie (`templates/methodologie.html`)

Constat : page sérieuse, sommaire en chips, chiffres honnêtes (22 sur 50 jours rouges annoncés, repère 86 %). Mais l'ordre sert le lecteur technique, pas le particulier qui vérifie le sérieux.

- **MET-T1 (P1)** l'introduction (5 lignes) parle de « scoring pondéré sur 7 critères » et de « modèles d'apprentissage automatique ». Remplacer par 2 phrases en langage courant (« Nous croisons la météo, la consommation prévue et les règles EDF pour estimer la couleur des 15 prochains jours. Les prévisions faites 2 à 5 jours avant sont les plus utiles. ») et placer juste dessous un renvoi « Ce que valent nos prévisions » vers `#performance`.
- **MET-T2 (P1)** ordre : passer « Performance » et « Limites » avant « Données », « Score », « Apprentissage » (renuméroter 1 et 2). Les sections techniques restent entières, le sommaire s'adapte. Impact SEO nul (mêmes titres, mêmes ancres).
- **MET-T3 (P2)** §2 : le paragraphe de 7 lignes (« Depuis le 29 septembre 2026, ces poids ne sont plus recalculés… ») va dans un `<details>` « Détail du recalcul des poids » ; §3 idem sous 768 px.
- **MET-T4 (P2, mobile)** 6 390 px : ajouter un lien « Retour au sommaire » en fin de chaque section (classe `.to-top`).
- Pour le 10 : MET-T1 et T2 font passer à 9 ; T3 et T4 au 10.

### 4.8 API Tempo (`templates/api_tempo.html`)

Constat : ordinateur excellent (tableau d'endpoints cliquables, champs documentés). Mobile : défaut visible.

- **API-T1 (P0, mobile)** blocs de code : le bouton « Copier » se superpose au texte et la ligne `curl …/api/today` est tronquée (`cur…/api/tod`). Placer le bouton dans une barre au-dessus du bloc (flex, `justify-content:flex-end`, cible 44 px), `pre` avec `overflow-x:auto` et `padding-right` normal ; sous 480 px, `white-space: pre-wrap; overflow-wrap:anywhere` sur le bloc curl.
- **API-T2 (P2)** exemple de réponse : remplacer `"AAAA-MM-JJ"` par la vraie réponse du jour rendue côté serveur (`ssr.today_iso`, `ssr.today_color` ; jamais une valeur inventée ; absent : garder le gabarit avec la mention « exemple de forme »).
- **API-T3 (P2)** `/api/predictions` : les 15 champs sont dans un paragraphe de 6 lignes avec des `<code>` inline, pénible sur mobile. Passer à un tableau « Champ | Contenu » (même composant que le tableau d'endpoints, empilé sous 480 px), `temp_moy_prevue` et `null` inclus.

### 4.9 Blog, index (`templates/blog_index.html`)

Constat : structure en piliers et rubriques réussie, titres clairs. Reste du bruit et de la longueur.

- **BLG-T1 (P1)** toutes les cartes affichent « Mis à jour le 29/09/2026 » (ou « Publié le » à la même date) : une date identique sur 22 cartes n'informe personne. Retirer la date des cartes (le `<time>` reste en `sr-only`), garder « 8 min de lecture » ; la date réapparaît dans l'article.
- **BLG-T2 (P1)** grille 2 colonnes : 3 rubriques ont un nombre impair de cartes, donc 3 cartes seules à demi-largeur (Comprendre Tempo, Calendrier et saisons, Préparer la saison). Passer à 3 colonnes ≥ 1024 px (2 colonnes 600 à 1023 px, 1 colonne mobile) : les rubriques de 3 cartes remplissent la ligne, il ne reste qu'une carte seule (Jours rouges, 4 cartes), et la grille s'aligne avec les 3 piliers du haut.
- **BLG-T3 (P1, mobile)** 6 700 px pour 22 articles. Sous le chapeau, ajouter `<nav class="blog-rubriques">` : 5 chips qui ancrent vers les H2 de rubrique (ids à poser) ; en rubrique, sous 600 px, cartes compactes : titre + temps de lecture seulement, description masquée (`display:none`, toujours dans le DOM). Hauteur attendue : environ 3 800 px.
- **BLG-T4 (P2)** le lien « Lire l'article → » répète le titre : rendre la carte entière cliquable (un seul `<a>`, hauteur ≥ 44 px) et retirer le lien texte.

### 4.10 Blog, article (`templates/blog_article.html`, `blog.py`)

Constat (article « alerte jour rouge ») : sommaire présent, « À lire aussi » présent, un CTA final. Long : 8 100 px (12 600 px sur mobile).

- **ART-T1 (P1)** le `<details>` « Sommaire (7 parties) » est fermé sur ordinateur (ART-01 demandait ouvert). Ouvrir par défaut ≥ 1024 px (attribut `open` posé par un petit script `matchMedia`), et à ≥ 1200 px le placer en colonne latérale collante (`position: sticky; top: 88px`, largeur 240 px) à gauche du corps (720 px).
- **ART-T2 (P1)** un seul CTA, tout en bas. Sur un texte de cette longueur, ajouter un bouton contextuel dans la section « Comment s'inscrire aux alertes Tempo… » (déjà 3 étapes), classe `.btn-primary`, libellé « S'inscrire aux alertes gratuites ». Je révise ici ART-03 de l'audit du jour : le CTA de fin reste, le contextuel s'ajoute, à cause de la longueur constatée.
- **ART-T3 (P1, à vérifier)** tableau « Comparaison des services d'alerte » (4 colonnes) : captures trop réduites pour juger le débordement mobile. Fournir une capture 390 px découpée sur ce tableau ; si `blog.py` n'enveloppe pas encore `<table>` dans `<div class="table-scroll" tabindex="0">`, le faire à la génération.
- **ART-T4 (P2)** hiérarchie : les intertitres H3 s'apparentent à du gras de paragraphe. `blog_article .content h2` : marge haute 48 px + filet fin ; `h3` : 1,1 rem, marge haute 28 px, couleur pleine.
- **ART-T5 (P2)** lien flottant « Haut de page » sous 768 px (classe existante de l'accueil).
- Non traité : le fond (l'article répète `/alertes` sur 8 100 px) relève de @copywriter.

### 4.11 Alertes (`templates/alertes.html`)

Constat : page courte et nette, CTA au-dessus du pli mobile, réassurance en une ligne, récapitulatif d'exemple honnête (« couleurs fictives »).

- **ALE-T1 (P1)** la promesse centrale est « une alerte avant chaque jour rouge », mais seul le récapitulatif du dimanche est montré. Ajouter sous le premier exemple une seconde bulle « Exemple d'alerte jour rouge (couleurs fictives) » avec le texte réel du message envoyé par `alerts.py` (le reprendre tel quel, sans l'inventer ; s'il dépend d'un gabarit, le rendre via la même fonction que pour le récapitulatif). C'est le seul élément de la page qui manque à un visiteur indécis.
- **ALE-T2 (P2)** `.alertes-how h2` et la liste sont alignés à gauche sous un héros centré : centrer le titre ou aligner le héros à gauche sous 600 px ; le sélecteur est dans le `<style>` de la page.
- **ALE-T3 (P2)** l. 108 : retirer l'emoji « 💬 » de l'étiquette d'exemple (les emoji de la bulle restent : ils font partie du vrai message).
- **ALE-T4 (P2)** `alertes.html` a son propre `<head>` et son `<style>` : le faire étendre `_base.html` comme les autres pages (risque de dérive des balises méta, du jeu d'icônes et des versions d'assets).
- Pour le 10 : ALE-T1 surtout.

### 4.12 À propos (`templates/a_propos.html`)

Constat : page sobre, méthode bien dédupliquée, transparence et limites, contact. Mobile lisible.

- **APR-T1 (P2)** la carte « API publique gratuite » ne répond pas à « qui êtes-vous ? » : la remplacer par une ligne dans la carte « Comment nous prévoyons » (« Données ouvertes : API Tempo ») avec le lien.
- **APR-T2 (P2)** deux `<details>` fermés (« Le détail de la méthode et des sources », « Les endpoints en bref ») recopient `/methodologie` et `/api-tempo` : les remplacer par des liens, ce qui raccourcit la page d'un écran mobile. Le texte SEO reste sur les pages de référence.

### 4.13 Mentions légales (`templates/legal.html`)

Constat : contenu complet, sommaire et bouton « Se désinscrire » en tête, dernière mise à jour datée. 6 260 px (10 700 px sur mobile).

- **LEG-T1 (P1)** le sommaire est un `<details>` fermé : un visiteur ne sait pas qu'il est là. Le laisser ouvert par défaut ≥ 768 px ; sur mobile, l'ouvrir aussi (9 lignes), avec « 9. Désinscription » en première ligne.
- **LEG-T2 (P1, mobile)** ajouter en fin de chaque H2 un lien « Retour au sommaire » et un lien flottant « Haut de page » (10 700 px sans repère).
- **LEG-T3 (P2)** sous-blocs 4.2 a) à f), 4.3, 4.5 : la lettre et l'intitulé en gras dans le paragraphe ne créent pas de hiérarchie. Passer en `<h4>`, marge haute 24 px, listes avec 6 px entre items. Ne rien replier (le contenu légal reste visible en entier, comme décidé dans l'audit du jour).

---

## 5. Corrections transverses

- **G1 (P0, preuve)** boucle visuelle : le script de capture doit attendre le sélecteur de contenu asynchrone (`#forecast-container .forecast-card` sur `/calendrier`, `.week-summary .week-dot` sur l'accueil) avant la capture ; ajouter pour le tour 2 des captures **découpées** 390 × 844 px (premier écran) et sections clés (tableau de l'article, bilan d'historique, tableau de demain), la fenêtre d'inscription ouverte sur mobile, et le menu hamburger ouvert. Les pleine-page de plus de 6 000 px ne sont pas lisibles en revue.
- **G2 (P1)** règle CSS commune des tableaux sous 480 px (`.data-table`, `.history-table`, `.history-bilan`) : `table-layout: fixed`, en-têtes abrégés via `.hide-sm`, cellules chiffrées `white-space: nowrap`, et `.table-scroll-wrap` + ombre seulement si le contenu dépasse encore. Couvre DEM-T1, HIS-T4, API-T3.
- **G3 (P2)** lien flottant « Haut de page » sous 768 px sur les pages de plus de 4 000 px (historique, méthodologie, article, mentions légales, blog).
- **GLO-T3 (P2)** styles en ligne restants (`calendrier.html` l. 199 à 202, 231, 234, 242 à 244 ; `alertes.html` l. 84, 147 ; `dashboard` à vérifier) vers classes ; `alertes.html` sur `_base.html` (ALE-T4).
- **G4 (P2)** bouton du bloc `.blog-cta.page-cta` : avec l'icône WhatsApp sur `/calendrier` et `/alertes`, sans icône sur tarif, demain, méthodologie, saison. Appeler la même macro `icons.whatsapp` partout.
- Conforme, à ne pas toucher : en-tête à 5 liens et badge (décisions fondateur), pied de page en 3 groupes, fil d'Ariane, un seul `<h1>` par page, hiérarchie des couleurs Tempo.

## 6. Les 5 corrections qui font gagner le plus de points

| Rang | Correction | Pages concernées | Gain estimé |
|---|---|---|---|
| 1 | Tableaux et code qui débordent sur mobile : DEM-T1 (demain), HIS-T4 (bilan), API-T1 (code) + règle commune G2 | Demain, Historique, API (mobile) | Demain mobile 7 vers 9 ; API mobile 7,5 vers 9 ; Historique mobile +1 |
| 2 | Historique cohérent avec lui-même : HIS-T1 (cases complétées visibles), HIS-T2 (causes des absences), HIS-T3 (tuiles en tête), HIS-T5 (introduction) | Historique (les deux formats) | 8 vers 9,5 ordinateur ; 7 vers 9 mobile ; supprime le seul risque de confiance du site |
| 3 | Calendrier : preuve des cartes avec météo (G1/CAL-T1) + CAL-T2 + CAL-T3 + CAL-T5 + CAL-T4 mobile | Calendrier (les deux formats) | 8 vers 9,5 ordinateur ; 7,5 vers 9 mobile |
| 4 | Blog : BLG-T1, T2, T3 (date, grille 3 colonnes, sauts de rubrique) et article : ART-T1, T2, T3 | Blog index, Article | Index 8,5 vers 9,5 ; article 8 vers 9 ordinateur, 7,5 vers 9 mobile |
| 5 | Accueil mobile et pages longues : ACC-T1, T2, T3, puis MET-T1/T2 (méthodologie en langage courant, performance d'abord), LEG-T1/T2, G3 | Accueil, Méthodologie, Mentions légales | Accueil 8,5 vers 9,5 ; méthodologie 8,5 vers 9,5 ; légales 8,5 vers 9 |

Après ces 5 lots, toutes les pages sont attendues à 9 ou plus. Le 10 demande ensuite les P2 (températures sous Aujourd'hui/Demain, alerte rouge d'exemple, API réelle).

## 7. Ordre d'exécution et garde-fous

Ordre : (1) lot 1 (tableaux mobiles) puis G1 (capture) ; (2) lot 2 historique (HIS-T1 à T5) ; (3) lot 3 calendrier ; (4) lot 4 blog ; (5) lot 5 accueil, méthodologie, légales ; (6) P2 restants (ALE-T1 en tête) ; (7) régénérer `style.min.css` et `app.min.js`, incrémenter `facts.ASSET_V`.

Garde-fous :
- Zéro donnée inventée : les tuiles, le détail des causes, l'exemple d'alerte et l'exemple d'API n'utilisent que des valeurs déjà calculées ou le texte réel des messages.
- SEO : pas de texte mot-clé supprimé, seulement déplacé, replié dans `<details>` (contenu dans le DOM) ou `sr-only` ; un seul `<h1>` ; FAQ visible et JSON-LD issus de la même liste (`site_facts`) ; liens internes conservés.
- Lien Selectra et « Plus de 2 500 foyers alertés » non touchés ; menu « Historique » et badge « En test » conservés ; cases d'historique complétées (seule leur distinction visuelle change, pas leur remplissage ni les taux).
- Zéro tiret cadratin dans tout texte ajouté, zéro nom de concurrent.
- Tests : mettre à jour `tests/test_qa_fixes.py`, `tests/test_history_page.py`, `tests/test_home_week_card.py`, `tests/test_audit_pages_2026_09_30.py` (assertions sur la légende de grille, `hg-none`, libellés du bilan, `history-csv`, ordre des sections, FAQ_HOME) et suivre la procédure de test du `CLAUDE.md` avant tout push.
- Boucle visuelle : captures 390, 768 et 1366 px de chaque page modifiée dans `tests/screenshots/` puis nouvelle revue notée (tour 2) par @ux.

## 8. Questions ouvertes

1. Cases complétées de l'historique : Thomas valide-t-il l'anneau pointillé + légende « couleur complétée, non comptée dans les taux » (HIS-T1), qui n'enlève pas le remplissage mais le rend visible ? Sans cela, la grille affiche 100 % pour un taux qui ne l'est pas.
2. Faire passer « Performance » et « Limites » avant les sections techniques de la méthodologie (MET-T2) : accord de @seo non nécessaire (mêmes ancres), mais à confirmer par Thomas.
3. Fenêtre d'inscription : non couverte par les captures de ce tour, à inclure au tour 2.

## 9. Agents à envisager (handoff @agent-factory)

| Agent | Type | Rôle | Justification | Priorité |
|---|---|---|---|---|
| testeur-abonné-tempo | testeur-persona | Rejoue sur captures mobiles 390 px : couleur de demain, lecture de l'historique (« 0 erronée, 26 sans prévision »), inscription | Les frictions HIS-T1, DEM-T1 et API-T1 doivent être rejouées par un lecteur qui ne connaît pas le service | Haute |

---

**Handoff -> @fullstack (application), @design (en parallèle : anneau pointillé des cases complétées, tuiles « En bref », rangées de chips du blog)**
- Fichier produit : `/home/user/Tempo/docs/audits/2026-09-30-revue-tour1-ux.md`
- Décisions prises : notes par page et format ; 4 pages à 9 des deux côtés (saison, tarif, alertes, à propos) ; aucune à 10 ; 5 lots de corrections ordonnés ; CTA contextuel ajouté dans l'article (révision d'ART-03) ; cases complétées conservées mais rendues identifiables.
- Points d'attention : débordements mobiles (demain, bilan, code API), cohérence grille / « En bref » de l'historique, preuve visuelle manquante des cartes de prévision avec météo sur `/calendrier`, tests à mettre à jour, `.min` à régénérer.



