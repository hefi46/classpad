"""SQLite-backed models for the central server.

Plain sqlite3, not an ORM — matches the rest of the project (no ORM
anywhere else) and this deployment's scale (a classroom's worth of
machines). Connections are scoped to `flask.g` per request since sqlite3
connections aren't thread-safe and Flask's server is threaded.

**Single shared plugin profile, not per-machine assignment.** Originally
Phase 7 had a machine<->plugin `assignments` join table so each machine
could carry its own button layout. Revisited 2026-07-31 while reviewing an
admin-portal mockup: individual per-machine layouts added real admin-portal
surface (an assignment UI per machine) for a use case that doesn't exist —
one classroom shares one set of apps. Dropped the `assignments` table
entirely; `enabled`/`position` now live directly on `plugins` as one global
profile every machine's `/config` reflects identically. `Config` is still
not a table — it's the JSON `get_config()` composes from a machine's
force_home flag plus the shared enabled/ordered plugin list.

`machines.display_name` is a separate, unrelated decision from the same
conversation: the hostname (`11e-<serial>`, from CLAUDE.md's Network &
Deployment Context) stays the machine's real identity — free, unique,
needs no coordination at image time — but is a poor thing to make a teacher
read off a screen. `display_name` is an optional admin-set friendly label
("blue-3") shown in the portal in its place and matched to a physical
sticker on the machine; the client never sees or uses it.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import flask

SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    last_seen TEXT,
    current_activity TEXT,
    last_error TEXT,
    force_home INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS plugins (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    zip_filename TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    position INTEGER
);
"""


@dataclass
class Machine:
    id: str
    display_name: str | None
    last_seen: str | None
    current_activity: str | None
    last_error: str | None
    force_home: bool


@dataclass
class Plugin:
    id: str
    name: str
    version: str
    type: str
    description: str
    zip_filename: str
    enabled: bool
    position: int | None


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    if "db" not in flask.g:
        db_path = flask.current_app.config["DATA_DIR"] / "classpad.db"
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        flask.g.db = db
    return flask.g.db


def close_db(_exception=None) -> None:
    db = flask.g.pop("db", None)
    if db is not None:
        db.close()


def plugins_dir() -> Path:
    d = flask.current_app.config["DATA_DIR"] / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_machine(machine_id: str) -> None:
    """Create a machine row if it doesn't exist yet.

    A freshly imaged machine (Phase 16) has no registration step of its own
    — its first /config or /telemetry request is what brings it into
    existence server-side. It immediately gets the shared plugin profile
    (there's nothing per-machine to assign); an admin can give it a
    display_name at their leisure.
    """
    db = get_db()
    db.execute(
        "INSERT INTO machines (id, force_home) VALUES (?, 0) "
        "ON CONFLICT(id) DO NOTHING",
        (machine_id,),
    )
    db.commit()


def get_machine(machine_id: str) -> Machine | None:
    db = get_db()
    row = db.execute("SELECT * FROM machines WHERE id = ?", (machine_id,)).fetchone()
    if row is None:
        return None
    return Machine(
        id=row["id"],
        display_name=row["display_name"],
        last_seen=row["last_seen"],
        current_activity=row["current_activity"],
        last_error=row["last_error"],
        force_home=bool(row["force_home"]),
    )


def list_machines() -> list[Machine]:
    db = get_db()
    rows = db.execute("SELECT * FROM machines ORDER BY id").fetchall()
    return [
        Machine(
            id=row["id"],
            display_name=row["display_name"],
            last_seen=row["last_seen"],
            current_activity=row["current_activity"],
            last_error=row["last_error"],
            force_home=bool(row["force_home"]),
        )
        for row in rows
    ]


def set_display_name(machine_id: str, display_name: str | None) -> None:
    upsert_machine(machine_id)
    db = get_db()
    db.execute(
        "UPDATE machines SET display_name = ? WHERE id = ?",
        (display_name, machine_id),
    )
    db.commit()


def record_telemetry(
    machine_id: str,
    *,
    current_activity: str | None,
    error: str | None,
    force_home_ack: bool,
) -> None:
    """Update a machine's telemetry fields.

    `force_home_ack=True` is how the client tells the server it has acted on
    a pending force_home command (Phase 9) — telemetry doubles as the ack
    channel rather than needing a separate endpoint, since the schema was
    going to carry current_activity/error either way.
    """
    upsert_machine(machine_id)
    db = get_db()
    if force_home_ack:
        db.execute(
            "UPDATE machines SET last_seen = ?, current_activity = ?, "
            "last_error = ?, force_home = 0 WHERE id = ?",
            (_now_iso(), current_activity, error, machine_id),
        )
    else:
        db.execute(
            "UPDATE machines SET last_seen = ?, current_activity = ?, "
            "last_error = ? WHERE id = ?",
            (_now_iso(), current_activity, error, machine_id),
        )
    db.commit()


def set_force_home(machine_id: str, value: bool) -> None:
    upsert_machine(machine_id)
    db = get_db()
    db.execute(
        "UPDATE machines SET force_home = ? WHERE id = ?",
        (1 if value else 0, machine_id),
    )
    db.commit()


def get_config(machine_id: str) -> dict:
    """Compose the /config/<machine_id> response.

    Every machine gets the same plugin list — the shared profile — so
    `machine_id` here only identifies whose force_home flag to report and
    triggers auto-registration (see upsert_machine). Deliberately minimal:
    just plugin ids/versions in profile order, plus force_home. The client
    already has full manifest detail (icon, launch_command, ...) for
    anything it has installed locally (Phase 3); this only needs to carry
    enough for the client to detect "new/updated/removed since last poll"
    (Phase 9/10).
    """
    upsert_machine(machine_id)
    db = get_db()
    machine_row = db.execute(
        "SELECT force_home FROM machines WHERE id = ?", (machine_id,)
    ).fetchone()
    rows = db.execute(
        "SELECT id, version FROM plugins WHERE enabled = 1 ORDER BY position"
    ).fetchall()
    return {
        "machine_id": machine_id,
        "plugins": [{"id": r["id"], "version": r["version"]} for r in rows],
        "force_home": bool(machine_row["force_home"]),
    }


def set_profile(plugin_ids: list[str]) -> None:
    """Replace the shared plugin profile with this ordered list of ids.

    Applies to every machine — there is one profile, not one per machine.
    Anything not in `plugin_ids` is disabled (still in the catalogue,
    just not handed out by /config).
    """
    db = get_db()
    db.execute("UPDATE plugins SET enabled = 0, position = NULL")
    db.executemany(
        "UPDATE plugins SET enabled = 1, position = ? WHERE id = ?",
        [(i, plugin_id) for i, plugin_id in enumerate(plugin_ids)],
    )
    db.commit()


def _renumber_enabled(db: sqlite3.Connection) -> None:
    rows = db.execute(
        "SELECT id FROM plugins WHERE enabled = 1 ORDER BY position"
    ).fetchall()
    for i, row in enumerate(rows):
        db.execute("UPDATE plugins SET position = ? WHERE id = ?", (i, row["id"]))


def toggle_plugin_enabled(plugin_id: str) -> None:
    """Flip one plugin's membership in the shared profile.

    The admin-portal profile screen is plain forms (no JS reorder widget),
    so the profile is edited incrementally — toggle one plugin, nudge one
    plugin up/down — rather than replaced wholesale like `set_profile`
    (which still exists for tests/scripting).
    """
    db = get_db()
    row = db.execute(
        "SELECT enabled FROM plugins WHERE id = ?", (plugin_id,)
    ).fetchone()
    if row is None:
        return
    if row["enabled"]:
        db.execute(
            "UPDATE plugins SET enabled = 0, position = NULL WHERE id = ?",
            (plugin_id,),
        )
        _renumber_enabled(db)
    else:
        count = db.execute(
            "SELECT COUNT(*) AS c FROM plugins WHERE enabled = 1"
        ).fetchone()["c"]
        db.execute(
            "UPDATE plugins SET enabled = 1, position = ? WHERE id = ?",
            (count, plugin_id),
        )
    db.commit()


def move_plugin(plugin_id: str, direction: str) -> None:
    """Swap an enabled plugin with its neighbour. direction: 'up' or 'down'."""
    db = get_db()
    rows = db.execute(
        "SELECT id FROM plugins WHERE enabled = 1 ORDER BY position"
    ).fetchall()
    ids = [r["id"] for r in rows]
    if plugin_id not in ids:
        return
    idx = ids.index(plugin_id)
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(ids):
        return
    other_id = ids[swap_idx]
    db.execute("UPDATE plugins SET position = ? WHERE id = ?", (swap_idx, plugin_id))
    db.execute("UPDATE plugins SET position = ? WHERE id = ?", (idx, other_id))
    db.commit()


def list_plugins() -> list[Plugin]:
    db = get_db()
    rows = db.execute(
        "SELECT id, name, version, type, description, zip_filename, "
        "enabled, position FROM plugins ORDER BY name"
    ).fetchall()
    return [_plugin_from_row(row) for row in rows]


def get_plugin(plugin_id: str) -> Plugin | None:
    db = get_db()
    row = db.execute(
        "SELECT id, name, version, type, description, zip_filename, "
        "enabled, position FROM plugins WHERE id = ?",
        (plugin_id,),
    ).fetchone()
    if row is None:
        return None
    return _plugin_from_row(row)


def _plugin_from_row(row: sqlite3.Row) -> Plugin:
    return Plugin(
        id=row["id"],
        name=row["name"],
        version=row["version"],
        type=row["type"],
        description=row["description"],
        zip_filename=row["zip_filename"],
        enabled=bool(row["enabled"]),
        position=row["position"],
    )


def upsert_plugin(
    plugin_id: str,
    *,
    name: str,
    version: str,
    type: str,
    description: str,
    zip_filename: str,
) -> None:
    """Add or update a catalogue entry. Never touches enabled/position —
    those are only ever changed via `set_profile`, so re-uploading a plugin
    (e.g. a version bump) doesn't silently add or reorder it in the profile.
    """
    db = get_db()
    db.execute(
        "INSERT INTO plugins (id, name, version, type, description, zip_filename) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "name = excluded.name, version = excluded.version, type = excluded.type, "
        "description = excluded.description, zip_filename = excluded.zip_filename",
        (plugin_id, name, version, type, description, zip_filename),
    )
    db.commit()
