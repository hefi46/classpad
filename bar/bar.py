#!/usr/bin/env python3
import subprocess
import time
from pathlib import Path

import gi
from Xlib import Xatom
from Xlib.display import Display

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

BAR_HEIGHT = 55
ACTIVITY_FILE = "/tmp/classpad_activity"
RECOVERY_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "recovery.sh"


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

        volume_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=Gtk.Adjustment(value=70, lower=0, upper=100, step_increment=5),
        )
        volume_scale.set_size_request(120, -1)
        volume_scale.set_draw_value(False)
        volume_scale.connect("value-changed", self._on_volume_changed)
        row.pack_end(volume_scale, False, False, 0)

        self.clock_label = Gtk.Label(label="")
        self.clock_label.set_halign(Gtk.Align.CENTER)
        self.clock_label.set_valign(Gtk.Align.CENTER)
        overlay.add_overlay(self.clock_label)

    def _tick_clock(self):
        self.clock_label.set_text(time.strftime("%H:%M:%S"))
        return True

    def _tick_activity(self):
        try:
            with open(ACTIVITY_FILE) as f:
                activity = f.read().strip()
        except FileNotFoundError:
            activity = ""
        self.activity_label.set_text(activity)
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


def main():
    bar = Bar()
    bar.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
