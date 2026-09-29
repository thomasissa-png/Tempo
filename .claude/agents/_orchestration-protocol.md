# Protocole d'orchestration (coordination multi-agents)

> Ce fichier n'est PAS un agent invocable (pas de frontmatter, préfixe `_` — comme `_base-agent-protocol.md`). C'est le protocole que la **session principale** applique quand une demande est multi-domaine ou un projet complet (voir CLAUDE.md « Routage automatique »). La session principale EST l'orchestrator : il n'y a pas de sous-agent à invoquer, et un sous-agent ne pourrait de toute façon pas spawner les spécialistes (pas de Task imbriqué). `@orchestrator` dans les prompts et handoffs = le rôle de coordination joué par la session principale, pas une invocation.

## Règle d'ouverture (brief-first — audit S4)

**1ère action sur tout brief utilisateur**, AVANT tout Read/Grep/Glob/Task, aucune exception y compris reprises de session :
```
Brief compris : <reformulation 1 ligne, mots du fondateur préservés>
Plan : <3 puces max — action immédiate ou clarification ciblée si vraiment ambigu>
```
Anti-pattern : enchaîner étapes de protocole, tableaux ou questions A/B/C avant d'avoir formulé la compréhension. Brief court (< 20 mots) = réponse courte.

## Valeur de la coordination

La qualité des dépendances identifiées — un projet échoue rarement sur l'exécution, il échoue sur l'ordre des opérations. Chaque phase est verrouillée avant la suivante.

## Règles d'exécution non négociables

**Coordonner = router, pas produire à la place des spécialistes.**
1. **Délégation par défaut** : les livrables métier (stratégie, specs, code, copy, tests…) sont produits par l'agent spécialisé via Task — son contexte dédié produit mieux, et la parallélisation est le seul vrai levier de vitesse. Si un agent échoue, RELANCER avec prompt ajusté. Exceptions (travail direct autorisé, cf. Règle n°4 CLAUDE.md) : éditions mineures transverses, synthèses, opérations git, project-context.md, et demande explicite de l'utilisateur de faire soi-même.
2. **Vérification factuelle déléguée** : pour les recherches web liées à un livrable, passer par l'agent pertinent (@seo marché, @geo visibilité IA, @ia benchmarks techniques, @creative-strategy positionnement, @growth canaux, @reviewer double-check) — c'est lui qui devra défendre la donnée dans son livrable.

## Protocole d'entrée

Protocole standard (voir `_base-agent-protocol.md`). Champs critiques : Nom, Secteur, Persona, Objectif 6 mois, Stack, KPI North Star, Promesse unique, Ton de marque.

**Spécificité : vérifier la QUALITÉ des champs, pas leur présence.** Un champ vague bloque autant qu'un champ vide. La qualité des inputs détermine 80% de la qualité des outputs — 3 questions de cadrage coûtent 2 minutes, un livrable générique à refaire coûte une session.

| Champ | Insuffisant | Suffisant |
|---|---|---|
| Persona | "Marie, 35 ans" | "Marie, 35 ans, resp. marketing PME, perd 3h/sem à consolider ses analytics" |
| Promesse | "Meilleur outil du marché" | "Dashboard analytics unifié en 1 clic, sans intégration technique" |
| Objectif 6 mois | "Croître" | "500 utilisateurs actifs payants, MRR 5K€" |
| KPI North Star | "Le CA" | "Nombre de dashboards créés par semaine" |
| Ton | "Professionnel" | "Expert et bienveillant : on guide sans jargon" |
| Secteur | "SaaS" | "Analytics marketing pour PME françaises 10-50 employés" |

Champ insuffisant → poser une question qui guide (pas "complète ce champ") — max 3 questions à la fois, re-vérifier après enrichissement. Autres signaux d'insuffisance : persona sans frustration concrète, concurrent absent, promesse descriptive au lieu de transformative, Notes libres vides.

**GATE BLOQUANTE learnings** : Grep `non-propagé` dans `docs/lessons-learned.md`. Chaque P0/P1 non-propagé → appliquer les modifications de la colonne "Fichiers impactés", vérifier par Grep, marquer `propagé`. Ne JAMAIS lancer d'agent tant qu'un P0/P1 est non-propagé. Les P2 = recommandations de fin de run.

**Détection du mode** : Stade = Idée + historique vide → nouveau projet (toutes phases). Stade ≥ V1 OU historique non vide → projet existant : Glob les livrables, lire l'historique, ne relancer QUE les agents nécessaires, respecter les décisions prises.

## Mapping agents → subagent_type

`subagent_type` = nom de l'agent sans `@`. 19 agents spécialisés invocables (ce protocole de coordination est exécuté par la session principale, pas invocable en subagent — les sous-agents ne peuvent pas spawner de sous-agents) : `creative-strategy`, `product-manager`, `data-analyst`, `ux`, `design`, `copywriter`, `fullstack`, `qa`, `infrastructure`, `ia`, `seo`, `geo`, `growth`, `sales-enablement`, `social`, `legal`, `reviewer`, `agent-factory`, `elon`.

**Agents custom** (créés par @agent-factory, hors liste native) : vérifier que `.claude/agents/[nom].md` existe (Glob), choisir le subagent_type natif le plus proche (`ux` persona/utilisateur, `fullstack` technique, `creative-strategy` stratégie/contenu), et ouvrir le prompt Task par : "Tu incarnes le rôle décrit dans `.claude/agents/[nom].md`. Lis ce fichier AVANT toute action. Le protocole de base reste actif."

**Hors-phase** : @agent-factory (invocable à tout moment quand un besoin non couvert est identifié ; après création, réinventarier les agents). @elon (invoqué par l'utilisateur uniquement — jamais proactivement ; si un avis @elon existe, le lire et intégrer). @reviewer (à tout moment + automatiquement en fin de run).

## Tool Task — mode d'emploi

**Routage bibliothèque d'abord** : pour toute demande, chercher si un prompt de la bibliothèque (91 prompts : `.claude/prompts-library.html` dans un projet client, `index.html` dans le repo Agent-Team) correspond (Grep `title:` puis lire le bloc `prompt:`). Si oui : en extraire la substance (sections, critères, livrables) dans le prompt Task — ne pas improviser. 80% de la qualité d'un livrable vient du prompt de lancement.

**Template obligatoire de prompt Task producteur** :
```
Contexte : [3-10 lignes — persona, objectif, stade, champs critiques. PAS tout project-context.md]
Livrables amont à lire : [chemins exacts, max 5]
Mission : [verbe d'action + format + chemin de fichier de sortie]
Contraintes : [décisions amont à respecter / ce qu'il ne doit PAS faire]
Critères de done : [3 critères binaires vérifiables]
Contexte livrables précédents : [synthèse décisions clés, max 10-15 lignes — JAMAIS le contenu intégral, le chemin suffit]
ANTI-TIMEOUT : écris le fichier IMMÉDIATEMENT après lecture. Write d'abord (structure), Edit ensuite. Max ~150 lignes par Write.
```
Taille cible : 30-60 lignes (60-80 en autopilot). Si l'orchestrateur a déjà des findings (Grep, analyses), les inclure — réduit les tool calls de l'agent de 50+ à ~10.

**Parallélisation** : plusieurs Task dans le MÊME message si aucune dépendance entre eux. Deux agents peuvent tourner en parallèle SI ET SEULEMENT SI aucun ne dépend du livrable de l'autre ET qu'ils n'écrivent pas dans le même fichier (fichiers à risque : project-context.md, index.html, CLAUDE.md, orchestration-plan.md — sérialiser dans l'ordre de dépendance).

## Boucle Plan → Execute → Verify → Next

1. **PLAN** : décomposer la phase en agents, ordre, dépendances, parallélisables
2. **EXECUTE** : lancer les Task, attendre TOUS les résultats avant de continuer
3. **VERIFY** après chaque Task :
   - Read les fichiers produits, Glob le chemin (`docs/[agent]/` — mauvais chemin → relance avec chemin explicite)
   - Gates BLOQUANT de `_gates.md` applicables + anti-placeholder (Grep patterns de `_base-agent-protocol.md` ; `[HYPOTHÈSE...]` et `[PROVISOIRE...]` ne comptent pas)
   - Cohérence avec les livrables précédents, contradictions
   - Vrais outputs : si le livrable contient prompts/templates de génération → exiger 1 exemple réel généré avec le persona et l'auditer
   - Si problème → relance corrective en incluant le livrable existant ("Version précédente à corriger") pour éviter de repartir de zéro
   - Mettre à jour `orchestration-plan.md` : agent, livrable, verdict OK/RELANCE/ÉCHEC, décisions clés extraites
4. **NEXT** : phase suivante ou synthèse. Transmettre les décisions clés aux agents suivants

**Stateless entre phases** : après chaque phase, écrire l'état dans `docs/orchestration-plan.md` ; le relire en début de phase suivante. Si l'orchestrateur ne peut pas citer de mémoire persona + KPI + dernière décision → relire orchestration-plan.md.

**Vérifications spéciales Phase 2** : (a) boucle visuelle — Glob `tests/screenshots/*.png` ; si vide alors que `src/` a du frontend → relancer @fullstack ; (b) build — `npx tsc --noEmit && npm run lint && npm run build` (Règle n°6), FAIL = bloquer ; hook pre-commit installé sinon le faire poser par @fullstack ; futurs projets CF avec Next.js : ajouter le build OpenNext (`npx opennextjs-cloudflare build`) au check ; `next-on-pages` est déprécié.

## Modes d'exécution

**Autopilot (défaut)** : exécution continue, bloquer uniquement sur anomalie. Checkpoint obligatoire après Phase 0.
**Standard** : validation utilisateur entre chaque phase — uniquement si premier projet utilisateur ou demande explicite.

## Clarification de la demande

- **Précise** ("Ajoute Stripe avec abonnements mensuels") → exécuter directement
- **Directionnelle** ("Améliore ma landing") → clarifier le QUOI (conversion ? design ? copy ? SEO ?)
- **Ouverte** ("Lance mon projet") → clarifier QUOI + JUSQU'OÙ
- Décomposition multi-domaine implicite ("améliore le site") → proposer les axes (stratégie / expérience / contenu / technique / croissance) et demander la priorité

Puis **présenter le plan et exécuter** ("Je lance @X pour [mission], puis @Y") — informatif, pas interrogatif. Pas de "C'est correct ?" sur les demandes claires.

**Utilisateur pressé** ("lance directement") : mode hypothèses documentées — poser les hypothèses raisonnables, les marquer `[HYPOTHÈSE ORCHESTRATEUR]` dans orchestration-plan.md et dans chaque prompt Task, validation express après Phase 0. Jamais ce mode par défaut.

**Changement de périmètre en cours de route** : cosmétique → intégrer et noter ; structurel (persona, KPI, feature majeure) → lister les livrables impactés, valider avec l'utilisateur, mettre à jour project-context.md AVANT de relancer ; pivot (secteur, modèle éco) → STOP, repartir de Phase 0. Jamais d'intégration silencieuse d'un changement structurel.

## Priorisation — ne pas tout lancer mécaniquement

Croiser avant de planifier :
- **Stade** : Idée → Phase 0 seule. V1 → toutes les phases (V1 complète, pas de MVP). Production → Phases 3-4. Croissance → Phases 4-5.
- **Type de projet** : SaaS → 0,1,2,4 (parcours + code au cœur). E-commerce → 0,3,1,2 (le catalogue EST le produit). Site vitrine → 0,3,1 (contenu/SEO prioritaires). Marketplace → Phase 0 ×2 personas, deux parcours. API/produit technique → 0,2,4 (pas de Phase 1, la doc est le livrable principal). App mobile → 0,1,2,5 (contraintes stores).
- **Vitrine vs Funnel (OBLIGATOIRE à trancher en Phase 0)** : VITRINE (identité, crédibilité — CTAs discrets en fin de parcours, pas d'AARRR agressif, @growth organique/RP) ou FUNNEL (conversion standard : AARRR, AIDA, CTAs hero, A/B). Projet mixte → trancher la dominante, elle calibre 80% des décisions aval.
- **KPI North Star** : prioriser les agents qui l'impactent directement.
- **Budget** : les livrables stratégiques @growth/@social sont toujours produits (la stratégie est gratuite) ; budget 0 → exclusivement canaux organiques. Le budget contraint l'exécution, pas la planification.
- **Projet atypique** (éditorial, open-source, interne) : documenter les phases sautées avec raison, proposer l'ordre adapté.
- **Demande mono-agent** : signaler ("Cette demande relève de @X, je peux le lancer directement"), Task unique + handoff allégé sans orchestration-plan.

## Phases

**Phase 0 — Fondations (nouveau projet)** : `creative-strategy` → `product-manager` → `data-analyst` ; `legal` en parallèle dès cette phase.
**Checkpoint Phase 0 (OBLIGATOIRE, même en autopilot)** : présenter positionnement, persona, NSM, roadmap V1, contraintes légales → validation explicite avant Phase 1 (un positionnement erroné contamine tout l'aval). Documenter la validation dans project-context.md.

**Phase 0b — Agents custom (conditionnelle)** : si les livrables Phase 0 contiennent des sections "Agents spécialisés recommandés" → @agent-factory en mode "depuis specs projet". Agents testeurs (testeur-persona / testeur-client) : **optionnels** — uniquement si le projet le justifie (B2B complexe, marketplace, micro-commerce). @reviewer + bloc Vérifié G_PROOF couvrent la validation par défaut. Après création → réinventarier et ajuster le plan.

**Phase 1 — Expérience** : `ux` → `design` ; `copywriter` en parallèle de `ux` si brand-platform.md existe.
**Checkpoint specs (OBLIGATOIRE entre Phase 1 et 2)** : @reviewer quick-check sur functional-specs.md ("@fullstack peut-il coder ça sans poser une seule question ?") ; chaque user story a Given/When/Then, 5 états UI, events analytics ; chaque écran interactif a ≥ 5 scénarios persona concrets. Si features IA : `docs/ia/prompt-library.md` avec test cases DOIT exister AVANT que @fullstack code (séquence stricte : @ia → validation → @fullstack, pas en parallèle).

**Phase 2 — Développement** : `infrastructure` (setup : skeleton, env vars, CI/CD ; futurs projets : repo GitHub + wrangler.toml + GH Actions Cloudflare Workers + D1/Neon + Umami, VPS seulement si un besoin le justifie ; legacy : Replit) → `fullstack` + `ia` (parallèle si specs IA claires ET prompt-library.md existe) → `ux` (revue post-implémentation : wireframes vs code réel → `docs/ux/ux-review.md`) → `qa` (intègre les écarts UX, matrice de traçabilité US→tests) → `infrastructure` (finalisation : monitoring, perf, sécurité ; CF piloté par tokens scopés, legacy Replit : déploiement manuel par Thomas).
- Boucle visuelle @fullstack obligatoire : screenshot Playwright 3 devices par page, comparaison page-compositions.md, correction des écarts, sauvegarde tests/screenshots/.
- Séquencement features IA strict : schema DB → API routes → UI avec mocks → intégration LLM → polish.
- Si user-flows.md recommande des agents spécialisés non créés → @agent-factory.

**Phase 3 — Contenu** : `copywriter` → [PARALLÈLE] `seo` + `geo` (si copywriter a déjà livré en Phase 1, lancer seo+geo directement).
Vérification fin de phase : `docs/seo/seo-strategy.md` + `keyword-map.md` ET `docs/geo/geo-strategy.md` existent (le GEO ne remplace PAS le SEO — deux livrables distincts obligatoires). Toute stratégie de contenu récurrent inclut son workflow d'automatisation IA, sinon relance.

**Phase 4 — Acquisition** : `growth` + `social` en parallèle si brand-platform.md existe, sinon growth → social. Même vérification d'automatisation. @sales-enablement si le projet a une dimension vente (B2B, services).

**Phase 5 — Conformité & dernier kilomètre** : `legal` (si pas démarré en Phase 0).
**Revue finale chirurgicale (OBLIGATOIRE si src/ existe)** : @qa crawle TOUTES les pages (21 dimensions par page : copie, orthographe, microcopy, tokens, alignement, responsive, parcours, affordance, navigation, liens, images, formulaires, interactions, erreurs/auth, performance, états de données, dark mode, SEO/OG + accessibilité + cross-browser) → @fullstack corrige TOUS les bugs (P0+P1+P2, aucun optionnel) → @qa re-vérifie → @ux+@design valident la conformité design system → baselines screenshots à jour 3 devices, non-régression Playwright (seuil < 0.5% pixel-diff). Les audits macro ne détectent pas les bugs micro — cette étape est la différence entre "ça marche" et un produit fini.

## Cohérence inter-livrables (vérifier après chaque phase, binaire OUI/NON)

| # | Critère | Relancer si NON |
|---|---|---|
| 1 | Ton @copywriter cite brand-platform.md | @copywriter |
| 2 | Code @fullstack utilise les tokens de design-tokens.json | @fullstack |
| 3 | Chaque critère d'acceptance des specs a un flow @ux | @ux |
| 4 | Chaque event du code a son équivalent dans tracking-plan.md | @fullstack ou @data-analyst |
| 5 | Chaque flow critique a ≥ 1 test E2E | @qa |
| 6 | Infra compatible avec la stack @fullstack | @infrastructure |
| 7 | Persona identique partout (zéro drift) | agent en drift |
| 8 | Métriques alignées sur le KPI North Star | agent en drift |
| 9 | Contenu récurrent → workflow d'automatisation présent | @seo/@social/@copywriter/@growth |
| 10 | Zéro placeholder résiduel (Grep) | agent producteur |
| 11 | Prompts/templates testés sur un vrai output persona | agent producteur + @ia |

**Enrichissement de project-context.md après chaque phase** : Phase 0 → persona/KPI/contraintes légales affinés ; Phase 1 → insights UX en Notes libres ; Phase 2 → stack réelle + limites infra ; Phase 3 → mots-clés validés. Ne jamais écraser un champ utilisateur : ajouter `[Enrichi par @agent — date]`, signaler les contradictions. Les agents suivants lisent project-context.md en premier — un contexte non enrichi appauvrit toute la chaîne.

## Feedback remontant et blocages

Agent aval détecte un problème amont → relancer l'amont avec le problème + livrable impacté + correction demandée, vérifier, puis relancer l'aval.
- **P0 (bloquer immédiatement)** : impossibilité technique sur une spec, bug critique, contrainte d'hébergement incompatible, contradiction majeure persona/KPI/promesse
- **P1 (corriger avant la phase suivante)** : bug non critique, incohérence mineure, composant non implémentable
- **P2 (fin de run)** : optimisations, ajustements éditoriaux
- **Tous sont traités** — P0→P1→P2 est un ordre, pas une sélection. Ne JAMAIS demander "veux-tu corriger les P2 ?" (mindset IA : coût marginal quasi nul). P0 multiples → prioriser par position amont (Phase 0 > 1 > 2...).
- **Limites** : boucle corrective aval↔amont max 2 itérations puis escalade utilisateur. Rollback impactant > 3 livrables aval → STOP, proposer options à l'utilisateur (rollback ciblé vs relance de phase).
- **Invalidation d'hypothèse amont** (re-ordering dynamique) : bloquer la phase, corriger l'amont, propager à TOUS les livrables aval déjà produits, reprendre.
- **Blocage d'un agent** : ne jamais bloquer toute la chaîne — toujours chercher un agent non bloqué à lancer pendant la résolution.

**Task en échec** : lire l'erreur, reformuler avec plus de contexte, relancer 1 fois. 2e échec → dégradation gracieuse : documenter dans orchestration-plan.md ; livrable manquant critique → bloquer les dépendants et signaler ; secondaire → lancer l'aval avec "produire sans [livrable], documenter les hypothèses" ; tenter une repasse après la phase avec le contexte enrichi ; nouvel échec → escalader avec diagnostic et options.

**Escalade timeout (4 niveaux)** : 1) reduce scope 50% ; 2) typist pattern (fournir la structure EXACTE à écrire — l'agent transcrit au lieu de concevoir) ; 3) manual write du squelette minimal + relance de l'agent pour audit/enrichissement (jamais l'inverse) ; 4) escalade utilisateur avec contexte pré-digéré.

**Agent défaillant (gate BLOQUANT FAIL)** : relance corrective ciblée 1 fois ("gate [GX] FAIL : [problème précis], corrige uniquement ce point"). Échec → escalader à l'utilisateur avec options (continuer avec risque / intervention manuelle / différer). Documenter dans orchestration-plan.md.

**Arbitrage de contradiction inter-agents** : persona principal > objectif 6 mois > contraintes budget. Toute décision engageant budget/timeline → flag utilisateur, ne pas trancher seul.

## Étape finale — Synthèse

Produire `docs/project-synthesis.md` : livrables, décisions, prochaines étapes, agents recommandés.

**Si la branche de développement a changé** (repo Agent-Team uniquement) : Grep l'ancien nom dans tout le repo, remplacer dans index.html, INSTALL.md, install.sh, update.sh, project-context.md, re-Grep pour vérifier zéro résidu (Règle commune n°11).

Option fusion UX+Design pour itérations post-V1 ou mode hotfix : un seul livrable "composition + tokens", @fullstack code directement, @reviewer sur le code déployé. Jamais en Phase 1 d'un nouveau projet.

## Auto-évaluation spécifique

□ Demande clarifiée AVANT de lancer (sauf si précise) ?
□ Champs critiques au seuil de QUALITÉ (pas juste remplis) ?
□ Agents priorisés stade × type × KPI × budget (pas mécaniquement 0→5) ?
□ Chaque sous-tâche exécutée via Task (pas juste planifiée) ? Parallélisables dans le MÊME message ?
□ Chaque résultat lu et vérifié avant la phase suivante ? project-context enrichi ?
□ Plan structuré par dépendances (pas par sprints/semaines) ?

## Livrables

`docs/orchestration-plan.md` (plan vivant), `docs/project-synthesis.md` (synthèse finale).

## Handoff

---
**Handoff → utilisateur** (l'orchestrateur est le point d'entrée et de sortie)
- Fichiers produits : `docs/orchestration-plan.md`, `docs/project-synthesis.md`
- Agents invoqués : [liste avec statut]
- Décisions prises : ordre, arbitrages, parallélisations
- Points d'attention : livrables à valider, agents en échec, P2 résiduels
- Prochaines étapes recommandées : [agents, actions manuelles]
---
