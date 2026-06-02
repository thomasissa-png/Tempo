# Partenariat Selectra — Analyse et décision

**Date** : 2026-05-29
**Demandeur** : Selectra (comparateur d'énergie FR)
**Projet** : calendrier-tempo.fr
**Statut** : Livré

---

## Synthèse de la proposition

Selectra propose un échange de visibilité :
- **Ce qu'ils demandent** : insertion d'une phrase + lien sortant sur calendrier-tempo.fr, après le passage « pour un foyer avec chauffage électrique, un jour rouge peut facilement coûter 50 à 150 € si vous ne réduisez pas votre consommation ». Formulation suggérée : « Vous pouvez consulter cette page de Selectra qui donne la grille tarifaire en fonction de votre abonnement. »
- **Ce qu'ils offrent en échange** : liens entrants depuis 4 sites — energie-reduc.com, observatoires.net, agence-energie.com, electricite.net
- **Bonus mentionné** : 2 articles invités possibles (energie-reduc.com + agence-energie.com)

---

## Synthèse exécutive

- **Décision insertion calendrier-tempo.fr** : **GO-CONDITIONNEL** (refus de la proposition initiale en l'état, contre-proposition argumentée).
- **Décision articles invités** : **GO** sur les 2 articles, à condition que la rédaction reste sous notre contrôle éditorial total et que la valeur lecteur prime sur l'objectif backlink.
- **Risques principaux** :
  1. Risque link-scheme Google modéré-fort si réciprocité explicite et sites Selectra-affiliés (à vérifier).
  2. Risque éditorial : la formulation suggérée par Selectra (« grille tarifaire en fonction de votre abonnement ») est ambiguë et déplace le lecteur vers une logique commerciale qui n'est pas la promesse de calendrier-tempo.fr (anticipation, pas comparaison de fournisseurs).
  3. Risque de crédibilité : ajouter un lien commercial sur une page de service public-de-fait peut entamer la perception d'indépendance — qui est l'atout différenciant n°1 du site.

---

## Livrable 1 — Décision et insertion

### 1.1 Audit SEO des 4 sites Selectra

**⚠️ Note méthodologique** : cette session n'a pas accès aux tools WebFetch/WebSearch. Les éléments ci-dessous reposent sur la connaissance générale du paysage SEO français et doivent être **vérifiés manuellement par le porteur** avant décision finale via :
- `ahrefs.com/website-authority-checker` (DR) ou Moz Link Explorer (DA)
- `similarweb.com` (trafic estimé)
- Recherche WHOIS publique (date de création, propriétaire)
- Recherche Google : `site:energie-reduc.com` et idem pour les 3 autres (volume indexé)
- Recherche Google : `"energie-reduc.com" "Selectra"` — mentions croisées

| Site | Hypothèse de positionnement | Autorité présumée | Risque structurel | Action de vérif |
|---|---|---|---|---|
| **energie-reduc.com** | Site éditorial Selectra ou affilié (URL « énergie + réduction » = ciblage économies d'énergie) | À vérifier (DR ?) | Affiliation Selectra probable → footprint de réseau | WHOIS + mentions légales + `link:` operator |
| **observatoires.net** | Nom générique (.net) — soit observatoire indépendant énergie, soit micro-site de contenu satellite | À vérifier (DR probablement faible) | Si .net peu rempli = signal PBN | Crawl manuel des pages indexées |
| **agence-energie.com** | Présenté comme une « agence » — peut être site marque ou site satellite éditorial | À vérifier | Si pas d'agence physique identifiable → site de contenu | Mentions légales + adresse |
| **electricite.net** | Domaine premium générique. Soit site historique d'autorité, soit acquis pour SEO | À vérifier (DR probablement moyen-fort) | Le plus intéressant des 4 si réel | WHOIS + Wayback Machine |

**Verdict SEO préliminaire** :
- Le pattern « 4 sites du même groupe proposent 4 backlinks contre 1 backlink » est un schéma classique de network linking. Si les 4 sites sont effectivement opérés par/pour Selectra (ce que la proposition groupée laisse fortement présumer), Google peut les détecter comme un même propriétaire et **dévaluer la valeur des 4 liens entrants à ~1 lien équivalent au mieux, voire à zéro si pattern flagrant**.
- La valeur SEO réelle pour calendrier-tempo.fr dépend du DR de **electricite.net** principalement. Les 3 autres ont des noms typiques de réseau satellite et apportent vraisemblablement peu de jus.
- **Pertinence thématique** : forte (énergie = même secteur). C'est le seul vrai point positif.

**Risque pénalité Google** :
- Faible si liens en nofollow ou sponsored, modéré si dofollow + ancre commerciale exacte, **fort si Google détecte la réciprocité explicite** (Selectra fait pointer vers nous, nous faisons pointer vers Selectra). Google Search Essentials > Link Spam > « Excessive link exchanges » est explicite : les échanges réciproques massifs sont contraires aux guidelines.

**Verdict SEO** : la contrepartie a une **valeur SEO faible à moyenne** mais surtout **un risque structurel non négligeable** pour calendrier-tempo.fr qui construit son autorité sur un profil de liens organique et propre. Recommandation : **NO-GO sur la formulation initiale, GO-CONDITIONNEL sur une version restructurée**.

### 1.2 Analyse risque link-scheme + conformité

**Référentiel Google Search Essentials — Link Spam Policy** (extraits applicables) :
- « Buying or selling links for ranking purposes » — interdit.
- « Excessive link exchanges (Link to me and I'll link to you) or partner pages exclusively for the sake of cross-linking » — interdit.
- « Requiring a link as part of a Terms of Service, contract, or similar arrangement » — interdit.

**Application au cas Selectra** :
- La proposition est explicitement un échange (« En échange, nous pouvons vous faire des liens sur ces sites »). C'est par définition un link exchange.
- 1 contre 4 sites n'enlève rien à la qualification : c'est même un signal plus fort pour Google (volume disproportionné).
- Le pattern d'ancre suggéré (« cette page de Selectra qui donne la grille tarifaire ») contient le nom de marque + un kw transactionnel — c'est une ancre optimisée commercialement.

**Mitigation possible** :
- Attribut `rel="nofollow sponsored"` sur le lien sortant → annule l'effet ranking, donc sort techniquement du link-scheme. Mais Selectra n'aura plus aucun intérêt SEO à l'échange — donc cette piste teste leur sincérité.
- Si Selectra refuse le nofollow/sponsored, c'est la confirmation que l'objectif est purement SEO (et non « apporter de la valeur aux lecteurs ») → NO-GO.

**Risque juridique / conformité éditoriale** :
- Si lien dofollow sans mention « partenaire » ou « sponsorisé », possible problème au regard de la **loi pour la Confiance dans l'Économie Numérique (LCEN art. 20)** et des recommandations ARPP sur la publicité native : tout contenu publi-rédactionnel doit être identifié comme tel.
- Au minimum, si insertion validée : marquage typographique discret (« Partenaire » ou « En savoir plus chez nos partenaires ») + `rel="sponsored"`.

### 1.3 Décision argumentée

**Décision : NO-GO sur la proposition initiale de Selectra, GO-CONDITIONNEL sur une version restructurée.**

**Pourquoi NO-GO sur la formulation initiale :**
1. La phrase suggérée (« grille tarifaire en fonction de votre abonnement ») est **factuellement vague** : calendrier-tempo.fr ne publie jamais les prix HC bleu/blanc parce qu'ils ne sont pas publics. Pointer vers une « grille tarifaire » externe ne sert pas un besoin réel du lecteur à ce moment précis de la page (le lecteur est en train de regarder une couleur prédite, pas de chercher un comparateur).
2. Elle déplace le lecteur vers un site commercial avec call-to-action commercial — incohérent avec la promesse d'indépendance du service.
3. La réciprocité explicite expose au risque link-scheme Google.
4. La valeur SEO réelle des 4 backlinks proposés est très probablement surévaluée par Selectra.

**Pourquoi GO-CONDITIONNEL est possible :**
- Selectra est un acteur réel et identifié du marché énergie français — refuser tout dialogue serait inutilement isolant.
- Une coopération **éditoriale** (articles invités) sur des sujets utiles aux lecteurs des deux audiences peut créer de la valeur réelle, sans contradiction avec la mission du site.
- Sur la page d'origine du lien proposé, une **citation éditoriale neutre** (sans réciprocité, à notre initiative) sur un point factuel vérifiable peut être envisagée — à condition que le lien apporte une information que le lecteur cherche vraiment à ce moment.

### 1.4 Conditions négociables — contre-proposition à envoyer

À renvoyer à Selectra dans cet ordre de priorité :

1. **Refuser la formulation initiale** : la phrase suggérée n'est pas utile au lecteur à cet endroit précis, et nous ne pratiquons pas l'échange de liens réciproque dofollow (raison invoquée : conformité aux Google Search Essentials et politique éditoriale d'indépendance).

2. **Proposer une alternative en 3 volets** :

   **Volet A — Articles invités (priorité)** : nous écrivons gratuitement 1 article pour energie-reduc.com et 1 pour agence-energie.com (briefs joints en livrable 2). En contrepartie : 1 mention auteur + 1 lien dofollow vers calendrier-tempo.fr dans le bio auteur (pas dans le corps), ancre marque (« calendrier-tempo.fr ») et non kw transactionnel. Ce schéma est éditorialement irréprochable et reste dans les standards de l'invité-blogging.

   **Volet B — Si Selectra tient à un lien sortant depuis nos pages** : nous pouvons étudier l'insertion d'une **ressource externe** (non sur la home, mais dans un article de blog pertinent : ex. « Tempo EDF : guide complet 2026 » ou un futur article « Comprendre votre facture pendant les jours rouges »), si :
   - Le lien sort en `rel="sponsored"` ou `rel="nofollow"` (annonce loyalement la nature partenariale).
   - L'ancre est non-commerciale : `« voir la grille tarifaire Tempo détaillée »` plutôt que `« cette page de Selectra »`.
   - La page de destination chez Selectra doit apporter une info concrète et factuelle (grille tarifaire HC/HP par jour) — pas un comparateur de fournisseurs.
   - Le passage est introduit par une mention transparence : « Pour la grille tarifaire complète, voir [lien partenaire]. »

   **Volet C — Pas de réciprocité explicite obligatoire** : si Selectra souhaite spontanément citer calendrier-tempo.fr depuis leurs articles, ils sont libres de le faire — mais cette citation ne doit pas être conditionnelle au lien depuis chez nous (sortir du schéma « do ut des »).

3. **Refus net** sur :
   - Tout lien dofollow réciproque sans marquage « sponsored »
   - Toute insertion sur la home / `/calendrier` (pages produit, vouées à l'usage, pas à du contenu sponsorisé)
   - Toute ancre contenant la marque Selectra + un kw transactionnel
   - Tout engagement de volume (« X liens / mois »)

### 1.5 Si Volet B accepté — Insertion rédigée

**Position d'insertion** : pas sur la home, mais dans un article de blog dédié (ex. « Combien coûte un jour rouge Tempo en 2026 ? » ou similaire), à un endroit où le lecteur cherche concrètement la grille de prix.

**3 variantes de formulation** (à choisir selon la page d'accueil) :

**Variante 1 — Sobre, neutre :**
> Pour la grille tarifaire Tempo complète (heures pleines / heures creuses pour chaque couleur), vous pouvez vous référer à [la grille Tempo détaillée](URL-SELECTRA){rel="sponsored"} mise à jour par notre partenaire Selectra.

**Variante 2 — Factuelle, mise en situation lecteur :**
> Pour vérifier le tarif exact qui s'applique à votre abonnement (selon votre puissance souscrite et votre option HC), [la grille tarifaire publiée par Selectra](URL-SELECTRA){rel="sponsored"} reprend l'ensemble des prix en vigueur.

**Variante 3 — Transparence maximale :**
> [Partenaire] La grille tarifaire détaillée par puissance et par couleur est consultable sur [le site Selectra](URL-SELECTRA){rel="sponsored"}.

**Recommandation orchestrateur** : **Variante 2**. Elle pose une vraie question utilisateur (« quel tarif s'applique à MON abonnement ? »), justifie le clic, n'utilise pas l'ancre marque + kw, et reste honnête.

**Garde-fous appliqués** :
- Aucun chiffre nouveau inventé (cohérent avec règle « jamais de prix bleu/blanc »).
- Mention transparente du caractère partenaire.
- Ancre non-suroptimisée.
- Page d'insertion ≠ home (préserve la mission produit).

---

## Livrable 2 — Angles articles invités

### Méthode

Sans accès WebFetch sur les 2 articles d'exemple fournis (`energie-reduc.com/economies/panneaux-solaires-reduire-facture-electrique` et `agence-energie.com/faq/prix-renovation-2026`), l'analyse repose sur :
- Le nom des deux URL d'exemple (intention de recherche identifiable).
- Le brief Selectra (« ton neutre, une seule citation, répond à l'intention de recherche »).
- L'audience cible probable des deux sites (lecteurs en recherche d'optimisation de facture énergie résidentielle).

À vérifier par lecture manuelle des deux articles d'exemple avant briefing copywriter.

### 2.1 Site cible 1 — energie-reduc.com

**Audience supposée** : foyers qui cherchent activement à réduire leur facture d'énergie. Intention dominante : informationnel → transactionnel doux (gestes, équipements, contrats).
**Ton observé sur l'exemple panneaux solaires** : pédagogique, mise en avant des économies chiffrées, guide pas-à-pas.

**3 angles candidats** :

**Angle A1 — « Tempo EDF : 4 réflexes simples pour transformer un jour rouge en économie réelle »**
- **Promesse** : 4 actions concrètes (chauffage, eau chaude, cuisson, électroménager) qui font passer un jour rouge de « 50-150 € de surcoût » à « surcoût neutralisé ».
- **Persona** : foyer chauffage électrique abonné Tempo, n'a pas encore optimisé ses jours rouges.
- **Intention** : informationnel — « comment ne pas payer cher les jours rouges ».
- **Différenciateur calendrier-tempo.fr** : on apporte la donnée d'anticipation (savoir AVANT que c'est rouge), pas juste les gestes. Le lien naturel : « pour anticiper les jours rouges avant qu'ils ne soient annoncés, voir calendrier-tempo.fr ».
- **Lien sortant proposé** : home calendrier-tempo.fr, ancre marque.
- **Sert le lecteur** : oui, conseils actionnables. **Sert la marque** : oui, autorité Tempo. **Cannibalisation interne** : modérée (sujet déjà partiellement couvert dans notre blog) → angle à différencier par la framing « gain par geste » plutôt que « guide général ».

**Angle A2 — « Combien rapporte vraiment l'anticipation des jours rouges Tempo ? Calcul sur une saison complète »**
- **Promesse** : modélisation chiffrée du gain annuel possible quand on anticipe correctement les 22 jours rouges d'une saison vs quand on les subit.
- **Persona** : abonné Tempo qui hésite à investir du temps dans le suivi quotidien.
- **Intention** : informationnel + décisionnel — « est-ce que ça vaut le coup ? ».
- **Différenciateur** : nous avons les données historiques de prédiction et nos predictions J+2→J+5 ; nous pouvons donner un chiffre crédible (ex. fourchette d'économie réaliste sur 22 jours × profil de foyer). À noter : ne pas inventer le chiffre — utiliser les chiffres déjà publiés par EDF officiel.
- **Lien sortant proposé** : `/calendrier` (le lecteur veut voir l'outil).
- **Sert le lecteur** : très oui (réponse à une vraie question avant achat de l'effort). **Sert la marque** : très oui (positionne le service comme ROI quantifiable). **Cannibalisation** : faible, angle de framing original.

**Angle A3 — « 7 idées reçues sur les jours rouges Tempo (et ce qui est vrai) »**
- **Promesse** : démontage de mythes ( « la box internet, faut-il l'éteindre ? », « le frigo, ça compte ? », « les heures creuses gardent leur tarif rouge ? »).
- **Persona** : abonné Tempo qui lit des conseils contradictoires sur les forums.
- **Intention** : informationnel — « qu'est-ce qui marche vraiment ? ».
- **Différenciateur** : factuel rigoureux, sourcing EDF officiel, ton expert calme.
- **Lien sortant proposé** : article blog « FAQ Tempo » ou similaire.
- **Sert le lecteur** : oui (anti-désinformation). **Sert la marque** : oui (positionne sur l'expertise). **Cannibalisation** : à vérifier dans la liste des 22 articles déjà rédigés — si « idées reçues Tempo » existe déjà → recadrer.

**Recommandation site 1 : Angle A2** — c'est l'angle le plus différenciant (très peu de contenu web sur le ROI quantifié de l'anticipation Tempo), le plus crédible (nous avons la donnée), le moins cannibalisant avec notre propre blog, et le plus naturellement pourvoyeur de trafic vers /calendrier.

**Brief minimal @copywriter (à activer après validation porteur)** :
- Titre : « Anticiper les jours rouges Tempo EDF : combien ça rapporte vraiment ? »
- Format : 1100-1400 mots, 4-5 H2, 1 tableau de simulation (3 profils foyer × gain annuel estimé), FAQ 3 questions.
- Sources autorisées : EDF officiel uniquement pour les prix. Pas de chiffre inventé.
- Ton : vouvoiement expert accessible (cohérent ton calendrier-tempo.fr).
- Liens internes vers energie-reduc.com : 2-3 max, ancres naturelles.
- Lien sortant : 1 vers calendrier-tempo.fr/calendrier (ancre : « calendrier Tempo avec prédictions »).
- Mention partenariale en bio auteur.

### 2.2 Site cible 2 — agence-energie.com

**Audience supposée** : profil un peu plus B2C-éclairé voire petit-pro, recherche pratique de prix/devis. L'exemple « prix-renovation-2026 » suggère une orientation « guide de prix » format FAQ.
**Ton observé sur l'exemple** : structurel (FAQ), neutre, orienté décision.

**3 angles candidats** :

**Angle B1 — « Tempo EDF en 2026 : tout ce qu'il faut savoir avant de souscrire (ou de garder son contrat actuel) »**
- **Promesse** : guide décisionnel : profil pour qui Tempo est rentable, pour qui non, conditions d'éligibilité, points de vigilance avant souscription.
- **Persona** : foyer qui se demande si passer à Tempo (ou y rester) est une bonne idée.
- **Intention** : décisionnel — « Tempo, est-ce pour moi ? ».
- **Différenciateur** : on regarde la question de l'extérieur, on ne vend pas, on donne les seuils de bascule honnêtes.
- **Lien sortant proposé** : home calendrier-tempo.fr.
- **Sert le lecteur** : oui (aide à décider). **Sert la marque** : oui (positionne en source indépendante). **Cannibalisation** : modérée — vérifier si un article « Tempo : pour qui ? » existe déjà.

**Angle B2 — « Tarif Tempo 2026 : prix HP et HC pour chaque couleur, par puissance souscrite »**
- **Promesse** : reprise structurée et lisible de la grille tarifaire officielle EDF Tempo en vigueur en 2026, avec exemples chiffrés sur 24h.
- **Persona** : abonné ou prospect Tempo en recherche directe de prix.
- **Intention** : transactionnel / kw « tarif tempo edf ».
- **Différenciateur** : tableau le plus lisible du web, mise en situation 24h pour 3 couleurs.
- **⚠️ Risque éthique majeur** : si nous publions cette grille tarifaire chez agence-energie.com, nous le faisons aussi de fait pour Selectra (puisque c'est leur propriété de page). Et nous nous coupons l'herbe sous le pied pour publier cet article chez nous (cannibalisation directe d'un futur article calendrier-tempo.fr).
- **Verdict** : à écarter — c'est précisément le contenu qu'on devrait écrire pour notre propre blog, pas pour un site tiers.

**Angle B3 — « Jours rouges Tempo : la check-list de la veille pour ne pas se faire surprendre »**
- **Promesse** : protocole simple (la veille à 11h, savoir si demain est rouge ; programmation chauffage, eau chaude, lessive, recharge VE ; mise sous tension différée).
- **Persona** : abonné Tempo qui a déjà essuyé un jour rouge surprise et veut s'organiser.
- **Intention** : informationnel actionnable.
- **Différenciateur** : on connaît mieux que personne le moment d'annonce EDF (11h J+1) et la fiabilité de J+2 chez nous → on sait écrire la check-list la plus fiable.
- **Lien sortant proposé** : /alertes (CTA abonnement WhatsApp) car le contenu pousse vers une logique d'alerte automatique.
- **Sert le lecteur** : très oui (très opérationnel). **Sert la marque** : très oui (conversion vers alertes). **Cannibalisation** : faible — angle « check-list veille » peu couvert ailleurs.

**Recommandation site 2 : Angle B3** — meilleure conversion potentielle (lien vers /alertes), différenciateur clair (on est seul à savoir vraiment dater la fenêtre d'annonce EDF), faible cannibalisation, valeur opérationnelle immédiate pour le lecteur.

**Brief minimal @copywriter (à activer après validation porteur)** :
- Titre : « Jour rouge Tempo : la check-list de la veille pour ne rien subir »
- Format : 1000-1300 mots, structure check-list (4 sections : « 11h05 — vérifier », « 11h30 — programmer », « 17h-22h — limiter », « lendemain — vérifier l'effet »), encadré « le piège classique », FAQ 2 questions.
- Sources autorisées : EDF officiel uniquement pour fenêtre d'annonce et tarifs. Pas de chiffre inventé.
- Ton : vouvoiement expert accessible, plus opérationnel que A2.
- Liens internes vers agence-energie.com : 2-3 max, ancres naturelles.
- Lien sortant : 1 vers calendrier-tempo.fr/alertes (ancre : « alerte WhatsApp gratuite la veille »).
- Mention partenariale en bio auteur.

### 2.3 Recommandation finale et brief @copywriter

**Recommandation orchestrateur** :
- Activer les 2 articles (A2 + B3) **si et seulement si** :
  1. Selectra accepte le schéma « 1 article = 1 lien dofollow en bio auteur, vers home ou page produit calendrier-tempo.fr », pas plus.
  2. Selectra accepte que la rédaction est sous notre contrôle éditorial total (pas de validation préalable de leur part sur le fond, juste vérif technique format).
  3. Selectra renonce à la demande d'insertion réciproque sur calendrier-tempo.fr **ou** accepte le Volet B (lien sponsored / nofollow sur article blog, pas sur home).

- **Si Selectra refuse ces conditions** : décliner poliment. La crédibilité de calendrier-tempo.fr comme service indépendant vaut plus que 4 backlinks dont la valeur SEO est incertaine.

- **Ne PAS activer @copywriter tant que** : conditions ci-dessus non agréées par Selectra par écrit.

---

## Email à envoyer à Selectra (décision porteur 2026-05-26 : GO sur insertion home + A2 + B3)

> Objet : Re: échange de visibilité calendrier-tempo.fr × Selectra
>
> Bonjour,
>
> Merci pour la proposition, qui me convient.
>
> ### Votre insertion est en place sur calendrier-tempo.fr
>
> Je l'ai ajoutée à la FAQ « Combien coûte réellement un jour rouge ? » de la page d'accueil, à la suite du paragraphe que vous suggériez. Formulation publiée :
>
> > Vous pouvez consulter cette page de Selectra qui donne la grille tarifaire en fonction de votre abonnement ou votre contrat EDF.
>
> L'ancre « cette page de Selectra » renvoie vers https://selectra.info/energie/fournisseurs/edf/tempo#tarifs, en lien éditorial standard (format symétrique à celui que vous appliquez sur votre lien retour vers calendrier-tempo.fr). Vous pouvez vérifier dès à présent.
>
> ### Articles invités — deux angles détaillés
>
> De mon côté, je vous propose deux articles construits autour de la donnée que je suis seul à publier en France : les **prédictions J+2 à J+5** des couleurs Tempo EDF (algorithme combinant météo 9 villes Météo France, consommation RTE et modèle de machine learning, mis à jour chaque jour).
>
> ---
>
> **Article 1 — pour `energie-reduc.com`**
>
> **Titre proposé :** *Anticiper les jours rouges Tempo EDF : combien ça rapporte vraiment sur une saison ?*
>
> **Promesse au lecteur :** un calcul honnête et chiffré du gain annuel possible quand on anticipe correctement les 22 jours rouges d'une saison Tempo, comparé à la situation « je les subis ». Le lecteur repart avec une fourchette d'économie réaliste calibrée sur son profil de foyer.
>
> **Pourquoi cet angle :** c'est la question qui précède l'action (« est-ce que ça vaut le coup d'investir du temps dans le suivi quotidien ? »), et c'est un sujet quasiment absent du web — la plupart des articles parlent de gestes, peu de ROI quantifié. Avec mes historiques de prédiction, je peux produire un chiffre crédible.
>
> **Format :**
> - 1 100-1 400 mots, 4-5 H2, ton pédagogique expert (vouvoiement), aligné sur votre éditorial « guides économies »
> - **1 tableau de simulation** : 3 profils foyer (par ex. T2 électrique, T4 électrique, T5 électrique + ECS) × gain annuel estimé sur 22 jours rouges
> - **FAQ 3 questions** calées sur les People Also Ask
> - Sources : grille tarifaire EDF officielle uniquement, fourchettes labellisées « estimation »
>
> **Insertion de calendrier-tempo.fr dans l'article (mécanique de notre lien retour) :**
> - **1 mention contextuelle dans le corps**, au moment où l'on évoque l'outil concret pour faire l'anticipation. Exemple de phrase : *« pour visualiser l'anticipation en pratique, calendrier-tempo.fr propose un calendrier avec prédictions J+2 à J+5, mis à jour chaque jour et gratuit. »*
>   - Ancre cliquable : « calendrier avec prédictions J+2 à J+5 »
>   - URL de destination : `https://calendrier-tempo.fr/calendrier`
> - **1 mention courte en bio auteur en fin d'article** : *« Article proposé par l'équipe de calendrier-tempo.fr, service gratuit d'anticipation des couleurs Tempo EDF. »*
>   - Ancre cliquable : « calendrier-tempo.fr »
>   - URL de destination : `https://calendrier-tempo.fr/`
>
> ---
>
> **Article 2 — pour `agence-energie.com`**
>
> **Titre proposé :** *Jour rouge Tempo : la check-list de la veille pour ne rien subir*
>
> **Promesse au lecteur :** un protocole horaire concret pour bien gérer la veille d'un jour rouge — savoir à 11h, programmer à 11h30, limiter à 17h, vérifier le lendemain. Le lecteur repart avec une routine opérationnelle reproductible.
>
> **Pourquoi cet angle :** EDF annonce J+1 vers 11h, et la fenêtre 11h-22h conditionne tout le confort financier du lendemain. Je date cette mécanique mieux que personne (et ma prédiction J+2 permet même de devancer EDF d'un jour). Format compatible avec votre éditorial FAQ / guide pratique.
>
> **Format :**
> - 1 000-1 300 mots, ton opérationnel (vouvoiement expert)
> - **Structure en check-list** sur 4 sections horaires : « 11h05 — vérifier l'annonce EDF », « 11h30 — programmer les équipements », « 17h-22h — limiter la consommation », « lendemain — vérifier l'effet sur la facture »
> - **1 encadré « le piège classique »** (l'erreur fréquente qu'on voit en pratique)
> - FAQ 2 questions
> - Sources : EDF officiel uniquement pour la fenêtre d'annonce et les tarifs
>
> **Insertion de calendrier-tempo.fr dans l'article (mécanique de notre lien retour) :**
> - **1 mention contextuelle dans le corps**, dans la section « 11h05 — vérifier l'annonce EDF ». Exemple de phrase : *« pour éviter d'avoir à vérifier vous-même chaque matin, calendrier-tempo.fr envoie une alerte WhatsApp gratuite la veille dès que J+1 est confirmé. »*
>   - Ancre cliquable : « alerte WhatsApp gratuite la veille »
>   - URL de destination : `https://calendrier-tempo.fr/alertes`
> - **1 mention courte en bio auteur en fin d'article** : *« Article proposé par l'équipe de calendrier-tempo.fr, service gratuit d'anticipation des couleurs Tempo EDF. »*
>   - Ancre cliquable : « calendrier-tempo.fr »
>   - URL de destination : `https://calendrier-tempo.fr/`
>
> ---
>
> ### Conditions de mon côté
>
> - Rédaction sous mon contrôle éditorial complet : vous validez le format (longueur, structure, métadonnées), pas le fond.
> - Ton neutre et factuel, sourcing EDF officiel uniquement, aucune mention d'un autre acteur du marché.
> - **2 liens retour par article** tels que décrits ci-dessus (1 contextuel à fort signal SEO + 1 mention en bio auteur). Si votre charte n'autorise qu'un seul lien, je privilégie le lien contextuel.
>
> ### Sur les liens retour côté Selectra
>
> Vous évoquiez aussi `observatoires.net` et `electricite.net` en plus des deux sites cibles. Quelle forme prendraient ces deux liens supplémentaires (mention éditoriale dans un article existant, insertion dans une ressource…) ? Cela m'aidera à calibrer l'ensemble du dispositif.
>
> ### Étapes suivantes
>
> Vous me confirmez les deux angles et les modalités d'insertion. Je rédige sous 1-2 semaines et vous transmets en relecture format (pas sur le fond), vous publiez avec les deux liens retour décrits ci-dessus. Pour l'instant on s'en tient à ces deux articles ; selon le retour de ce premier cycle, on évaluera la suite.
>
> Cordialement,
> [Votre nom]

---

## Brouillon initial (NO-GO insertion — archivé, non utilisé)

> Bonjour,
>
> Merci pour cette proposition d'échange. Après examen, voici ma position.
>
> Sur l'insertion proposée sur la page calendrier-tempo.fr : je préfère ne pas la retenir dans la formulation actuelle. Deux raisons principales. D'abord, la phrase suggérée renvoie vers une grille tarifaire à un moment où le lecteur consulte une prédiction de couleur — l'information n'est pas alignée avec ce qu'il cherche. Ensuite, calendrier-tempo.fr s'est construit sur un positionnement de service indépendant, et je veille à ce que les pages produit restent exemptes de liens commerciaux pour préserver cette indépendance.
>
> En revanche, je suis intéressé par les deux articles invités que vous mentionnez. Je peux vous proposer deux sujets concrets et utiles à vos audiences :
>
> 1. Pour energie-reduc.com : un article chiffré sur le ROI réel de l'anticipation des jours rouges Tempo sur une saison complète (titre travaillé : « Anticiper les jours rouges Tempo EDF : combien ça rapporte vraiment ? »).
> 2. Pour agence-energie.com : une check-list opérationnelle « la veille d'un jour rouge » (titre travaillé : « Jour rouge Tempo : la check-list de la veille pour ne rien subir »).
>
> Conditions de mon côté : rédaction sous mon contrôle éditorial, ton neutre et factuel, 1 lien retour en bio auteur vers calendrier-tempo.fr, ancre marque. Pas d'engagement de volume au-delà de ces deux articles à ce stade.
>
> Si une mention de Selectra a vraiment du sens à un endroit de calendrier-tempo.fr en dehors de la home, je peux étudier une insertion dans un article de blog dédié (ex. guide complet Tempo), en `rel="sponsored"` avec marquage transparent. Dites-moi si cette piste vous va.
>
> Cordialement,

---

## Annexes — Analyses détaillées

### Annexe A — Critères de vérification SEO à faire par le porteur

À effectuer avant envoi de la réponse :
1. WHOIS des 4 domaines + recherche `"Selectra" registered` → vérifier propriété commune.
2. DR/DA via Ahrefs Free Tool ou Moz pour chacun des 4 sites.
3. SimilarWeb : trafic mensuel estimé (free tier suffit pour ordre de grandeur).
4. Lecture rapide des 2 articles d'exemple `energie-reduc.com/economies/panneaux-solaires-reduire-facture-electrique` et `agence-energie.com/faq/prix-renovation-2026` → confirmer ton, audience, format.
5. Search `site:calendrier-tempo.fr "Selectra"` → vérifier qu'on n'a jamais mentionné cet acteur (cohérence règle « pas de mention nominative concurrent ») — note : ici Selectra n'est pas un concurrent direct (ils comparent fournisseurs, on prédit Tempo), donc la règle s'applique mais avec souplesse.

### Annexe B — Notes éthiques

Le pattern « insertion d'une phrase dans un article existant » est techniquement du native advertising dissimulé si pas marqué. Toute insertion validée doit comporter un marquage typographique distinct (italique, encadré « partenaire », ou mention de transparence). Ne PAS faire l'insertion en cas de doute — la crédibilité long terme de calendrier-tempo.fr vaut plus qu'une opération ponctuelle.

### Annexe C — Décisions structurantes pour la suite

- Si ce partenariat est mené à terme, prévoir une **policy partenariats** documentée pour les futurs cas (Selectra ne sera pas le dernier à proposer ce type de deal) : critères d'acceptation, rejet par défaut sur insertion home/calendrier, principe nofollow/sponsored par défaut, contrôle éditorial total sur tout article invité.
- Cette policy devrait être visible dans la page « Mentions légales » ou dans une page dédiée « Partenariats & transparence » — c'est un signal de crédibilité fort qui valorise l'indépendance auprès des lecteurs et de Google.

