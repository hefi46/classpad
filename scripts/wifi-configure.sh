#!/usr/bin/env bash
# Adds the classpad-school-wifi NetworkManager connection from
# CLASSPAD_WIFI_SSID/IDENTITY/PASSWORD/CA_CERT in the environment.
#
# Extracted out of install.sh's own WiFi step (2026-08-06) into its own
# script mainly for readability — install.sh's WiFi step is the only
# caller: CLASSPAD_WIFI_* already set from the environment install.sh
# itself was invoked with (test/dev override, or a site that knows its
# creds at install time).
#
# Deliberately never fatal for the "no SSID configured" case (exits 0) —
# WiFi is treated as optional/best-effort at install time, not a hard
# requirement. Exits non-zero only for genuine misconfiguration (SSID set
# but IDENTITY/PASSWORD missing).

set -euo pipefail

CONN_NAME="classpad-school-wifi"

if [ -z "${CLASSPAD_WIFI_SSID:-}" ]; then
    echo "wifi-configure.sh: CLASSPAD_WIFI_SSID not set, skipping WiFi profile creation" >&2
    exit 0
fi

: "${CLASSPAD_WIFI_IDENTITY:?CLASSPAD_WIFI_IDENTITY must be set alongside CLASSPAD_WIFI_SSID}"
: "${CLASSPAD_WIFI_PASSWORD:?CLASSPAD_WIFI_PASSWORD must be set alongside CLASSPAD_WIFI_SSID}"

if nmcli -t -f NAME connection show | grep -Fxq "$CONN_NAME"; then
    echo "wifi-configure.sh: nmcli connection '$CONN_NAME' already exists, leaving it untouched" >&2
    exit 0
fi

# ifname "*" + empty connection.permissions: matches any wifi device and
# isn't bound to a logged-in user, so it's up at the greeter before
# autologin — the launcher polls the server on startup.
nmcli connection add \
    type wifi con-name "$CONN_NAME" ifname "*" ssid "$CLASSPAD_WIFI_SSID" \
    wifi-sec.key-mgmt wpa-eap \
    802-1x.eap peap \
    802-1x.phase2-auth mschapv2 \
    802-1x.identity "$CLASSPAD_WIFI_IDENTITY" \
    802-1x.password "$CLASSPAD_WIFI_PASSWORD" \
    connection.autoconnect yes \
    connection.permissions ""

if [ -n "${CLASSPAD_WIFI_CA_CERT:-}" ]; then
    nmcli connection modify "$CONN_NAME" 802-1x.ca-cert "$CLASSPAD_WIFI_CA_CERT"
else
    echo "wifi-configure.sh: WARNING CLASSPAD_WIFI_CA_CERT not set — PEAP server certificate is not pinned for this connection. Set it once the site's RADIUS CA cert is available." >&2
fi
