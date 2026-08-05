#!/usr/bin/env bash
set -euo pipefail

ACTIVITY_FILE="${CLASSPAD_ACTIVITY_FILE:-/tmp/classpad_activity}"

# Must match launcher/process_manager.py's KILL_LIST. Corrected from "gcompris"
# — trixie only ships the Qt/QML rewrite, gcompris-qt. Matched by name (no -f):
# these are the real binary names, and -f would also catch e.g. a custom
# plugin's argv that happens to mention one of them.
# soffice.bin: `soffice` is a wrapper script that execs this; comm is the real binary.
# luanti: Debian's current package/binary name for Minetest, comm confirmed on real hardware.
KILL_LIST=(chromium tuxpaint tuxtype tuxmath gcompris-qt xylophone wordprocessor cheese soffice.bin luanti)

# SIGKILL, not the pkill default (SIGTERM) — confirmed on real hardware that
# TuxPaint catches SIGTERM and shows an "unsaved changes?" dialog instead of
# exiting. This is the emergency escape hatch for a child stuck in a
# fullscreen app; it can't depend on the stuck app cooperating with a
# graceful shutdown.
for name in "${KILL_LIST[@]}"; do
    pkill -9 "$name" 2>/dev/null || true
done

# Force-killing the bar and launcher themselves is opt-in via --full, not the
# default: bar.py's Home button also shells out to this script (killing the
# tracked child is enough to send the launcher home, see bar.py's
# _on_home_clicked), and a routine Home click has no reason to SIGKILL the
# bar that's handling the click. --full is for the xbindkeys emergency combo
# only (system/xbindkeys/xbindkeysrc), which is also the path that needs to
# recover a wedged bar/launcher, not just a wedged child app.
if [ "${1:-}" = "--full" ]; then
    # Both run as systemd --user units with Restart=always specifically so
    # this works. `systemctl kill` (not `stop`/`restart`) sends the signal
    # directly without going through the normal stop lifecycle and its
    # timeout, matching the SIGKILL-not-SIGTERM reasoning above: this is the
    # emergency path, it can't wait on a graceful shutdown. The unit's own
    # Restart=always then brings each back up fresh a couple seconds later.
    # `|| true` also covers dev/test hosts with no classpad-bar/launcher
    # units installed at all.
    systemctl --user kill --signal=SIGKILL classpad-bar.service classpad-launcher.service 2>/dev/null || true
fi

: > "$ACTIVITY_FILE"
