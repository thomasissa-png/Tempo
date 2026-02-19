"""Tests for the SEO agent module (seo_agent.py).

Covers:
- Path resolution & security (no path traversal)
- Tool execution (read, write, edit, list, search, bash)
- Bash blocklist enforcement
- Graceful failure without API key
- Config wiring
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from seo_agent import (
    _resolve_path,
    _exec_tool,
    _BASH_BLOCKLIST,
    _PROJECT_ROOT,
    _TOOLS,
    run_seo_agent,
)


# ================================================================
# _resolve_path
# ================================================================

class TestResolvePath:
    def test_relative_path_resolved_under_project(self):
        p = _resolve_path("articles/test.md")
        assert str(p).startswith(str(_PROJECT_ROOT))

    def test_absolute_path_inside_project(self):
        p = _resolve_path(str(_PROJECT_ROOT / "articles" / "test.md"))
        assert str(p).startswith(str(_PROJECT_ROOT))

    def test_path_traversal_blocked(self):
        with pytest.raises(PermissionError, match="Accès interdit"):
            _resolve_path("../../etc/passwd")

    def test_path_traversal_absolute_blocked(self):
        with pytest.raises(PermissionError, match="Accès interdit"):
            _resolve_path("/etc/passwd")


# ================================================================
# Bash blocklist
# ================================================================

class TestBashBlocklist:
    @pytest.mark.parametrize("cmd", [
        "rm -rf /",
        "rm -rf .",
        "DROP TABLE users",
        "git reset --hard HEAD",
        "git push --force",
        "git clean -f",
    ])
    def test_dangerous_commands_blocked(self, cmd):
        assert _BASH_BLOCKLIST.search(cmd) is not None

    @pytest.mark.parametrize("cmd", [
        "git add .",
        "git commit -m 'test'",
        "git push origin main",
        "wc -w articles/test.md",
        "ls -la",
    ])
    def test_safe_commands_allowed(self, cmd):
        assert _BASH_BLOCKLIST.search(cmd) is None


# ================================================================
# _exec_tool — file operations
# ================================================================

class TestExecToolFiles:
    def test_read_file(self, tmp_path, monkeypatch):
        # Create a test file inside project root
        test_file = _PROJECT_ROOT / "test_seo_tmp_read.txt"
        test_file.write_text("hello world", encoding="utf-8")
        try:
            result = _exec_tool("read_file", {"path": "test_seo_tmp_read.txt"})
            assert "hello world" in result
        finally:
            test_file.unlink(missing_ok=True)

    def test_read_file_not_found(self):
        result = _exec_tool("read_file", {"path": "nonexistent_xyz_file.md"})
        assert "ERREUR" in result
        assert "introuvable" in result

    def test_write_file(self):
        test_path = "test_seo_tmp_write.txt"
        full_path = _PROJECT_ROOT / test_path
        try:
            result = _exec_tool("write_file", {"path": test_path, "content": "test content"})
            assert "OK" in result
            assert full_path.read_text(encoding="utf-8") == "test content"
        finally:
            full_path.unlink(missing_ok=True)

    def test_edit_file(self):
        test_path = "test_seo_tmp_edit.txt"
        full_path = _PROJECT_ROOT / test_path
        full_path.write_text("hello world", encoding="utf-8")
        try:
            result = _exec_tool("edit_file", {
                "path": test_path,
                "old_string": "hello",
                "new_string": "bonjour",
            })
            assert "OK" in result
            assert full_path.read_text(encoding="utf-8") == "bonjour world"
        finally:
            full_path.unlink(missing_ok=True)

    def test_edit_file_string_not_found(self):
        test_path = "test_seo_tmp_edit2.txt"
        full_path = _PROJECT_ROOT / test_path
        full_path.write_text("hello world", encoding="utf-8")
        try:
            result = _exec_tool("edit_file", {
                "path": test_path,
                "old_string": "nonexistent",
                "new_string": "replacement",
            })
            assert "ERREUR" in result
            assert "introuvable" in result
        finally:
            full_path.unlink(missing_ok=True)

    def test_write_file_path_traversal_blocked(self):
        result = _exec_tool("write_file", {
            "path": "../../etc/evil.txt",
            "content": "malicious",
        })
        assert "ERREUR" in result

    def test_list_files(self):
        result = _exec_tool("list_files", {"pattern": "*.py"})
        # Should find at least seo_agent.py
        assert "seo_agent.py" in result

    def test_search_files(self):
        test_path = "test_seo_tmp_search.md"
        full_path = _PROJECT_ROOT / test_path
        full_path.write_text("ligne 1\nTEMPO EDF\nligne 3", encoding="utf-8")
        try:
            result = _exec_tool("search_files", {
                "pattern": "TEMPO",
                "path": test_path,
            })
            assert "TEMPO EDF" in result
        finally:
            full_path.unlink(missing_ok=True)


# ================================================================
# _exec_tool — bash
# ================================================================

class TestExecToolBash:
    def test_bash_safe_command(self):
        result = _exec_tool("bash", {"command": "echo hello"})
        assert "hello" in result

    def test_bash_blocked_command(self):
        result = _exec_tool("bash", {"command": "rm -rf /"})
        assert "ERREUR" in result
        assert "interdite" in result

    def test_unknown_tool(self):
        result = _exec_tool("unknown_tool", {})
        assert "ERREUR" in result
        assert "inconnu" in result


# ================================================================
# Tool definitions
# ================================================================

class TestToolDefinitions:
    def test_all_tools_have_required_fields(self):
        for tool in _TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_expected_tool_names(self):
        names = {t["name"] for t in _TOOLS}
        expected = {"read_file", "write_file", "edit_file", "list_files", "search_files", "web_search", "bash"}
        assert names == expected


# ================================================================
# run_seo_agent — graceful failures
# ================================================================

class TestRunSeoAgent:
    def test_no_api_key(self, monkeypatch):
        """Without ANTHROPIC_API_KEY, agent should fail gracefully."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Also patch Config to return empty key
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            result = run_seo_agent()
        assert result["success"] is False
        assert result["turns"] == 0
        assert "ANTHROPIC_API_KEY" in result["error"]

    def test_missing_prompt_file(self, monkeypatch, tmp_path):
        """If prompt file doesn't exist, agent should fail gracefully."""
        import seo_agent
        original = seo_agent._PROMPT_PATH
        seo_agent._PROMPT_PATH = tmp_path / "nonexistent.md"
        # Mock Config to have a valid API key so we reach the prompt check
        fake_cfg = type("FakeConfig", (), {"ANTHROPIC_API_KEY": "test-key-123"})()
        try:
            with patch("config.Config", fake_cfg):
                result = run_seo_agent()
            assert result["success"] is False
            assert "introuvable" in result["error"]
        finally:
            seo_agent._PROMPT_PATH = original

    def test_missing_anthropic_package(self, monkeypatch):
        """If anthropic package not installed, agent should fail gracefully."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        import seo_agent
        # Ensure prompt file exists
        if not seo_agent._PROMPT_PATH.exists():
            pytest.skip("Prompt file not present")

        with patch.dict("sys.modules", {"anthropic": None}):
            result = run_seo_agent()
            # Should fail with ImportError message
            assert result["success"] is False


# ================================================================
# Config integration
# ================================================================

class TestConfigIntegration:
    def test_config_has_anthropic_key(self):
        from config import Config
        assert hasattr(Config, "ANTHROPIC_API_KEY")

    def test_config_has_seo_model(self):
        from config import Config
        assert hasattr(Config, "SEO_AGENT_MODEL")
        assert "claude" in Config.SEO_AGENT_MODEL.lower() or "sonnet" in Config.SEO_AGENT_MODEL.lower()

    def test_config_has_max_turns(self):
        from config import Config
        assert hasattr(Config, "SEO_AGENT_MAX_TURNS")
        assert isinstance(Config.SEO_AGENT_MAX_TURNS, int)
        assert Config.SEO_AGENT_MAX_TURNS > 0
