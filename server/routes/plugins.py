"""Plugin catalogue and download.

`download_plugin` validates `plugin_id` against the same
`^[a-z0-9][a-z0-9-]*$` pattern CLAUDE.md mandates for plugin ids elsewhere
(plugin_manager.py, plugin-install.sh) *before* it touches the database or
filesystem, and uses `send_from_directory` rather than joining the id into
a path by hand — this endpoint builds a filesystem path from a URL segment,
which is the same path-traversal shape as those other two call sites.
"""

import re

from flask import Blueprint, abort, jsonify, send_from_directory

from server import models

bp = Blueprint("plugins", __name__)

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@bp.get("/plugins/")
def list_plugins():
    return jsonify(
        [
            {
                "id": p.id,
                "name": p.name,
                "version": p.version,
                "type": p.type,
                "description": p.description,
            }
            for p in models.list_plugins()
        ]
    )


@bp.get("/plugins/<plugin_id>/download")
def download_plugin(plugin_id):
    if not ID_PATTERN.match(plugin_id):
        abort(404)
    plugin = models.get_plugin(plugin_id)
    if plugin is None:
        abort(404)
    return send_from_directory(
        models.plugins_dir(), plugin.zip_filename, as_attachment=True
    )
