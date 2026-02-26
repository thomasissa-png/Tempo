#!/usr/bin/env python3
"""Validation programmatique des articles de blog SEO.

Usage:
    python validate_article.py articles/mon-article.md

Verifie :
  - Frontmatter obligatoire (title, description, publish_date, keywords)
  - Longueur du contenu (>= 800 mots)
  - Liens internes minimums (>= 3 blog + /calendrier + /#subscribe)
  - Presence d'une section FAQ
  - Hierarchie des titres (H1 absent dans body, H2/H3 corrects)
  - Meta description longueur (50-160 caracteres)
  - Keyword dans le titre et la description

Retourne exit code 0 si OK, 1 si erreurs.
"""

import re
import sys
from pathlib import Path


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Parse le frontmatter YAML simplifie."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
    if not m:
        return {}, raw
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    body = raw[m.end():]
    return meta, body


def validate(filepath: str) -> list[str]:
    """Valide un article et retourne la liste des erreurs."""
    errors: list[str] = []
    warnings: list[str] = []

    p = Path(filepath)
    if not p.exists():
        return [f"Fichier introuvable : {filepath}"]

    raw = p.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)

    # --- Frontmatter ---
    required_fields = ["title", "description", "publish_date", "keywords"]
    for field in required_fields:
        if not meta.get(field):
            errors.append(f"Frontmatter manquant : '{field}'")

    # Date format
    if meta.get("publish_date"):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", meta["publish_date"]):
            errors.append(f"Format publish_date invalide : '{meta['publish_date']}' (attendu YYYY-MM-DD)")

    # Meta description length
    desc = meta.get("description", "")
    if desc and len(desc) < 50:
        warnings.append(f"Meta description trop courte ({len(desc)} car., min 50)")
    if desc and len(desc) > 160:
        warnings.append(f"Meta description trop longue ({len(desc)} car., max 160)")

    # --- Contenu ---
    words = body.split()
    word_count = len(words)
    if word_count < 800:
        errors.append(f"Contenu trop court : {word_count} mots (minimum 800)")

    # Heading hierarchy : no H1 in body
    h1_matches = re.findall(r"^# [^#]", body, re.MULTILINE)
    if h1_matches:
        errors.append(f"H1 detecte dans le corps ({len(h1_matches)}x) - seul le template a un H1")

    # Count H2s
    h2_count = len(re.findall(r"^## ", body, re.MULTILINE))
    if h2_count < 2:
        warnings.append(f"Seulement {h2_count} H2 - structurez mieux le contenu")

    # --- Liens internes ---
    internal_links = re.findall(r"\[.*?\]\((/?[^)]+)\)", body)
    blog_links = [l for l in internal_links if l.startswith("/blog/") or l.startswith("blog/")]
    has_calendrier = any("/calendrier" in l for l in internal_links)
    has_subscribe = any("/#subscribe" in l or "#subscribe" in l for l in internal_links)

    if len(blog_links) < 3:
        errors.append(f"Liens internes blog insuffisants : {len(blog_links)}/3 minimum")
    if not has_calendrier:
        errors.append("Lien vers /calendrier manquant")
    if not has_subscribe:
        errors.append("Lien vers /#subscribe (CTA alertes) manquant")

    total_internal = len([l for l in internal_links if l.startswith("/")])
    if total_internal < 5:
        warnings.append(f"Seulement {total_internal} liens internes (recommande >= 5)")

    # --- FAQ ---
    has_faq = bool(re.search(r"^##.*(?:FAQ|[Ff]oire|[Qq]uestions?\s+fr[ée]quentes?)", body, re.MULTILINE))
    if not has_faq:
        errors.append("Section FAQ manquante (obligatoire pour featured snippets)")

    # --- Keyword in title/description ---
    keywords_str = meta.get("keywords", "")
    if keywords_str and meta.get("title"):
        primary_kw = keywords_str.split(",")[0].strip().lower()
        title_lower = meta["title"].lower()
        desc_lower = desc.lower()
        if primary_kw and primary_kw not in title_lower:
            warnings.append(f"Mot-cle principal '{primary_kw}' absent du titre")
        if primary_kw and primary_kw not in desc_lower:
            warnings.append(f"Mot-cle principal '{primary_kw}' absent de la description")

    # --- Cluster ---
    if not meta.get("cluster"):
        warnings.append("Cluster non defini dans le frontmatter")

    return errors, warnings


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_article.py <fichier.md>")
        sys.exit(1)

    filepath = sys.argv[1]
    result = validate(filepath)

    if isinstance(result, list):
        # Old format: just errors
        errors = result
        warnings = []
    else:
        errors, warnings = result

    # Print results
    if warnings:
        for w in warnings:
            print(f"  WARN: {w}")

    if errors:
        for e in errors:
            print(f"  ERREUR: {e}")
        print(f"\n{len(errors)} erreur(s), {len(warnings)} avertissement(s)")
        sys.exit(1)
    else:
        word_count = len(Path(filepath).read_text(encoding="utf-8").split())
        print(f"  OK ({word_count} mots, {len(warnings)} avertissement(s))")
        sys.exit(0)


if __name__ == "__main__":
    main()
