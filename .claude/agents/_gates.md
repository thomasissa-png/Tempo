# Gates binaires — Référence qualité

Source de vérité : 8 gates conservés + G_PROOF bloquant.
Consommateurs : @reviewer (exécute), @orchestrator (vérifie BLOQUANT après chaque livrable).

## Les 9 gates (PASS/FAIL)

| # | Gate | Classe | Vérification |
|---|---|---|---|
| G1 | Toutes les sections du template présentes (0 TODO) | BLOQUANT | Grep `[TODO]`, `[À REMPLIR]`, sections < 2 lignes |
| G3 | Bloc Handoff structuré présent | BLOQUANT | Grep `Handoff` |
| G5 | Persona identique à project-context.md | BLOQUANT | Grep nom persona, adresser frustrations/objections |
| G7 | 0 contradiction avec livrables amont | BLOQUANT | Read livrables amont, comparer décisions clés |
| G12 | Implémentable sans question | BLOQUANT | Verbe d'action + objet + inputs/outputs + critère de done |
| G13 | 0 donnée inventée | BLOQUANT | Grep chiffres sans source |
| G15 | 0 placeholder résiduel | BLOQUANT | Grep `[À REMPLIR`, `[À COMPLÉTER`, `[PLACEHOLDER`, `[TODO`, `[NOM`, `[EXEMPLE`, `[XX`, `[VOTRE`, `[INSÉRER`, `[REMPLACER` |
| G17 | Pas copiable pour un concurrent (test d'inversion) | BLOQUANT | Si > 50% applicable sans modif → FAIL |
| **G_PROOF** | **Bloc `Vérifié :` présent et reproductible** | **BLOQUANT** | **Grep `Vérifié :` + `Read <chemin>` ou `Grep <pattern>` ou `Bash <cmd>` + 3 lignes d'output max. Sinon NO-GO.** |

## Verdicts

- **GO** : 9/9 PASS
- **NO-GO** : >= 1 FAIL → relance immédiate

Tous les gates sont BLOQUANT. Un seul standard, une seule barre.
