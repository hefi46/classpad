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
                "active,ssid,signal": "yes:NetComm 3494:53\n",
                "ip4.address": "IP4.ADDRESS[1]:192.168.1.40/24\n",
            }
        ),
    )

    status = bar.get_wifi_status()

    assert status == {"connected": True, "ssid": "NetComm 3494", "ip": "192.168.1.40", "signal": 53}


def test_get_wifi_status_disconnected(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run({"device,type,state": "wlp3s0:wifi:disconnected\n"}),
    )

    assert bar.get_wifi_status() == {"connected": False, "ssid": None, "ip": None, "signal": None}


def test_get_wifi_status_no_wifi_device_at_all(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run({"device,type,state": "enp2s0:ethernet:connected\n"}),
    )

    assert bar.get_wifi_status() == {"connected": False, "ssid": None, "ip": None, "signal": None}


def test_get_wifi_status_nmcli_missing_or_failing(monkeypatch):
    def fail(*a, **k):
        raise FileNotFoundError("no such file: nmcli")

    monkeypatch.setattr(subprocess, "run", fail)

    assert bar.get_wifi_status() == {"connected": False, "ssid": None, "ip": None, "signal": None}


def test_get_wifi_status_timeout_treated_as_disconnected(monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="nmcli", timeout=3)

    monkeypatch.setattr(subprocess, "run", timeout)

    assert bar.get_wifi_status() == {"connected": False, "ssid": None, "ip": None, "signal": None}


# --- wifi_indicator_markup -----------------------------------------------


def test_wifi_indicator_markup_disconnected():
    markup = bar.wifi_indicator_markup({"connected": False, "ssid": None, "ip": None, "signal": None})
    assert markup == f'<span foreground="{bar.WIFI_COLOR_DISCONNECTED}">✕</span>'


def test_wifi_indicator_markup_unknown_signal_while_connected():
    markup = bar.wifi_indicator_markup({"connected": True, "ssid": "x", "ip": "1.2.3.4", "signal": None})
    assert markup == f'<span foreground="{bar.WIFI_COLOR_INACTIVE}">?</span>'


@pytest.mark.parametrize(
    "signal,expected_active_bars",
    [
        (100, 4),
        (53, 3),
        (25, 1),
        (10, 1),
        (0, 0),
    ],
)
def test_wifi_indicator_markup_bar_count_by_signal(signal, expected_active_bars):
    status = {"connected": True, "ssid": "x", "ip": "1.2.3.4", "signal": signal}
    markup = bar.wifi_indicator_markup(status)

    # All lit bars share one flat color regardless of signal level — only
    # the *count* of lit bars varies with strength.
    active_spans = markup.count(f'foreground="{bar.WIFI_COLOR_ACTIVE}"')
    inactive_spans = markup.count(f'foreground="{bar.WIFI_COLOR_INACTIVE}"')
    assert active_spans == expected_active_bars
    assert inactive_spans == 4 - expected_active_bars


# --- resolve_wifi_status ------------------------------------------------


def test_resolve_wifi_status_passes_through_good_reading():
    previous = {"connected": True, "ssid": "old", "ip": "1.1.1.1", "signal": 40}
    current = {"connected": True, "ssid": "new", "ip": "2.2.2.2", "signal": 70}

    assert bar.resolve_wifi_status(previous, current) == current


def test_resolve_wifi_status_passes_through_real_disconnect():
    previous = {"connected": True, "ssid": "old", "ip": "1.1.1.1", "signal": 40}
    current = {"connected": False, "ssid": None, "ip": None, "signal": None}

    assert bar.resolve_wifi_status(previous, current) == current


def test_resolve_wifi_status_fills_in_transient_missing_signal(monkeypatch):
    # The race this exists for: device-state says connected, but the
    # active-network query briefly finds no "yes:" line mid-roam.
    previous = {"connected": True, "ssid": "NetComm 3494", "ip": "192.168.1.40", "signal": 56}
    current = {"connected": True, "ssid": None, "ip": "192.168.1.40", "signal": None}

    resolved = bar.resolve_wifi_status(previous, current)

    assert resolved == {"connected": True, "ssid": "NetComm 3494", "ip": "192.168.1.40", "signal": 56}


def test_resolve_wifi_status_prefers_fresh_ip_even_when_filling_in_signal():
    previous = {"connected": True, "ssid": "NetComm 3494", "ip": "192.168.1.40", "signal": 56}
    current = {"connected": True, "ssid": None, "ip": "192.168.1.99", "signal": None}

    resolved = bar.resolve_wifi_status(previous, current)

    assert resolved["ip"] == "192.168.1.99"
    assert resolved["signal"] == 56


def test_resolve_wifi_status_no_good_previous_shows_unknown():
    previous = {"connected": False, "ssid": None, "ip": None, "signal": None}
    current = {"connected": True, "ssid": None, "ip": "192.168.1.40", "signal": None}

    assert bar.resolve_wifi_status(previous, current) == current


# --- get_battery_status -----------------------------------------------------


def _make_battery(tmp_path, name, capacity, status):
    bat_dir = tmp_path / name
    bat_dir.mkdir()
    (bat_dir / "capacity").write_text(str(capacity))
    (bat_dir / "status").write_text(status)
    return bat_dir


def _make_ac_adapter(tmp_path, name, online):
    ac_dir = tmp_path / name
    ac_dir.mkdir()
    (ac_dir / "type").write_text("Mains")
    (ac_dir / "online").write_text("1" if online else "0")
    return ac_dir


def test_get_battery_status_discharging(monkeypatch, tmp_path):
    _make_battery(tmp_path, "BAT0", 62, "Discharging")
    monkeypatch.setattr(bar, "POWER_SUPPLY_DIR", tmp_path)

    assert bar.get_battery_status() == {"present": True, "percent": 62, "charging": False}


def test_get_battery_status_charging(monkeypatch, tmp_path):
    _make_battery(tmp_path, "BAT0", 81, "Charging")
    monkeypatch.setattr(bar, "POWER_SUPPLY_DIR", tmp_path)

    assert bar.get_battery_status() == {"present": True, "percent": 81, "charging": True}


def test_get_battery_status_full_is_not_charging(monkeypatch, tmp_path):
    _make_battery(tmp_path, "BAT0", 100, "Full")
    monkeypatch.setattr(bar, "POWER_SUPPLY_DIR", tmp_path)

    assert bar.get_battery_status() == {"present": True, "percent": 100, "charging": False}


def test_get_battery_status_ac_online_overrides_not_charging_status(monkeypatch, tmp_path):
    # The real-hardware case that motivated this: plugged in at 99%, kernel
    # reports BAT status "Not charging" (charge-threshold behavior) even
    # though the AC adapter is genuinely online.
    _make_battery(tmp_path, "BAT0", 99, "Not charging")
    _make_ac_adapter(tmp_path, "ACAD", online=True)
    monkeypatch.setattr(bar, "POWER_SUPPLY_DIR", tmp_path)

    assert bar.get_battery_status() == {"present": True, "percent": 99, "charging": True}


def test_get_battery_status_ac_offline_not_charging(monkeypatch, tmp_path):
    _make_battery(tmp_path, "BAT0", 40, "Discharging")
    _make_ac_adapter(tmp_path, "ACAD", online=False)
    monkeypatch.setattr(bar, "POWER_SUPPLY_DIR", tmp_path)

    assert bar.get_battery_status() == {"present": True, "percent": 40, "charging": False}


def test_get_battery_status_no_ac_adapter_falls_back_to_status_field(monkeypatch, tmp_path):
    _make_battery(tmp_path, "BAT0", 75, "Charging")
    monkeypatch.setattr(bar, "POWER_SUPPLY_DIR", tmp_path)

    assert bar.get_battery_status() == {"present": True, "percent": 75, "charging": True}


def test_get_battery_status_no_battery_present(monkeypatch, tmp_path):
    monkeypatch.setattr(bar, "POWER_SUPPLY_DIR", tmp_path)

    assert bar.get_battery_status() == {"present": False, "percent": None, "charging": None}


def test_get_battery_status_dir_missing_entirely(monkeypatch, tmp_path):
    monkeypatch.setattr(bar, "POWER_SUPPLY_DIR", tmp_path / "does-not-exist")

    assert bar.get_battery_status() == {"present": False, "percent": None, "charging": None}


def test_get_battery_status_unreadable_files_treated_as_absent(monkeypatch, tmp_path):
    bat_dir = tmp_path / "BAT0"
    bat_dir.mkdir()
    # capacity/status missing entirely — simulates a stray non-battery entry
    monkeypatch.setattr(bar, "POWER_SUPPLY_DIR", tmp_path)

    assert bar.get_battery_status() == {"present": False, "percent": None, "charging": None}


# --- battery_fill_color -------------------------------------------------


@pytest.mark.parametrize(
    "percent,expected_color",
    [
        (100, "GOOD"),
        (50, "GOOD"),
        (49, "FAIR"),
        (20, "FAIR"),
        (19, "LOW"),
        (0, "LOW"),
    ],
)
def test_battery_fill_color_tiers(percent, expected_color):
    color = {"GOOD": bar.BATTERY_COLOR_GOOD, "FAIR": bar.BATTERY_COLOR_FAIR, "LOW": bar.BATTERY_COLOR_LOW}[
        expected_color
    ]
    assert bar.battery_fill_color(percent) == color


# --- get_serial_number ------------------------------------------------------


def test_get_serial_number_strips_11e_prefix(monkeypatch):
    monkeypatch.setattr(launcher_config, "get_machine_id", lambda: "11e-LR06HKDE")
    assert bar.get_serial_number() == "LR06HKDE"


def test_get_serial_number_falls_back_to_full_id_without_prefix(monkeypatch):
    monkeypatch.setattr(launcher_config, "get_machine_id", lambda: "some-other-host")
    assert bar.get_serial_number() == "some-other-host"


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
