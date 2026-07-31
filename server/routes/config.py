"""Machine config and telemetry.

Telemetry lives here rather than its own route module (not in CLAUDE.md's
directory tree) because it only ever touches the same `machines` table
`/config` reads — it's the write side of the same state, including the
force_home acknowledgement (see models.record_telemetry).
"""

from flask import Blueprint, jsonify, request

from server import models

bp = Blueprint("config", __name__)


@bp.get("/config/<machine_id>")
def get_config(machine_id):
    return jsonify(models.get_config(machine_id))


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
