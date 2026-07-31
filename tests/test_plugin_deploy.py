import http.server
import importlib.util
import json
import subprocess
import threading
import urllib.error
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "plugin_deploy.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("plugin_deploy", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin_deploy():
    return _load_module()


class FakePlugin:
    def __init__(self, id, version):
        self.id = id
        self.version = version


# --- parse_version / needs_install / is_safe_to_install --------------------


def test_parse_version_valid(plugin_deploy):
    assert plugin_deploy.parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_malformed_returns_none(plugin_deploy):
    assert plugin_deploy.parse_version("not-a-version") is None
    assert plugin_deploy.parse_version("1.2") is None
    assert plugin_deploy.parse_version(None) is None


def test_needs_install_true_when_not_installed(plugin_deploy):
    entry = {"id": "tuxpaint", "version": "1.0.0"}
    assert plugin_deploy.needs_install(entry, {}) is True


def test_needs_install_false_when_versions_match(plugin_deploy):
    entry = {"id": "tuxpaint", "version": "1.0.0"}
    installed = {"tuxpaint": FakePlugin("tuxpaint", "1.0.0")}
    assert plugin_deploy.needs_install(entry, installed) is False


def test_needs_install_true_when_versions_differ_either_direction(plugin_deploy):
    installed = {"tuxpaint": FakePlugin("tuxpaint", "1.0.0")}
    assert plugin_deploy.needs_install({"id": "tuxpaint", "version": "2.0.0"}, installed) is True
    assert plugin_deploy.needs_install({"id": "tuxpaint", "version": "0.9.0"}, installed) is True


def test_needs_install_true_when_local_manifest_version_malformed(plugin_deploy):
    entry = {"id": "tuxpaint", "version": "1.0.0"}
    installed = {"tuxpaint": FakePlugin("tuxpaint", "garbage")}
    assert plugin_deploy.needs_install(entry, installed) is True


def test_is_safe_to_install_true_when_activity_file_empty(plugin_deploy, tmp_path, monkeypatch):
    activity_file = tmp_path / "classpad_activity"
    activity_file.write_text("")
    monkeypatch.setattr(plugin_deploy, "ACTIVITY_FILE", str(activity_file))

    assert plugin_deploy.is_safe_to_install() is True


def test_is_safe_to_install_false_when_something_running(plugin_deploy, tmp_path, monkeypatch):
    activity_file = tmp_path / "classpad_activity"
    activity_file.write_text("TuxPaint")
    monkeypatch.setattr(plugin_deploy, "ACTIVITY_FILE", str(activity_file))

    assert plugin_deploy.is_safe_to_install() is False


def test_is_safe_to_install_true_when_activity_file_missing(plugin_deploy, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_deploy, "ACTIVITY_FILE", str(tmp_path / "does-not-exist"))

    assert plugin_deploy.is_safe_to_install() is True


# --- main() orchestration ---------------------------------------------------


def test_main_skips_when_server_url_unset(plugin_deploy, monkeypatch, capsys):
    monkeypatch.setattr(plugin_deploy.launcher_config, "get_server_url", lambda: None)

    plugin_deploy.main()

    assert "server URL not configured" in capsys.readouterr().err


def test_main_skips_when_catalogue_fetch_fails(plugin_deploy, monkeypatch, capsys):
    monkeypatch.setattr(plugin_deploy.launcher_config, "get_server_url", lambda: "http://example.invalid")

    def fail(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(plugin_deploy, "fetch_catalogue", fail)

    plugin_deploy.main()

    assert "failed to fetch plugin catalogue" in capsys.readouterr().err


def test_main_skips_whole_cycle_when_unsafe(plugin_deploy, monkeypatch, capsys):
    monkeypatch.setattr(plugin_deploy.launcher_config, "get_server_url", lambda: "http://example.invalid")
    monkeypatch.setattr(plugin_deploy, "fetch_catalogue", lambda url: [{"id": "tuxpaint", "version": "1.0.0"}])
    monkeypatch.setattr(plugin_deploy, "is_safe_to_install", lambda: False)
    calls = []
    monkeypatch.setattr(plugin_deploy, "deploy_plugin", lambda *a, **k: calls.append(a))

    plugin_deploy.main()

    assert calls == []
    assert "skipping this cycle" in capsys.readouterr().err


def test_main_installs_new_and_updated_only(plugin_deploy, monkeypatch):
    monkeypatch.setattr(plugin_deploy.launcher_config, "get_server_url", lambda: "http://example.invalid")
    monkeypatch.setattr(plugin_deploy, "is_safe_to_install", lambda: True)
    monkeypatch.setattr(
        plugin_deploy,
        "fetch_catalogue",
        lambda url: [
            {"id": "already-installed", "version": "1.0.0"},
            {"id": "needs-update", "version": "2.0.0"},
            {"id": "brand-new", "version": "1.0.0"},
        ],
    )
    monkeypatch.setattr(
        plugin_deploy,
        "scan_plugins",
        lambda: [FakePlugin("already-installed", "1.0.0"), FakePlugin("needs-update", "1.0.0")],
    )
    calls = []
    monkeypatch.setattr(plugin_deploy, "deploy_plugin", lambda url, plugin_id: calls.append(plugin_id))

    plugin_deploy.main()

    assert sorted(calls) == ["brand-new", "needs-update"]


def test_main_continues_after_one_plugin_fails(plugin_deploy, monkeypatch, capsys):
    monkeypatch.setattr(plugin_deploy.launcher_config, "get_server_url", lambda: "http://example.invalid")
    monkeypatch.setattr(plugin_deploy, "is_safe_to_install", lambda: True)
    monkeypatch.setattr(
        plugin_deploy,
        "fetch_catalogue",
        lambda url: [{"id": "broken", "version": "1.0.0"}, {"id": "fine", "version": "1.0.0"}],
    )
    monkeypatch.setattr(plugin_deploy, "scan_plugins", lambda: [])
    calls = []

    def fake_deploy(url, plugin_id):
        if plugin_id == "broken":
            raise RuntimeError("network blip")
        calls.append(plugin_id)

    monkeypatch.setattr(plugin_deploy, "deploy_plugin", fake_deploy)

    plugin_deploy.main()

    assert calls == ["fine"]
    assert "failed to install 'broken'" in capsys.readouterr().err


def test_running_twice_against_unchanged_catalogue_installs_nothing(plugin_deploy, monkeypatch):
    """A version-compare bug that always reports "different" would rm -rf
    and reinstall every plugin on every 5-minute timer tick — silently, and
    it would eventually nuke a plugin mid-use. This is the check for that.
    """
    monkeypatch.setattr(plugin_deploy.launcher_config, "get_server_url", lambda: "http://example.invalid")
    monkeypatch.setattr(plugin_deploy, "is_safe_to_install", lambda: True)
    monkeypatch.setattr(plugin_deploy, "fetch_catalogue", lambda url: [{"id": "tuxpaint", "version": "1.0.0"}])
    monkeypatch.setattr(plugin_deploy, "scan_plugins", lambda: [FakePlugin("tuxpaint", "1.0.0")])
    calls = []
    monkeypatch.setattr(plugin_deploy, "deploy_plugin", lambda url, plugin_id: calls.append(plugin_id))

    plugin_deploy.main()
    plugin_deploy.main()

    assert calls == []


def test_deploy_plugin_downloads_and_runs_install_script(plugin_deploy, monkeypatch):
    monkeypatch.setattr(
        plugin_deploy,
        "download_plugin_zip",
        lambda url, plugin_id, dest_dir: Path(dest_dir) / f"{plugin_id}.zip",
    )
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, check: calls.append((argv, check)))

    plugin_deploy.deploy_plugin("http://example.invalid", "tuxpaint")

    assert len(calls) == 1
    argv, check = calls[0]
    assert argv[0] == str(plugin_deploy.INSTALL_SCRIPT)
    assert argv[1].endswith("tuxpaint.zip")
    assert check is True


# --- fetch_catalogue / download_plugin_zip against a real local stub server


class _CatalogueStubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/plugins/":
            body = json.dumps(self.server.catalogue).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/plugins/") and self.path.endswith("/download"):
            plugin_id = self.path[len("/plugins/") : -len("/download")]
            zip_bytes = self.server.zips.get(plugin_id)
            if zip_bytes is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.end_headers()
            self.wfile.write(zip_bytes)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def catalogue_stub_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _CatalogueStubHandler)
    server.catalogue = []
    server.zips = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join()


def test_fetch_catalogue_returns_parsed_json(plugin_deploy, catalogue_stub_server):
    catalogue_stub_server.catalogue = [
        {"id": "tuxpaint", "name": "TuxPaint", "version": "1.0.0", "type": "app", "description": "..."}
    ]
    url = f"http://127.0.0.1:{catalogue_stub_server.server_port}"

    assert plugin_deploy.fetch_catalogue(url) == catalogue_stub_server.catalogue


def test_download_plugin_zip_writes_bytes_to_dest_dir(plugin_deploy, catalogue_stub_server, tmp_path):
    catalogue_stub_server.zips["tuxpaint"] = b"fake-zip-bytes"
    url = f"http://127.0.0.1:{catalogue_stub_server.server_port}"

    zip_path = plugin_deploy.download_plugin_zip(url, "tuxpaint", tmp_path)

    assert zip_path.read_bytes() == b"fake-zip-bytes"


def test_download_plugin_zip_raises_on_404(plugin_deploy, catalogue_stub_server, tmp_path):
    url = f"http://127.0.0.1:{catalogue_stub_server.server_port}"

    with pytest.raises(urllib.error.HTTPError):
        plugin_deploy.download_plugin_zip(url, "does-not-exist", tmp_path)
