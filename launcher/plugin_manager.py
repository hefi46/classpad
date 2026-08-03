import json
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

import pygame

PLUGINS_DIR = Path("/opt/classpad/plugins")

VALID_TYPES = {"app", "website", "custom"}
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class PluginValidationError(Exception):
    pass


@dataclass
class Plugin:
    id: str
    name: str
    version: str
    type: str
    icon: Path
    description: str
    launch_command: str = None
    url: str = None
    chromium_flags: str = None


def validate_manifest(data, plugin_dir):
    for field in ("id", "name", "version", "type", "icon", "description"):
        if field not in data:
            raise PluginValidationError(f"missing required field '{field}'")
        if not isinstance(data[field], str):
            raise PluginValidationError(f"field '{field}' must be a string")

    plugin_id = data["id"]
    if not ID_PATTERN.match(plugin_id):
        raise PluginValidationError(
            f"invalid id '{plugin_id}' — must match {ID_PATTERN.pattern}"
        )

    if not VERSION_PATTERN.match(data["version"]):
        raise PluginValidationError(f"invalid version '{data['version']}' — expected major.minor.patch")

    plugin_type = data["type"]
    if plugin_type not in VALID_TYPES:
        raise PluginValidationError(f"invalid type '{plugin_type}' — must be one of {sorted(VALID_TYPES)}")

    plugin_dir_resolved = plugin_dir.resolve()
    icon_path = (plugin_dir / data["icon"]).resolve()
    if plugin_dir_resolved not in icon_path.parents and icon_path != plugin_dir_resolved:
        raise PluginValidationError(f"icon path escapes plugin directory: {data['icon']}")
    if not icon_path.is_file():
        raise PluginValidationError(f"icon file not found: {icon_path}")
    try:
        pygame.image.load(str(icon_path))
    except pygame.error as e:
        # Found on real hardware (2026-08-03): an orphaned plugin with a
        # corrupt icon.png crashed button.py's Button() construction with
        # an uncaught pygame.error deep in main()'s startup path — not a
        # hang, a genuine unhandled exception, so systemd's Restart=always
        # just relaunched straight back into the same crash forever, and
        # the recovery hotkey (which only kills/restarts processes) had
        # nothing it could do about it. Rejecting an undecodable icon here
        # means it's caught by scan_plugins()'s existing PluginValidationError
        # skip-with-a-warning handling instead — the same "one bad plugin
        # doesn't take down the whole grid" treatment a missing manifest.json
        # already gets, just extended to cover unreadable icon bytes too.
        raise PluginValidationError(f"icon file is not a loadable image: {icon_path}: {e}")

    if plugin_type in ("app", "custom"):
        launch_command = data.get("launch_command")
        if not isinstance(launch_command, str) or not shlex.split(launch_command):
            raise PluginValidationError(f"type '{plugin_type}' requires a non-empty 'launch_command' string")
    elif plugin_type == "website":
        url = data.get("url")
        if not isinstance(url, str) or not url:
            raise PluginValidationError("type 'website' requires a non-empty 'url' string")
        chromium_flags = data.get("chromium_flags")
        if chromium_flags is not None and not isinstance(chromium_flags, str):
            raise PluginValidationError("'chromium_flags' must be a string")

    return Plugin(
        id=plugin_id,
        name=data["name"],
        version=data["version"],
        type=plugin_type,
        icon=icon_path,
        description=data["description"],
        launch_command=data.get("launch_command"),
        url=data.get("url"),
        chromium_flags=data.get("chromium_flags"),
    )


def load_manifest(plugin_dir):
    manifest_path = plugin_dir / "manifest.json"
    try:
        with open(manifest_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        raise PluginValidationError(f"no manifest.json in {plugin_dir}")
    except json.JSONDecodeError as e:
        raise PluginValidationError(f"malformed manifest.json in {plugin_dir}: {e}")

    return validate_manifest(data, plugin_dir)


def scan_plugins(plugins_dir=PLUGINS_DIR):
    plugins_dir = Path(plugins_dir)
    plugins = []

    if not plugins_dir.is_dir():
        return plugins

    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir():
            continue
        try:
            plugins.append(load_manifest(entry))
        except PluginValidationError as e:
            print(f"warning: skipping plugin '{entry.name}': {e}", file=sys.stderr)

    plugins.sort(key=lambda p: p.name)
    return plugins


if __name__ == "__main__":
    for plugin in scan_plugins():
        print(f"{plugin.id}\t{plugin.name}\t{plugin.type}")
