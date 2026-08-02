#!/usr/bin/env python3
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import gi
from Xlib import Xatom
from Xlib.display import Display

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
import cairo  # noqa: E402 — needed for the hand-drawn icon buttons below

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
BATTERY_POLL_INTERVAL_MS = 30000  # sysfs reads are cheap, but a laptop's
# charge level doesn't move fast enough to need wifi's 5s cadence
INFO_POLL_INTERVAL_MS = 2000
NMCLI_TIMEOUT_SECONDS = 3
POWER_SUPPLY_DIR = Path("/sys/class/power_supply")

# Hand-drawn (Cairo) rather than Unicode glyphs or a bundled icon theme —
# "⌂" for Home was found on real hardware to be barely visible at normal text
# size, and there's no dependency-free speaker glyph at all: the obvious
# Unicode candidates (🔈/🔉/🔊, U+1F508-U+1F50A) are emoji-range codepoints
# DejaVu Sans (this image's only installed font, confirmed elsewhere in this
# file) doesn't cover — same tofu-box problem the "⌂ not 🏠" comment below
# already ran into once. Drawing simple flat vector shapes directly with
# Cairo sidesteps font coverage entirely, matching how launcher/button.py's
# draw_plus_icon/draw_trash_icon/draw_back_arrow_icon already hand-draw
# icons with Pygame primitives for the same reason.
HOME_ICON_SIZE = 34
SPEAKER_ICON_SIZE = 24
ICON_COLOR = (0.20, 0.20, 0.20)
CLOCK_CSS = b".classpad-clock { font-size: 20px; font-weight: bold; }"
BAR_BUTTON_CSS = b".classpad-bar-btn { padding: 3px; }"

# Ascending-height bar glyphs for the compact signal-strength indicator —
# four bars rather than the previous "<dot> SSID" text, since the SSID
# doesn't fit "hugging the corner" sizing and isn't the thing a teacher
# glancing at the bar actually needs; SSID/IP move to the hover tooltip
# (unchanged, see _tick_wifi).
WIFI_BAR_GLYPHS = ["▁", "▃", "▅", "▇"]  # ▁ ▃ ▅ ▇
# One flat color for every lit bar regardless of signal level — the
# per-tier green/amber/red coloring this replaced was read by a teacher as
# a health/warning signal ("red = something's wrong"), when it was only ever
# meant to reflect an ordinary weak-but-fine connection. Bar *count* still
# reflects signal strength; only the color no longer does.
WIFI_COLOR_ACTIVE = "#1565c0"
WIFI_COLOR_INACTIVE = "#9e9e9e"
WIFI_COLOR_DISCONNECTED = "#c62828"

# RGB tuples (0-1 floats), not hex strings like the WIFI_COLOR_* above —
# these feed cairo's set_source_rgb directly (the battery indicator is a
# hand-drawn icon, matching draw_home_icon/draw_speaker_icon below, not
# Pango markup like the wifi text glyphs).
BATTERY_COLOR_GOOD = (0.18, 0.49, 0.20)  # #2e7d32
BATTERY_COLOR_FAIR = (0.98, 0.66, 0.15)  # #f9a825
BATTERY_COLOR_LOW = (0.78, 0.16, 0.16)  # #c62828

BATTERY_ICON_WIDTH = 30
BATTERY_ICON_HEIGHT = 16
BATTERY_NUB_WIDTH = 3
BATTERY_NUB_HEIGHT = 8
BATTERY_CORNER_RADIUS = 2
BATTERY_FILL_PADDING = 2.5
ZAP_COLOR = (1.0, 1.0, 1.0)


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
    four ascending bars, lit bar count reflecting level but all lit bars a
    single flat color (see WIFI_COLOR_ACTIVE) rather than colored by tier —
    kept pure and GTK-free so this logic is directly unit-testable. The
    SSID/IP detail stays in the hover tooltip (see _tick_wifi), unchanged.
    """
    if not status["connected"]:
        return f'<span foreground="{WIFI_COLOR_DISCONNECTED}">✕</span>'

    signal = status.get("signal")
    if signal is None:
        return f'<span foreground="{WIFI_COLOR_INACTIVE}">?</span>'

    level = 0 if signal <= 0 else min(4, -(-signal // 25))  # ceil-div, capped at 4

    return "".join(
        f'<span foreground="{WIFI_COLOR_ACTIVE if i < level else WIFI_COLOR_INACTIVE}">{glyph}</span>'
        for i, glyph in enumerate(WIFI_BAR_GLYPHS)
    )


def resolve_wifi_status(previous, current):
    """Smooths over a real, observed nmcli race rather than a hypothetical
    one: get_wifi_status makes its "is a wifi device connected" and "which
    network is active" checks as two separate nmcli calls a few hundred ms
    apart. During a brief roam/reassociation blip on an AP network with
    multiple access points sharing one SSID (confirmed present on this
    network via `nmcli dev wifi` showing the same SSID at several signal
    levels), the device can still read "connected" while the second call
    briefly finds no line marked active — connected=True but signal=None
    for exactly one poll, even though the network never actually dropped.
    Rather than flash the bar to "?" for that one 5s tick, carry forward the
    previous poll's signal/SSID until either a real reading or a genuine
    disconnect comes in. Pure and GTK-free, same reasoning as
    get_wifi_status/wifi_indicator_markup.
    """
    if not current["connected"]:
        return current
    if current["signal"] is not None:
        return current
    if previous["connected"] and previous["signal"] is not None:
        return {
            "connected": True,
            "ssid": previous["ssid"],
            "ip": current["ip"] or previous["ip"],
            "signal": previous["signal"],
        }
    return current


def _read_ac_online():
    """Whether any Mains-type power supply (the AC adapter) reports
    `online`. Found on real hardware (this ThinkPad, plugged in at 99%):
    BAT*/status reads "Not charging" rather than "Charging" once the
    battery is near-full — a charge-threshold behavior, not a fault — so
    keying the zap overlay off BAT*/status alone misses "plugged in" for
    exactly the case a teacher would check it. Returns None (not False) if
    no Mains-type entry is found at all, so the caller can fall back to the
    status-field check rather than silently reporting "not plugged in" on
    hardware that just names the adapter differently.
    """
    try:
        entries = list(POWER_SUPPLY_DIR.iterdir())
    except OSError:
        return None

    for entry in entries:
        try:
            if (entry / "type").read_text().strip() != "Mains":
                continue
            return (entry / "online").read_text().strip() == "1"
        except OSError:
            continue

    return None


def get_battery_status():
    """Reads directly from sysfs rather than shelling out to `acpi`/`upower`
    — neither is guaranteed installed on this image, while
    /sys/class/power_supply/ is a stable kernel interface needing no extra
    package. Pure and GTK-free like get_wifi_status, for the same reason:
    directly unit-testable. Picks the first BAT* entry with readable
    capacity/status files; a machine with no battery at all (or an
    unreadable one) reports "not present" rather than guessing.

    `charging` reflects the AC adapter's `online` state (see
    _read_ac_online) rather than BAT*/status directly — "plugged into
    charge" is what the zap icon promises, and status alone under-reports
    that near-full. Falls back to status == "Charging" only if no AC
    adapter entry exists at all.
    """
    try:
        battery_dirs = sorted(p for p in POWER_SUPPLY_DIR.iterdir() if p.name.startswith("BAT"))
    except OSError:
        return {"present": False, "percent": None, "charging": None}

    ac_online = _read_ac_online()

    for battery_dir in battery_dirs:
        try:
            percent = int((battery_dir / "capacity").read_text().strip())
            status = (battery_dir / "status").read_text().strip()
        except (OSError, ValueError):
            continue
        charging = ac_online if ac_online is not None else status == "Charging"
        return {"present": True, "percent": percent, "charging": charging}

    return {"present": False, "percent": None, "charging": None}


def battery_fill_color(percent):
    """Tier color for the battery icon's fill level — pure so the tiering
    logic is directly unit-testable, same reasoning as wifi_indicator_markup.
    Charging state is conveyed separately by the zap overlay (see
    draw_battery_icon), not by overriding this color, so a charging-but-low
    battery still reads as low.
    """
    if percent >= 50:
        return BATTERY_COLOR_GOOD
    if percent >= 20:
        return BATTERY_COLOR_FAIR
    return BATTERY_COLOR_LOW


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


def draw_home_icon(cr, size, color=ICON_COLOR):
    """A simple filled house silhouette: a triangular roof over a square
    body, centered in a size x size box.
    """
    cr.set_source_rgb(*color)
    margin = size * 0.10
    roof_y = size * 0.42

    cr.move_to(size / 2, margin)
    cr.line_to(margin, roof_y)
    cr.line_to(size - margin, roof_y)
    cr.close_path()
    cr.fill()

    body_left = size * 0.22
    body_right = size * 0.78
    # Slight overlap with the roof (roof_y - 1px-equivalent) so there's no
    # visible seam between the two filled shapes at this resolution.
    cr.rectangle(body_left, roof_y - size * 0.02, body_right - body_left, size - margin - roof_y + size * 0.02)
    cr.fill()


def draw_speaker_icon(cr, size, wave_count, color=ICON_COLOR):
    """A speaker body (box + flared cone) plus `wave_count` sound-wave arcs
    to its right — 1 arc for the "quieter" (-) button, 3 for "louder" (+),
    the classic speaker-with-sound-waves volume icon.
    """
    cr.set_source_rgb(*color)

    box_left = size * 0.06
    cone_start = size * 0.38
    cone_end = size * 0.56
    top, bottom = size * 0.30, size * 0.70

    cr.rectangle(box_left, top, cone_start - box_left, bottom - top)
    cr.fill()

    cr.move_to(cone_start, top)
    cr.line_to(cone_end, size * 0.12)
    cr.line_to(cone_end, size * 0.88)
    cr.line_to(cone_start, bottom)
    cr.close_path()
    cr.fill()

    cr.set_line_width(size * 0.09)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cx, cy = cone_end - size * 0.04, size / 2
    for i in range(wave_count):
        radius = size * 0.14 + i * size * 0.15
        cr.new_path()
        cr.arc(cx, cy, radius, -0.6, 0.6)
        cr.stroke()


def _rounded_rect_path(cr, x, y, w, h, r):
    cr.new_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_zap_icon(cr, cx, cy, size, color=ZAP_COLOR, outline_color=ICON_COLOR):
    """A small lightning-bolt zigzag centered at (cx, cy) — the charging
    overlay on the battery icon. Filled white with a thin dark outline
    (rather than just the fill color) so it stays legible regardless of
    what tier color the battery fill underneath happens to be.
    """
    points = [
        (0.55, 0.0),
        (0.15, 0.55),
        (0.40, 0.55),
        (0.28, 1.0),
        (0.85, 0.35),
        (0.55, 0.35),
    ]
    x0, y0 = cx - size / 2, cy - size / 2

    cr.new_path()
    cr.move_to(x0 + points[0][0] * size, y0 + points[0][1] * size)
    for px, py in points[1:]:
        cr.line_to(x0 + px * size, y0 + py * size)
    cr.close_path()

    cr.set_source_rgb(*color)
    cr.fill_preserve()
    cr.set_line_width(0.6)
    cr.set_source_rgb(*outline_color)
    cr.stroke()


def draw_battery_icon(cr, status):
    """Battery body + terminal nub outline (always drawn, even with no
    battery present — an empty outline rather than nothing, so the icon
    slot doesn't just disappear), filled left-to-right by charge percentage
    in a tier color, with a zap overlay while charging. No percentage text
    on the icon itself — that's surfaced via tooltip on hover instead (see
    Bar._tick_battery), matching how the wifi indicator keeps SSID/IP out of
    the icon and in its tooltip.
    """
    body_width = BATTERY_ICON_WIDTH - BATTERY_NUB_WIDTH
    height = BATTERY_ICON_HEIGHT

    cr.set_line_width(1.5)
    cr.set_source_rgb(*ICON_COLOR)
    _rounded_rect_path(cr, 0.75, 0.75, body_width - 1.5, height - 1.5, BATTERY_CORNER_RADIUS)
    cr.stroke()

    nub_y = (height - BATTERY_NUB_HEIGHT) / 2
    cr.rectangle(body_width - 0.5, nub_y, BATTERY_NUB_WIDTH, BATTERY_NUB_HEIGHT)
    cr.fill()

    if not status["present"]:
        return

    percent = max(0, min(100, status["percent"]))
    pad = BATTERY_FILL_PADDING
    inner_width = body_width - 2 * pad
    inner_height = height - 2 * pad
    fill_width = inner_width * percent / 100

    cr.set_source_rgb(*battery_fill_color(percent))
    cr.rectangle(pad, pad, fill_width, inner_height)
    cr.fill()

    if status["charging"]:
        draw_zap_icon(cr, body_width / 2, height / 2, height * 0.85)


def make_icon_button(draw_fn, size, tooltip):
    """A borderless Gtk.Button wrapping a Gtk.DrawingArea that renders
    draw_fn(cr, size) on click. `.classpad-bar-btn` (CSS, set up by Bar)
    trims the stock GTK theme's button padding, which otherwise dwarfs an
    icon this size on a 55px-tall bar.
    """
    button = Gtk.Button()
    button.set_tooltip_text(tooltip)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.get_style_context().add_class("classpad-bar-btn")
    button.set_valign(Gtk.Align.CENTER)

    # Gtk.Box's cross-axis (vertical, in this horizontal row) allocation
    # defaults to stretching a packed child to the box's full height — found
    # on real hardware that this DrawingArea was getting stretched to the
    # bar's full 55px, but draw_fn(cr, size) still only draws within the
    # requested size x size (34 or 24) at the top-left of that taller cairo
    # surface, so the icon rendered high and off-center instead of centered
    # in the bar. Pinning both align properties to CENTER keeps the
    # DrawingArea at its natural size regardless of the row's height.
    area = Gtk.DrawingArea()
    area.set_size_request(size, size)
    area.set_halign(Gtk.Align.CENTER)
    area.set_valign(Gtk.Align.CENTER)
    area.connect("draw", lambda _widget, cr: draw_fn(cr, size))
    button.add(area)

    return button


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

        self._wifi_status = {"connected": False, "ssid": None, "ip": None, "signal": None}

        self.connect("realize", self._on_realize)
        self.connect("destroy", Gtk.main_quit)

        GLib.timeout_add(1000, self._tick_clock)
        GLib.timeout_add(1000, self._tick_activity)
        GLib.timeout_add(WIFI_POLL_INTERVAL_MS, self._tick_wifi)
        self._tick_wifi()  # first update immediately, not 5s after startup
        GLib.timeout_add(BATTERY_POLL_INTERVAL_MS, self._tick_battery)
        self._tick_battery()

    def _on_realize(self, *_args):
        set_strut_partial(self.get_window().get_xid(), self.screen_width, BAR_HEIGHT)

    def _build_ui(self):
        css = Gtk.CssProvider()
        css.load_from_data(BAR_BUTTON_CSS + b"\n" + CLOCK_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        overlay = Gtk.Overlay()
        self.add(overlay)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_start(10)
        row.set_margin_end(10)
        overlay.add(row)

        home_button = make_icon_button(draw_home_icon, HOME_ICON_SIZE, "Home")
        home_button.connect("clicked", self._on_home_clicked)
        row.pack_start(home_button, False, False, 0)

        self.activity_label = Gtk.Label(label="")
        self.activity_label.set_valign(Gtk.Align.CENTER)
        row.pack_start(self.activity_label, False, False, 0)

        self.wifi_label = Gtk.Label(label="")
        self.wifi_label.set_valign(Gtk.Align.CENTER)
        row.pack_end(self.wifi_label, False, False, 0)

        # Packed right after wifi_label so it lands immediately to wifi's
        # left (pack_end packs right-to-left — see note below) — next to the
        # wifi indicator, both hugging the top-right corner. A DrawingArea,
        # not a Gtk.Label like wifi_label — the battery indicator is a
        # hand-drawn icon (draw_battery_icon) rather than Pango text/glyphs,
        # to match the home/speaker icon style. Not wrapped in
        # make_icon_button/Gtk.Button since it isn't clickable; tooltips
        # work directly on a plain widget the same as they already do on
        # wifi_label (also not a button).
        self._battery_status = {"present": False, "percent": None, "charging": None}
        self.battery_area = Gtk.DrawingArea()
        self.battery_area.set_size_request(BATTERY_ICON_WIDTH, BATTERY_ICON_HEIGHT)
        self.battery_area.set_valign(Gtk.Align.CENTER)
        self.battery_area.connect("draw", self._on_battery_draw)
        row.pack_end(self.battery_area, False, False, 0)

        # row.pack_end() packs right-to-left: the first call ends up
        # rightmost, and each subsequent call lands to its left — so calls
        # go here in the reverse of the desired left-to-right visual order
        # (minus, slider, plus), with wifi_label/battery_area further right
        # still.
        volume_up = make_icon_button(lambda cr, size: draw_speaker_icon(cr, size, 3), SPEAKER_ICON_SIZE, "Louder")
        volume_up.connect("clicked", lambda _b: self._step_volume(VOLUME_STEP))
        row.pack_end(volume_up, False, False, 0)

        self.volume_adjustment = Gtk.Adjustment(value=70, lower=0, upper=100, step_increment=VOLUME_STEP)
        volume_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.volume_adjustment)
        volume_scale.set_size_request(120, -1)
        volume_scale.set_draw_value(False)
        volume_scale.set_valign(Gtk.Align.CENTER)
        volume_scale.connect("value-changed", self._on_volume_changed)
        row.pack_end(volume_scale, False, False, 0)

        volume_down = make_icon_button(lambda cr, size: draw_speaker_icon(cr, size, 1), SPEAKER_ICON_SIZE, "Quieter")
        volume_down.connect("clicked", lambda _b: self._step_volume(-VOLUME_STEP))
        row.pack_end(volume_down, False, False, 0)

        self.clock_label = Gtk.Label(label="")
        self.clock_label.get_style_context().add_class("classpad-clock")
        self.clock_label.set_halign(Gtk.Align.CENTER)
        self.clock_label.set_valign(Gtk.Align.CENTER)
        overlay.add_overlay(self.clock_label)

    def _tick_clock(self):
        # %-I (no leading zero) is a glibc/GNU extension — fine here since
        # this project only ever targets Debian. No seconds (just "7:57 PM")
        # — a live ticking seconds counter isn't information a classroom
        # wall clock needs, and this bar isn't a stopwatch.
        self.clock_label.set_text(time.strftime("%-I:%M %p"))
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
        status = resolve_wifi_status(self._wifi_status, get_wifi_status())
        self._wifi_status = status
        self.wifi_label.set_markup(wifi_indicator_markup(status))
        if status["connected"]:
            tooltip = f"SSID: {status['ssid'] or 'unknown'}\nIP: {status['ip'] or 'unknown'}"
        else:
            tooltip = "Not connected"
        self.wifi_label.set_tooltip_text(tooltip)
        return True

    def _on_battery_draw(self, _widget, cr):
        draw_battery_icon(cr, self._battery_status)

    def _tick_battery(self):
        self._battery_status = get_battery_status()
        self.battery_area.queue_draw()

        status = self._battery_status
        if status["present"]:
            state = "Charging" if status["charging"] else "On battery"
            tooltip = f"{state}: {status['percent']}%"
        else:
            tooltip = "No battery detected"
        self.battery_area.set_tooltip_text(tooltip)
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
