#!/usr/bin/env bash
set -euo pipefail

ACTIVITY_FILE="${CLASSPAD_ACTIVITY_FILE:-/tmp/classpad_activity}"

# Must match launcher/process_manager.py's KILL_LIST. Corrected from "gcompris"
# — trixie only ships the Qt/QML rewrite, gcompris-qt. Matched by name (no -f):
# these are the real binary names, and -f would also catch e.g. a custom
# plugin's argv that happens to mention one of them.
KILL_LIST=(chromium tuxpaint tuxtype tuxmath gcompris-qt xylophone wordprocessor)

# SIGKILL, not the pkill default (SIGTERM) — confirmed on real hardware that
# TuxPaint catches SIGTERM and shows an "unsaved changes?" dialog instead of
# exiting. This is the emergency escape hatch for a child stuck in a
# fullscreen app; it can't depend on the stuck app cooperating with a
# graceful shutdown.
for name in "${KILL_LIST[@]}"; do
    pkill -9 "$name" 2>/dev/null || true
done

: > "$ACTIVITY_FILE"
