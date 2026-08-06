import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "wifi-setup-interactive.sh"


def make_fake_bin(path: Path, name: str, body: str) -> None:
    fake = path / name
    fake.write_text(f"#!/bin/bash\n{body}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def run(tmp_path, marker, env_extra=None, settle=1):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    env["CLASSPAD_WIFI_SETUP_MARKER"] = str(marker)
    env["CLASSPAD_WIFI_SETUP_SETTLE_SECONDS"] = str(settle)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=fakebin,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    ), fakebin


def test_skips_immediately_when_marker_already_exists(tmp_path):
    marker = tmp_path / "state" / ".wifi-setup-done"
    marker.parent.mkdir()
    marker.write_text("")
    # No curl/nmtui on PATH at all — if the script tried to use either, it
    # would fail loudly (command not found), so a clean exit 0 here proves
    # it genuinely short-circuited on the marker check.
    result, _ = run(tmp_path, marker)
    assert result.returncode == 0
    assert result.stdout == ""


def test_creates_marker_without_nmtui_when_already_reachable(tmp_path):
    marker = tmp_path / "state" / ".wifi-setup-done"
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    # No nmtui provided on PATH at all — if the script ever reached the
    # wizard path, this would fail with "command not found" instead of a
    # clean exit, so a passing test proves it never got there.
    make_fake_bin(fakebin, "curl", "exit 0")

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=fakebin,
        env={
            **os.environ,
            "PATH": f"{fakebin}:{os.environ['PATH']}",
            "CLASSPAD_WIFI_SETUP_MARKER": str(marker),
            "CLASSPAD_WIFI_SETUP_SETTLE_SECONDS": "3",
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert marker.exists()
    assert "WiFi setup" not in result.stdout


def test_answer_file_configures_wifi_without_nmtui(tmp_path):
    marker = tmp_path / "state" / ".wifi-setup-done"
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    state_file = tmp_path / "connected"

    # curl "succeeds" only once state_file exists — i.e. only after the
    # fake nmcli below has "added" the connection via the answer-file path.
    make_fake_bin(fakebin, "curl", f'[ -f "{state_file}" ]')
    # lsblk reports one removable device carrying the answer-file label.
    make_fake_bin(fakebin, "lsblk", 'echo "/dev/fakedev CLASSPAD-WIFI"')
    # mount "mounts" it by just writing a valid wifi-answers.env straight
    # into the target directory (the last argument) — no real block device
    # involved, matching this project's existing fake-binary test style.
    make_fake_bin(
        fakebin,
        "mount",
        'target="${@: -1}"\n'
        "cat > \"$target/wifi-answers.env\" <<'ENVFILE'\n"
        "CLASSPAD_WIFI_SSID=SchoolNet\n"
        "CLASSPAD_WIFI_IDENTITY=kiosk\n"
        "CLASSPAD_WIFI_PASSWORD=hunter2\n"
        "ENVFILE\n",
    )
    make_fake_bin(fakebin, "umount", "exit 0")
    make_fake_bin(
        fakebin,
        "nmcli",
        f'if [ "$1" = "-t" ]; then exit 0; fi\n'
        f'touch "{state_file}"\n',
    )
    # No nmtui on PATH at all — if the script ever fell through to the
    # interactive wizard, it would fail with "command not found" instead
    # of the clean exit this test asserts on.

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=fakebin,
        env={
            **os.environ,
            "PATH": f"{fakebin}:{os.environ['PATH']}",
            "CLASSPAD_WIFI_SETUP_MARKER": str(marker),
            "CLASSPAD_WIFI_SETUP_SETTLE_SECONDS": "1",
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert marker.exists()
    assert "Found WiFi answer file" in result.stdout
    assert "via WiFi answer file" in result.stdout
    assert "WiFi setup" not in result.stdout  # the interactive banner never showed


def test_invokes_nmtui_when_unreachable_then_stops_once_connected(tmp_path):
    marker = tmp_path / "state" / ".wifi-setup-done"
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    state_file = tmp_path / "connected"

    # curl "succeeds" only once `state_file` exists — i.e. only after the
    # fake nmtui below has run once, simulating a successful connection.
    make_fake_bin(fakebin, "curl", f'[ -f "{state_file}" ]')
    make_fake_bin(fakebin, "nmtui", f'echo ran-nmtui >> "{tmp_path}/nmtui.log"; touch "{state_file}"')

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=fakebin,
        env={
            **os.environ,
            "PATH": f"{fakebin}:{os.environ['PATH']}",
            "CLASSPAD_WIFI_SETUP_MARKER": str(marker),
            "CLASSPAD_WIFI_SETUP_SETTLE_SECONDS": "1",
        },
        input="\n",  # answers the "Press Enter to open WiFi setup..." prompt
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert marker.exists()
    assert (tmp_path / "nmtui.log").read_text().count("ran-nmtui") == 1
    assert "WiFi setup" in result.stdout
