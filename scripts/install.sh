#!/usr/bin/env bash
set -euo pipefail

# First-time machine setup. Idempotent and add-only — safe to re-run on an
# already-provisioned machine (e.g. after a git pull) without disturbing
# unrelated system state (existing NetworkManager connections in particular
# are never modified or deleted, only added).
#
# Ordered so the least reversible steps run last: if this script is
# interrupted partway through, the machine is left in a still-usable state
# rather than half-switched to the kiosk user.

if [ "$(id -u)" -ne 0 ]; then
    echo "install.sh must be run as root (sudo)" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="/opt/classpad"
KIOSK_USER="classpad"

echo "== [1/11] Installing apt dependencies =="
apt-get update
# python3-xlib is required by bar/bar.py (see requirements.txt) but was
# missing from CLAUDE.md's original required-packages list — added here
# alongside the already-documented alsa-utils gap.
apt-get install -y \
    openbox obconf lightdm lightdm-gtk-greeter \
    python3 python3-pip python3-pygame \
    python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-xlib \
    chromium git curl rsync xbindkeys x11-utils xdotool \
    alsa-utils network-manager

echo "== [2/11] Creating $KIOSK_USER user =="
if ! id -u "$KIOSK_USER" >/dev/null 2>&1; then
    # Groups mirror what `adduser` grants a normal Debian desktop user by
    # default (checked against the dev account on this same image) — audio
    # is what actually matters for the bar's volume slider, in case logind's
    # seat ACL doesn't cover it.
    useradd --create-home --shell /bin/bash --groups audio,video,plugdev "$KIOSK_USER"
fi

echo "== [3/11] Deploying repo to $DEPLOY_DIR =="
mkdir -p "$DEPLOY_DIR"
# machine_id/server_url/config_cache.json are runtime-generated, not part of
# the repo — --delete would wipe them on every re-run otherwise (machine_id
# gets rewritten in step 7 and server_url in step 9 anyway if their env vars
# are set, but config_cache.json has no such regeneration step, and any of
# the three set via an env var not present on a given re-run would otherwise
# just vanish, contradicting this script's "safe to re-run" claim).
rsync -a --delete \
    --exclude='.git' --exclude='.claude' \
    --exclude='tests' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='machine_id' --exclude='server_url' --exclude='config_cache.json' \
    "$REPO_DIR"/ "$DEPLOY_DIR"/
chown -R "$KIOSK_USER":"$KIOSK_USER" "$DEPLOY_DIR"

echo "== [4/11] Installing Chromium managed policy =="
mkdir -p /etc/chromium/policies/managed
cp "$DEPLOY_DIR/system/chromium/policies/managed/classpad-policy.json" \
    /etc/chromium/policies/managed/classpad-policy.json

echo "== [5/11] Installing systemd user unit and openbox autostart =="
mkdir -p /etc/systemd/user
cp "$DEPLOY_DIR/system/systemd/classpad-launcher.service" \
    /etc/systemd/user/classpad-launcher.service

install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "/home/$KIOSK_USER/.config/openbox"
install -o "$KIOSK_USER" -g "$KIOSK_USER" -m 755 \
    "$DEPLOY_DIR/system/openbox/autostart" \
    "/home/$KIOSK_USER/.config/openbox/autostart"

echo "== [6/11] Installing plugin deployment timer (Phase 10) =="
# System-level (/etc/systemd/system/), not the user-level unit dir above —
# runs as root deliberately: plugin install.sh scripts are expected to run
# with elevated privileges (see CLAUDE.md's Plugin System trust model), so
# this stays a separate, root-owned background job rather than living
# inside the unprivileged, always-on classpad-launcher.service.
cp "$DEPLOY_DIR/system/systemd/classpad-plugin-deploy.service" \
    /etc/systemd/system/classpad-plugin-deploy.service
cp "$DEPLOY_DIR/system/systemd/classpad-plugin-deploy.timer" \
    /etc/systemd/system/classpad-plugin-deploy.timer
systemctl daemon-reload
systemctl enable --now classpad-plugin-deploy.timer

echo "== [7/11] Setting default volume =="
# Boots muted at 0% on this hardware with no visible error (found on real
# hardware, see CLAUDE.md) — 70% matches the bar's default slider position.
amixer sset Master 70% unmute >/dev/null

echo "== [8/11] Setting hostname from serial number =="
SERIAL="$(dmidecode -s system-serial-number 2>/dev/null | tr -d '[:space:]')"
if [ -z "$SERIAL" ] || [ "$SERIAL" = "None" ]; then
    echo "install.sh: WARNING dmidecode returned no usable serial number; leaving hostname and machine_id untouched" >&2
else
    MACHINE_ID="11e-$SERIAL"
    hostnamectl set-hostname "$MACHINE_ID"
    # hostnamectl alone leaves /etc/hosts' 127.0.1.1 line pointing at the old
    # name — found on real hardware: harmless but every subsequent `sudo`
    # invocation prints "unable to resolve host <old-name>" until this is fixed.
    if grep -q '^127\.0\.1\.1' /etc/hosts; then
        sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$MACHINE_ID/" /etc/hosts
    else
        printf '127.0.1.1\t%s\n' "$MACHINE_ID" >> /etc/hosts
    fi
    # Written once here rather than re-derived at every boot: dmidecode needs
    # root, and a later boot path might not have it (see pre-build-decisions.md §7).
    echo "$MACHINE_ID" > "$DEPLOY_DIR/machine_id"
    chown "$KIOSK_USER":"$KIOSK_USER" "$DEPLOY_DIR/machine_id"
fi

echo "== [9/11] WiFi (WPA2-Enterprise PEAP/MSCHAPv2) =="
if [ -n "${CLASSPAD_WIFI_SSID:-}" ]; then
    : "${CLASSPAD_WIFI_IDENTITY:?CLASSPAD_WIFI_IDENTITY must be set alongside CLASSPAD_WIFI_SSID}"
    : "${CLASSPAD_WIFI_PASSWORD:?CLASSPAD_WIFI_PASSWORD must be set alongside CLASSPAD_WIFI_SSID}"
    CONN_NAME="classpad-school-wifi"
    if nmcli -t -f NAME connection show | grep -Fxq "$CONN_NAME"; then
        echo "install.sh: nmcli connection '$CONN_NAME' already exists, leaving it untouched" >&2
    else
        # ifname "*" + empty connection.permissions: matches any wifi device
        # and isn't bound to a logged-in user, so it's up at the greeter
        # before autologin — the launcher polls the server on startup.
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
            echo "install.sh: WARNING CLASSPAD_WIFI_CA_CERT not set — PEAP server certificate is not pinned for this connection. Set it once the site's RADIUS CA cert is available." >&2
        fi
    fi
else
    echo "install.sh: CLASSPAD_WIFI_SSID not set, skipping WiFi profile creation" >&2
fi

echo "== [10/11] Server URL (Phase 9 polling) =="
if [ -n "${CLASSPAD_SERVER_URL:-}" ]; then
    # Written once here rather than baked into the tracked systemd unit file
    # (which would force this script to template a repo file) — same
    # file-based pattern as machine_id above. launcher/config.py reads this
    # unless CLASSPAD_SERVER_URL is set directly in its own environment,
    # which takes priority (test/dev override).
    printf '%s' "$CLASSPAD_SERVER_URL" > "$DEPLOY_DIR/server_url"
    chown "$KIOSK_USER":"$KIOSK_USER" "$DEPLOY_DIR/server_url"
else
    echo "install.sh: WARNING CLASSPAD_SERVER_URL not set — server polling (Phase 9) stays disabled; the launcher runs on locally-installed plugins only until this is configured." >&2
fi

echo "== [11/11] Enabling autologin =="
mkdir -p /etc/lightdm/lightdm.conf.d
cp "$DEPLOY_DIR/system/lightdm/lightdm.conf" /etc/lightdm/lightdm.conf.d/50-classpad.conf

echo "install.sh: done. Reboot to apply the hostname change and log in as $KIOSK_USER."
