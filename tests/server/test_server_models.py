"""Covers server/models.py behaviour that isn't reachable through a route
(currently just the settings-table migration) — see the other
tests/server/test_server_*.py files for route-level coverage.
"""

import sqlite3

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
