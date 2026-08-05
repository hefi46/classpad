"""launcher/main.py is otherwise integration-tested on real hardware (it's
the pygame render loop), not unit tested — see CLAUDE.md. `_resolve_lock`
is the one pure, side-effect-free piece of the lock-to-app feature living
there, worth covering directly rather than only through real-hardware runs.
"""

from launcher.main import _resolve_lock


class DummyPlugin:
    def __init__(self, plugin_id):
        self.id = plugin_id


def test_resolve_lock_returns_none_when_no_lock_id():
    assert _resolve_lock([DummyPlugin("tuxpaint")], None) is None
    assert _resolve_lock([DummyPlugin("tuxpaint")], "") is None


def test_resolve_lock_finds_matching_plugin():
    tuxpaint = DummyPlugin("tuxpaint")
    tuxmath = DummyPlugin("tuxmath")
    assert _resolve_lock([tuxpaint, tuxmath], "tuxmath") is tuxmath


def test_resolve_lock_falls_back_to_none_when_not_installed_locally(capsys):
    # Not raising or hanging is the point — see the docstring in main.py:
    # an unresolvable lock must degrade to "show the normal grid", not a
    # frozen/blank kiosk. The warning going to stderr is a secondary check.
    result = _resolve_lock([DummyPlugin("tuxpaint")], "not-installed")
    assert result is None
    assert "not-installed" in capsys.readouterr().err
