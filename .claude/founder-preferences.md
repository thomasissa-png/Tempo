# Préférences fondateur — Thomas

Ce fichier est la source de vérité des préférences du fondateur, lue par tous les agents (protocole d'entrée standard). Il est alimenté par TOUS les projets (Gradient Agents, Sarani, Mandataire-Immo, etc.) et accessible dans chaque projet client via `.claude/founder-preferences.md` (copie posée par install.sh et rafraîchie par update.sh) et via l'URL GitHub raw.

**URL d'accès** : `https://raw.githubusercontent.com/thomasissa-png/Agent-Team/main/docs/founder-preferences.md`

---

## Stack par défaut (tous projets, sauf décision contraire dans project-context.md)

- **Hébergement : Cloudflare** (Workers + static assets, D1, R2, DNS), déployé depuis GitHub (branche `main`). Replit = legacy des projets existants uniquement.
- **Statistiques : Umami** (pas GA4). Events via le tracker Umami ; configuration à valider avec @legal pour la dispense de consentement.
- **VPS de Thomas (Caddy) disponible si besoin**, en renfort de Cloudflare et pas par défaut : processus longs, services auto-hébergés, crons lourds, base de données dédiée. Toute mise en place sur le VPS se documente dans le repo d'infra (crons synchronisés par script, pas de `crontab -e` à la main).
- **Auth : open source et possédée** (Better Auth pour les nouveaux projets, jamais un service payant par facilité).

## Préférences observées

- **Pas de tiret cadratin (—) dans le client-facing.** Thomas le perçoit comme une signature d'écriture IA qui nuit à la crédibilité de marque. Règle : aucun — dans le site, les landing pages, le copy, les emails, les posts et les livrables destinés aux clients. Le remplacer par une virgule, un deux-points, des parenthèses ou une phrase séparée selon le sens. Ne s'applique pas aux instructions internes (prompts, fichiers d'agents). Vaut pour tous les projets du fondateur. (S5, 2026-06-11)

| Date | Projet | Préférence | Contexte |
|---|---|---|---|
| 2026-03-26 | Agent-Team | Préfère une auth open source qu'il possède à Clerk (NextAuth.js à l'époque ; Better Auth pour les nouveaux projets depuis qu'Auth.js est en maintenance, 2026-09) — ownership total, gratuit, pas de vendor lock-in. Ne jamais choisir une techno par facilité de dev. | A insisté pour que le prompt Auth dise "ne JAMAIS choisir parce que plus rapide à coder" |
| 2026-03-26 | Agent-Team | Les P2 ne sont PAS optionnels — tout corriger, l'IA a un coût marginal quasi nul. La classification sert à ordonner, pas à filtrer. | A demandé "pourquoi est-ce que je ne voudrais pas corriger P2 ?" |
| 2026-03-26 | Agent-Team | Exige 9/10 minimum sur chaque livrable, pas de compromis. Itère jusqu'à atteindre le seuil. | A dit "@orchestrator je veux 9/10" sur le chemin logique des prompts |
| 2026-03-26 | Agent-Team | Le mode autopilot doit produire le MÊME résultat que les prompts lancés un par un. L'autopilot est un raccourci d'exécution, pas de qualité. | "ce qui m'intéresse c'est la création d'un projet de A à Z en utilisant le meilleur de tous nos agents" |
| 2026-03-26 | Agent-Team | Pas de concept "MVP minimal" — quand on code, on code la V1 complète. La seule raison d'exclure une feature : elle n'apporte pas de valeur au persona. JAMAIS "trop complexe" ou "pas le temps". | "autant coder jusqu'au bout, pas que MVP" |
| 2026-03-26 | Agent-Team | Calendrier éditorial perpétuel avec scheduling automatique illimité — pas de fin de cycle, régénération auto. | A demandé "est-ce qu'on inclut pas un plan éditorial avec scheduling à durée illimitée ?" |
| 2026-03-26 | Agent-Team | Anti-répétition obligatoire — ne jamais écrire deux fois sur le même sujet. Registre des articles publiés. | "est-ce qu'on tient bien compte des articles du passé ?" |
| 2026-03-26 | Agent-Team | Attention aux accents français — un livrable avec des accents manquants est inacceptable. | A demandé "une revue spécifique de tous les accents aussi" |
| 2026-03-26 | Agent-Team | Audit de tous les clics/interactions d'un site — pas juste visuellement, vérifier que chaque élément fait ce qu'il doit faire. | "un audit de tous les clics du site, que tout se passe bien" |
| 2026-03-26 | Agent-Team | PostgreSQL Replit perd ses données — protections de persistance obligatoires (migrate deploy, seed conditionnel, Replit Secrets). | "y a souvent des soucis avec PostgreSQL où il faut faire pas mal d'aller retour" |
| 2026-03-26 | Agent-Team | Veut que la clôture de session capture les learnings pour améliorer l'équipe — problèmes, insistances, requêtes, biais, préférences fondateur. | "résumer les learnings pour les donner à l'orchestrateur pour voir si on peut encore améliorer l'équipe" |
| 2026-05-06 | Agent-Team | **Stack par défaut futurs projets : GitHub + Cloudflare** (Workers ; détail dans « Stack par défaut » ci-dessus) plutôt que Replit. Raisons : déploiement automatisable par Claude (push direct, logs, rollback, DNS), coûts CDN edge quasi nuls, vélocité KPI North Star. **BDD tranchée en S3** : D1 (CRUD simple) ou Neon (Postgres). Replit reste fallback pour POC instantanés ou si besoin Postgres natif intégré. Migration progressive (DevRefs pilote, puis projet par projet). | "est-ce que ce serait pas plus simple de te donner la main sur github et cloudflare pour faciliter les déploiements" — peur explicite sur la BDD |
| 2026-03-25 | Agent-Team | Mindset IA obligatoire — pas de sprint-plan, pas de vélocité en jours/homme, parallélisation par défaut, MVP "complet et rapide". | A fait ajouter la règle n°5 dans CLAUDE.md |
| 2026-03-26 | ImmoCrew | Prix ronds obligatoires — pas de charm pricing "en 7" (497/197/97). Cohérence de marque "zéro bullshit" prime sur l'optimisation tarifaire. | A refusé le charm pricing, préfère 400/150/100 même si ça augmente le seuil de rentabilité |
| 2026-03-26 | ImmoCrew | Zéro concurrent nommé dans le contenu client — utiliser des catégories génériques. | A fait supprimer toutes les mentions de concurrents par nom sur le site |
| 2026-03-26 | ImmoCrew | Modal popup pour l'auth — pas de page pleine. Header + Footer visibles sur les pages auth. Sophie ne doit jamais perdre ses repères. | A insisté pour un modal au lieu d'une page dédiée |
| 2026-03-26 | ImmoCrew | Pas de duplication d'info — si un champ est rempli à l'inscription, l'onboarding le pré-remplit. Ne jamais redemander une information déjà fournie. | A refusé que l'onboarding redemande prénom/nom |
| 2026-03-26 | ImmoCrew | LinkedIn plutôt que questionnaire long — un lien LinkedIn est plus réaliste qu'un formulaire d'histoire personnelle. | "Sophie ne racontera pas son parcours entre deux visites" |
| 2026-03-26 | ImmoCrew | Admin valide avant automatisation complète — le flow de production reste déclenché manuellement tant que le pipeline n'est pas prouvé. | Auto-produce prêt mais désactivé, validation humaine d'abord |
| 2026-03-26 | ImmoCrew | Résiliation = perte d'accès aux livrables — les livrables sont liés à l'abonnement actif, pas acquis à vie. | Décision business : incentive au maintien de l'abonnement |
| 2026-03-26 | ImmoCrew | Valeurs business centralisées — jamais de prix hardcodé dans 15+ fichiers. Un fichier config unique (pricing.ts). | Changement de prix a nécessité une passe Grep sur tout le code |
| 2026-03-27 | Agent-Team | Les outputs générés par la plateforme doivent être au niveau des meilleurs du secteur — WebSearch les références avant de produire. | "je veux que la création utilise toujours les meilleures références possibles par rapport au marché" |
| 2026-03-27 | Agent-Team | Personas des clients de nos personas obligatoires — comprendre toute la chaîne de valeur (le mandataire ET son acheteur). | "je veux qu'il définisse non seulement les personas projet mais aussi les personas des clients de nos personas" |
| 2026-03-27 | Agent-Team | [REMPLACÉ S4 : testeurs optionnels, @reviewer + G_PROOF par défaut] Agents testeurs sur TOUS les angles : copy, design, contenu, pricing, conviction, recommandation, fidélisation. Pas de revue partielle. | "je veux qu'ils soient impliqués sur toutes les étapes, sur tous les angles" |
| 2026-03-27 | Agent-Team | Les alertes de session ne doivent pas être frustantes — seule ROUGE conservée, pas de JAUNE qui interrompt. | "l'alerte arrive très tôt, c'est très frustrant et oblige à changer de session très souvent" |
| 2026-03-28 | Sarani S6 | Prompt engineering = livrable à part entière, avant le code. Pas un détail d'implémentation — un actif stratégique. | "le meilleur prompt du monde possible avant toute implémentation" |
| 2026-03-28 | Sarani S6 | Documents client-facing (devis, proposals, PDF) = même niveau de design que le site web. Un devis "simpliste" pour une agence de designers est rédhibitoire. | Thomas juge "simpliste" un devis sans branding |
| 2026-03-28 | Sarani S7 | Proposals en lien web, pas PDF, pour tout ce qui est interactif. PDF réservé aux devis/factures. | "Option 1 = lien web magnifique" |
| 2026-03-28 | Sarani S7 | Flux progressifs avec points de validation : brief → storyboard → final. Pas de direct brief → livrable. | Thomas préfère les flux avec validation intermédiaire |
| 2026-03-28 | Sarani S7 | 7 critères visuels pour tout livrable visuel : PRO, BEAU, BRAND-ALIGNED, MÊME IDENTITÉ QUE LE SITE, PROPRE, ALIGNÉ, AÉRÉ. | Grille de validation visuelle Thomas, chaque critère doit passer |
| 2026-03-28 | Sarani S7 | Colonnes monétaires alignées à droite dans les tableaux — standard comptable non négociable. | Thomas a demandé 2+ fois |
| 2026-03-28 | Sarani S6 | Labels texte > icônes seules pour les actions back-office. Les icônes seules sont incompréhensibles. | Thomas a trouvé les icônes d'action incompréhensibles (3+ demandes) |
| 2026-03-28 | Mandataire S6 | Dashboard = coaching personnalisé, pas bibliothèque de fichiers. La valeur est le plan d'action contextualisé. | "le dashboard doit commencer par un résumé personnalisé puis un plan d'action structuré" |
| 2026-03-28 | Mandataire S6 | 10 scénarios d'usage concrets par écran pour tester les personas — pas des critères abstraits. | "Sophie doit simuler 10 scénarios concrets" |
| 2026-03-28 | Mandataire S6 | Screenshots obligatoires pour validation UI — le code "qui marche en théorie" ne suffit pas. | Thomas teste en prod sur mobile, envoie des screenshots |
| 2026-03-28 | Archi S26c | Persistance > vitesse pour les données critiques. 2-3s de latence acceptable, donnée perdue inacceptable. | Photos galerie perdues = critère n°1 d'échec |
| 2026-03-28 | Archi S26c | Formats standard du secteur > créativité pour les livrables B2B. Crédibilité > originalité. | "format portail immo standard (T3 60 m²) plutôt que titres créatifs" |
| 2026-03-28 | Archi S26c | Zéro écran cassé ou placeholder gris — chaque composant a un loading + fallback propre. | "un utilisateur ne doit jamais voir un écran cassé" |
| 2026-03-28 | Archi S26c | Assets critiques homepage dans le repo git, pas en Object Storage. Zéro dépendance runtime pour le hero. | Images hero disparaissaient à chaque deploy (5+ fois) |
| 2026-03-28 | Archi S26c | Modals mobile = pattern bottom sheet (items-end, 100dvh, safe-area). items-center + overflow cassé sur iOS Safari. | 4+ demandes de fix modal auth mobile |
| 2026-03-28 | Archi S27 | Valider les clés API contre les placeholders, pas juste truthy. `key !== "..."` obligatoire. | Placeholder truthy = 8s timeout avant fallback |
| 2026-03-28 | Archi S26c | Replit autoscale : JAMAIS de fire-and-forget après réponse HTTP. Tout save critique doit être await avant return. | Photos et logs perdus silencieusement |
| 2026-03-28 | Archi S27b | Zéro donnée inventée pour les benchmarks — mesurer sur exemples réels, jamais "à sec". | "assure-toi que ce soit des exemples réels et pas du vent" |
| 2026-03-28 | Archi S26c | Fondateur teste en production sur mobile — les screenshots sont le critère de vérité, pas le code. QA doit simuler le parcours mobile réel. | Envoie des screenshots à chaque bug |
| 2026-03-28 | Sarani S8 | **Conviction-first, not conversion-first** — chaque page suit l'INTENTION du visiteur. CTAs commerciaux en fin de parcours, pas en hero (sauf pages d'action). | About restructurée, Services H1 reformulé, Blog CTA adouci |
| 2026-03-28 | Sarani S8 | **H1 uniques par page** — si le H1 pourrait être sur une autre page du site, il est trop générique. | Services avait le même H1 que Homepage → reformulé |
| 2026-03-28 | Sarani S9 | **Vérifier chaque changement après application** — ne JAMAIS dire "fait" sans Grep/Read de confirmation. Si demandé 2+ fois = bug de process. | Thomas a demandé 4+ fois la même correction |
| 2026-04-02 | Agent-Team | **@elon : ne jamais recommander "lancer un projet"** — Thomas a DÉJÀ des projets en cours (Sarani, Mandataire-Immo, Versiroom). @elon doit auditer le framework et les projets existants, pas répéter qu'il faut en lancer un nouveau. Les recommandations doivent porter sur l'amélioration de ce qui existe, pas sur l'inaction supposée. | "ça sert à rien de répéter sans cesse de lancer de nouveaux projets, ce n'est pas ce que j'attends de lui" |
| 2026-04-07 | ISSA Capital | **Vitrine ≠ Funnel — question à trancher en Phase 0** sur tout site institutionnel / family office / brand showcase. En mode vitrine : pas de CTA agressif, pas de AARRR, gates testeur adaptées (Respect inspiré, Identité lisible, Mémorabilité). | "On est pas là pour plaire au prospect. On est là pour avoir une belle vitrine." |
| 2026-03-28 | Mandataire S7 | **Zéro fausse promesse** — ne jamais écrire un texte promettant une feature non implémentée. Reformuler honnêtement. | "on récupère tout automatiquement" mais scraping inexistant |
| 2026-03-28 | Mandataire S7 | **Agents persona calibrés sur la VALEUR** — un testeur qui valide le code mais pas la valeur perçue est inutile. Score 9/10→6/10 après recalibration. Learning le plus impactant cross-projets. | @mandataire validait des écrans que Thomas jugeait inacceptables |
| 2026-03-28 | Archi S28 | **Acheter = achat DIRECT** — clic→auth si nécessaire→Stripe. Zéro page intermédiaire, zéro checkbox rétractation. | "Pourquoi ça m'amène sur une autre page pricing ?" |
| 2026-03-28 | Archi S28 | **Boutons = état réel contextuel** — "Voir" si existe, "Générer" si pas encore, "Regénérer" si déjà fait. Jamais de lien vers liste globale depuis une fiche. | "Voir les dossiers" renvoyait vers la liste globale |
| 2026-03-28 | Archi S28 | **Backoffice = même qualité que le site** — même design system, même standard visuel. Ce n'est pas un outil interne moche. | "que ce soit propre comme le reste du site" |
| 2026-03-28 | Sarani S7 | **7 critères visuels** — PRO, BEAU, BRAND-ALIGNED, MÊME IDENTITÉ, PROPRE, ALIGNÉ, AÉRÉ. Chaque critère 10/10. | Grille de validation visuelle Thomas |
| 2026-03-28 | Archi S28 | **Parcours d'achat testé end-to-end** — CTA→auth→checkout Stripe→retour pour CHAQUE persona AVANT déploiement. Backend/frontend pricing synchronisés. | Parcours d'achat jamais testé, prix désynchronisés |
| 2026-03-28 | Mandataire S6 | **Bugs identifiés = corrigés immédiatement** — quand un agent identifie un bug, le corriger sans demander confirmation. La perfection est le standard. | Thomas : "je ne suis pas là pour tester" — frustration 5+ fois |
| 2026-05-28 | Agent-Team S4 | **Reformuler 2+ fois sa demande = signal d'échec d'écoute (P0)**. Quand Thomas dit "encore une fois", "je veux juste", "tu n'écoutes pas" : STOP toute cérémonie/tableau/A-B-C, relire son brief littéralement, exécuter au défaut évident, et auto-diagnostiquer franchement le pattern manqué. JAMAIS reformuler avec un nouveau livrable cosmétique. | "Donc encore une fois, que @ia... me fasse le plan d'amélioration pour qu'on arrête ça" — 3 reformulations cette session |
| 2026-05-28 | Agent-Team S4 | **"Fais travailler l'équipe" = orchestration parallèle multi-agents avec perspectives indépendantes**, pas séquentiel mono-agent. Lancer 3-4 agents (@elon first principles + @ia technique + @qa critères + autre) en parallèle → 1 agent synthèse → @reviewer notation /10 → itération diff exact (max 3 boucles). Pas @ia tout seul qui refait un audit académique. | "fais travailler l'équipe pour que j'aie un vrai plan d'un système fonctionnel qui marche pour moi, et itère-le jusqu'à 10/10" |
| 2026-05-28 | Agent-Team S4 | **Cure proportionnelle au diagnostic (P0)**. Si diagnostic = "framework bloat / rituel sature pensée" et le framework fait 2000+ lignes, la cure DOIT être à l'échelle (>-200L net), pas cosmétique (-17L). Sinon le plan EST le pattern qu'il dénonce. Test ratio : (lignes retirées / lignes diagnostiquées comme bloat) ≥ 30%. | "Si tu veux vraiment 'comme avant', il faut un plan de réduction, pas un plan d'amélioration" — Cure S4 = -843L (-38%) |

---

## Anti-patterns de Thomas (ce qu'il refuse)

1. Choix technique par facilité de dev ("c'est plus rapide avec Clerk")
2. Scope réduit artificiellement ("pour un MVP, limiter à 5 features")
3. P2 traités comme optionnels ("veux-tu corriger les P2 ?")
4. Formulations vides / théâtre ("il est important de noter que...")
5. Accents manquants dans le contenu français
6. Demander permission au lieu de faire ("veux-tu que je..." → juste faire)
7. Vendor lock-in sans justification explicite
8. Incohérence de nommage entre livrables
9. Contenu qui se répète (même sujet, même angle)
10. Autopilot qui produit moins bien que le mode manuel
11. Charm pricing "en 7" (incohérent avec positionnement transparent)
12. Valeurs business hardcodées dans les composants (prix, emails, URLs)
13. Pages auth sans Header/Footer (perte de repères de navigation)
14. Redemander une info déjà fournie (duplication formulaires)
15. Fire-and-forget après réponse HTTP sur Replit (données perdues silencieusement)
16. Icônes seules sans labels texte dans le back-office
17. Livrables B2B avec format "créatif" au lieu du format standard du secteur
18. Écran cassé ou placeholder gris visible par l'utilisateur
19. Assets critiques en Object Storage au lieu de git
20. Clé API placeholder évaluée truthy (`sk_test_xxx` en production)
21. PDF "simpliste" pour un document client-facing — doit être au niveau du site
22. Audit "en théorie" sans screenshots réels comme preuve
23. Copy qui promet une feature non implémentée (fausse promesse)
24. Agent persona qui note le code au lieu de la valeur perçue (validateur déguisé)
25. Galerie avec images placeholder identiques entre styles différents
26. Témoignages fictifs avec noms de personas du projet
27. Migration modèle IA sans lire la doc API ni tester
28. Correction de prompt sur un builder sans propager aux autres
29. Dire "fait" sans vérification (Grep/Read) après un changement
30. Laisser un bug identifié "en attente" au lieu de le corriger immédiatement
31. Reformulation cérémoniale après plainte "tu réfléchis mal / vérifies rien" (refaire un tableau au lieu de réécouter et auto-diagnostiquer)
32. Cure cosmétique (-X0L) sur diagnostic structurel (framework bloat -X00L attendu)

---

## Style de review de Thomas

- Exige des corrections EXACTES (texte à remplacer), pas des observations vagues
- Vérifie via 100% gates PASS — ne valide pas en dessous
- Vérifie la cohérence inter-livrables (Grep cross-fichiers)
- Pose des questions directes quand quelque chose manque
- Fait confiance aux agents mais vérifie les résultats

---

*Dernière mise à jour : 2026-05-28 — S4 : reformulation 2+ fois = échec d'écoute, orchestration parallèle multi-agents pour "fais travailler l'équipe", cure proportionnelle au diagnostic, +2 anti-patterns. URL accès master→main.*
