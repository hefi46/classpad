#!/usr/bin/env python3
import json
import subprocess
import sys
import time
from pathlib import Path

import gi
from Xlib import Xatom
from Xlib.display import Display

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

# Reuses launcher/config.py's server-URL/status conventions rather than
# duplicating them — bar.py and the launcher are separate processes, so this
# is read-only cross-process reuse, not a runtime coupling between them.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from launcher import config as launcher_config  # noqa: E402

BAR_HEIGHT = 55
INFO_BUTTON_SIZE = 44
INFO_BUTTON_MARGIN = 12
ACTIVITY_FILE = "/tmp/classpad_activity"
RECOVERY_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "recovery.sh"
VOLUME_STEP = 5
WIFI_POLL_INTERVAL_MS = 5000
INFO_POLL_INTERVAL_MS = 2000
NMCLI_TIMEOUT_SECONDS = 3


def set_strut_partial(xid, screen_width, height):
    # GDK/GObject-Introspection doesn't expose gdk_property_change (raw C array,
    # not annotated for GI), so this talks to X directly via python-xlib instead.
    # left, right, top, bottom, left_start_y, left_end_y, right_start_y, right_end_y,
    # top_start_x, top_end_x, bottom_start_x, bottom_end_x
    strut = [0, 0, height, 0, 0, 0, 0, 0, 0, screen_width, 0, 0]
    xlib_display = Display()
    window = xlib_display.create_resource_object("window", xid)
    net_wm_strut_partial = xlib_display.intern_atom("_NET_WM_STRUT_PARTIAL")
    window.change_property(net_wm_strut_partial, Xatom.CARDINAL, 32, strut)
    xlib_display.sync()


def get_wifi_status():
    """Pure, GTK-free so it's directly unit-testable. Three nmcli calls
    rather than one — device/state, active SSID, and IP are three separate
    nmcli queries — but this only runs every WIFI_POLL_INTERVAL_MS (5s), so
    the extra process spawns are not a concern the way they would be on a
    per-frame or per-second tick.
    """
    try:
        devices = subprocess.run(
            ["nmcli", "-t", "-f", "device,type,state", "device"],
            capture_output=True,
            text=True,
            timeout=NMCLI_TIMEOUT_SECONDS,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {"connected": False, "ssid": None, "ip": None}

    wifi_device = None
    for line in devices.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[1] == "wifi" and parts[2] == "connected":
            wifi_device = parts[0]
            break

    if wifi_device is None:
        return {"connected": False, "ssid": None, "ip": None}

    ssid = None
    try:
        active = subprocess.run(
            ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
            capture_output=True,
            text=True,
            timeout=NMCLI_TIMEOUT_SECONDS,
            check=True,
        ).stdout
        for line in active.splitlines():
            if line.startswith("yes:"):
                ssid = line.split(":", 1)[1]
                break
    except (subprocess.SubprocessError, OSError):
        pass

    ip = None
    try:
        ip_out = subprocess.run(
            ["nmcli", "-t", "-f", "ip4.address", "dev", "show", wifi_device],
            capture_output=True,
            text=True,
            timeout=NMCLI_TIMEOUT_SECONDS,
            check=True,
        ).stdout
        for line in ip_out.splitlines():
            if line.startswith("IP4.ADDRESS"):
                ip = line.split(":", 1)[1].split("/")[0]
                break
    except (subprocess.SubprocessError, OSError):
        pass

    return {"connected": True, "ssid": ssid, "ip": ip}


def format_relative_time(seconds_ago):
    if seconds_ago < 10:
        return "just now"
    if seconds_ago < 60:
        return f"{int(seconds_ago)}s ago"
    if seconds_ago < 3600:
        return f"{int(seconds_ago // 60)}m ago"
    return f"{int(seconds_ago // 3600)}h ago"


def read_server_status():
    """Returns (dot_color, status_text) for the info panel. Two states: no
    poll has completed yet or the status file is unreadable/corrupt (treated
    the same — "unknown" rather than a guessed color), and a real
    last-known result. There's no "never configured" state here — a server
    URL is always available (see launcher/config.py's DEFAULT_SERVER_URL).
    """
    try:
        status = json.loads(launcher_config.SERVER_STATUS_FILE.read_text())
        age = format_relative_time(time.time() - status["checked_at"])
        if status["ok"]:
            return "green", f"Connected ({age})"
        return "red", f"Unreachable ({age})"
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return "grey", "Waiting for first check"


class Bar(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_decorated(False)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.stick()

        screen = Gdk.Screen.get_default()
        self.screen_width = screen.get_width()
        self.set_default_size(self.screen_width, BAR_HEIGHT)
        self.move(0, 0)
        self.set_resizable(False)

        self._build_ui()

        self.connect("realize", self._on_realize)
        self.connect("destroy", Gtk.main_quit)

        GLib.timeout_add(1000, self._tick_clock)
        GLib.timeout_add(1000, self._tick_activity)
        GLib.timeout_add(WIFI_POLL_INTERVAL_MS, self._tick_wifi)
        self._tick_wifi()  # first update immediately, not 5s after startup

    def _on_realize(self, *_args):
        set_strut_partial(self.get_window().get_xid(), self.screen_width, BAR_HEIGHT)

    def _build_ui(self):
        overlay = Gtk.Overlay()
        self.add(overlay)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_start(10)
        row.set_margin_end(10)
        overlay.add(row)

        home_button = Gtk.Button(label="Home")
        home_button.connect("clicked", self._on_home_clicked)
        row.pack_start(home_button, False, False, 0)

        self.activity_label = Gtk.Label(label="")
        row.pack_start(self.activity_label, False, False, 0)

        self.wifi_label = Gtk.Label(label="")
        row.pack_end(self.wifi_label, False, False, 0)

        # row.pack_end() packs right-to-left: the first call ends up
        # rightmost, and each subsequent call lands to its left — so calls
        # go here in the reverse of the desired left-to-right visual order
        # (minus, slider, plus), with wifi_label further right still.
        volume_up = Gtk.Button(label="+")
        volume_up.connect("clicked", lambda _b: self._step_volume(VOLUME_STEP))
        row.pack_end(volume_up, False, False, 0)

        self.volume_adjustment = Gtk.Adjustment(value=70, lower=0, upper=100, step_increment=VOLUME_STEP)
        volume_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.volume_adjustment)
        volume_scale.set_size_request(120, -1)
        volume_scale.set_draw_value(False)
        volume_scale.connect("value-changed", self._on_volume_changed)
        row.pack_end(volume_scale, False, False, 0)

        volume_down = Gtk.Button(label="−")  # minus sign
        volume_down.connect("clicked", lambda _b: self._step_volume(-VOLUME_STEP))
        row.pack_end(volume_down, False, False, 0)

        self.clock_label = Gtk.Label(label="")
        self.clock_label.set_halign(Gtk.Align.CENTER)
        self.clock_label.set_valign(Gtk.Align.CENTER)
        overlay.add_overlay(self.clock_label)

    def _tick_clock(self):
        # %-I (no leading zero) is a glibc/GNU extension — fine here since
        # this project only ever targets Debian.
        self.clock_label.set_text(time.strftime("%-I:%M:%S %p"))
        return True

    def _tick_activity(self):
        try:
            with open(ACTIVITY_FILE) as f:
                activity = f.read().strip()
        except FileNotFoundError:
            activity = ""
        self.activity_label.set_text(activity)
        return True

    def _tick_wifi(self):
        status = get_wifi_status()
        if status["connected"]:
            self.wifi_label.set_markup('<span foreground="#2e7d32">●</span> ' + GLib.markup_escape_text(status["ssid"] or "Wi-Fi"))
            tooltip = f"SSID: {status['ssid'] or 'unknown'}\nIP: {status['ip'] or 'unknown'}"
        else:
            self.wifi_label.set_markup('<span foreground="#c62828">●</span> No Wi-Fi')
            tooltip = "Not connected"
        self.wifi_label.set_tooltip_text(tooltip)
        return True

    def _on_home_clicked(self, _button):
        # Killing the tracked child is enough to send the launcher home: its
        # blocking wait_for_exit() unblocks the moment the process dies, and
        # it takes care of its own cleanup (activity file, chromium profile
        # dir) from there — see launcher/process_manager.py.
        subprocess.Popen(["bash", str(RECOVERY_SCRIPT)])

    def _on_volume_changed(self, scale):
        volume = int(scale.get_value())
        subprocess.Popen(["amixer", "-q", "sset", "Master", f"{volume}%"])

    def _step_volume(self, delta):
        # Setting the shared Adjustment fires "value-changed" on the Scale
        # bound to it, which already calls amixer — no separate call needed.
        clamped = max(0, min(100, self.volume_adjustment.get_value() + delta))
        self.volume_adjustment.set_value(clamped)


class InfoPanel(Gtk.Window):
    """Small always-on-top "(i)" button, bottom-right of the screen. Same
    reachability limitation as the bar itself — covered during a genuinely
    fullscreen child, not just the ones below it (see CLAUDE.md's Persistent
    Bar / Recovery model) — teachers use it from the home screen or over a
    non-fullscreen app, matching how the bar's own Home button is reached.
    """

    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_decorated(False)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.stick()
        self.set_default_size(INFO_BUTTON_SIZE, INFO_BUTTON_SIZE)
        self.set_resizable(False)

        button = Gtk.Button(label="ℹ")  # info symbol
        button.connect("clicked", self._on_clicked)
        self.add(button)

        self.connect("realize", self._on_realize)

        # A plain toggled Gtk.Window rather than Gtk.Popover: found on real
        # hardware that Popover.popup() silently produces nothing when
        # anchored to a tiny DOCK-hinted toplevel like this one (no error,
        # no exception — it just never appears). A second plain window,
        # positioned by hand, uses the exact same window/WM mechanics this
        # class and Bar already rely on successfully.
        self.detail_window = self._build_detail_window()

    def _on_realize(self, *_args):
        screen = Gdk.Screen.get_default()
        x = screen.get_width() - INFO_BUTTON_SIZE - INFO_BUTTON_MARGIN
        y = screen.get_height() - INFO_BUTTON_SIZE - INFO_BUTTON_MARGIN
        self.move(x, y)

    def _build_detail_window(self):
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_decorated(False)
        window.set_type_hint(Gdk.WindowTypeHint.DOCK)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.set_keep_above(True)
        window.stick()
        window.set_resizable(False)

        frame = Gtk.Frame()
        window.add(frame)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        frame.add(box)

        self.admin_url_label = Gtk.Label(xalign=0)
        box.add(self.admin_url_label)

        self.status_label = Gtk.Label(xalign=0)
        box.add(self.status_label)

        refresh_button = Gtk.Button(label="Refresh config now")
        refresh_button.connect("clicked", self._on_refresh_clicked)
        box.add(refresh_button)

        close_button = Gtk.Button(label="Close")
        close_button.connect("clicked", lambda _b: window.hide())
        box.add(close_button)

        return window

    def _refresh_detail_content(self):
        admin_url = f"{launcher_config.get_server_url()}/admin"
        self.admin_url_label.set_text(f"Admin portal: {admin_url}")

        color, status_text = read_server_status()
        color_hex = {"green": "#2e7d32", "red": "#c62828", "grey": "#757575"}[color]
        self.status_label.set_markup(f'<span foreground="{color_hex}">●</span> {GLib.markup_escape_text(status_text)}')

    def _on_clicked(self, _button):
        if self.detail_window.get_visible():
            self.detail_window.hide()
            return
        self._refresh_detail_content()
        self.detail_window.show_all()
        # Positioned above the info button (not below — there's no screen
        # space below it) after show_all(), since the window needs a real
        # size allocation before get_size() is meaningful.
        screen = Gdk.Screen.get_default()
        _width, height = self.detail_window.get_size()
        x = screen.get_width() - INFO_BUTTON_SIZE - INFO_BUTTON_MARGIN - 220
        y = screen.get_height() - INFO_BUTTON_SIZE - INFO_BUTTON_MARGIN - height - 8
        self.detail_window.move(x, y)

    def _on_refresh_clicked(self, _button):
        try:
            launcher_config.REFRESH_REQUEST_FILE.touch()
        except OSError:
            pass
        self._refresh_detail_content()


def main():
    bar = Bar()
    bar.show_all()
    info_panel = InfoPanel()
    info_panel.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
