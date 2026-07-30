import json
import subprocess
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "plugin-install.sh"

VALID_MANIFEST = {
    "id": "tuxpaint",
    "name": "TuxPaint",
    "version": "1.0.0",
    "type": "app",
    "launch_command": "/usr/bin/tuxpaint --fullscreen=yes",
    "icon": "icon.png",
    "description": "Drawing and painting app",
}


def make_zip(zip_path, entries):
    with zipfile.ZipFile(zip_path, "w") as zf:
        for arcname, content in entries.items():
            zf.writestr(arcname, content)


def valid_entries(manifest=None, extra=None):
    entries = {
        "tuxpaint/manifest.json": json.dumps(manifest or VALID_MANIFEST),
        "tuxpaint/icon.png": "not-really-a-png",
    }
    if extra:
        entries.update(extra)
    return entries


def run_install(zip_path, plugins_dir):
    return subprocess.run(
        ["bash", str(SCRIPT), str(zip_path)],
        env={"CLASSPAD_PLUGINS_DIR": str(plugins_dir), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )


def test_valid_zip_installs(tmp_path):
    zip_path = tmp_path / "plugin.zip"
    make_zip(
        zip_path,
        valid_entries(extra={"tuxpaint/install.sh": "#!/bin/sh\ntouch \"$(dirname \"$0\")/installed.marker\"\n"}),
    )
    plugins_dir = tmp_path / "plugins"

    result = run_install(zip_path, plugins_dir)

    assert result.returncode == 0, result.stderr
    target = plugins_dir / "tuxpaint"
    assert (target / "manifest.json").is_file()
    assert (target / "icon.png").is_file()
    assert (target / "installed.marker").is_file()


def test_reinstall_replaces_existing_content(tmp_path):
    plugins_dir = tmp_path / "plugins"

    zip_v1 = tmp_path / "v1.zip"
    make_zip(zip_v1, valid_entries(extra={"tuxpaint/old_file.txt": "v1"}))
    assert run_install(zip_v1, plugins_dir).returncode == 0
    assert (plugins_dir / "tuxpaint" / "old_file.txt").is_file()

    zip_v2 = tmp_path / "v2.zip"
    manifest_v2 = dict(VALID_MANIFEST, version="2.0.0")
    make_zip(zip_v2, valid_entries(manifest=manifest_v2, extra={"tuxpaint/new_file.txt": "v2"}))
    result = run_install(zip_v2, plugins_dir)

    assert result.returncode == 0, result.stderr
    assert not (plugins_dir / "tuxpaint" / "old_file.txt").exists()
    assert (plugins_dir / "tuxpaint" / "new_file.txt").is_file()


def test_relative_path_traversal_entry_rejected(tmp_path):
    zip_path = tmp_path / "malicious.zip"
    make_zip(zip_path, valid_entries(extra={"../../etc/cron.d/x": "* * * * * root pwned\n"}))
    plugins_dir = tmp_path / "plugins"

    result = run_install(zip_path, plugins_dir)

    assert result.returncode != 0
    assert "path traversal" in result.stderr
    assert not plugins_dir.exists() or list(plugins_dir.iterdir()) == []
    assert not (tmp_path.parent / "etc").exists()


def test_absolute_path_entry_rejected(tmp_path):
    zip_path = tmp_path / "malicious.zip"
    make_zip(zip_path, valid_entries(extra={"/etc/cron.d/x": "* * * * * root pwned\n"}))
    plugins_dir = tmp_path / "plugins"

    result = run_install(zip_path, plugins_dir)

    assert result.returncode != 0
    assert "absolute path" in result.stderr
    assert not plugins_dir.exists() or list(plugins_dir.iterdir()) == []


def test_malicious_manifest_id_rejected(tmp_path):
    zip_path = tmp_path / "malicious.zip"
    manifest = dict(VALID_MANIFEST, id="../../etc")
    make_zip(zip_path, valid_entries(manifest=manifest))
    plugins_dir = tmp_path / "plugins"

    result = run_install(zip_path, plugins_dir)

    assert result.returncode != 0
    assert "invalid or missing plugin id" in result.stderr
    assert not plugins_dir.exists() or list(plugins_dir.iterdir()) == []
    assert not (tmp_path.parent / "etc").exists()


def test_empty_manifest_id_rejected(tmp_path):
    zip_path = tmp_path / "malicious.zip"
    manifest = dict(VALID_MANIFEST, id="")
    make_zip(zip_path, valid_entries(manifest=manifest))
    plugins_dir = tmp_path / "plugins"

    result = run_install(zip_path, plugins_dir)

    assert result.returncode != 0
    assert not plugins_dir.exists() or list(plugins_dir.iterdir()) == []


def test_missing_manifest_rejected(tmp_path):
    zip_path = tmp_path / "no_manifest.zip"
    make_zip(zip_path, {"tuxpaint/icon.png": "not-really-a-png"})
    plugins_dir = tmp_path / "plugins"

    result = run_install(zip_path, plugins_dir)

    assert result.returncode != 0
    assert "no manifest.json" in result.stderr
    assert not plugins_dir.exists() or list(plugins_dir.iterdir()) == []


def test_malformed_manifest_json_rejected(tmp_path):
    zip_path = tmp_path / "malformed.zip"
    make_zip(zip_path, {"tuxpaint/manifest.json": "{not valid json", "tuxpaint/icon.png": "x"})
    plugins_dir = tmp_path / "plugins"

    result = run_install(zip_path, plugins_dir)

    assert result.returncode != 0
    assert "malformed manifest.json" in result.stderr
    assert not plugins_dir.exists() or list(plugins_dir.iterdir()) == []
