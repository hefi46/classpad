"""Populate a freshly installed server's plugin catalogue.

Run automatically as part of the Docker container's startup (see
Dockerfile's CMD), before gunicorn starts — not just once: it's idempotent
(models.seed_default_catalogue only ever runs against a catalogue confirmed
empty via models.catalogue_is_empty()), so it's safe to run on every
container start against the same persistent /data volume, not just the
very first one, without ever touching an admin's already-customized
catalogue.

Bundles every plugins/<id>/ directory in the repo that has a manifest.json
into a zip in plugins_dir() and seeds the catalogue with the curated
default enabled/position order below (skips anything that fails
load_manifest's validation — a corrupt/missing manifest — the same
"one bad plugin doesn't take the rest down" treatment scan_plugins already
gives this on the client, logged rather than fatal).

DEFAULT_ENABLED_ORDER / DEFAULT_DISABLED is a project decision (2026-08-05,
see CLAUDE.md's Central Server section), not something to infer from the
plugins/ directory automatically — anything in plugins/ not listed in
either is skipped by the seed entirely (it still exists in the repo for a
manual admin-portal upload later; a plugin needs a human decision about its
default on/off state before it's part of the automatic seed, not an
automatic "everything on by default"). memory-game/storybook/emotion-checkin
are deliberately absent from both lists — they're currently placeholder
directories (just a .gitkeep, no manifest.json) with no scope decided yet.
"""

import sys
import zipfile
from pathlib import Path

from launcher.plugin_manager import PluginValidationError, load_manifest
from server import models
from server.app import create_app

REPO_PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins"

# Curated, vetted-enough-for-default-on set (see each plugin's manifest
# description for individual caveats). Order here is the launcher grid
# order.
DEFAULT_ENABLED_ORDER = [
    "tuxpaint",
    "tuxtype",
    "tuxmath",
    "gcompris",
    "coolmathgames",
    "wordprocessor",
    "xylophone",
    "libreoffice-writer",
]

# Included in the base image (so enabling them later is instant, no network
# install) but off by default — each still marked "test tile, not yet
# vetted for classroom use" in its own manifest description, or (cheese)
# camera access warranting an opt-in default rather than on-by-default.
DEFAULT_DISABLED = [
    "libreoffice-calc",
    "libreoffice-impress",
    "luanti",
    "luanti-singleplayer",
    "cheese",
]


def _zip_plugin_dir(plugin_dir: Path, dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(plugin_dir.rglob("*")):
            if f.is_file():
                zf.write(f, arcname=str(f.relative_to(plugin_dir)))


def _build_entries() -> list[dict]:
    entries = []
    for plugin_id in DEFAULT_ENABLED_ORDER + DEFAULT_DISABLED:
        plugin_dir = REPO_PLUGINS_DIR / plugin_id
        if not plugin_dir.is_dir():
            print(
                f"seed_plugins: warning: {plugin_id!r} not found in "
                f"{REPO_PLUGINS_DIR}, skipping",
                file=sys.stderr,
            )
            continue
        try:
            plugin = load_manifest(plugin_dir)
        except PluginValidationError as e:
            print(f"seed_plugins: warning: skipping {plugin_id!r}: {e}", file=sys.stderr)
            continue

        zip_filename = f"{plugin.id}.zip"
        _zip_plugin_dir(plugin_dir, models.plugins_dir() / zip_filename)

        enabled = plugin_id in DEFAULT_ENABLED_ORDER
        position = DEFAULT_ENABLED_ORDER.index(plugin_id) if enabled else None
        entries.append(
            {
                "id": plugin.id,
                "name": plugin.name,
                "version": plugin.version,
                "type": plugin.type,
                "description": plugin.description,
                "zip_filename": zip_filename,
                "enabled": enabled,
                "position": position,
            }
        )
    return entries


def main() -> None:
    app = create_app()
    with app.app_context():
        if not models.catalogue_is_empty():
            print("seed_plugins: catalogue already has entries, skipping seed")
            return
        entries = _build_entries()
        models.seed_default_catalogue(entries)
        print(f"seed_plugins: seeded {len(entries)} plugin(s)")


if __name__ == "__main__":
    main()
