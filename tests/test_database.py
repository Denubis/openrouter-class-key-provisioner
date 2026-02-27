"""Tests for database initialisation and update logic."""

import sqlite3

import pytest
from click.testing import CliRunner

from manage_keys import SCHEMA_VERSION, cli, update_database


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture()
def initialized_db(db_path, runner, monkeypatch):
    """Return path to an initialized database."""
    monkeypatch.setenv("OPENROUTER_PROVISIONING_KEY", "test-key")
    result = runner.invoke(cli, ["init-db", "--db", db_path])
    assert result.exit_code == 0
    return db_path


class TestInitDb:
    def test_creates_tables(self, initialized_db):
        conn = sqlite3.connect(initialized_db)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in c.fetchall()}
        conn.close()
        assert tables == {"changelog", "key", "schema_version", "student", "usage"}

    def test_schema_version_recorded(self, initialized_db):
        conn = sqlite3.connect(initialized_db)
        c = conn.cursor()
        c.execute("SELECT version FROM schema_version")
        version = c.fetchone()[0]
        conn.close()
        assert version == SCHEMA_VERSION

    def test_student_table_has_constraints(self, initialized_db):
        conn = sqlite3.connect(initialized_db)
        c = conn.cursor()
        # mq_id UNIQUE constraint: inserting duplicate should fail
        c.execute(
            "INSERT INTO student (email, first_name, last_name, mq_id, created_at) VALUES (?, ?, ?, ?, ?)",
            ("a@example.com", "A", "B", "12345", "2026-01-01"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO student (email, first_name, last_name, mq_id, created_at) VALUES (?, ?, ?, ?, ?)",
                ("b@example.com", "C", "D", "12345", "2026-01-01"),
            )
        conn.close()

    def test_student_not_null_constraints(self, initialized_db):
        conn = sqlite3.connect(initialized_db)
        c = conn.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO student (email, first_name, last_name, mq_id, created_at) VALUES (?, ?, ?, ?, ?)",
                ("a@example.com", None, "B", "12345", "2026-01-01"),
            )
        conn.close()

    def test_idempotent_on_current_schema(self, initialized_db, runner, monkeypatch):
        monkeypatch.setenv("OPENROUTER_PROVISIONING_KEY", "test-key")
        result = runner.invoke(cli, ["init-db", "--db", initialized_db])
        assert result.exit_code == 0
        assert "already at schema version" in result.output

    def test_rejects_outdated_schema_without_version_table(self, db_path, runner, monkeypatch):
        """A database with student table but no schema_version is rejected."""
        monkeypatch.setenv("OPENROUTER_PROVISIONING_KEY", "test-key")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE student (email TEXT PRIMARY KEY, name TEXT)")
        conn.commit()
        conn.close()

        result = runner.invoke(cli, ["init-db", "--db", db_path])
        assert result.exit_code != 0
        assert "outdated" in result.output

    def test_rejects_outdated_schema_version(self, db_path, runner, monkeypatch):
        """A database with old schema version is rejected."""
        monkeypatch.setenv("OPENROUTER_PROVISIONING_KEY", "test-key")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        conn.commit()
        conn.close()

        result = runner.invoke(cli, ["init-db", "--db", db_path])
        assert result.exit_code != 0
        assert "outdated" in result.output


class TestUpdateDatabase:
    def test_inserts_student_and_key(self, initialized_db):
        conn = sqlite3.connect(initialized_db)
        roster = {
            "yuki@example.com": {
                "first_name": "Yuki",
                "last_name": "Aoki",
                "mq_id": "48385123",
                "budget": 3.0,
                "limit_reset": "weekly",
            }
        }
        keys = [
            {
                "name": "20260227_Yuki Aoki_48385123",
                "hash": "abc123",
                "label": "test-label",
                "usage": 0.5,
                "limit": 3.0,
                "disabled": False,
                "created_at": "2026-02-27T00:00:00",
            }
        ]
        update_database(conn, keys, roster)

        c = conn.cursor()
        c.execute("SELECT first_name, last_name, mq_id FROM student WHERE email = ?", ("yuki@example.com",))
        row = c.fetchone()
        assert row == ("Yuki", "Aoki", "48385123")

        c.execute("SELECT key_hash, email FROM key WHERE key_hash = ?", ("abc123",))
        row = c.fetchone()
        assert row == ("abc123", "yuki@example.com")

        c.execute("SELECT usage FROM usage WHERE key_hash = ?", ("abc123",))
        row = c.fetchone()
        assert row[0] == 0.5

        conn.close()

    def test_upsert_updates_student_info(self, initialized_db):
        """ON CONFLICT(email) DO UPDATE should update name fields."""
        conn = sqlite3.connect(initialized_db)
        roster = {
            "yuki@example.com": {
                "first_name": "Yuki",
                "last_name": "Aoki",
                "mq_id": "48385123",
            }
        }
        keys = [{"name": "20260227_Yuki Aoki_48385123", "hash": "abc123", "usage": 0}]
        update_database(conn, keys, roster)

        # Update the name
        roster["yuki@example.com"]["first_name"] = "YUKI"
        update_database(conn, keys, roster)

        c = conn.cursor()
        c.execute("SELECT first_name FROM student WHERE email = ?", ("yuki@example.com",))
        assert c.fetchone()[0] == "YUKI"
        conn.close()

    def test_unmatched_keys_are_not_inserted(self, initialized_db):
        """Keys not matched to roster should not create student records."""
        conn = sqlite3.connect(initialized_db)
        keys = [{"name": "20260227_Unknown_99999999", "hash": "xyz789", "usage": 0}]
        update_database(conn, keys, {})

        c = conn.cursor()
        c.execute("SELECT count(*) FROM student")
        assert c.fetchone()[0] == 0
        c.execute("SELECT count(*) FROM key")
        assert c.fetchone()[0] == 0
        conn.close()
