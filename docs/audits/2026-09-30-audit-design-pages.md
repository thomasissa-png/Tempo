# Audit design des pages publiques : AVANT (Replit) vs APRÈS (Cloudflare)

Date : 2026-09-30. Auteur : @design. Périmètre : rendu visuel uniquement (la hiérarchie et le placement sont traités par @ux en parallèle). Aucun code modifié : ce document est la liste de travail de @fullstack.

Sources visuelles lues : `scratchpad/captures/avant/*` (prod actuelle), `apres_verif/*` (fiables), `apres/*` (mobile 390 px et pages sans version verif).
Sources code : `static/css/style.css` (2 704 lignes), `templates/dashboard.html`, autres templates cités page par page.

> Statut : SQUELETTE en cours de remplissage. Les sections marquées `[À COMPLÉTER]` sont ajoutées au fil de l'audit.

## 0. Diagnostic racine (à lire avant les P0)

1. **Aucune règle globale pour les liens.** `style.css` ne contient aucun sélecteur `a { ... }` : chaque composant (`.breadcrumb a`, `.legal-text a`, `.blog-nav-articles a`...) restyle ses liens un par un. Tout lien placé dans un composant neuf (`.today-answer`, `.page-content`, FAQ, cartes des nouvelles pages) retombe sur le style navigateur : bleu `#0000EE` souligné, violet une fois visité. C'est exactement ce que le fondateur voit sous le H1.
2. **Les pages ajoutées les 29-30/09 réutilisent peu les composants de l'AVANT.** Elles s'appuient sur `.page-content` (texte brut, max 860 px) au lieu des cartes (`.step-card`, `.legal-section`, `.subscribe-cta-card`, `.counter-card`) qui font le langage visuel du site.
3. **Variables CSS non définies** utilisées dans `style.css` : `--primary` (`.loading-spinner`, donc spinner sans couleur), `--card-bg` et `--text-primary` (`.maintenance-banner`), `--bg-secondary` (fallback `#f5f5f5` hors palette dans `.last-update-bar`). Le design system dit « si un token n'est pas dans le système, il n'existe pas » : ce sont des bugs.
4. **Palette parallèle hors tokens** : le fichier contient une seconde palette Material (`#2E7D32`, `#E8F5E9`, `#E65100`, `#FFF3E0`, `#E3F2FD`, `#FFB74D`, `#EF9A9A`) à côté des tokens `--vert`, `--vert-bg`, etc. 

## 1. Résumé des P0

`[À COMPLÉTER APRÈS AUDIT PAGE PAR PAGE]`

## 2. Pages

`[À COMPLÉTER]`
