#!/usr/bin/env bash
# First-boot interactive WiFi setup gate. Runs once, on the real console
# (tty1), before lightdm starts — see classpad-wifi-setup.service and its
# lightdm.service.d drop-in (system/systemd/), both installed by
# scripts/install.sh.
#
# scripts/install.sh's own WiFi step is non-interactive and env-var-driven
# (CLASSPAD_WIFI_SSID/...) — good when the classroom's credentials are
# already known at imaging time. This script is the fallback for the more
# common real case: a machine is imaged on a bench over wired Ethernet (see
# preseeds/classpad/preseed.cfg) and only meets its actual classroom
# WPA2-Enterprise network afterward, at which point whoever is physically
# setting it up needs to pick the SSID and type credentials once — or, for
# a multi-machine rollout, plugs in a prebuilt "answer file" USB stick
# instead (see try_answer_file below and scripts/write-wifi-answers-usb.sh)
# and skips typing anything at all.
#
# Gated on actual network reachability, not on the presence of a specific
# nmcli connection name — install.sh's own idempotency check cares about
# exact-name matching for its own re-run safety, but this gate only cares
# "is there a route to the world right now." Any connection that provides
# that (wired, an already-preseeded profile, or one from a previous run of
# this script) satisfies it equally.
#
# Only ever gates the FIRST boot: once the marker file exists it is never
# re-checked, even if WiFi later stops working (e.g. a rotated password).
# That's deliberate — a machine with a stale WiFi profile should boot
# straight to the launcher and run off its local plugin cache (same
# never-block-the-kid philosophy as launcher/config.py's poller), not sit
# on a console prompt with nobody there to answer it.

set -euo pipefail

MARKER="${CLASSPAD_WIFI_SETUP_MARKER:-/opt/classpad/.wifi-setup-done}"
SETTLE_SECONDS="${CLASSPAD_WIFI_SETUP_SETTLE_SECONDS:-8}"
ANSWER_LABEL="${CLASSPAD_WIFI_ANSWERS_LABEL:-CLASSPAD-WIFI}"
# Same directory as this script — scripts/install.sh deploys the whole repo
# tree together, so wifi-configure.sh is always right next to it.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WIFI_CONFIGURE="$SCRIPT_DIR/wifi-configure.sh"

have_network() {
    # A real reachability check against the thing that actually matters
    # (can we reach classpad-admin), not just "is a link up" — nmcli's own
    # connectivity check depends on a connectivity URL being configured,
    # which isn't guaranteed on a fresh image.
    curl --silent --fail --max-time 3 "http://classpad-admin:5000/plugins/" >/dev/null 2>&1
}

# Looks for a removable volume labeled CLASSPAD-WIFI carrying a
# wifi-answers.env (same CLASSPAD_WIFI_* vars wifi-configure.sh already
# understands, see that script) and, if found, sources it — letting the
# caller configure WiFi non-interactively instead of showing the nmtui
# wizard. Deliberately a SEPARATE stick from the reusable provisioning
# ISO/USB (preseeds/classpad/preseed.cfg / build-provisioning-iso.sh)
# rather than baked into it: the base image is meant to be shareable across
# sites with no secrets on it, and this is the one artefact that's
# site-specific — see scripts/write-wifi-answers-usb.sh for how to build
# one and its plaintext-secret handling note.
try_answer_file() {
    local dev mnt found=1
    mnt="$(mktemp -d)"
    for dev in $(lsblk -rno PATH,LABEL 2>/dev/null | awk -v want="$ANSWER_LABEL" '$2==want{print $1}'); do
        if mount -o ro "$dev" "$mnt" 2>/dev/null; then
            if [ -f "$mnt/wifi-answers.env" ]; then
                echo "Found WiFi answer file on $dev, configuring automatically..."
                set -a
                # shellcheck disable=SC1091
                . "$mnt/wifi-answers.env"
                set +a
                found=0
            fi
            umount "$mnt" || true
        fi
        [ "$found" -eq 0 ] && break
    done
    rmdir "$mnt"
    return "$found"
}

if [ -f "$MARKER" ]; then
    exit 0
fi

# Give an already-up connection (wired bench link, or a previous successful
# run whose marker got lost some other way) a few seconds to settle before
# deciding a wizard is actually needed, rather than popping it unconditionally.
for _ in $(seq 1 "$SETTLE_SECONDS"); do
    if have_network; then
        mkdir -p "$(dirname "$MARKER")"
        touch "$MARKER"
        exit 0
    fi
    sleep 1
done

# Try a prebuilt answer-file USB before ever showing the interactive
# wizard — if it's present, valid, and results in a working connection,
# nobody needs to type anything.
if try_answer_file; then
    if bash "$WIFI_CONFIGURE"; then
        for _ in $(seq 1 "$SETTLE_SECONDS"); do
            if have_network; then
                mkdir -p "$(dirname "$MARKER")"
                touch "$MARKER"
                echo "Network confirmed via WiFi answer file -- continuing startup."
                exit 0
            fi
            sleep 1
        done
        echo "wifi-setup-interactive.sh: WiFi answer file was applied but the network still isn't reachable -- falling back to manual setup." >&2
    else
        echo "wifi-setup-interactive.sh: WiFi answer file was found but invalid -- see wifi-configure.sh output above. Falling back to manual setup." >&2
    fi
fi

clear
cat <<'BANNER'

  ==========================================
   Classpad -- WiFi setup
  ==========================================

   This machine can't reach the classpad
   server yet. Use the screen that opens
   next to add this classroom's WiFi
   network, then select it to connect.

   Network type:   WPA & WPA2 Enterprise
   Authentication: PEAP / MSCHAPv2

   Ask your network admin for the SSID,
   username, and password if you don't
   already have them.

   When you're done and connected, this
   screen will confirm it and continue
   starting up on its own.

BANNER
read -r -p "Press Enter to open WiFi setup..." _

# `nmtui edit` opens straight to the connection list/add screen, skipping
# nmtui's own top-level menu -- the fastest path to "add a WiFi connection"
# for someone who isn't a Linux user. NOT yet verified on real 11e hardware
# that a root-run nmtui creates a system-wide (not session-scoped)
# connection profile the way install.sh's own nmcli block explicitly does
# (connection.permissions "") -- confirm this on real hardware before
# relying on it; if it turns out scoped, add an
# `nmcli connection modify <name> connection.permissions ""` step after
# nmtui exits, same as install.sh's WiFi step already documents needing.
nmtui edit

# nmtui doesn't return a code that means "and it worked" -- re-check
# reachability rather than trusting its exit status, and loop instead of
# giving up after one attempt, in case the first attempt had a typo.
until have_network; do
    echo
    echo "Still can't reach the classpad server. Opening WiFi setup again..."
    sleep 2
    nmtui edit
done

mkdir -p "$(dirname "$MARKER")"
touch "$MARKER"
echo "Network confirmed -- continuing startup."
