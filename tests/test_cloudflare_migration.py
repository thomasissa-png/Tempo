"""Scripts de migration Cloudflare : transport HTTPS Neon, copie, fusion, contrôle de la clé.

Aucun appel réseau : httpx.MockTransport et fausses bases en mémoire.
"""
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cloudflare"))

import copy_database  # noqa: E402
import merge_users  # noqa: E402
import neon_http  # noqa: E402
from neon_http import NeonError, NeonHTTP  # noqa: E402

URL = "postgresql://u:motdepasse@ep-test.eu-central-1.aws.neon.tech/neondb?sslmode=require"


def _client(handler, readonly=False):
    return NeonHTTP(URL, readonly=readonly, transport=httpx.MockTransport(handler))


class TestNeonHTTP:
    def test_readonly_always_uses_read_only_transaction(self):
        seen = []

        def handler(request):
            seen.append((dict(request.headers), json.loads(request.content)))
            return httpx.Response(200, json={"results": [{"rows": [["23"]]}]})

        db = _client(handler, readonly=True)
        assert db.scalar("SELECT MAX(version) FROM schema_version") == "23"
        headers, body = seen[0]
        assert headers["neon-batch-read-only"] == "true"
        assert "queries" in body and "query" not in body
        assert headers["neon-raw-text-output"] == "true"

    def test_write_client_single_query_without_read_only(self):
        seen = []

        def handler(request):
            seen.append((str(request.url), dict(request.headers), json.loads(request.content)))
            return httpx.Response(200, json={"rows": [["1"]]})

        db = _client(handler)
        assert db.query("SELECT $1, $2, $3, $4", [1, None, True, "x"]) == [["1"]]
        url, headers, body = seen[0]
        assert url == "https://ep-test.eu-central-1.aws.neon.tech/sql"
        assert "neon-batch-read-only" not in headers
        assert body == {"query": "SELECT $1, $2, $3, $4", "params": ["1", None, "t", "x"]}

    def test_error_never_leaks_connection_string(self):
        db = _client(lambda r: httpx.Response(400, json={"message": "syntax error at or near \"x\""}))
        with pytest.raises(NeonError) as e:
            db.query("x")
        assert "syntax error" in str(e.value) and "motdepasse" not in str(e.value)

    def test_network_error_mentions_host_only(self):
        def handler(request):
            raise httpx.ConnectError("boom")

        with pytest.raises(NeonError) as e:
            _client(handler).query("SELECT 1")
        assert "ep-test" in str(e.value) and "motdepasse" not in str(e.value)

    def test_empty_batch_sends_nothing(self):
        db = _client(lambda r: pytest.fail("aucune requête attendue"))
        assert db.batch([]) == []


class TestEnvHelpers:
    def test_phone_key_restores_lost_padding(self, monkeypatch):
        monkeypatch.setenv("PHONE_ENCRYPTION_KEY", "A" * 43)
        assert neon_http.phone_key() == "A" * 43 + "="
        monkeypatch.setenv("PHONE_ENCRYPTION_KEY", "A" * 43 + "=")
        assert neon_http.phone_key() == "A" * 43 + "="
        monkeypatch.delenv("PHONE_ENCRYPTION_KEY")
        assert neon_http.phone_key() == ""

    def test_tempo_prefix_and_cloudflare_token_fallback(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("TEMPO_ANTHROPIC_API_KEY", "k")
        assert neon_http.env("ANTHROPIC_API_KEY") == "k"
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        monkeypatch.setenv("CLOUDFLARE_Token_Value", "t")
        assert neon_http.cloudflare_token() == "t"
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "prioritaire")
        assert neon_http.cloudflare_token() == "prioritaire"


class FakeDB:
    """Base en mémoire qui répond aux requêtes de copy_database / merge_users."""

    def __init__(self, tables, fks=(), serials=(), version="23", readonly=False):
        self.tables = tables  # {nom: (colonnes, lignes)}
        self.fks, self.serials, self.version = list(fks), list(serials), version
        self.readonly, self.writes = readonly, []

    def query(self, sql, params=()):
        if "to_regclass" in sql:
            return [["t" if self.version else "f"]]
        if "MAX(version)" in sql:
            return [[self.version]]
        if "information_schema.tables" in sql:
            return [[t] for t in self.tables]
        if "FOREIGN KEY" in sql:
            return [list(f) for f in self.fks]
        if "pg_get_serial_sequence" in sql:
            return [list(s) for s in self.serials]
        if "information_schema.columns" in sql:
            return [[t, c] for t, (cols, _) in self.tables.items() for c in cols]
        if sql.startswith("SELECT"):
            table = sql.split('FROM "')[1].rstrip('"')
            cols, rows = self.tables[table]
            wanted = [c.strip('" ') for c in sql[len("SELECT "):sql.index(" FROM")].split(",")]
            return [[r[cols.index(c)] for c in wanted] for r in rows]
        raise AssertionError(f"requête inattendue : {sql}")

    def scalar(self, sql, params=()):
        return self.query(sql, params)[0][0]

    def batch(self, queries):
        out = []
        for sql, params in queries:
            if not sql.startswith("SELECT") or "setval" in sql:
                assert not self.readonly, f"écriture sur la base en lecture seule : {sql[:40]}"
                self.writes.append((sql, params))
                out.append([])
            elif sql.startswith("SELECT COUNT(*)"):
                table = sql.split('"')[1]
                width = len(self.tables[table][0])
                out.append([[str(sum(len(p) // width for s, p in self.writes
                                     if s.startswith(f'INSERT INTO "{table}"')))]])
            else:
                out.append(self.query(sql, params))
        return out


def _replit():
    return FakeDB({
        "users": (["id", "phone_hash", "updated_at"], [["1", "h1", "2026-01-01"], ["2", "h2", "2026-02-01"]]),
        "sms_logs": (["id", "user_id", "statut"], [["1", "1", "sent"]]),
        "schema_version": (["version"], [["23"]]),
        "replit_only": (["x"], [["1"]]),
    }, fks=[("sms_logs", "users")], readonly=True)


def _neon(version="25"):
    return FakeDB({
        "users": (["id", "phone_hash", "updated_at"], []),
        "sms_logs": (["id", "user_id", "statut"], []),
        "schema_version": (["version"], []),
        "rte_forecast_log": (["id"], []),
    }, serials=[("users", "id"), ("sms_logs", "id"), ("rte_forecast_log", "id")], version=version)


class TestCopyDatabase:
    def test_insert_statements_respect_param_limit(self, monkeypatch):
        monkeypatch.setattr(copy_database, "MAX_PARAMS", 6)
        stmts = copy_database.insert_statements("t", ["a", "b"], [["1", "2"]] * 7)
        assert [len(p) for _, p in stmts] == [6, 6, 2]
        assert stmts[0][0] == 'INSERT INTO "t" ("a", "b") VALUES ($1, $2), ($3, $4), ($5, $6)'

    def test_copy_is_one_transaction_parents_first_and_never_writes_replit(self):
        src, dst = _replit(), _neon()
        copy_database.run(src, dst)
        assert src.writes == []
        sqls = [s for s, _ in dst.writes]
        assert sqls[0] == 'TRUNCATE "users", "sms_logs" RESTART IDENTITY CASCADE'
        inserts = [s.split('"')[1] for s in sqls if s.startswith("INSERT")]
        assert inserts == ["users", "sms_logs"]
        assert not any("replit_only" in s or "schema_version" in s for s in sqls)
        # Séquences recalées pour les tables copiées seulement
        assert [p for s, p in dst.writes if "setval" in s] == [["users", "id"], ["sms_logs", "id"]]

    def test_copy_refuses_target_without_schema(self):
        with pytest.raises(SystemExit):
            copy_database.run(_replit(), _neon(version=None))

    def test_copy_refuses_newer_source(self):
        src = _replit()
        src.version = "26"
        with pytest.raises(SystemExit):
            copy_database.run(src, _neon())

    def test_same_database_detected(self):
        assert copy_database._same_database(URL, URL.replace("?sslmode=require", ""))
        assert not copy_database._same_database(URL, URL.replace("ep-test", "ep-autre"))


class TestMergeUsers:
    COLS = ["phone_hash", "actif", "updated_at"]

    def test_plan_inserts_new_updates_newer_keeps_newer_on_cloudflare(self):
        replit = [["nouveau", "1", "2026-10-01T13:05"], ["modifie", "0", "2026-10-01T13:10"],
                  ["cf_plus_recent", "1", "2026-10-01T12:00"], ["identique", "1", "2026-09-01"]]
        neon = {"modifie": "2026-09-01", "cf_plus_recent": "2026-10-01T13:30", "identique": "2026-09-01"}
        (ins, ins_p), (upd, upd_p) = merge_users.plan_merge(self.COLS, replit, neon)
        assert ins.startswith("INSERT INTO users") and ins_p == replit[0]
        assert upd == 'UPDATE users SET "actif" = $1, "updated_at" = $2 WHERE "phone_hash" = $3'
        assert upd_p == ["0", "2026-10-01T13:10", "modifie"]

    def test_plan_is_idempotent(self):
        assert merge_users.plan_merge(self.COLS, [["a", "1", "2026-10-01"]], {"a": "2026-10-01"}) == []


def _user(i, created="2026-09-01"):
    from database import encrypt_phone, hash_phone
    phone = f"+3361234567{i}"
    return [str(i), hash_phone(phone), encrypt_phone(phone), created]


class TestPhoneKeyCheck:
    def test_all_readable_ok(self):
        import check_access
        ok, detail = check_access.verify_phone_key([_user(i) for i in range(3)])
        assert ok and detail.startswith("3/3")

    def test_old_unreadable_row_reported_not_blocking(self, monkeypatch):
        import check_access
        monkeypatch.setattr(check_access, "RECENT", 2)
        rows = [_user(i) for i in (4, 3, 2)] + [["1", "x", "gAAAAjetoninvalide", "2026-02-23"]]
        ok, detail = check_access.verify_phone_key(rows)
        assert ok and "id 1 du 2026-02-23" in detail

    def test_recent_unreadable_blocks(self):
        import check_access
        ok, _ = check_access.verify_phone_key([["9", "x", "gAAAAjetoninvalide", "2026-09-30"], _user(8)])
        assert not ok

    def test_wrong_hash_blocks(self):
        import check_access
        row = _user(8)
        row[1] = "empreinte-d-une-autre-cle"
        ok, detail = check_access.verify_phone_key([row])
        assert not ok and "mauvaise clé" in detail


class TestDnsZone:
    def test_records_match_ionos_capture(self):
        import dns_zone
        assert len(dns_zone.RECORDS) == 14
        types = sorted(r[0] for r in dns_zone.RECORDS)
        assert types.count("MX") == 2 and types.count("CNAME") == 6 and types.count("TXT") == 4
        # Le site reste sur Replit tant que la bascule (phase 2) n'est pas faite
        assert {r[2] for r in dns_zone.RECORDS if r[0] == "A"} == {"34.111.179.208"}

    def test_missing_records_ignores_quotes_case_and_trailing_dot(self):
        import dns_zone
        existing = [{"type": "MX", "name": "calendrier-tempo.fr", "content": "MX00.ionos.fr."},
                    {"type": "TXT", "name": "calendrier-tempo.fr",
                     "content": "v=spf1 include:_spf-eu.ionos.com ~all"}]
        todo = dns_zone.missing_records(existing)
        assert len(todo) == 12
        assert ("MX", "calendrier-tempo.fr", "mx00.ionos.fr", 3600, 10) not in todo


class TestPushSecrets:
    def test_long_secret_split_and_rejoined_in_order(self):
        import push_secrets
        value = "".join(chr(65 + i % 26) for i in range(12000))
        parts = push_secrets.split_secret("METEOFRANCE_AROME_KEY", value)
        assert [k for k, _ in parts] == [f"METEOFRANCE_AROME_KEY__PART{i}" for i in (1, 2, 3)]
        assert all(len(v) <= push_secrets.SECRET_MAX for _, v in parts)
        assert "".join(v for _, v in parts) == value
        assert push_secrets.split_secret("K", "court") == [("K", "court")]

    def test_worker_rejoins_parts(self):
        worker = open(os.path.join(os.path.dirname(copy_database.__file__), "worker.ts")).read()
        assert "__PART${i}" in worker and "readSecret(env, key)" in worker
