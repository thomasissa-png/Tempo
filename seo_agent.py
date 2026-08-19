"""Agent SEO autonome — publication hebdomadaire via l'API Claude.

Ce module implémente une boucle d'agent qui exécute le prompt SEO
(`.claude/seo-agent-prompt.md`) en utilisant l'API Anthropic avec tool use.

L'agent peut :
- Lire/écrire/modifier des fichiers (articles Markdown, calendrier YAML)
- Chercher du contenu dans les fichiers (grep)
- Lister des fichiers (glob)
- Faire des recherches web (via l'API)
- Exécuter des commandes bash (git commit/push, wc)

Déclenché chaque mardi à 9h par le scheduler APScheduler.
Nécessite la variable d'environnement ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import glob as glob_module
import json
import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent
_PROMPT_PATH = _PROJECT_ROOT / ".claude" / "seo-agent-prompt.md"

# Valeurs par défaut (surchargées par Config si disponible)
# max_tokens plafonne le TOTAL (raisonnement + reponse). Sonnet 5 active le
# raisonnement adaptatif par defaut : marge relevee de 16384 a 24000 pour eviter
# une reponse tronquee (stop_reason='max_tokens'). On reste en non-streaming.
_MAX_TOKENS = 24000


# ================================================================
# Définitions des outils pour l'agent
# ================================================================

_TOOLS = [
    {
        "name": "read_file",
        "description": "Lit le contenu d'un fichier. Chemin relatif depuis la racine du projet /home/user/Tempo/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Chemin du fichier à lire (relatif ou absolu)"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Écrit du contenu dans un fichier (crée ou écrase). Pour créer un nouvel article Markdown.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Chemin du fichier"},
                "content": {"type": "string", "description": "Contenu à écrire"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Remplace une chaîne exacte dans un fichier existant. Pour modifier un article existant (maillage rétroactif).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Chemin du fichier"},
                "old_string": {"type": "string", "description": "Texte exact à remplacer"},
                "new_string": {"type": "string", "description": "Texte de remplacement"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_files",
        "description": "Liste les fichiers correspondant à un pattern glob. Ex: 'articles/*.md'",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Pattern glob (ex: 'articles/*.md')"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "search_files",
        "description": "Cherche un pattern regex dans les fichiers. Retourne les lignes correspondantes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Pattern regex à chercher"},
                "path": {"type": "string", "description": "Répertoire ou fichier où chercher (défaut: articles/)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "web_search",
        "description": "Effectue une recherche web. Retourne un résumé des résultats.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Requête de recherche"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "bash",
        "description": "Exécute une commande bash. Pour git add/commit/push et wc -w. INTERDIT : rm, drop, reset --hard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Commande bash à exécuter"},
            },
            "required": ["command"],
        },
    },
]


# ================================================================
# Exécution des outils
# ================================================================

# Commandes bash interdites (sécurité)
_BASH_BLOCKLIST = re.compile(
    r"\b(rm\s+-rf|rm\s+[^|]*\.(?:py|html|db|yaml|json)|drop\s+table|reset\s+--hard|push\s+--force|clean\s+-f|chmod\s|curl\s.*\|\s*(?:ba)?sh)\b",
    re.IGNORECASE,
)

# Fichiers protégés en écriture (A-1 : l'agent ne doit pas modifier son propre prompt)
_WRITE_PROTECTED = {
    ".claude/seo-agent-prompt.md",
    "seo_agent.py",
    "validate_article.py",
    "config.py",
    "database.py",
    "app.py",
    "scheduler.py",
}

# Répertoires protégés en écriture (l'agent ne doit écrire que dans articles/)
_WRITE_PROTECTED_DIRS = {"templates/", "tests/", "static/"}


def _is_write_protected(rel_path: str) -> bool:
    """Vérifie si un chemin relatif est protégé en écriture."""
    if rel_path in _WRITE_PROTECTED:
        return True
    return any(rel_path.startswith(d) for d in _WRITE_PROTECTED_DIRS)


def _resolve_path(path: str) -> Path:
    """Résout un chemin relatif depuis la racine du projet."""
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    # Sécurité : ne pas sortir du projet
    try:
        p.resolve().relative_to(_PROJECT_ROOT.resolve())
    except ValueError:
        raise PermissionError(f"Accès interdit hors du projet : {path}")
    return p


def _exec_tool(name: str, input_data: dict) -> str:
    """Exécute un outil et retourne le résultat sous forme de texte."""
    try:
        if name == "read_file":
            p = _resolve_path(input_data["path"])
            if not p.exists():
                return f"ERREUR : fichier introuvable : {p}"
            return p.read_text(encoding="utf-8")[:50000]

        elif name == "write_file":
            p = _resolve_path(input_data["path"])
            # A-1 : protection en écriture des fichiers critiques
            rel = str(p.relative_to(_PROJECT_ROOT))
            if _is_write_protected(rel):
                return f"ERREUR : écriture interdite sur {rel} (fichier protégé)"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(input_data["content"], encoding="utf-8")
            return f"OK : {p} écrit ({len(input_data['content'])} caractères)"

        elif name == "edit_file":
            p = _resolve_path(input_data["path"])
            # A-1 : protection en écriture des fichiers critiques
            rel = str(p.relative_to(_PROJECT_ROOT))
            if _is_write_protected(rel):
                return f"ERREUR : écriture interdite sur {rel} (fichier protégé)"
            if not p.exists():
                return f"ERREUR : fichier introuvable : {p}"
            content = p.read_text(encoding="utf-8")
            old = input_data["old_string"]
            new = input_data["new_string"]
            if old not in content:
                return f"ERREUR : chaîne introuvable dans {p}"
            count = content.count(old)
            content = content.replace(old, new, 1)
            p.write_text(content, encoding="utf-8")
            return f"OK : remplacement effectué dans {p} ({count} occurrence(s) trouvée(s), 1 remplacée)"

        elif name == "list_files":
            pattern = input_data["pattern"]
            matches = sorted(glob_module.glob(str(_PROJECT_ROOT / pattern)))
            # Retourner des chemins relatifs
            rel = [str(Path(m).relative_to(_PROJECT_ROOT)) for m in matches]
            return "\n".join(rel) if rel else "Aucun fichier trouvé"

        elif name == "search_files":
            pattern = input_data["pattern"]
            search_path = _resolve_path(input_data.get("path", "articles/"))
            results = []
            if search_path.is_file():
                files = [search_path]
            else:
                files = sorted(search_path.rglob("*"))
            for f in files:
                if not f.is_file() or f.suffix not in (".md", ".yaml", ".yml", ".html", ".py", ".txt"):
                    continue
                try:
                    text = f.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if re.search(pattern, line, re.IGNORECASE):
                        rel = str(f.relative_to(_PROJECT_ROOT))
                        results.append(f"{rel}:{i}: {line.strip()}")
                if len(results) > 100:
                    break
            return "\n".join(results[:100]) if results else "Aucun résultat"

        elif name == "web_search":
            # Utilise l'API web search d'Anthropic si dispo, sinon message
            return _web_search(input_data["query"])

        elif name == "bash":
            cmd = input_data["command"]
            if _BASH_BLOCKLIST.search(cmd):
                return f"ERREUR : commande interdite pour des raisons de sécurité : {cmd}"
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=60, cwd=str(_PROJECT_ROOT),
            )
            output = result.stdout + result.stderr
            return output[:10000] if output else "(pas de sortie)"

        else:
            return f"ERREUR : outil inconnu : {name}"

    except PermissionError as e:
        return f"ERREUR : {e}"
    except subprocess.TimeoutExpired:
        return "ERREUR : commande timeout (60s)"
    except Exception as e:
        return f"ERREUR : {type(e).__name__}: {e}"


def _web_search(query: str) -> str:
    """Effectue une recherche web basique via DuckDuckGo HTML."""
    import urllib.request
    import urllib.parse
    try:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # Extraire les résultats : titre (<a class="result__a">) + snippet (<a class="result__snippet">)
        results = []
        # Méthode 1 : extraction structurée titre + snippet
        for m in re.finditer(
            r'class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</(?:a|span|div)',
            html, re.DOTALL,
        ):
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            snippet = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if title:
                results.append(f"- {title}\n  {snippet}")
        # Méthode 2 (fallback) : extraire uniquement les snippets
        if not results:
            for m in re.finditer(r'class="result__snippet"[^>]*>(.*?)</(?:a|span|div)', html, re.DOTALL):
                text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                if text and len(text) > 20:
                    results.append(f"- {text}")
        # Méthode 3 (dernier recours) : extraire les titres seuls
        if not results:
            for m in re.finditer(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL):
                title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                if title and len(title) > 5:
                    results.append(f"- {title}")
        return "\n\n".join(results[:8]) if results else f"Aucun résultat pour : {query}"
    except Exception as e:
        return f"Recherche web indisponible ({e}). Continuez avec vos connaissances existantes."


# ================================================================
# Boucle principale de l'agent
# ================================================================

def run_seo_agent() -> dict:
    """Exécute l'agent SEO complet. Retourne un rapport d'exécution.

    Returns:
        dict avec clés: success (bool), report (str), turns (int), error (str|None)
    """
    try:
        from config import Config as _Cfg
        api_key = _Cfg.ANTHROPIC_API_KEY
    except Exception:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        msg = (
            "[Agent SEO] ANTHROPIC_API_KEY non configurée. "
            "Ajoutez-la dans les Secrets Replit pour activer l'agent autonome."
        )
        logger.error(msg)
        return {"success": False, "report": "", "turns": 0, "error": msg}

    # Charger le prompt système
    if not _PROMPT_PATH.exists():
        msg = f"[Agent SEO] Prompt introuvable : {_PROMPT_PATH}"
        logger.error(msg)
        return {"success": False, "report": "", "turns": 0, "error": msg}

    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    # Ajouter le contexte dynamique
    from datetime import date
    system_prompt += f"\n\n---\n**Date du jour** : {date.today().isoformat()}\n"
    system_prompt += f"**Répertoire de travail** : {_PROJECT_ROOT}\n"

    try:
        import anthropic
    except ImportError:
        msg = "[Agent SEO] Package 'anthropic' non installé. pip install anthropic"
        logger.error(msg)
        return {"success": False, "report": "", "turns": 0, "error": msg}

    client = anthropic.Anthropic(api_key=api_key)

    # Message initial pour lancer le workflow
    messages = [
        {
            "role": "user",
            "content": (
                "C'est mardi. Exécutez votre mission hebdomadaire complète "
                "(étapes 0 à 8). Commencez par l'étape 0."
            ),
        }
    ]

    logger.info("[Agent SEO] Démarrage de la mission hebdomadaire")

    turns = 0
    final_report = ""
    total_input_tokens = 0
    total_output_tokens = 0

    try:
        from config import Config
        max_turns = Config.SEO_AGENT_MAX_TURNS
        model = Config.SEO_AGENT_MODEL
    except Exception:
        max_turns = 40
        model = "claude-sonnet-5"

    while turns < max_turns:
        turns += 1

        try:
            response = client.messages.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=system_prompt,
                tools=_TOOLS,
                messages=messages,
                # Explicite plutot qu'implicite : Sonnet 5 active le raisonnement
                # adaptatif meme si le parametre est omis. effort='high' est le
                # defaut ; passer a 'medium' est le levier d'economie principal.
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
            )
        except anthropic.APIError as e:
            msg = f"[Agent SEO] Erreur API Claude (turn {turns}): {e}"
            logger.error(msg)
            return {"success": False, "report": final_report, "turns": turns, "error": msg}

        # Comptabiliser les tokens
        if hasattr(response, "usage"):
            total_input_tokens += getattr(response.usage, "input_tokens", 0)
            total_output_tokens += getattr(response.usage, "output_tokens", 0)

        # Traiter la réponse
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        # Extraire le texte pour le rapport final
        for block in assistant_content:
            if hasattr(block, "text"):
                final_report += block.text + "\n"

        # Si pas d'appel d'outil, l'agent a terminé
        if response.stop_reason == "max_tokens":
            logger.warning(
                "[Agent SEO] Reponse tronquee a max_tokens (turn %s) : le raisonnement "
                "adaptatif a consomme le budget. Augmenter _MAX_TOKENS ou passer "
                "output_config effort a 'medium'.", turns
            )

        if response.stop_reason == "end_turn":
            logger.info(f"[Agent SEO] Terminé en {turns} tours")
            break

        # Exécuter les outils demandés
        tool_results = []
        for block in assistant_content:
            if block.type == "tool_use":
                logger.info(f"[Agent SEO] Outil: {block.name}({json.dumps(block.input, ensure_ascii=False)[:200]})")
                result = _exec_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result[:20000],  # limiter la taille
                })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            # Pas d'outil mais stop_reason != end_turn → forcer fin
            break

        # Context trimming : garder les 3 premiers + les N derniers échanges
        # pour éviter de dépasser la fenêtre de contexte
        if len(messages) > 20:
            # Garder le message initial (user) + les 16 derniers messages
            messages = messages[:1] + messages[-16:]

    if turns >= max_turns:
        logger.warning(f"[Agent SEO] Limite de {max_turns} tours atteinte")

    # Estimer le cout au tarif catalogue Sonnet 5 ($3/M input, $15/M output).
    # Deux reserves : un tarif d'introduction ($2/$10) court jusqu'au 2026-08-31,
    # et Sonnet 5 utilise un nouveau tokenizer qui produit ~30 % de tokens en plus
    # pour le meme texte qu'en Sonnet 4.5 — la hausse mesuree n'est donc pas une
    # derive de l'agent. Re-etalonner les baselines de cout avant de reagir.
    est_cost = (total_input_tokens * 3 + total_output_tokens * 15) / 1_000_000
    logger.info(
        f"[Agent SEO] Tokens: {total_input_tokens} input + {total_output_tokens} output "
        f"= coût estimé ${est_cost:.2f}"
    )

    return {
        "success": True,
        "report": final_report,
        "turns": turns,
        "error": None,
        "tokens": {"input": total_input_tokens, "output": total_output_tokens, "est_cost_usd": round(est_cost, 2)},
    }
