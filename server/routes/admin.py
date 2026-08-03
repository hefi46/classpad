"""Admin portal: single-account login, machine roster, and the shared
plugin profile (CLAUDE.md: "single account, credentials in environment
variable"; "Keep the portal simple — this is an internal tool").

All state-changing views are plain HTML forms with full-page POSTs — no JS
reorder widget, matching the mockup's "simplicity is the brand" reasoning
from the 2026-07-31 profile-model revision (see models.py). The profile is
edited incrementally (toggle one plugin, nudge one up/down) rather than
submitted as one big reordered list, which is what makes a JS-free form UI
practical.

Session-based auth guards every route in this module. The machine-facing
endpoints in config.py/plugins.py deliberately stay unauthenticated (local
network only, same trust model as the rest of the project) — this is the
one part of the system with a login, because it's the one part that can
push arbitrary plugin code (see CLAUDE.md's Plugin trust model) or force a
machine's screen. A synchronizer CSRF token guards every POST here for the
same reason: it's the one place in the system a forged request could do
real damage.
"""

import json
import re
import secrets
import shutil
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from launcher.plugin_manager import PluginValidationError, load_manifest
from server import models

bp = Blueprint("admin", __name__, url_prefix="/admin")

_ONLINE_WITHIN = 120  # seconds
_STALE_WITHIN = 3600  # seconds


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@bp.context_processor
def inject_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return {"csrf_token": session["csrf_token"]}


def _check_csrf():
    token = request.form.get("csrf_token", "")
    if not secrets.compare_digest(token, session.get("csrf_token", "")):
        abort(403)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        valid = secrets.compare_digest(
            username, current_app.config["ADMIN_USERNAME"]
        ) and secrets.compare_digest(password, current_app.config["ADMIN_PASSWORD"])
        if valid:
            session.clear()
            session["admin"] = True
            return redirect(request.args.get("next") or url_for("admin.machines"))
        return render_template("admin/login.html", error="Incorrect username or password."), 401
    return render_template("admin/login.html", error=None)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@bp.route("/")
@login_required
def index():
    return redirect(url_for("admin.machines"))


def _machine_status(machine: models.Machine) -> str:
    if machine.last_seen is None:
        return "offline"
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(machine.last_seen)).total_seconds()
    if age < _ONLINE_WITHIN:
        return "online"
    if age < _STALE_WITHIN:
        return "stale"
    return "offline"


@bp.route("/machines")
@login_required
def machines():
    rows = [(m, _machine_status(m)) for m in models.list_machines()]
    return render_template("admin/machines.html", rows=rows)


@bp.route("/machines/<machine_id>/display-name", methods=["POST"])
@login_required
def set_display_name(machine_id):
    _check_csrf()
    display_name = request.form.get("display_name", "").strip() or None
    models.set_display_name(machine_id, display_name)
    return redirect(url_for("admin.machines"))


@bp.route("/machines/<machine_id>/force-home", methods=["POST"])
@login_required
def force_home(machine_id):
    _check_csrf()
    models.set_force_home(machine_id, True)
    flash(f"Return-to-Home sent to {machine_id} — clears once it checks in.")
    return redirect(url_for("admin.machines"))


@bp.route("/plugins")
@login_required
def plugins():
    all_plugins = models.list_plugins()
    enabled = sorted((p for p in all_plugins if p.enabled), key=lambda p: p.position)
    disabled = sorted((p for p in all_plugins if not p.enabled), key=lambda p: p.name)
    return render_template(
        "admin/plugins.html",
        plugins=enabled + disabled,
        enabled_count=len(enabled),
        settings=models.get_settings(),
    )


@bp.route("/plugins/<plugin_id>/toggle", methods=["POST"])
@login_required
def toggle_plugin(plugin_id):
    _check_csrf()
    models.toggle_plugin_enabled(plugin_id)
    return redirect(url_for("admin.plugins"))


@bp.route("/plugins/<plugin_id>/move", methods=["POST"])
@login_required
def move_plugin(plugin_id):
    _check_csrf()
    direction = request.form.get("direction")
    if direction in ("up", "down"):
        models.move_plugin(plugin_id, direction)
    return redirect(url_for("admin.plugins"))


def _reject_traversal(zf: zipfile.ZipFile) -> str | None:
    """Same check as scripts/plugin-install.sh's Python block, applied here
    too so a malicious zip is rejected at the upload boundary rather than
    only ever being caught client-side, one kiosk at a time."""
    for name in zf.namelist():
        normalized = name.replace("\\", "/")
        if normalized.startswith("/"):
            return f"archive entry has an absolute path: {name}"
        if ".." in normalized.split("/"):
            return f"archive entry attempts path traversal: {name}"
    return None


@bp.route("/plugins/upload", methods=["POST"])
@login_required
def upload_plugin():
    _check_csrf()
    file = request.files.get("plugin_zip")
    if file is None or file.filename == "":
        flash("Choose a .zip file to upload.")
        return redirect(url_for("admin.plugins"))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # The browser-supplied filename is never used to build a path —
        # only the manifest's validated `id` is, same rule as
        # plugin-install.sh and the /plugins/<id>/download route.
        zip_path = tmp_path / "upload.zip"
        file.save(zip_path)

        try:
            with zipfile.ZipFile(zip_path) as zf:
                traversal_error = _reject_traversal(zf)
                if traversal_error:
                    flash(f"Rejected: {traversal_error}")
                    return redirect(url_for("admin.plugins"))
                staging_dir = tmp_path / "staging"
                staging_dir.mkdir()
                zf.extractall(staging_dir)
        except zipfile.BadZipFile:
            flash("That file isn't a valid zip archive.")
            return redirect(url_for("admin.plugins"))

        manifest_paths = sorted(
            staging_dir.rglob("manifest.json"), key=lambda p: len(p.parts)
        )
        if not manifest_paths:
            flash("No manifest.json found in the archive.")
            return redirect(url_for("admin.plugins"))

        try:
            plugin = load_manifest(manifest_paths[0].parent)
        except PluginValidationError as e:
            flash(f"Invalid manifest: {e}")
            return redirect(url_for("admin.plugins"))

        zip_filename = f"{plugin.id}.zip"
        shutil.copy(zip_path, models.plugins_dir() / zip_filename)
        models.upsert_plugin(
            plugin.id,
            name=plugin.name,
            version=plugin.version,
            type=plugin.type,
            description=plugin.description,
            zip_filename=zip_filename,
        )
        flash(f"Uploaded {plugin.name} {plugin.version} — enable it below to add it to the profile.")

    return redirect(url_for("admin.plugins"))


def _slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "site"


def _unique_plugin_id(base_slug: str) -> str:
    existing = {p.id for p in models.list_plugins()}
    if base_slug not in existing:
        return base_slug
    n = 2
    while f"{base_slug}-{n}" in existing:
        n += 1
    return f"{base_slug}-{n}"


@bp.route("/plugins/create-website", methods=["POST"])
@login_required
def create_website_plugin():
    """Builds a type:website plugin (manifest + zip) from a name/URL/icon
    entered directly in the portal, instead of requiring the admin to
    hand-assemble a zip like upload_plugin above — same install path once
    the manifest is built, just generated here rather than uploaded.
    """
    _check_csrf()
    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip()
    chromium_flags = request.form.get("chromium_flags", "").strip()
    enable_now = request.form.get("enable_now") == "on"
    icon_file = request.files.get("icon")

    if not name or not url:
        flash("A name and URL are required to create a website tile.")
        return redirect(url_for("admin.plugins"))
    if not (url.startswith("http://") or url.startswith("https://")):
        flash("URL must start with http:// or https://.")
        return redirect(url_for("admin.plugins"))
    if icon_file is None or icon_file.filename == "":
        flash("Choose an icon image for the tile.")
        return redirect(url_for("admin.plugins"))

    plugin_id = _unique_plugin_id(_slugify(name))

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = Path(tmp) / plugin_id
        plugin_dir.mkdir()
        icon_file.save(plugin_dir / "icon.png")

        manifest = {
            "id": plugin_id,
            "name": name,
            "version": "1.0.0",
            "type": "website",
            "url": url,
            "icon": "icon.png",
            "description": f"Website: {url}",
        }
        if chromium_flags:
            manifest["chromium_flags"] = chromium_flags
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        try:
            plugin = load_manifest(plugin_dir)
        except PluginValidationError as e:
            flash(f"Could not create tile: {e}")
            return redirect(url_for("admin.plugins"))

        zip_filename = f"{plugin.id}.zip"
        with zipfile.ZipFile(models.plugins_dir() / zip_filename, "w") as zf:
            zf.write(plugin_dir / "manifest.json", "manifest.json")
            zf.write(plugin_dir / "icon.png", "icon.png")

        models.upsert_plugin(
            plugin.id,
            name=plugin.name,
            version=plugin.version,
            type=plugin.type,
            description=plugin.description,
            zip_filename=zip_filename,
        )
        if enable_now:
            models.toggle_plugin_enabled(plugin.id)

    suffix = " and enabled it." if enable_now else " — enable it above to add it to the profile."
    flash(f"Created website tile '{name}'{suffix}")
    return redirect(url_for("admin.plugins"))


@bp.route("/background/color", methods=["POST"])
@login_required
def set_background_color():
    _check_csrf()
    color = request.form.get("color", "").strip()
    if not re.match(r"^#[0-9a-fA-F]{6}$", color):
        flash("Background colour must be a hex value like #add8f0.")
        return redirect(url_for("admin.plugins"))
    models.set_background_color(color)
    flash("Background colour updated — applies to every machine on their next check-in.")
    return redirect(url_for("admin.plugins"))


@bp.route("/background/image", methods=["POST"])
@login_required
def upload_background_image():
    _check_csrf()
    file = request.files.get("background_image")
    if file is None or file.filename == "":
        flash("Choose an image file for the background.")
        return redirect(url_for("admin.plugins"))

    old_filename = models.get_settings()["background_image_filename"]
    # Random filename (not derived from the upload) so image_version in
    # get_config() always changes on a re-upload, even if the admin
    # re-uploads a file with the same original name.
    new_filename = f"background-{secrets.token_hex(8)}.png"
    file.save(models.background_dir() / new_filename)
    models.set_background_image(new_filename)
    if old_filename:
        (models.background_dir() / old_filename).unlink(missing_ok=True)

    flash("Background artwork updated — applies to every machine on their next check-in.")
    return redirect(url_for("admin.plugins"))


@bp.route("/background/image/clear", methods=["POST"])
@login_required
def clear_background_image():
    _check_csrf()
    old_filename = models.get_settings()["background_image_filename"]
    models.set_background_image(None)
    if old_filename:
        (models.background_dir() / old_filename).unlink(missing_ok=True)
    flash("Background artwork removed — machines fall back to the solid colour.")
    return redirect(url_for("admin.plugins"))
