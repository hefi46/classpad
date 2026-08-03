"""Machine config and telemetry.

Telemetry lives here rather than its own route module (not in CLAUDE.md's
directory tree) because it only ever touches the same `machines` table
`/config` reads — it's the write side of the same state, including the
force_home acknowledgement (see models.record_telemetry).
"""

from flask import Blueprint, abort, jsonify, request, send_from_directory

from server import models

bp = Blueprint("config", __name__)


@bp.get("/config/<machine_id>")
def get_config(machine_id):
    return jsonify(models.get_config(machine_id))


@bp.get("/background/image")
def get_background_image():
    """Serves the current global background artwork, if one is set.

    Filename comes straight from the settings row (admin.py generates it
    with secrets.token_hex, never from user input), so send_from_directory
    here is a formality rather than a path-traversal boundary like the
    plugin download route.
    """
    filename = models.get_settings()["background_image_filename"]
    if not filename:
        abort(404)
    return send_from_directory(models.background_dir(), filename)


@bp.post("/telemetry/<machine_id>")
def post_telemetry(machine_id):
    body = request.get_json(silent=True) or {}
    models.record_telemetry(
        machine_id,
        current_activity=body.get("current_activity"),
        error=body.get("error"),
        force_home_ack=bool(body.get("force_home_ack", False)),
    )
    return jsonify({"ok": True})
