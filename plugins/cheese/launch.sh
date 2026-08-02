#!/usr/bin/env bash
# Cheese has no --maximized/--fullscreen flag of its own (checked --help),
# and its GSettings-remembered window state doesn't persist in this session
# (no dbus-launch, confirmed on real hardware: "failed to commit changes to
# dconf"). Maximizing it explicitly here, rather than adding an Openbox
# rc.xml <application> rule, keeps this scoped to just this one plugin
# instead of a global WM config change.
set -u

/usr/bin/cheese &
pid=$!

# Poll for the window instead of `xdotool search --sync` — confirmed on real
# hardware that --sync combined with --onlyvisible/--class reliably times out
# here even though the window does map within ~2s, a known rough edge in the
# xdotool version this image ships. A bounded poll loop is what actually
# works; if cheese never maps a window (crash, no camera), this just falls
# through to `wait` below without maximizing anything.
win=""
for _ in $(seq 1 30); do
    win="$(xdotool search --onlyvisible --class cheese 2>/dev/null | head -1)"
    [ -n "$win" ] && break
    sleep 0.2
done
if [ -n "$win" ]; then
    wmctrl -x -r cheese.Cheese -b add,maximized_vert,maximized_horz 2>/dev/null || true
fi

wait "$pid"
