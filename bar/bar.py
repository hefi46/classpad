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
INFO_BUTTON_SIZE = 22
INFO_BUTTON_MARGIN = 2
DETAIL_WINDOW_GAP = 4
ACTIVITY_FILE = "/tmp/classpad_activity"
RECOVERY_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "recovery.sh"
VOLUME_STEP = 5
WIFI_POLL_INTERVAL_MS = 5000
INFO_POLL_INTERVAL_MS = 2000
NMCLI_TIMEOUT_SECONDS = 3

# Ascending-height bar glyphs for the compact signal-strength indicator —
# four bars rather than the previous "<dot> SSID" text, since the SSID
# doesn't fit "hugging the corner" sizing and isn't the thing a teacher
# glancing at the bar actually needs; SSID/IP move to the hover tooltip
# (unchanged, see _tick_wifi).
WIFI_BAR_GLYPHS = ["▁", "▃", "▅", "▇"]  # ▁ ▃ ▅ ▇
WIFI_COLOR_GOOD = "#2e7d32"
WIFI_COLOR_FAIR = "#f9a825"
WIFI_COLOR_WEAK = "#c62828"
WIFI_COLOR_INACTIVE = "#9e9e9e"


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
    rather than one — device/state, active SSID+signal, and IP are three
    separate nmcli queries — but this only runs every WIFI_POLL_INTERVAL_MS
    (5s), so the extra process spawns are not a concern the way they would
    be on a per-frame or per-second tick.
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
        return {"connected": False, "ssid": None, "ip": None, "signal": None}

    wifi_device = None
    for line in devices.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[1] == "wifi" and parts[2] == "connected":
            wifi_device = parts[0]
            break

    if wifi_device is None:
        return {"connected": False, "ssid": None, "ip": None, "signal": None}

    ssid = None
    signal = None
    try:
        active = subprocess.run(
            ["nmcli", "-t", "-f", "active,ssid,signal", "dev", "wifi"],
            capture_output=True,
            text=True,
            timeout=NMCLI_TIMEOUT_SECONDS,
            check=True,
        ).stdout
        for line in active.splitlines():
            if line.startswith("yes:"):
                # rsplit off the trailing numeric SIGNAL field rather than a
                # plain split(":") — an SSID itself may legitimately contain
                # a colon (nmcli escapes it, but we're tolerant either way,
                # same as the pre-existing SSID parsing below).
                rest = line[len("yes:") :]
                ssid_part, _, signal_part = rest.rpartition(":")
                ssid = ssid_part
                try:
                    signal = int(signal_part)
                except ValueError:
                    signal = None
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

    return {"connected": True, "ssid": ssid, "ip": ip, "signal": signal}


def wifi_indicator_markup(status):
    """Pango markup for the bar's compact top-right icon: signal strength as
    four ascending bars (colored by tier), not the SSID text — kept pure and
    GTK-free so the tiering logic is directly unit-testable. The SSID/IP
    detail stays in the hover tooltip (see _tick_wifi), unchanged.
    """
    if not status["connected"]:
        return f'<span foreground="{WIFI_COLOR_WEAK}">✕</span>'

    signal = status.get("signal")
    if signal is None:
        return f'<span foreground="{WIFI_COLOR_INACTIVE}">?</span>'

    level = 0 if signal <= 0 else min(4, -(-signal // 25))  # ceil-div, capped at 4
    color = WIFI_COLOR_GOOD if signal >= 50 else WIFI_COLOR_FAIR if signal >= 25 else WIFI_COLOR_WEAK

    return "".join(
        f'<span foreground="{color if i < level else WIFI_COLOR_INACTIVE}">{glyph}</span>'
        for i, glyph in enumerate(WIFI_BAR_GLYPHS)
    )


def get_serial_number():
    """The machine's serial, parsed out of the machine_id (`11e-<serial>`,
    see CLAUDE.md's Network & Deployment Context) rather than calling
    dmidecode directly — /sys/class/dmi/id/product_serial is root-only
    (0400) on this hardware, but machine_id is already world-readable
    (chowned to the kiosk user by install.sh), so no privilege is needed.
    """
    machine_id = launcher_config.get_machine_id()
    prefix = "11e-"
    if machine_id.startswith(prefix):
        return machine_id[len(prefix) :]
    return machine_id


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

        # "⌂" (U+2302) rather than a house emoji: confirmed rendering cleanly
        # in DejaVu Sans (this image's default font, no emoji font present),
        # while 🏠 shows as a tofu box — same dependency-free Unicode-glyph
        # approach as the info/refresh/close buttons, no icon-theme lookup.
        home_button = Gtk.Button(label="⌂")
        home_button.set_tooltip_text("Home")
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
        self.wifi_label.set_markup(wifi_indicator_markup(status))
        if status["connected"]:
            tooltip = f"SSID: {status['ssid'] or 'unknown'}\nIP: {status['ip'] or 'unknown'}"
        else:
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
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("classpad-info-btn")
        button.connect("clicked", self._on_clicked)
        self.add(button)

        # GTK theme padding on a stock Gtk.Button easily exceeds
        # INFO_BUTTON_SIZE on its own — zero it out and let the button fill
        # the (already small) window instead.
        css = Gtk.CssProvider()
        css.load_from_data(
            b".classpad-info-btn { padding: 0; min-width: 0; min-height: 0; }"
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

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

        # Condensed: tight spacing/margins, but the window still sizes itself
        # to its widest label (the admin URL) rather than a fixed width, so
        # it's never narrower than the URL needs.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(6)
        frame.add(box)

        self.admin_url_label = Gtk.Label(xalign=0)
        box.add(self.admin_url_label)

        self.status_label = Gtk.Label(xalign=0)
        box.add(self.status_label)

        self.serial_label = Gtk.Label(xalign=0)
        box.add(self.serial_label)

        self.nickname_label = Gtk.Label(xalign=0)
        box.add(self.nickname_label)

        # Icon buttons instead of worded ones, side by side to save the
        # vertical space a stacked pair of full-width text buttons took —
        # tooltips carry the words instead.
        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        button_row.set_halign(Gtk.Align.END)
        box.add(button_row)

        refresh_button = Gtk.Button(label="⟳")
        refresh_button.set_tooltip_text("Refresh config now")
        refresh_button.connect("clicked", self._on_refresh_clicked)
        button_row.add(refresh_button)

        close_button = Gtk.Button(label="✕")
        close_button.set_tooltip_text("Close")
        close_button.connect("clicked", lambda _b: window.hide())
        button_row.add(close_button)

        return window

    def _refresh_detail_content(self):
        admin_url = f"{launcher_config.get_server_url()}/admin"
        self.admin_url_label.set_text(f"Admin portal: {admin_url}")

        color, status_text = read_server_status()
        color_hex = {"green": "#2e7d32", "red": "#c62828", "grey": "#757575"}[color]
        self.status_label.set_markup(f'<span foreground="{color_hex}">●</span> {GLib.markup_escape_text(status_text)}')

        self.serial_label.set_text(f"Serial: {get_serial_number()}")

        # display_name only exists once the server has sent it down and a
        # poll has cached it locally (see server/models.get_config and
        # launcher/config.save_cached_config) — a fresh/offline machine has
        # no cache yet, hence the "not set yet" fallback rather than blank.
        cached = launcher_config.load_cached_config() or {}
        nickname = cached.get("display_name")
        self.nickname_label.set_text(f"Nickname: {nickname or '(not set)'}")

    def _on_clicked(self, _button):
        if self.detail_window.get_visible():
            self.detail_window.hide()
            return
        self._refresh_detail_content()
        self.detail_window.show_all()
        # Positioned above and right-aligned with the info button (not below
        # — there's no screen space below it) after show_all(), since the
        # window needs a real size allocation before get_size() is
        # meaningful. Measuring the actual width rather than a hardcoded
        # offset keeps this correct as the panel's content changes size.
        screen = Gdk.Screen.get_default()
        width, height = self.detail_window.get_size()
        x = screen.get_width() - INFO_BUTTON_MARGIN - width
        y = screen.get_height() - INFO_BUTTON_SIZE - INFO_BUTTON_MARGIN - height - DETAIL_WINDOW_GAP
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
