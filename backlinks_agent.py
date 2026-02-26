"""Agent Backlinks autonome — prospection et netlinking hebdomadaire.

Ce module implémente une boucle d'agent qui exécute le prompt backlinks
(`.claude/backlinks-agent-prompt.md`) en utilisant l'API Anthropic avec tool use.

L'agent peut :
- Rechercher des opportunités de backlinks (forums, blogs, GitHub, annuaires)
- Rédiger des drafts de contributions prêts à poster
- Préparer des pitchs personnalisés pour les blogs/médias
- Mettre à jour le journal de netlinking et les prospects
- Committer les fichiers dans le dépôt git

Déclenché chaque mercredi à 10h par le scheduler APScheduler.
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
_PROMPT_PATH = _PROJECT_ROOT / ".claude" / "backlinks-agent-prompt.md"
_BACKLINKS_DIR = _PROJECT_ROOT / "backlinks"

_MAX_TOKENS = 16384


# ================================================================
# Définitions des outils (identiques à seo_agent.py)
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
        "description": "Écrit du contenu dans un fichier (crée ou écrase). Pour créer des drafts de contributions et pitchs.",
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
        "description": "Remplace une chaîne exacte dans un fichier existant. Pour mettre à jour le journal.",
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
        "description": "Liste les fichiers correspondant à un pattern glob. Ex: 'backlinks/drafts/*.md'",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Pattern glob"},
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
                "path": {"type": "string", "description": "Répertoire ou fichier (défaut: backlinks/)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "web_search",
        "description": "Effectue une recherche web. Retourne un résumé des résultats. Essentiel pour trouver des opportunités de backlinks.",
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
        "description": "Exécute une commande bash. Pour git add/commit/push et mkdir. INTERDIT : rm -rf, drop, reset --hard.",
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

_BASH_BLOCKLIST = re.compile(
    r"\b(rm\s+-rf|drop\s+table|reset\s+--hard|push\s+--force|clean\s+-f)\b",
    re.IGNORECASE,
)


def _resolve_path(path: str) -> Path:
    """Résout un chemin relatif depuis la racine du projet."""
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
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
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(input_data["content"], encoding="utf-8")
            return f"OK : {p} écrit ({len(input_data['content'])} caractères)"

        elif name == "edit_file":
            p = _resolve_path(input_data["path"])
            if not p.exists():
                return f"ERREUR : fichier introuvable : {p}"
            content = p.read_text(encoding="utf-8")
            old = input_data["old_string"]
            new = input_data["new_string"]
            if old not in content:
                return f"ERREUR : chaîne introuvable dans {p}"
            content = content.replace(old, new, 1)
            p.write_text(content, encoding="utf-8")
            return f"OK : remplacement effectué dans {p}"

        elif name == "list_files":
            pattern = input_data["pattern"]
            matches = sorted(glob_module.glob(str(_PROJECT_ROOT / pattern)))
            rel = [str(Path(m).relative_to(_PROJECT_ROOT)) for m in matches]
            return "\n".join(rel) if rel else "Aucun fichier trouvé"

        elif name == "search_files":
            pattern = input_data["pattern"]
            search_path = _resolve_path(input_data.get("path", "backlinks/"))
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
        results = []
        for m in re.finditer(r'class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</snippet', html, re.DOTALL):
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            snippet = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if title:
                results.append(f"- {title}\n  {snippet}")
        if not results:
            for m in re.finditer(r'class="result__snippet"[^>]*>(.*?)</(?:span|div)', html, re.DOTALL):
                text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                if text and len(text) > 20:
                    results.append(f"- {text}")
        return "\n\n".join(results[:10]) if results else f"Aucun résultat pour : {query}"
    except Exception as e:
        return f"Recherche web indisponible ({e}). Continuez avec vos connaissances."


# ================================================================
# Initialisation des fichiers de suivi
# ================================================================

def _ensure_backlinks_structure():
    """Crée la structure de fichiers backlinks si elle n'existe pas."""
    dirs = [
        _BACKLINKS_DIR,
        _BACKLINKS_DIR / "drafts",
        _BACKLINKS_DIR / "data_studies",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    log_file = _BACKLINKS_DIR / "_backlinks_log.md"
    if not log_file.exists():
        log_file.write_text(
            "# Journal de netlinking — calendrier-tempo.fr\n\n"
            "> Ce fichier est mis à jour automatiquement par l'agent backlinks.\n"
            "> Il trace toutes les actions de link building.\n\n"
            "---\n\n",
            encoding="utf-8",
        )

    prospects_file = _BACKLINKS_DIR / "_prospects.md"
    if not prospects_file.exists():
        prospects_file.write_text(
            "# Prospects backlinks — calendrier-tempo.fr\n\n"
            "> Liste des sites/forums/blogs identifiés comme cibles de backlinks.\n"
            "> Mis à jour par l'agent chaque semaine.\n\n"
            "## Canaux prioritaires\n\n"
            "### Forums et communautés\n"
            "| Site | URL | Type | DR estimé | Statut |\n"
            "|------|-----|------|-----------|--------|\n\n"
            "### Blogs et médias énergie\n"
            "| Site | URL | Type | DR estimé | Statut |\n"
            "|------|-----|------|-----------|--------|\n\n"
            "### Intégrations techniques (domotique)\n"
            "| Projet | URL | Stars | API utilisée | Statut |\n"
            "|--------|-----|-------|-------------|--------|\n\n"
            "### Annuaires et profils\n"
            "| Annuaire | URL | Statut |\n"
            "|----------|-----|--------|\n\n",
            encoding="utf-8",
        )


# ================================================================
# Boucle principale de l'agent
# ================================================================

def run_backlinks_agent() -> dict:
    """Exécute l'agent backlinks complet. Retourne un rapport d'exécution.

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
            "[Agent Backlinks] ANTHROPIC_API_KEY non configurée. "
            "Ajoutez-la dans les Secrets Replit pour activer l'agent autonome."
        )
        logger.error(msg)
        return {"success": False, "report": "", "turns": 0, "error": msg}

    # Créer la structure de fichiers si nécessaire
    _ensure_backlinks_structure()

    # Charger le prompt système
    if not _PROMPT_PATH.exists():
        msg = f"[Agent Backlinks] Prompt introuvable : {_PROMPT_PATH}"
        logger.error(msg)
        return {"success": False, "report": "", "turns": 0, "error": msg}

    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    # Ajouter le contexte dynamique
    from datetime import date
    system_prompt += f"\n\n---\n**Date du jour** : {date.today().isoformat()}\n"
    system_prompt += f"**Répertoire de travail** : {_PROJECT_ROOT}\n"
    system_prompt += f"**Répertoire backlinks** : {_BACKLINKS_DIR}\n"

    try:
        import anthropic
    except ImportError:
        msg = "[Agent Backlinks] Package 'anthropic' non installé. pip install anthropic"
        logger.error(msg)
        return {"success": False, "report": "", "turns": 0, "error": msg}

    client = anthropic.Anthropic(api_key=api_key)

    messages = [
        {
            "role": "user",
            "content": (
                "C'est mercredi. Exécutez votre mission hebdomadaire complète "
                "(étapes 0 à 7). Commencez par l'étape 0."
            ),
        }
    ]

    logger.info("[Agent Backlinks] Démarrage de la prospection hebdomadaire")

    turns = 0
    final_report = ""

    try:
        from config import Config
        max_turns = Config.BACKLINKS_AGENT_MAX_TURNS
        model = Config.SEO_AGENT_MODEL  # même modèle que l'agent SEO
    except Exception:
        max_turns = 35
        model = "claude-sonnet-4-5-20250929"

    while turns < max_turns:
        turns += 1

        try:
            response = client.messages.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=system_prompt,
                tools=_TOOLS,
                messages=messages,
            )
        except Exception as e:
            msg = f"[Agent Backlinks] Erreur API Claude (turn {turns}): {e}"
            logger.error(msg)
            return {"success": False, "report": final_report, "turns": turns, "error": msg}

        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        for block in assistant_content:
            if hasattr(block, "text"):
                final_report += block.text + "\n"

        if response.stop_reason == "end_turn":
            logger.info(f"[Agent Backlinks] Terminé en {turns} tours")
            break

        tool_results = []
        for block in assistant_content:
            if block.type == "tool_use":
                logger.info(f"[Agent Backlinks] Outil: {block.name}({json.dumps(block.input, ensure_ascii=False)[:200]})")
                result = _exec_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result[:20000],
                })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    if turns >= max_turns:
        logger.warning(f"[Agent Backlinks] Limite de {max_turns} tours atteinte")

    return {
        "success": True,
        "report": final_report,
        "turns": turns,
        "error": None,
    }
