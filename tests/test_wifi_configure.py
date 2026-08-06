import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "wifi-configure.sh"


def make_fake_bin(path: Path, name: str, body: str) -> None:
    fake = path / name
    fake.write_text(f"#!/bin/bash\n{body}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def run(env_extra, fakebin):
    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10
    )


def test_noop_when_ssid_unset(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    # No nmcli provided at all — if the script tried to call it, this
    # would fail with "command not found" rather than a clean skip.
    result = run({}, fakebin)
    assert result.returncode == 0
    assert "skipping" in result.stderr


def test_fails_when_identity_missing(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    result = run({"CLASSPAD_WIFI_SSID": "SchoolNet"}, fakebin)
    assert result.returncode != 0


def test_skips_when_connection_already_exists(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    log = tmp_path / "nmcli.log"
    make_fake_bin(
        fakebin,
        "nmcli",
        f'echo "$@" >> "{log}"\n'
        'if [ "$1" = "-t" ]; then\n'
        "    echo classpad-school-wifi\n"
        "fi\n",
    )
    result = run(
        {
            "CLASSPAD_WIFI_SSID": "SchoolNet",
            "CLASSPAD_WIFI_IDENTITY": "kiosk",
            "CLASSPAD_WIFI_PASSWORD": "hunter2",
        },
        fakebin,
    )
    assert result.returncode == 0
    assert "already exists" in result.stderr
    assert "add" not in log.read_text()


def test_adds_connection_with_expected_nmcli_args(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    log = tmp_path / "nmcli.log"
    make_fake_bin(
        fakebin,
        "nmcli",
        f'echo "$@" >> "{log}"\n'
        'if [ "$1" = "-t" ]; then\n'
        "    exit 0\n"  # empty connection list — nothing exists yet
        "fi\n",
    )
    result = run(
        {
            "CLASSPAD_WIFI_SSID": "SchoolNet",
            "CLASSPAD_WIFI_IDENTITY": "kiosk",
            "CLASSPAD_WIFI_PASSWORD": "hunter2",
            "CLASSPAD_WIFI_CA_CERT": "/etc/ssl/school-ca.pem",
        },
        fakebin,
    )
    assert result.returncode == 0
    log_text = log.read_text()
    assert "connection add" in log_text
    assert "ssid SchoolNet" in log_text
    assert "802-1x.identity kiosk" in log_text
    assert "802-1x.password hunter2" in log_text
    assert "connection modify classpad-school-wifi 802-1x.ca-cert /etc/ssl/school-ca.pem" in log_text


def test_warns_when_ca_cert_missing(tmp_path):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    make_fake_bin(
        fakebin,
        "nmcli",
        'if [ "$1" = "-t" ]; then exit 0; fi\n',
    )
    result = run(
        {
            "CLASSPAD_WIFI_SSID": "SchoolNet",
            "CLASSPAD_WIFI_IDENTITY": "kiosk",
            "CLASSPAD_WIFI_PASSWORD": "hunter2",
        },
        fakebin,
    )
    assert result.returncode == 0
    assert "not pinned" in result.stderr
