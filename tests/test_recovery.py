import os
import shutil
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "recovery.sh"
SLEEP_BIN = shutil.which("sleep")


def spawn_fake(tmp_path, name):
    # A straight copy of /bin/sleep, executed directly under this name, gets
    # a real /proc/pid/comm of `name` (the kernel sets comm from the exec'd
    # binary's own basename) — unlike `exec -a NAME`, which only changes
    # argv[0] and wouldn't be matched by recovery.sh's plain `pkill NAME`.
    fake_bin = tmp_path / name
    shutil.copy(SLEEP_BIN, fake_bin)
    fake_bin.chmod(0o755)
    return subprocess.Popen([str(fake_bin), "100"])


def spawn_fake_ignoring_sigterm(tmp_path, name):
    # Stands in for TuxPaint's real behavior, found on real hardware: it
    # catches SIGTERM and shows an "unsaved changes?" dialog instead of
    # exiting. `trap '' TERM` makes this shell ignore SIGTERM outright, so a
    # correct recovery.sh (using SIGKILL) must kill it anyway.
    fake_bin = tmp_path / name
    fake_bin.write_text("#!/bin/bash\ntrap '' TERM\nsleep 100\n")
    fake_bin.chmod(0o755)
    return subprocess.Popen([str(fake_bin)])


def is_alive(process):
    return process.poll() is None


def wait_until_dead(process, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_alive(process):
            return True
        time.sleep(0.1)
    return False


def run_recovery(activity_file):
    env = dict(os.environ)
    env["CLASSPAD_ACTIVITY_FILE"] = str(activity_file)
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def test_kills_processes_in_the_kill_list(tmp_path):
    activity_file = tmp_path / "activity"
    activity_file.write_text("TuxPaint")

    tuxpaint = spawn_fake(tmp_path, "tuxpaint")
    tuxmath = spawn_fake(tmp_path, "tuxmath")
    try:
        result = run_recovery(activity_file)

        assert result.returncode == 0, result.stderr
        assert wait_until_dead(tuxpaint)
        assert wait_until_dead(tuxmath)
    finally:
        for p in (tuxpaint, tuxmath):
            if is_alive(p):
                p.kill()


def test_kills_wordprocessor(tmp_path):
    # wordprocessor.py renames itself via prctl(PR_SET_NAME) specifically so
    # it gets its own kill-list entry instead of the generic "python3" name
    # it would otherwise share with the launcher and bar — see CLAUDE.md's
    # Process kill list section (same fix xylophone needed first).
    activity_file = tmp_path / "activity"
    activity_file.write_text("Word Processor")

    wordprocessor = spawn_fake(tmp_path, "wordprocessor")
    try:
        result = run_recovery(activity_file)

        assert result.returncode == 0, result.stderr
        assert wait_until_dead(wordprocessor)
    finally:
        if is_alive(wordprocessor):
            wordprocessor.kill()


def test_kills_a_process_that_ignores_sigterm(tmp_path):
    activity_file = tmp_path / "activity"
    activity_file.write_text("TuxPaint")

    tuxpaint = spawn_fake_ignoring_sigterm(tmp_path, "tuxpaint")
    try:
        result = run_recovery(activity_file)

        assert result.returncode == 0, result.stderr
        assert wait_until_dead(tuxpaint)
    finally:
        if is_alive(tuxpaint):
            tuxpaint.kill()


def test_leaves_unrelated_processes_running(tmp_path):
    activity_file = tmp_path / "activity"
    activity_file.write_text("")

    unrelated = spawn_fake(tmp_path, "storybook-server")
    try:
        result = run_recovery(activity_file)

        assert result.returncode == 0, result.stderr
        assert is_alive(unrelated)
    finally:
        unrelated.kill()
        unrelated.wait()


def test_clears_the_activity_file(tmp_path):
    activity_file = tmp_path / "activity"
    activity_file.write_text("Website Placeholder")

    result = run_recovery(activity_file)

    assert result.returncode == 0, result.stderr
    assert activity_file.read_text() == ""


def test_is_idempotent_with_nothing_running(tmp_path):
    activity_file = tmp_path / "activity"
    activity_file.write_text("")

    result = run_recovery(activity_file)

    assert result.returncode == 0, result.stderr
    assert activity_file.read_text() == ""


def test_creates_activity_file_if_missing(tmp_path):
    activity_file = tmp_path / "activity"
    assert not activity_file.exists()

    result = run_recovery(activity_file)

    assert result.returncode == 0, result.stderr
    assert activity_file.read_text() == ""
