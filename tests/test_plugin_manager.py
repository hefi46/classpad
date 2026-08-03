import base64
import json

import pytest

from launcher.plugin_manager import PluginValidationError, load_manifest, scan_plugins

# A real (if minimal) 1x1 PNG — plain magic-number-only bytes used to work
# as a stand-in icon here, but validate_manifest now actually decodes the
# icon (see plugin_manager.py's 2026-08-03 fix), so the fixture needs a
# genuinely loadable image or every test using it would fail for the wrong
# reason.
MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVQImWP4"
    "//8/AAX+Av5Y8msOAAAAAElFTkSuQmCC"
)

VALID_APP_MANIFEST = {
    "id": "tuxpaint",
    "name": "TuxPaint",
    "version": "1.0.0",
    "type": "app",
    "launch_command": "/usr/bin/tuxpaint --fullscreen=yes",
    "icon": "icon.png",
    "description": "Drawing and painting app",
}

VALID_WEBSITE_MANIFEST = {
    "id": "phonics-site",
    "name": "Phonics Game",
    "version": "1.0.0",
    "type": "website",
    "url": "https://example.com/phonics",
    "icon": "icon.png",
    "chromium_flags": "--force-device-scale-factor=1.25",
    "description": "Phonics practice website",
}


def make_plugin_dir(tmp_path, name, manifest=None, write_icon=True, manifest_text=None):
    plugin_dir = tmp_path / name
    plugin_dir.mkdir()
    if write_icon:
        (plugin_dir / "icon.png").write_bytes(MINIMAL_PNG)
    if manifest_text is not None:
        (plugin_dir / "manifest.json").write_text(manifest_text)
    elif manifest is not None:
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest))
    return plugin_dir


def test_valid_app_manifest_loads(tmp_path):
    plugin_dir = make_plugin_dir(tmp_path, "tuxpaint", VALID_APP_MANIFEST)

    plugin = load_manifest(plugin_dir)

    assert plugin.id == "tuxpaint"
    assert plugin.type == "app"
    assert plugin.launch_command == "/usr/bin/tuxpaint --fullscreen=yes"
    assert plugin.icon == plugin_dir / "icon.png"


def test_valid_website_manifest_loads(tmp_path):
    plugin_dir = make_plugin_dir(tmp_path, "phonics-site", VALID_WEBSITE_MANIFEST)

    plugin = load_manifest(plugin_dir)

    assert plugin.type == "website"
    assert plugin.url == "https://example.com/phonics"
    assert plugin.chromium_flags == "--force-device-scale-factor=1.25"


@pytest.mark.parametrize("missing_field", ["id", "name", "version", "type", "icon", "description"])
def test_missing_required_field_rejected(tmp_path, missing_field):
    manifest = dict(VALID_APP_MANIFEST)
    del manifest[missing_field]
    plugin_dir = make_plugin_dir(tmp_path, "broken", manifest)

    with pytest.raises(PluginValidationError, match=missing_field):
        load_manifest(plugin_dir)


def test_malformed_json_rejected(tmp_path):
    plugin_dir = make_plugin_dir(tmp_path, "broken", manifest_text="{not valid json")

    with pytest.raises(PluginValidationError, match="malformed"):
        load_manifest(plugin_dir)


def test_missing_manifest_file_rejected(tmp_path):
    plugin_dir = tmp_path / "empty"
    plugin_dir.mkdir()

    with pytest.raises(PluginValidationError, match="no manifest.json"):
        load_manifest(plugin_dir)


def test_missing_icon_file_rejected(tmp_path):
    plugin_dir = make_plugin_dir(tmp_path, "tuxpaint", VALID_APP_MANIFEST, write_icon=False)

    with pytest.raises(PluginValidationError, match="icon"):
        load_manifest(plugin_dir)


def test_corrupt_icon_file_rejected(tmp_path):
    plugin_dir = make_plugin_dir(tmp_path, "tuxpaint", VALID_APP_MANIFEST, write_icon=False)
    (plugin_dir / "icon.png").write_bytes(b"not-an-image")

    with pytest.raises(PluginValidationError, match="not a loadable image"):
        load_manifest(plugin_dir)


def test_scan_plugins_skips_corrupt_icon_without_crashing(tmp_path):
    make_plugin_dir(tmp_path, "tuxpaint", VALID_APP_MANIFEST)
    broken_dir = make_plugin_dir(
        tmp_path, "broken-icon", dict(VALID_APP_MANIFEST, id="broken-icon"), write_icon=False
    )
    (broken_dir / "icon.png").write_bytes(b"not-an-image")

    plugins = scan_plugins(tmp_path)

    assert [p.id for p in plugins] == ["tuxpaint"]


def test_icon_absolute_path_rejected(tmp_path):
    manifest = dict(VALID_APP_MANIFEST, icon="/etc/hostname")
    plugin_dir = make_plugin_dir(tmp_path, "broken", manifest, write_icon=True)

    with pytest.raises(PluginValidationError, match="escapes plugin directory"):
        load_manifest(plugin_dir)


def test_icon_relative_traversal_rejected(tmp_path):
    manifest = dict(VALID_APP_MANIFEST, icon="../../etc/hostname")
    plugin_dir = make_plugin_dir(tmp_path, "broken", manifest, write_icon=True)

    with pytest.raises(PluginValidationError, match="escapes plugin directory"):
        load_manifest(plugin_dir)


def test_non_string_launch_command_rejected(tmp_path):
    manifest = dict(VALID_APP_MANIFEST, launch_command=["/usr/bin/tuxpaint"])
    plugin_dir = make_plugin_dir(tmp_path, "broken", manifest)

    with pytest.raises(PluginValidationError, match="launch_command"):
        load_manifest(plugin_dir)


def test_non_string_required_field_rejected(tmp_path):
    manifest = dict(VALID_APP_MANIFEST, name=123)
    plugin_dir = make_plugin_dir(tmp_path, "broken", manifest)

    with pytest.raises(PluginValidationError, match="must be a string"):
        load_manifest(plugin_dir)


def test_scan_plugins_skips_non_string_fields_without_crashing(tmp_path):
    make_plugin_dir(tmp_path, "tuxpaint", VALID_APP_MANIFEST)
    make_plugin_dir(tmp_path, "broken-command", dict(VALID_APP_MANIFEST, id="broken-command", launch_command=["x"]))
    make_plugin_dir(tmp_path, "broken-name", dict(VALID_APP_MANIFEST, id="broken-name", name=123))

    plugins = scan_plugins(tmp_path)

    assert [p.id for p in plugins] == ["tuxpaint"]


def test_invalid_id_format_rejected(tmp_path):
    manifest = dict(VALID_APP_MANIFEST, id="Tux Paint!")
    plugin_dir = make_plugin_dir(tmp_path, "broken", manifest)

    with pytest.raises(PluginValidationError, match="invalid id"):
        load_manifest(plugin_dir)


def test_invalid_version_format_rejected(tmp_path):
    manifest = dict(VALID_APP_MANIFEST, version="v1")
    plugin_dir = make_plugin_dir(tmp_path, "broken", manifest)

    with pytest.raises(PluginValidationError, match="invalid version"):
        load_manifest(plugin_dir)


def test_invalid_type_rejected(tmp_path):
    manifest = dict(VALID_APP_MANIFEST, type="malicious")
    plugin_dir = make_plugin_dir(tmp_path, "broken", manifest)

    with pytest.raises(PluginValidationError, match="invalid type"):
        load_manifest(plugin_dir)


def test_app_requires_launch_command(tmp_path):
    manifest = dict(VALID_APP_MANIFEST)
    del manifest["launch_command"]
    plugin_dir = make_plugin_dir(tmp_path, "broken", manifest)

    with pytest.raises(PluginValidationError, match="launch_command"):
        load_manifest(plugin_dir)


def test_website_requires_url(tmp_path):
    manifest = dict(VALID_WEBSITE_MANIFEST)
    del manifest["url"]
    plugin_dir = make_plugin_dir(tmp_path, "broken", manifest)

    with pytest.raises(PluginValidationError, match="url"):
        load_manifest(plugin_dir)


def test_scan_plugins_skips_invalid_and_returns_sorted(tmp_path):
    make_plugin_dir(tmp_path, "tuxpaint", VALID_APP_MANIFEST)
    make_plugin_dir(tmp_path, "phonics-site", VALID_WEBSITE_MANIFEST)
    make_plugin_dir(tmp_path, "broken", manifest_text="{not valid json")
    make_plugin_dir(tmp_path, "no-manifest", write_icon=True)

    plugins = scan_plugins(tmp_path)

    assert [p.name for p in plugins] == ["Phonics Game", "TuxPaint"]


def test_scan_plugins_missing_directory_returns_empty(tmp_path):
    assert scan_plugins(tmp_path / "does-not-exist") == []
