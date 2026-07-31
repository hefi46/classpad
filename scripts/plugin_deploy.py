#!/usr/bin/env python3
"""Background plugin deployment (Phase 10).

Runs as root via a systemd timer (system/systemd/classpad-plugin-deploy.{service,timer}),
deliberately separate from the unprivileged classpad-launcher.service —
plugin install.sh scripts are expected to run with elevated privileges (see
CLAUDE.md's Plugin System / trust model), so this stays out of the
always-on, user-facing launcher process entirely.

Deliberately has no signalling to the launcher: Phase 9's run_poller()
already calls scan_plugins() fresh on every /config poll, so a newly
installed plugin appears in the button grid on its own next poll, with
zero IPC between the two processes. Don't add one — it isn't needed.

Deliberately does not remove locally-installed plugins that have
disappeared from the catalogue — out of Phase 10's stated scope.
"""
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from launcher import config as launcher_config  # noqa: E402
from launcher.plugin_manager import scan_plugins  # noqa: E402

ACTIVITY_FILE = "/tmp/classpad_activity"
HTTP_TIMEOUT_SECONDS = 5
INSTALL_SCRIPT = Path(__file__).resolve().parent / "plugin-install.sh"


def parse_version(version_str):
    """'major.minor.patch' -> (int, int, int); None if malformed.

    A malformed local manifest shouldn't crash the whole run — treated as
    "needs reinstall" by the caller instead (see needs_install).
    """
    try:
        parts = tuple(int(p) for p in version_str.split("."))
    except (ValueError, AttributeError):
        return None
    return parts if len(parts) == 3 else None


def fetch_catalogue(server_url, timeout=HTTP_TIMEOUT_SECONDS):
    with urllib.request.urlopen(f"{server_url}/plugins/", timeout=timeout) as response:
        return json.loads(response.read())


def download_plugin_zip(server_url, plugin_id, dest_dir, timeout=HTTP_TIMEOUT_SECONDS):
    # urlopen raises urllib.error.HTTPError for any non-2xx response (e.g. a
    # 404 from a catalogue/DB mismatch) rather than returning it — so there's
    # no risk of an HTML error page silently being written out as a .zip and
    # handed to plugin-install.sh.
    with urllib.request.urlopen(f"{server_url}/plugins/{plugin_id}/download", timeout=timeout) as response:
        zip_path = Path(dest_dir) / f"{plugin_id}.zip"
        zip_path.write_bytes(response.read())
        return zip_path


def needs_install(catalogue_entry, installed_by_id):
    """Any version difference (not just "catalogue is newer") triggers a
    reinstall — the catalogue is authoritative truth to converge toward, not
    just a source of monotonic upgrades (covers a rollback to an older
    version too, not just picking up new ones).
    """
    installed = installed_by_id.get(catalogue_entry["id"])
    if installed is None:
        return True
    installed_version = parse_version(installed.version)
    if installed_version is None:
        return True
    return parse_version(catalogue_entry["version"]) != installed_version


def is_safe_to_install():
    """plugin-install.sh does `rm -rf` on a plugin's install directory before
    re-copying — if that plugin has a process currently running from it
    (e.g. plugins/xylophone/app/xylophone.py), this would delete files out
    from under a live process. Skip the whole cycle if anything is running
    and let the next timer tick retry; cheap, and reuses state the launcher
    already maintains rather than adding new coordination.
    """
    try:
        return Path(ACTIVITY_FILE).read_text().strip() == ""
    except FileNotFoundError:
        return True


def deploy_plugin(server_url, plugin_id):
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = download_plugin_zip(server_url, plugin_id, tmp_dir)
        subprocess.run([str(INSTALL_SCRIPT), str(zip_path)], check=True)


def main():
    server_url = launcher_config.get_server_url()
    if not server_url:
        print("plugin_deploy: server URL not configured, skipping", file=sys.stderr)
        return

    try:
        catalogue = fetch_catalogue(server_url)
    except Exception as e:
        print(f"plugin_deploy: warning: failed to fetch plugin catalogue: {e}", file=sys.stderr)
        return

    if not is_safe_to_install():
        print("plugin_deploy: a plugin is currently running, skipping this cycle", file=sys.stderr)
        return

    installed_by_id = {plugin.id: plugin for plugin in scan_plugins()}

    for entry in catalogue:
        if not needs_install(entry, installed_by_id):
            continue
        try:
            deploy_plugin(server_url, entry["id"])
            print(f"plugin_deploy: installed '{entry['id']}' version {entry['version']}")
        except Exception as e:
            print(f"plugin_deploy: warning: failed to install '{entry['id']}': {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
