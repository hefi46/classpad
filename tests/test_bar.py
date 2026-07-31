import json
import subprocess

import pytest

from bar import bar
from launcher import config as launcher_config


# --- format_relative_time ---------------------------------------------------


@pytest.mark.parametrize(
    "seconds_ago,expected",
    [
        (0, "just now"),
        (9, "just now"),
        (10, "10s ago"),
        (59, "59s ago"),
        (60, "1m ago"),
        (125, "2m ago"),
        (3599, "59m ago"),
        (3600, "1h ago"),
        (7300, "2h ago"),
    ],
)
def test_format_relative_time(seconds_ago, expected):
    assert bar.format_relative_time(seconds_ago) == expected


# --- get_wifi_status ---------------------------------------------------------


def _fake_run(outputs):
    """outputs: dict mapping the nmcli field spec (the -f value) to the
    stdout that call should return, keyed loosely by a substring of argv.
    """

    def run(argv, **kwargs):
        for key, stdout in outputs.items():
            if key in argv:
                return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
        raise AssertionError(f"unexpected nmcli invocation: {argv}")

    return run


def test_get_wifi_status_connected(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(
            {
                "device,type,state": "wlp3s0:wifi:connected\nlo:loopback:connected (externally)\n",
                "active,ssid": "yes:NetComm 3494\n",
                "ip4.address": "IP4.ADDRESS[1]:192.168.1.40/24\n",
            }
        ),
    )

    status = bar.get_wifi_status()

    assert status == {"connected": True, "ssid": "NetComm 3494", "ip": "192.168.1.40"}


def test_get_wifi_status_disconnected(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run({"device,type,state": "wlp3s0:wifi:disconnected\n"}),
    )

    assert bar.get_wifi_status() == {"connected": False, "ssid": None, "ip": None}


def test_get_wifi_status_no_wifi_device_at_all(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run({"device,type,state": "enp2s0:ethernet:connected\n"}),
    )

    assert bar.get_wifi_status() == {"connected": False, "ssid": None, "ip": None}


def test_get_wifi_status_nmcli_missing_or_failing(monkeypatch):
    def fail(*a, **k):
        raise FileNotFoundError("no such file: nmcli")

    monkeypatch.setattr(subprocess, "run", fail)

    assert bar.get_wifi_status() == {"connected": False, "ssid": None, "ip": None}


def test_get_wifi_status_timeout_treated_as_disconnected(monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="nmcli", timeout=3)

    monkeypatch.setattr(subprocess, "run", timeout)

    assert bar.get_wifi_status() == {"connected": False, "ssid": None, "ip": None}


# --- read_server_status ------------------------------------------------------


def test_read_server_status_configured_but_never_polled(monkeypatch, tmp_path):
    monkeypatch.setenv(launcher_config.SERVER_URL_ENV_VAR, "http://example.invalid")
    monkeypatch.setattr(launcher_config, "SERVER_STATUS_FILE", tmp_path / "does-not-exist")

    assert bar.read_server_status() == ("grey", "Waiting for first check")


def test_read_server_status_ok(monkeypatch, tmp_path):
    monkeypatch.setenv(launcher_config.SERVER_URL_ENV_VAR, "http://example.invalid")
    status_file = tmp_path / "server_status"
    status_file.write_text(json.dumps({"ok": True, "checked_at": __import__("time").time()}))
    monkeypatch.setattr(launcher_config, "SERVER_STATUS_FILE", status_file)

    color, text = bar.read_server_status()

    assert color == "green"
    assert text.startswith("Connected")


def test_read_server_status_unreachable(monkeypatch, tmp_path):
    monkeypatch.setenv(launcher_config.SERVER_URL_ENV_VAR, "http://example.invalid")
    status_file = tmp_path / "server_status"
    status_file.write_text(json.dumps({"ok": False, "checked_at": __import__("time").time()}))
    monkeypatch.setattr(launcher_config, "SERVER_STATUS_FILE", status_file)

    color, text = bar.read_server_status()

    assert color == "red"
    assert text.startswith("Unreachable")


def test_read_server_status_corrupt_file_treated_as_unknown(monkeypatch, tmp_path):
    monkeypatch.setenv(launcher_config.SERVER_URL_ENV_VAR, "http://example.invalid")
    status_file = tmp_path / "server_status"
    status_file.write_text("not json{{{")
    monkeypatch.setattr(launcher_config, "SERVER_STATUS_FILE", status_file)

    assert bar.read_server_status() == ("grey", "Waiting for first check")
