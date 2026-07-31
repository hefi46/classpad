import subprocess
import tempfile

import pytest

from launcher import process_manager
from launcher.plugin_manager import Plugin


def make_plugin(**overrides):
    defaults = dict(
        id="tuxpaint",
        name="TuxPaint",
        version="1.0.0",
        type="app",
        icon="icon.png",
        description="Drawing and painting app",
        launch_command="/usr/bin/tuxpaint --fullscreen=yes",
        url=None,
        chromium_flags=None,
    )
    defaults.update(overrides)
    return Plugin(**defaults)


class FakeProcess:
    def __init__(self):
        self.waited = False

    def wait(self):
        self.waited = True


@pytest.fixture(autouse=True)
def reset_module_state():
    process_manager._current_process = None
    process_manager._current_chromium_profile_dir = None
    process_manager._last_error = None
    yield
    process_manager._current_process = None
    process_manager._current_chromium_profile_dir = None
    process_manager._last_error = None


@pytest.fixture
def activity_file(tmp_path, monkeypatch):
    path = tmp_path / "classpad_activity"
    monkeypatch.setattr(process_manager, "ACTIVITY_FILE", str(path))
    return path


def test_build_argv_app_splits_launch_command_with_shlex():
    plugin = make_plugin(type="app", launch_command="/usr/bin/tuxpaint --fullscreen=yes")
    assert process_manager.build_argv(plugin) == ["/usr/bin/tuxpaint", "--fullscreen=yes"]


def test_build_argv_custom_splits_launch_command():
    plugin = make_plugin(type="custom", launch_command="/usr/bin/python3 app/server.py")
    assert process_manager.build_argv(plugin) == ["/usr/bin/python3", "app/server.py"]


def test_website_argv_uses_app_flag_dedicated_profile_and_base_flags():
    plugin = make_plugin(
        type="website",
        launch_command=None,
        url="https://example.com/phonics",
        chromium_flags=None,
    )
    argv = process_manager.website_argv(plugin, "/tmp/fake-profile-dir")

    assert argv[0] == process_manager.CHROMIUM_BIN
    assert argv[1] == "--app=https://example.com/phonics"
    assert "--user-data-dir=/tmp/fake-profile-dir" in argv
    assert "--kiosk" not in argv
    for flag in process_manager.CHROMIUM_BASE_FLAGS:
        assert flag in argv


def test_website_argv_appends_extra_chromium_flags():
    plugin = make_plugin(
        type="website",
        launch_command=None,
        url="https://example.com/phonics",
        chromium_flags="--force-device-scale-factor=1.25",
    )
    argv = process_manager.website_argv(plugin, "/tmp/fake-profile-dir")

    assert argv[-1] == "--force-device-scale-factor=1.25"


def test_build_argv_website_creates_a_dedicated_profile_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "mkdtemp", lambda prefix: str(tmp_path / "profile"))
    plugin = make_plugin(type="website", launch_command=None, url="https://example.com")

    argv = process_manager.build_argv(plugin)

    assert f"--user-data-dir={tmp_path / 'profile'}" in argv


def test_build_argv_unknown_type_raises():
    plugin = make_plugin(type="app", launch_command="/bin/true")
    plugin.type = "mystery"
    with pytest.raises(process_manager.LaunchError):
        process_manager.build_argv(plugin)


def test_launch_invokes_popen_with_argv_and_shell_false(monkeypatch, activity_file):
    captured = {}

    def fake_popen(argv, shell):
        captured["argv"] = argv
        captured["shell"] = shell
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    plugin = make_plugin()
    process = process_manager.launch(plugin)

    assert captured["shell"] is False
    assert captured["argv"] == ["/usr/bin/tuxpaint", "--fullscreen=yes"]
    assert isinstance(process, FakeProcess)
    assert activity_file.read_text() == "TuxPaint"
    assert process_manager._current_process is process


def test_launch_website_uses_a_dedicated_profile_dir_and_records_it(monkeypatch, tmp_path, activity_file):
    captured = {}
    monkeypatch.setattr(subprocess, "Popen", lambda argv, shell: captured.setdefault("argv", argv) or FakeProcess())
    monkeypatch.setattr(tempfile, "mkdtemp", lambda prefix: str(tmp_path / "profile"))

    plugin = make_plugin(type="website", launch_command=None, url="https://example.com", name="Phonics")
    process_manager.launch(plugin)

    assert f"--user-data-dir={tmp_path / 'profile'}" in captured["argv"]
    assert process_manager._current_chromium_profile_dir == str(tmp_path / "profile")


def test_launch_failure_is_logged_not_raised(monkeypatch, activity_file):
    def fake_popen(argv, shell):
        raise OSError("No such file or directory")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    plugin = make_plugin()
    process = process_manager.launch(plugin)

    assert process is None
    assert process_manager._current_process is None
    assert not activity_file.exists()


def test_launch_failure_records_last_error_for_telemetry(monkeypatch, activity_file):
    monkeypatch.setattr(subprocess, "Popen", lambda argv, shell: (_ for _ in ()).throw(OSError("No such file")))

    plugin = make_plugin(id="tuxpaint")
    process_manager.launch(plugin)

    error = process_manager.pop_last_error()
    assert error is not None
    assert "tuxpaint" in error
    assert "No such file" in error


def test_pop_last_error_clears_after_read():
    process_manager._last_error = "boom"
    assert process_manager.pop_last_error() == "boom"
    assert process_manager.pop_last_error() is None


def test_pop_last_error_defaults_to_none():
    assert process_manager.pop_last_error() is None


def test_wait_for_exit_waits_and_clears_activity(activity_file):
    activity_file.write_text("TuxPaint")
    process_manager._current_process = FakeProcess()
    tracked = process_manager._current_process

    process_manager.wait_for_exit()

    assert tracked.waited
    assert activity_file.read_text() == ""
    assert process_manager._current_process is None


def test_wait_for_exit_accepts_explicit_process(activity_file):
    process_manager._current_process = None
    process = FakeProcess()

    process_manager.wait_for_exit(process)

    assert process.waited
    assert activity_file.read_text() == ""


def test_wait_for_exit_with_no_process_is_a_no_op(activity_file):
    process_manager.wait_for_exit()
    assert not activity_file.exists()


def test_wait_for_exit_cleans_up_chromium_profile_dir(tmp_path, activity_file):
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    process_manager._current_process = FakeProcess()
    process_manager._current_chromium_profile_dir = str(profile_dir)

    process_manager.wait_for_exit()

    assert not profile_dir.exists()
    assert process_manager._current_chromium_profile_dir is None


def test_kill_all_pkills_every_name_in_kill_list_and_clears_activity(monkeypatch, activity_file):
    activity_file.write_text("TuxPaint")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv: calls.append(argv))

    process_manager._current_process = FakeProcess()
    process_manager.kill_all()

    assert calls == [["pkill", "-9", name] for name in process_manager.KILL_LIST]
    assert activity_file.read_text() == ""
    assert process_manager._current_process is None


def test_kill_all_cleans_up_chromium_profile_dir(tmp_path, monkeypatch, activity_file):
    monkeypatch.setattr(subprocess, "run", lambda argv: None)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    process_manager._current_chromium_profile_dir = str(profile_dir)

    process_manager.kill_all()

    assert not profile_dir.exists()
    assert process_manager._current_chromium_profile_dir is None
