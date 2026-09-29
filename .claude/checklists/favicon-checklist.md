# Favicon Checklist universelle — Gradient Agents

Source de vérité cross-projets. Référencée par `@design`, `@fullstack`, `@qa`. Verdict via gate **G31 Favicon Coverage** (`_gates.md`).

Tous les fichiers vont dans `/public/` (Next.js, Vite) ou `/static/` selon le framework. Adapter chemins si custom.

**Obsolètes 2026 (NE PAS générer)** : `mstile-*.png`, `browserconfig.xml`, `safari-pinned-tab.svg`. Microsoft a retiré le support des Tiles (Windows 10 EOL), Apple recommande désormais `apple-touch-icon` standard.

---

## 1. Tableau complet (12 items obligatoires)

| # | Icône | Dimension | Format | Chemin | Balise HTML / config | Plateforme cible |
|---|---|---|---|---|---|---|
| 1 | favicon.ico (legacy) | 16×16 + 32×32 + 48×48 multi-res | ICO | `/favicon.ico` | `<link rel="icon" href="/favicon.ico" sizes="any">` | IE, legacy browsers, Bing fallback |
| 2 | favicon SVG (moderne) | vectoriel | SVG | `/favicon.svg` | `<link rel="icon" type="image/svg+xml" href="/favicon.svg">` | Chrome, Firefox, Safari, Edge modern |
| 3 | Favicon 16×16 | 16×16 | PNG | `/favicon-16x16.png` | `<link rel="icon" type="image/png" sizes="16x16" href="/favicon-16x16.png">` | Browser tabs |
| 4 | Favicon 32×32 | 32×32 | PNG | `/favicon-32x32.png` | `<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png">` | Browser tabs retina |
| 5 | Favicon 48×48 | 48×48 | PNG | `/favicon-48x48.png` | `<link rel="icon" type="image/png" sizes="48x48" href="/favicon-48x48.png">` | **Google SERP (minimum requis)** + Bing |
| 6 | Favicon 96×96 | 96×96 | PNG | `/favicon-96x96.png` | `<link rel="icon" type="image/png" sizes="96x96" href="/favicon-96x96.png">` | Google Desktop shortcuts |
| 7 | Apple touch icon | 180×180 | PNG (sans transparence) | `/apple-touch-icon.png` | `<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">` | iOS home screen + macOS Safari pinned (depuis Big Sur) |
| 8 | Android Chrome 192 | 192×192 | PNG | `/android-chrome-192x192.png` | Déclaré dans `site.webmanifest` `icons[]` | Android home screen |
| 9 | Android Chrome 512 | 512×512 | PNG | `/android-chrome-512x512.png` | Déclaré dans `site.webmanifest` `icons[]` | Android splash screen / PWA |
| 10 | Maskable icon | 512×512 (safe zone 409×409 centrée) | PNG | `/maskable-icon-512.png` | `site.webmanifest` `icons[]` avec `"purpose": "maskable"` | Android adaptive icons |
| 11 | Web manifest | JSON | JSON | `/site.webmanifest` (ou `/manifest.json`) | `<link rel="manifest" href="/site.webmanifest">` | PWA / Android |
| 12 | OG image (social) | 1200×630 | PNG/JPG | `/og-image.png` | `<meta property="og:image" content="/og-image.png">` + `<meta name="twitter:image" content="/og-image.png">` + `<meta name="twitter:card" content="summary_large_image">` | Facebook, LinkedIn, Twitter, Slack preview |

### Meta complémentaires (obligatoires aussi)

| # | Meta | Valeur | Balise HTML |
|---|---|---|---|
| M1 | Theme color | hex (palette projet) | `<meta name="theme-color" content="#HEX">` |
| M2 | OG title + description | par page (cf. SEO) | `<meta property="og:title" content="...">` + `<meta property="og:description" content="...">` |

---

## 2. Fichier site.webmanifest minimal (à adapter)

```json
{
  "name": "[Nom du projet]",
  "short_name": "[NomCourt]",
  "icons": [
    { "src": "/android-chrome-192x192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/android-chrome-512x512.png", "sizes": "512x512", "type": "image/png" },
    { "src": "/maskable-icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ],
  "theme_color": "#HEX",
  "background_color": "#HEX",
  "display": "standalone",
  "start_url": "/"
}
```

---

## 3. Vérification automatisable (script bash)

```bash
#!/bin/bash
# Vérifie que les 12 fichiers favicon sont présents
PUB="${1:-public}"
MISS=0
for f in favicon.ico favicon.svg favicon-16x16.png favicon-32x32.png favicon-48x48.png favicon-96x96.png apple-touch-icon.png android-chrome-192x192.png android-chrome-512x512.png maskable-icon-512.png site.webmanifest og-image.png; do
  [ -f "$PUB/$f" ] || { echo "MANQUE: $PUB/$f"; MISS=$((MISS+1)); }
done

# Vérifie balises HTML head (adapter chemin index.html ou app/layout.tsx)
HTML="${2:-public/index.html}"
for tag in 'rel="icon"' 'apple-touch-icon' 'theme-color' 'manifest' 'og:image' 'twitter:image' 'twitter:card'; do
  grep -q "$tag" "$HTML" || { echo "BALISE MANQUE: $tag dans $HTML"; MISS=$((MISS+1)); }
done

[ $MISS -eq 0 ] && echo "OK Favicon coverage 12/12 PASS" || echo "FAIL Favicon coverage : $MISS items manquants"
```

---

## 4. Génération automatique recommandée

Outils pour générer les 12 fichiers à partir d'un seul SVG source :
- **realfavicongenerator.net** (gratuit, web) — recommandé, génère tous les formats + balises (décocher Microsoft Tiles + Safari Pinned Tab pour matcher cette checklist 2026)
- **favicon-generator npm** — pour CI/CD
- **sharp + svg2png** (Node.js) — pour intégration custom

Source idéale : SVG carré, marges internes 10%, lisible à 16×16 (test obligatoire avant génération).

### Next.js — convention App Router (alternative recommandée)

Au lieu du chemin `/public/`, placer directement dans `app/` :
- `app/icon.ico` (favicon principal)
- `app/icon.png` ou `app/icon.svg`
- `app/apple-icon.png` (180×180)
- `app/opengraph-image.jpg` (1200×630)
- `app/twitter-image.jpg`

Next.js détecte automatiquement et génère les balises HTML head correctes. Toujours fournir `site.webmanifest` dans `/public/` pour PWA.

---

## 5. Quand utiliser cette checklist

- **Phase 0/1** : @design produit le SVG source carré + palette theme-color hex
- **Phase 2** : @fullstack génère les 12 fichiers + intègre balises HTML head + crée `site.webmanifest`
- **Phase 5 (pre-launch)** : @qa exécute le script §3 → gate G31 PASS/FAIL

---

**Référence** : Standards 2026 (Google Search Central, Apple HIG depuis macOS Big Sur, web.dev PWA, Microsoft Edge docs Windows 11).
**Maintenance** : revoir annuellement si Google modifie ses exigences SERP. Microsoft Tiles/browserconfig retirés depuis Windows 10 EOL.
