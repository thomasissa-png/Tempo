"""Persistance DB des fichiers écrits par les agents SEO/backlinks (migration v24).

Replit autoscale = disque éphémère : les fichiers écrits par les agents sont
copiés dans la table agent_files puis restaurés au démarrage (règle 3-voies).
Base SQLite temporaire fournie par la fixture autouse ``use_test_db``.
"""

import hashlib
import os
import subprocess
import sys
import textwrap
import uuid

import pytest

import database
from database import (
    get_db,
    init_db,
    normalize_agent_path,
    persist_agent_file,
    restore_agent_files,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _row(path: str):
    conn = get_db()
    try:
        return conn.execute(
            "SELECT path, content, base_hash, content_hash, agent FROM agent_files WHERE path = ?",
            (path,),
        ).fetchone()
    finally:
        conn.close()


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "repo"
    (r / "articles").mkdir(parents=True)
    return r


# ---------------------------------------------------------------- migration

class TestMigrationV24:
    def test_table_exists_and_version_current(self):
        conn = get_db()
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 25
            cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_files)").fetchall()]
            assert cols == ["path", "content", "base_hash", "content_hash", "agent", "updated_at"]
        finally:
            conn.close()

    def test_migration_idempotent(self):
        persist_agent_file("articles/a.md", "x", "seo_agent", None)
        init_db()
        init_db()
        # Rejouer la migration depuis v23 ne doit ni échouer ni perdre de données
        conn = get_db()
        conn.execute("PRAGMA user_version = 23")
        conn.commit()
        conn.close()
        init_db()
        conn = get_db()
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 25
        finally:
            conn.close()
        assert _row("articles/a.md")["content"] == "x"

    def test_conflict_cols_mapping(self):
        assert database._CONFLICT_COLS["agent_files"] == "(path)"


# ---------------------------------------------------------------- périmètre

class TestPerimeter:
    @pytest.mark.parametrize("bad", [
        "../etc/passwd", "articles/../app.py", "/articles/x.md", "app.py",
        "templates/x.html", "articles", "articles/", "articles\\x.md", "", None,
        "backlinks/../../x",
    ])
    def test_refuse_out_of_perimeter(self, bad):
        assert normalize_agent_path(bad) is None
        if bad is not None:
            assert persist_agent_file(bad, "x", "seo_agent", None) is False

    def test_accept_perimeter(self):
        assert normalize_agent_path("articles/./x.md") == "articles/x.md"
        assert normalize_agent_path("backlinks/drafts/p.md") == "backlinks/drafts/p.md"

    def test_restore_skips_malicious_row(self, root):
        conn = get_db()
        conn.execute(
            "INSERT INTO agent_files (path, content, base_hash, content_hash, agent, updated_at) "
            "VALUES (?, ?, NULL, ?, 'x', 'now')",
            ("articles/../evil.py", "boom", _sha("boom")),
        )
        conn.commit()
        conn.close()
        stats = restore_agent_files(str(root))
        assert stats["skipped"] == 1
        assert not (root / "evil.py").exists()


# ---------------------------------------------------------------- persistance

class TestPersist:
    def test_create_sets_base_none(self):
        assert persist_agent_file("articles/new.md", "v1", "seo_agent", None)
        r = _row("articles/new.md")
        assert r["content"] == "v1"
        assert r["content_hash"] == _sha("v1")
        assert r["base_hash"] is None
        assert r["agent"] == "seo_agent"

    def test_edit_existing_repo_file_sets_base(self):
        persist_agent_file("articles/a.md", "agent1", "seo_agent", "repo")
        assert _row("articles/a.md")["base_hash"] == _sha("repo")

    def test_second_edit_keeps_original_base(self):
        persist_agent_file("articles/a.md", "agent1", "seo_agent", "repo")
        persist_agent_file("articles/a.md", "agent2", "seo_agent", "agent1")
        r = _row("articles/a.md")
        assert r["content"] == "agent2"
        assert r["base_hash"] == _sha("repo")

    def test_created_then_edited_keeps_none_base(self):
        persist_agent_file("articles/n.md", "v1", "seo_agent", None)
        persist_agent_file("articles/n.md", "v2", "seo_agent", "v1")
        assert _row("articles/n.md")["base_hash"] is None

    def test_disk_diverged_resets_base(self):
        persist_agent_file("articles/a.md", "agent1", "seo_agent", "repo")
        # Le disque ne correspond plus au content précédent (nouveau déploiement)
        persist_agent_file("articles/a.md", "agent2", "seo_agent", "repo-v2")
        assert _row("articles/a.md")["base_hash"] == _sha("repo-v2")

    def test_db_failure_is_non_blocking(self, monkeypatch):
        def boom():
            raise RuntimeError("DB down")
        monkeypatch.setattr(database, "get_db", boom)
        assert persist_agent_file("articles/a.md", "x", "seo_agent", None) is False
        stats = restore_agent_files()
        assert stats["errors"] == 1


# ---------------------------------------------------------------- restauration 3-voies

class TestRestoreThreeWay:
    def test_absent_file_is_written(self, root):
        persist_agent_file("articles/new.md", "agent", "seo_agent", None)
        stats = restore_agent_files(str(root))
        assert (root / "articles/new.md").read_text(encoding="utf-8") == "agent"
        assert stats["restored"] == 1

    def test_absent_subdir_is_created(self, root):
        persist_agent_file("backlinks/drafts/p.md", "draft é", "backlinks_agent", None)
        restore_agent_files(str(root))
        assert (root / "backlinks/drafts/p.md").read_text(encoding="utf-8") == "draft é"

    def test_disk_equals_content_noop(self, root):
        f = root / "articles/a.md"
        f.write_text("agent", encoding="utf-8")
        persist_agent_file("articles/a.md", "agent", "seo_agent", "repo")
        mtime = f.stat().st_mtime_ns
        stats = restore_agent_files(str(root))
        assert stats["unchanged"] == 1 and stats["restored"] == 0
        assert f.stat().st_mtime_ns == mtime

    def test_disk_equals_base_writes_agent_content(self, root):
        f = root / "articles/a.md"
        f.write_text("repo", encoding="utf-8")
        persist_agent_file("articles/a.md", "agent", "seo_agent", "repo")
        stats = restore_agent_files(str(root))
        assert f.read_text(encoding="utf-8") == "agent"
        assert stats["restored"] == 1

    def test_human_change_keeps_disk_and_never_overwrites(self, root, caplog):
        f = root / "articles/a.md"
        persist_agent_file("articles/a.md", "agent", "seo_agent", "repo")
        f.write_text("human", encoding="utf-8")
        with caplog.at_level("WARNING"):
            stats = restore_agent_files(str(root))
        assert stats["conflicts"] == 1
        assert f.read_text(encoding="utf-8") == "human"
        assert "Conflit" in caplog.text
        r = _row("articles/a.md")
        assert r["content"] == "human"
        assert r["base_hash"] == r["content_hash"] == _sha("human")
        # Passe suivante : plus de conflit, rien d'écrasé
        stats2 = restore_agent_files(str(root))
        assert stats2["unchanged"] == 1 and stats2["conflicts"] == 0
        assert f.read_text(encoding="utf-8") == "human"

    def test_created_file_then_repo_has_other_version_is_conflict(self, root):
        persist_agent_file("articles/n.md", "agent", "seo_agent", None)
        (root / "articles/n.md").write_text("repo", encoding="utf-8")
        stats = restore_agent_files(str(root))
        assert stats["conflicts"] == 1
        assert (root / "articles/n.md").read_text(encoding="utf-8") == "repo"

    def test_restore_is_idempotent(self, root):
        persist_agent_file("articles/new.md", "agent", "seo_agent", None)
        restore_agent_files(str(root))
        stats = restore_agent_files(str(root))
        assert stats == {"restored": 0, "unchanged": 1, "conflicts": 0, "skipped": 0, "errors": 0}


# ---------------------------------------------------------------- intégration agents

class TestAgentToolsPersist:
    @pytest.mark.parametrize("mod_name,agent", [("seo_agent", "seo_agent"),
                                                ("backlinks_agent", "backlinks_agent")])
    def test_write_then_edit_persisted(self, root, monkeypatch, mod_name, agent):
        mod = __import__(mod_name)
        monkeypatch.setattr(mod, "_PROJECT_ROOT", root)
        (root / "articles/a.md").write_text("repo text", encoding="utf-8")
        out = mod._exec_tool("edit_file", {"path": "articles/a.md",
                                           "old_string": "repo", "new_string": "agent"})
        assert out.startswith("OK")
        r = _row("articles/a.md")
        assert r["content"] == "agent text" and r["agent"] == agent
        assert r["base_hash"] == _sha("repo text")
        out = mod._exec_tool("write_file", {"path": "articles/a.md", "content": "v2"})
        assert out.startswith("OK")
        r = _row("articles/a.md")
        assert r["content"] == "v2" and r["base_hash"] == _sha("repo text")

    @pytest.mark.parametrize("mod_name", ["seo_agent", "backlinks_agent"])
    def test_tool_succeeds_when_db_fails(self, root, monkeypatch, mod_name):
        mod = __import__(mod_name)
        monkeypatch.setattr(mod, "_PROJECT_ROOT", root)

        def boom():
            raise RuntimeError("DB down")
        monkeypatch.setattr(database, "get_db", boom)
        out = mod._exec_tool("write_file", {"path": "articles/x.md", "content": "ok"})
        assert out.startswith("OK")
        assert (root / "articles/x.md").read_text(encoding="utf-8") == "ok"

    @pytest.mark.parametrize("mod_name", ["seo_agent", "backlinks_agent"])
    def test_out_of_perimeter_write_not_persisted(self, root, monkeypatch, mod_name):
        mod = __import__(mod_name)
        monkeypatch.setattr(mod, "_PROJECT_ROOT", root)
        out = mod._exec_tool("write_file", {"path": "docs/notes.md", "content": "x"})
        assert out.startswith("OK")
        conn = get_db()
        try:
            assert conn.execute("SELECT COUNT(*) FROM agent_files").fetchone()[0] == 0
        finally:
            conn.close()

    def test_seo_traversal_cannot_bypass_protection(self, root, monkeypatch):
        import seo_agent
        monkeypatch.setattr(seo_agent, "_PROJECT_ROOT", root)
        out = seo_agent._exec_tool("write_file", {"path": "articles/../app.py", "content": "x"})
        assert "protégé" in out
        assert not (root / "app.py").exists()

    def test_hardcoded_model_fallback(self):
        import inspect
        import backlinks_agent
        import seo_agent
        for mod in (seo_agent, backlinks_agent):
            src = inspect.getsource(mod)
            assert '"claude-sonnet-5-5"' in src
            assert '"claude-sonnet-5"' not in src


# ---------------------------------------------------------------- scheduler

class TestSchedulerHook:
    def test_post_startup_restores_first(self):
        import inspect
        import scheduler
        src = inspect.getsource(scheduler._task_post_startup)
        assert src.index("restore_agent_files_and_invalidate") < src.index("auto_import_if_empty")
        assert "_task_restore_agent_files" in inspect.getsource(scheduler.schedule_post_startup)

    def test_restore_and_invalidate(self, root, monkeypatch):
        import scheduler
        monkeypatch.setattr(database, "_AGENT_FILES_ROOT", str(root))
        persist_agent_file("articles/new.md", "agent", "seo_agent", None)
        stats = scheduler.restore_agent_files_and_invalidate()
        assert stats["restored"] == 1
        assert (root / "articles/new.md").exists()


# ---------------------------------------------------------------- PostgreSQL (optionnel)
# Lancé seulement si TEMPO_TEST_PG_URL pointe vers un serveur PostgreSQL
# (ex. conteneur docker postgres:16). Chaque test crée une base jetable et
# exécute database.py dans un sous-processus avec DATABASE_URL (le backend est
# choisi à l'import du module).

_PG_URL = os.environ.get("TEMPO_TEST_PG_URL")


@pytest.mark.skipif(not _PG_URL, reason="TEMPO_TEST_PG_URL non défini")
class TestPostgres:
    @pytest.fixture
    def pg_db_url(self):
        import psycopg2
        name = f"tempo_test_{uuid.uuid4().hex[:10]}"
        admin = psycopg2.connect(_PG_URL)
        admin.autocommit = True
        admin.cursor().execute(f"CREATE DATABASE {name}")
        base, _, _ = _PG_URL.rpartition("/")
        yield f"{base}/{name}"
        admin.cursor().execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()

    def _run(self, db_url, tmp_path, code):
        env = dict(os.environ, DATABASE_URL=db_url)
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = "import sys; sys.path.insert(0, %r)\n" % repo + textwrap.dedent(code)
        res = subprocess.run([sys.executable, "-c", script], env=env, cwd=str(tmp_path),
                             capture_output=True, text=True, timeout=120)
        assert res.returncode == 0, res.stdout + res.stderr
        return res.stdout

    def test_init_db_fresh_and_roundtrip(self, pg_db_url, tmp_path):
        out = self._run(pg_db_url, tmp_path, f"""
            import database
            assert database._USE_POSTGRES
            database.init_db()
            database.init_db()  # idempotent
            c = database.get_db()
            assert c.execute("PRAGMA user_version").fetchone()[0] == 25
            c.close()
            assert database.persist_agent_file("articles/a.md", "agent 100% é", "seo_agent", "repo")
            assert database.persist_agent_file("articles/a.md", "agent2", "seo_agent", "agent 100% é")
            c = database.get_db()
            r = c.execute("SELECT base_hash, content FROM agent_files WHERE path = ?", ("articles/a.md",)).fetchone()
            c.close()
            import hashlib
            assert r["base_hash"] == hashlib.sha256(b"repo").hexdigest(), r
            assert r["content"] == "agent2"
            import os
            root = {str(tmp_path)!r}
            os.makedirs(os.path.join(root, "articles"), exist_ok=True)
            open(os.path.join(root, "articles/a.md"), "w").write("human")
            s = database.restore_agent_files(root)
            assert s["conflicts"] == 1, s
            s = database.restore_agent_files(root)
            assert s["unchanged"] == 1, s
            print("PG_OK")
        """)
        assert "PG_OK" in out

    def test_migration_from_v23(self, pg_db_url, tmp_path):
        out = self._run(pg_db_url, tmp_path, """
            import database
            database.init_db()
            c = database.get_db()
            c.execute("DROP TABLE agent_files")
            c.execute("PRAGMA user_version = 23")
            c.commit()
            c.close()
            database.init_db()
            c = database.get_db()
            assert c.execute("PRAGMA user_version").fetchone()[0] == 25
            assert c.execute("SELECT COUNT(*) FROM agent_files").fetchone()[0] == 0
            c.close()
            # Rejeu depuis v23 avec la table déjà présente : pas d'erreur
            c = database.get_db()
            c.execute("PRAGMA user_version = 23")
            c.commit()
            c.close()
            database.init_db()
            print("PG_MIG_OK")
        """)
        assert "PG_MIG_OK" in out
