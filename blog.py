"""Module blog — articles Markdown avec publication programmée.

Les articles sont stockés dans le dossier ``articles/`` au format Markdown
avec un en-tête YAML (frontmatter). Seuls les articles dont la ``publish_date``
est ≤ aujourd'hui sont visibles publiquement.

Aucune base de données nécessaire — tout est versionné dans Git.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import markdown

_ARTICLES_DIR = Path(__file__).parent / "articles"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Extensions Markdown pour une meilleure typographie
_MD_EXTENSIONS = ["extra", "codehilite", "toc", "smarty"]


@dataclass
class Article:
    slug: str
    title: str
    description: str
    publish_date: date
    keywords: str
    content_html: str
    reading_time: int  # minutes
    updated_date: date | None = None  # date de dernière mise à jour (si différente de publish_date)
    cluster: str = ""  # topic cluster (pilier ou satellite)
    faq_items: list | None = None  # [(question, answer), ...] extracted from FAQ section


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Parse le frontmatter YAML simplifié (clé: valeur) et retourne (meta, body)."""
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    body = raw[m.end():]
    return meta, body


def _estimate_reading_time(text: str) -> int:
    """Estime le temps de lecture (~220 mots/min en français)."""
    words = len(text.split())
    return max(1, round(words / 220))


_FAQ_SECTION_RE = re.compile(
    r"^## (?:FAQ|Questions)[^\n]*\n(.*?)(?=\n## |\n---|\Z)",
    re.DOTALL | re.MULTILINE,
)
_FAQ_QA_RE = re.compile(
    r"^### ([^\n]+\?)\s*\n\n(.+?)(?=\n### |\n## |\n---|\Z)",
    re.DOTALL | re.MULTILINE,
)


def _extract_faq(body: str) -> list[tuple[str, str]] | None:
    """Extrait les paires Q/A de la section FAQ d'un article Markdown.

    Format attendu : ## FAQ ... ### Question ? \\n\\n Réponse
    Retourne None si aucune FAQ trouvée.
    """
    m = _FAQ_SECTION_RE.search(body)
    if not m:
        return None
    faq_text = m.group(1)
    items = []
    for qa in _FAQ_QA_RE.finditer(faq_text):
        question = qa.group(1).strip()
        # Nettoyer le Markdown de la réponse (retirer ** et [] links)
        answer = qa.group(2).strip()
        answer = re.sub(r"\*\*([^*]+)\*\*", r"\1", answer)
        answer = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", answer)
        items.append((question, answer))
    return items if items else None


def _load_article(filepath: Path) -> Article | None:
    """Charge un article depuis un fichier Markdown."""
    try:
        raw = filepath.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _parse_frontmatter(raw)
    if not meta.get("title") or not meta.get("publish_date"):
        return None
    try:
        pub_date = date.fromisoformat(meta["publish_date"])
    except ValueError:
        return None
    # Parse optional updated_date
    updated = None
    if meta.get("updated_date"):
        try:
            updated = date.fromisoformat(meta["updated_date"])
        except ValueError:
            pass
    md = markdown.Markdown(extensions=_MD_EXTENSIONS)
    content_html = md.convert(body)
    faq_items = _extract_faq(body)
    return Article(
        slug=filepath.stem,
        title=meta["title"],
        description=meta.get("description", ""),
        publish_date=pub_date,
        keywords=meta.get("keywords", ""),
        content_html=content_html,
        reading_time=_estimate_reading_time(body),
        updated_date=updated,
        cluster=meta.get("cluster", ""),
        faq_items=faq_items,
    )


def get_published_articles() -> list[Article]:
    """Retourne tous les articles publiés (publish_date <= aujourd'hui), triés du plus récent au plus ancien."""
    today = date.today()
    articles: list[Article] = []
    if not _ARTICLES_DIR.is_dir():
        return articles
    for f in sorted(_ARTICLES_DIR.glob("*.md")):
        art = _load_article(f)
        if art and art.publish_date <= today:
            articles.append(art)
    articles.sort(key=lambda a: a.publish_date, reverse=True)
    return articles


def get_article_by_slug(slug: str) -> Article | None:
    """Retourne un article par son slug, uniquement s'il est publié."""
    filepath = _ARTICLES_DIR / f"{slug}.md"
    if not filepath.is_file():
        return None
    art = _load_article(filepath)
    if art and art.publish_date <= date.today():
        return art
    return None


def get_all_article_slugs() -> list[tuple[str, date]]:
    """Retourne tous les slugs + dates pour le sitemap (y compris futurs pour pre-génération)."""
    result: list[tuple[str, date]] = []
    if not _ARTICLES_DIR.is_dir():
        return result
    for f in sorted(_ARTICLES_DIR.glob("*.md")):
        art = _load_article(f)
        if art and art.publish_date <= date.today():
            result.append((art.slug, art.publish_date))
    return result


def get_all_article_meta() -> list[tuple[str, str, str, date]]:
    """Retourne (slug, title, description, publish_date) des articles publiés pour llms.txt."""
    result: list[tuple[str, str, str, date]] = []
    if not _ARTICLES_DIR.is_dir():
        return result
    for f in sorted(_ARTICLES_DIR.glob("*.md")):
        art = _load_article(f)
        if art and art.publish_date <= date.today():
            result.append((art.slug, art.title, art.description, art.publish_date))
    result.sort(key=lambda x: x[3], reverse=True)
    return result
