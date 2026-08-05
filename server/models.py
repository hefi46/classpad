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
sticker on the machine. Also surfaced back to the client itself via
`get_config()` (2026-08-01) so the bar's info panel can show it — a
teacher glancing at the info panel wants to confirm "is this blue-3", not
just see it in the admin portal.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

-- Single row (id=1) — global launcher appearance, same "one shared profile"
-- philosophy as the plugin table: every machine gets the same background,
-- there is no per-machine override.
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    background_color TEXT NOT NULL DEFAULT '#DCEEF7',
    background_image_filename TEXT,
    lock_to_app TEXT,
    lock_to_app_expires_at TEXT
);
"""

# `lock_to_app` was added to `settings` after `CREATE TABLE IF NOT EXISTS`
# above had already shipped to a real deployed server (see CLAUDE.md's
# "Server host bring-up" notes) — that server's `settings` table already
# exists on disk without the column, and `IF NOT EXISTS` is a no-op against
# it, so a plain ALTER TABLE is needed on top. Same idempotent-migration
# shape either way: check first, only alter if missing, so this is also a
# genuine no-op against a schema that already has the column (every fresh
# `CREATE TABLE` from here on already includes it).
def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

# Curated background themes (2026-08-03 redesign) — replaces a freeform hex
# colour picker in the admin portal. A raw <input type="color"> let an admin
# pick a background that clashed with or killed contrast against the
# launcher's fixed tile/accent palette (launcher/button.py); a small,
# pre-approved set is guaranteed to work with it. Storage stays a plain hex
# string in `settings.background_color` — the client-facing /config
# contract doesn't change, only how the admin portal lets you choose one.
BACKGROUND_THEMES = {
    "Sky": "#DCEEF7",
    "Mint": "#E2F0E4",
    "Blush": "#FBE7E6",
    "Butter": "#FCF2DA",
    "Lilac": "#ECE4F5",
}

# Curated durations for Lock to App (2026-08-05), same "fixed set, not a
# freeform input" reasoning as BACKGROUND_THEMES — a raw number-of-hours
# field invites a typo that locks a classroom out for a nonsense span with
# no easy undo if the admin isn't back at a computer. `None` (indefinite)
# means no expiry at all, matching the feature's original no-timer
# behaviour — existing/manual `set_lock_to_app` calls that don't pass
# `expires_at` stay indefinite by default, so this is purely additive.
LOCK_DURATIONS = {
    "2h": timedelta(hours=2),
    "24h": timedelta(hours=24),
    "indefinite": None,
}
# Kept alongside LOCK_DURATIONS rather than derived from its keys, so a
# label can read naturally ("2 hours") without reverse-parsing "2h".
LOCK_DURATION_LABELS = {
    "2h": "2 hours",
    "24h": "24 hours",
    "indefinite": "indefinitely",
}


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
        _ensure_column(conn, "settings", "lock_to_app", "TEXT")
        _ensure_column(conn, "settings", "lock_to_app_expires_at", "TEXT")
        conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
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


def background_dir() -> Path:
    d = flask.current_app.config["DATA_DIR"] / "background"
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


def _expire_lock_if_needed(db: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
    """Lazily clear an expired timed lock on read, rather than running a
    scheduler — there's no job-scheduling infra anywhere else in this
    project (SQLite, no Celery/cron), and every machine already polls
    /config every 30s, so the lock is guaranteed to be re-checked soon
    after it actually expires regardless of whether an admin is looking at
    the portal. Every caller of `settings` goes through get_settings()
    (get_config() included), so doing it here covers all of them in one
    place. Mutating state inside a "getter" is a deliberate lazy-TTL
    pattern, not an oversight.
    """
    expires_at = row["lock_to_app_expires_at"]
    if row["lock_to_app"] and expires_at:
        if datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc):
            db.execute(
                "UPDATE settings SET lock_to_app = NULL, lock_to_app_expires_at = NULL WHERE id = 1"
            )
            db.commit()
            row = db.execute(
                "SELECT background_color, background_image_filename, lock_to_app, "
                "lock_to_app_expires_at FROM settings WHERE id = 1"
            ).fetchone()
    return row


def get_settings() -> dict:
    db = get_db()
    row = db.execute(
        "SELECT background_color, background_image_filename, lock_to_app, "
        "lock_to_app_expires_at FROM settings WHERE id = 1"
    ).fetchone()
    row = _expire_lock_if_needed(db, row)
    return {
        "background_color": row["background_color"],
        "background_image_filename": row["background_image_filename"],
        "lock_to_app": row["lock_to_app"],
        "lock_to_app_expires_at": row["lock_to_app_expires_at"],
    }


def set_background_color(color: str) -> None:
    db = get_db()
    db.execute("UPDATE settings SET background_color = ? WHERE id = 1", (color,))
    db.commit()


def set_background_image(filename: str | None) -> None:
    db = get_db()
    db.execute(
        "UPDATE settings SET background_image_filename = ? WHERE id = 1", (filename,)
    )
    db.commit()


def set_lock_to_app(plugin_id: str | None, duration: timedelta | None = None) -> None:
    """Global, same "one shared profile" philosophy as background/plugins —
    there is no per-machine lock, `plugin_id=None` clears it (and its
    expiry, if any). The caller (admin.py) is responsible for only ever
    passing an id that's currently an enabled catalogue entry, and a
    `duration` that's one of LOCK_DURATIONS' values; this layer doesn't
    re-validate either (same division of responsibility as
    set_profile/toggle_plugin_enabled below, which don't validate ids
    either).

    `duration=None` means indefinite — no expiry column set, matching this
    function's original (pre-timer) behaviour, so every existing call site
    that doesn't pass a duration keeps working unchanged.
    """
    expires_at = (
        (datetime.now(timezone.utc) + duration).isoformat() if (plugin_id and duration) else None
    )
    db = get_db()
    db.execute(
        "UPDATE settings SET lock_to_app = ?, lock_to_app_expires_at = ? WHERE id = 1",
        (plugin_id, expires_at),
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

    `background` is the one part of this response that isn't per-machine —
    same global settings row for every machine_id, folded in here rather
    than a separate endpoint since the client already polls this one on the
    same schedule. `image_version` is just the stored filename itself (it
    changes on every upload, see admin.py's upload_background_image) — good
    enough as a change marker without a separate version column.

    `lock_to_app` is the same global-settings pattern again — every machine
    gets the identical value, "locks all devices" per the feature's own
    framing, not a per-machine flag like force_home. Just the plugin id (or
    null); the client already has full manifest detail for anything
    installed locally, same reasoning as the `plugins` list above. Worth
    noting this id is deliberately *not* filtered against the `plugins`
    list above (which only carries enabled ones) — if an admin disables the
    locked plugin without clearing the lock first, the client still needs
    to see which id it's supposed to be locked to in order to detect that
    it's no longer resolvable and fall back safely (see launcher/main.py).
    A timed lock (LOCK_DURATIONS) that's since expired is already cleared
    by the time this reads it — see get_settings()/_expire_lock_if_needed,
    called here rather than duplicated, so a poll landing right after
    expiry sees `null` with no separate expiry-check of its own.
    """
    upsert_machine(machine_id)
    db = get_db()
    machine_row = db.execute(
        "SELECT force_home, display_name FROM machines WHERE id = ?", (machine_id,)
    ).fetchone()
    rows = db.execute(
        "SELECT id, version FROM plugins WHERE enabled = 1 ORDER BY position"
    ).fetchall()
    settings = get_settings()
    return {
        "machine_id": machine_id,
        "display_name": machine_row["display_name"],
        "plugins": [{"id": r["id"], "version": r["version"]} for r in rows],
        "force_home": bool(machine_row["force_home"]),
        "background": {
            "color": settings["background_color"],
            "image_version": settings["background_image_filename"],
        },
        "lock_to_app": settings["lock_to_app"],
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


def delete_plugin(plugin_id: str) -> str | None:
    """Remove a catalogue entry entirely (not just disable it).

    Returns the deleted row's zip_filename so the caller can remove the file
    from disk (kept out of this function — plugins_dir() file I/O lives in
    admin.py, same split as upload_background_image's old-file cleanup).
    Returns None if there was no such plugin, so the route can 404/flash
    without a separate existence check.

    Deliberately does not touch machines that already have this plugin
    installed locally — Phase 10's plugin_deploy.py never removes
    locally-installed plugins that vanish from the catalogue (out of its
    stated scope), so a deleted catalogue entry just stops being offered/
    enabled going forward.
    """
    db = get_db()
    row = db.execute(
        "SELECT zip_filename, enabled FROM plugins WHERE id = ?", (plugin_id,)
    ).fetchone()
    if row is None:
        return None
    db.execute("DELETE FROM plugins WHERE id = ?", (plugin_id,))
    if row["enabled"]:
        _renumber_enabled(db)
    db.commit()
    return row["zip_filename"]


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


def catalogue_is_empty() -> bool:
    db = get_db()
    return db.execute("SELECT COUNT(*) AS c FROM plugins").fetchone()["c"] == 0


def seed_default_catalogue(entries: list[dict]) -> None:
    """Populate a brand-new catalogue with the project's curated plugin set.

    Called once from server/seed_plugins.py (run from the Docker CMD, before
    gunicorn starts on every container start) — deliberately not wired into
    init_db()/create_app() itself, since every test calls create_app()
    against a fresh empty DB (tests/server/conftest.py) and would otherwise
    get silently seeded too, changing what "empty catalogue" means
    throughout the existing test suite.

    A no-op unless the catalogue is genuinely empty (checked by the caller
    via catalogue_is_empty(), not re-checked here, so a caller building
    `entries` — which involves reading manifests and writing zips to
    plugins_dir() — can skip that work entirely on the common case of an
    already-seeded server, not just skip the DB write), so this never
    overwrites an admin's already-customized catalogue: docker-compose.yml
    has `restart: unless-stopped`, so the container (and this script) can
    start many times against the same persistent /data volume, not just
    once.

    `entries` dicts carry id/name/version/type/description/zip_filename
    (the same shape upsert_plugin takes) plus `enabled`/`position` — built
    by the caller from each plugins/<id>/manifest.json. Unlike upsert_plugin,
    this does set enabled/position, since every row here is a fresh INSERT
    (never an update — the empty-catalogue precondition guarantees no
    conflict), and the whole point of seeding is to land with a curated
    default profile already in place, not an empty one.
    """
    db = get_db()
    for e in entries:
        db.execute(
            "INSERT INTO plugins (id, name, version, type, description, "
            "zip_filename, enabled, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e["id"],
                e["name"],
                e["version"],
                e["type"],
                e["description"],
                e["zip_filename"],
                int(e["enabled"]),
                e["position"],
            ),
        )
    db.commit()
