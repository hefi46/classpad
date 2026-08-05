"""Covers server/models.py behaviour that isn't reachable through a route
(the settings-table migration, and Lock to App's timed-expiry logic) — see
the other tests/server/test_server_*.py files for route-level coverage.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from server import models


def test_init_db_migrates_pre_existing_settings_table_missing_lock_to_app(tmp_path):
    """`lock_to_app` was added to `settings` after a real server had already
    been deployed with the old schema (see the comment above _ensure_column
    in models.py) — `CREATE TABLE IF NOT EXISTS` is a no-op against a table
    that already exists without the column, so init_db needs to actually
    ALTER it. Build exactly that pre-existing, old-schema DB by hand rather
    than going through init_db (which would already include the column),
    then confirm a second init_db call against it adds the column without
    losing the existing row's data.
    """
    db_path = tmp_path / "classpad.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE settings ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "background_color TEXT NOT NULL DEFAULT '#DCEEF7', "
        "background_image_filename TEXT)"
    )
    conn.execute(
        "INSERT INTO settings (id, background_color, background_image_filename) "
        "VALUES (1, '#123456', 'bg.png')"
    )
    conn.commit()
    conn.close()

    models.init_db(db_path)

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT background_color, background_image_filename, lock_to_app FROM settings WHERE id = 1"
    ).fetchone()
    conn.close()
    assert row == ("#123456", "bg.png", None)  # pre-existing data intact, new column present and NULL


def test_init_db_is_idempotent_against_already_migrated_schema(tmp_path):
    db_path = tmp_path / "classpad.db"
    models.init_db(db_path)
    models.init_db(db_path)  # second call (e.g. container restart) must not raise

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT lock_to_app FROM settings WHERE id = 1").fetchone()
    conn.close()
    assert row == (None,)


def test_set_lock_to_app_with_indefinite_duration_sets_no_expiry(app):
    with app.app_context():
        models.set_lock_to_app("tuxpaint", duration=None)
        assert models.get_settings()["lock_to_app_expires_at"] is None


def test_set_lock_to_app_with_duration_stores_a_future_expiry(app):
    with app.app_context():
        models.set_lock_to_app("tuxpaint", duration=timedelta(hours=2))
        settings = models.get_settings()
    assert settings["lock_to_app"] == "tuxpaint"
    expires_at = datetime.fromisoformat(settings["lock_to_app_expires_at"])
    delta = expires_at - datetime.now(timezone.utc)
    assert timedelta(hours=1, minutes=59) < delta <= timedelta(hours=2)


def test_get_settings_auto_clears_an_expired_lock(app):
    with app.app_context():
        models.set_lock_to_app("tuxpaint", duration=timedelta(hours=2))
        # Directly overwrite with an already-past expiry rather than
        # mocking the clock — set_lock_to_app itself always computes a
        # future expiry, so this is the simplest way to get an expired one
        # into the DB for the test.
        db = models.get_db()
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        db.execute("UPDATE settings SET lock_to_app_expires_at = ? WHERE id = 1", (past,))
        db.commit()

        settings = models.get_settings()
    assert settings["lock_to_app"] is None
    assert settings["lock_to_app_expires_at"] is None


def test_get_settings_leaves_a_not_yet_expired_lock_alone(app):
    with app.app_context():
        models.set_lock_to_app("tuxpaint", duration=timedelta(hours=2))
        settings = models.get_settings()
    assert settings["lock_to_app"] == "tuxpaint"
    assert settings["lock_to_app_expires_at"] is not None


def test_get_config_reflects_auto_cleared_expired_lock(app, client):
    with app.app_context():
        models.set_lock_to_app("tuxpaint", duration=timedelta(hours=2))
        db = models.get_db()
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        db.execute("UPDATE settings SET lock_to_app_expires_at = ? WHERE id = 1", (past,))
        db.commit()

    data = client.get("/config/11e-abc123").get_json()
    assert data["lock_to_app"] is None
    assert "lock_to_app_expires_at" not in data  # never leaked to the client — see get_config's docstring
