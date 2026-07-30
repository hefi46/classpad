import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ACTIVITY_FILE = "/tmp/classpad_activity"  # must match bar/bar.py

CHROMIUM_BIN = "chromium"
CHROMIUM_PROFILE_PREFIX = "classpad-chromium-"
CHROMIUM_BASE_FLAGS = [
    "--start-maximized",
    "--no-first-run",
    "--disable-session-crashed-bubble",
    "--disable-features=TranslateUI",
    "--overscroll-history-navigation=0",
    "--disable-pinch",
    "--incognito",
]

# Corrected from "gcompris" — trixie only ships the Qt/QML rewrite, gcompris-qt.
# Matched against `comm` (no -f): these are the real binary names, and -f would
# also catch e.g. a custom plugin's argv that happens to mention one of them.
KILL_LIST = ["chromium", "tuxpaint", "tuxtype", "tuxmath", "gcompris-qt"]

_current_process = None
_current_chromium_profile_dir = None


class LaunchError(Exception):
    pass


def _write_activity(name):
    Path(ACTIVITY_FILE).write_text(name)


def _clear_activity():
    Path(ACTIVITY_FILE).write_text("")


def website_argv(plugin, user_data_dir):
    # A dedicated --user-data-dir is required, not cosmetic: without one,
    # Chromium hands off to an already-running instance and the launch process
    # exits immediately, so wait() would return while the browser is still on
    # screen (and stray chromium processes left by a recovery kill would make
    # this worse, not better).
    argv = [
        CHROMIUM_BIN,
        f"--app={plugin.url}",
        f"--user-data-dir={user_data_dir}",
        *CHROMIUM_BASE_FLAGS,
    ]
    if plugin.chromium_flags:
        argv.extend(shlex.split(plugin.chromium_flags))
    return argv


def build_argv(plugin):
    if plugin.type in ("app", "custom"):
        return shlex.split(plugin.launch_command)
    elif plugin.type == "website":
        return website_argv(plugin, tempfile.mkdtemp(prefix=CHROMIUM_PROFILE_PREFIX))
    else:
        raise LaunchError(f"unknown plugin type '{plugin.type}'")


def launch(plugin):
    """Spawn plugin.launch_command (or Chromium, for websites) as an argv list
    with shell=False — launch_command/chromium_flags come from a server-controlled
    manifest and must never be interpolated into a shell string.

    Manifests are server-controlled, so a launch_command naming a binary that
    isn't actually installed is a real possibility, not a hypothetical — treated
    as a system boundary rather than crashing the launcher.
    """
    global _current_process, _current_chromium_profile_dir
    profile_dir = tempfile.mkdtemp(prefix=CHROMIUM_PROFILE_PREFIX) if plugin.type == "website" else None
    argv = website_argv(plugin, profile_dir) if profile_dir else build_argv(plugin)

    try:
        process = subprocess.Popen(argv, shell=False)
    except OSError as e:
        print(f"warning: failed to launch '{plugin.id}': {e}", file=sys.stderr)
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)
        return None

    _current_process = process
    _current_chromium_profile_dir = profile_dir
    _write_activity(plugin.name)
    return process


def wait_for_exit(process=None):
    global _current_process, _current_chromium_profile_dir
    process = process or _current_process
    if process is None:
        return
    process.wait()
    if process is _current_process:
        _current_process = None
        if _current_chromium_profile_dir:
            shutil.rmtree(_current_chromium_profile_dir, ignore_errors=True)
        _current_chromium_profile_dir = None
    _clear_activity()


def kill_all():
    global _current_process, _current_chromium_profile_dir
    # SIGKILL, not the pkill default (SIGTERM) — confirmed on real hardware
    # that TuxPaint catches SIGTERM and shows an "unsaved changes?" dialog
    # instead of exiting. This exists for recovery use; it can't depend on a
    # stuck app cooperating with a graceful shutdown.
    for name in KILL_LIST:
        subprocess.run(["pkill", "-9", name])
    if _current_chromium_profile_dir:
        shutil.rmtree(_current_chromium_profile_dir, ignore_errors=True)
    _current_process = None
    _current_chromium_profile_dir = None
    _clear_activity()
