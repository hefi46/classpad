#!/usr/bin/env bash
# Writes a wifi-answers.env file onto an already-prepared, already-mounted
# USB stick, for scripts/wifi-setup-interactive.sh's answer-file path.
#
# Deliberately does NOT format or label the stick itself — partitioning or
# formatting a block device from a script is destructive and one wrong
# device argument away from wiping the wrong disk. Prepare the stick once,
# by hand:
#   1. Format it (FAT32 or ext4 — wifi-setup-interactive.sh only needs a
#      mounted filesystem, doesn't care which).
#   2. Label the FILESYSTEM (not the partition table) "CLASSPAD-WIFI" —
#      e.g. `sudo fatlabel /dev/sdX1 CLASSPAD-WIFI` for FAT32, or
#      `sudo e2label /dev/sdX1 CLASSPAD-WIFI` for ext4. This exact label is
#      what wifi-setup-interactive.sh's try_answer_file() scans for.
#   3. Mount it, then run this script with the mountpoint as its argument.
#
# The stick this produces carries a plaintext WPA2-Enterprise password —
# treat it like any other credential: build one per site, keep it
# physically secured once built, don't leave it lying around a classroom.
# This is the same trust tradeoff the rest of this project already accepts
# for plugin zips (admin-trusted input, no sandboxing) — convenience for a
# fleet rollout, not a hardened secrets-delivery mechanism.

set -euo pipefail

MOUNTPOINT="${1:?Usage: $0 <mountpoint-of-already-mounted-usb-stick>}"

if [ ! -d "$MOUNTPOINT" ] || ! mountpoint -q "$MOUNTPOINT"; then
    echo "write-wifi-answers-usb.sh: '$MOUNTPOINT' is not a mounted filesystem" >&2
    exit 1
fi

read -r -p "SSID: " ssid
read -r -p "Identity (username): " identity
read -r -s -p "Password: " password
echo
read -r -p "CA cert path on the target machine (optional, leave blank if none): " ca_cert

if [ -z "$ssid" ] || [ -z "$identity" ] || [ -z "$password" ]; then
    echo "write-wifi-answers-usb.sh: SSID, identity, and password are all required" >&2
    exit 1
fi

OUT="$MOUNTPOINT/wifi-answers.env"
{
    # %q shell-quotes each value so special characters in a password can't
    # break the file when wifi-setup-interactive.sh later sources it.
    printf 'CLASSPAD_WIFI_SSID=%q\n' "$ssid"
    printf 'CLASSPAD_WIFI_IDENTITY=%q\n' "$identity"
    printf 'CLASSPAD_WIFI_PASSWORD=%q\n' "$password"
    if [ -n "$ca_cert" ]; then
        printf 'CLASSPAD_WIFI_CA_CERT=%q\n' "$ca_cert"
    fi
} > "$OUT"
chmod 600 "$OUT"

echo "write-wifi-answers-usb.sh: wrote $OUT"
echo "write-wifi-answers-usb.sh: unmount the stick and keep it physically secured until it's used."
