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

# Pin PATH explicitly rather than trusting the caller's environment to
# already include the sbin dirs — useradd/usermod/hostnamectl etc. live in
# /usr/sbin. Found failing with "useradd: command not found" despite
# genuinely running as root: some sudo `secure_path` configs and `su`
# without `-` are not guaranteed to hand down a PATH that includes it.
export PATH="/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="/opt/classpad"
KIOSK_USER="classpad"

# Found running this script from preseeds/classpad/preseed.cfg's
# late_command (d-i's `in-target`, i.e. a chroot with no PID 1 systemd
# actually running): `systemctl enable --now <unit>`/`daemon-reload` try to
# talk to a live systemd over D-Bus and fail outright there, which — under
# `set -e` above — aborts this whole script partway through, well before
# reaching the graphical.target step near the end. `enable` alone is
# symlink-only and safe in a chroot (the unit still starts normally on the
# real first boot); only the "start it right now" half needs skipping.
# /run/systemd/system only exists under a live, running systemd — see
# systemd.exec(5) — so it's what d-i's own chroot itself lacks and a normal
# post-boot invocation of this script always has.
enable_and_start_if_live() {
    systemctl enable "$1"
    if [ -d /run/systemd/system ]; then
        systemctl start "$1"
    fi
}

echo "== [1/15] Installing apt dependencies =="
apt-get update
# python3-xlib is required by bar/bar.py (see requirements.txt) but was
# missing from CLAUDE.md's original required-packages list — added here
# alongside the already-documented alsa-utils gap.
apt-get install -y \
    openbox obconf lightdm lightdm-gtk-greeter \
    python3 python3-pip python3-pygame \
    python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-xlib \
    chromium git curl rsync xbindkeys x11-utils xdotool \
    alsa-utils network-manager fonts-quicksand unattended-upgrades

# Binaries for the curated apps server/seed_plugins.py (2026-08-05) puts in
# every fresh server's plugin catalogue — a seeded catalogue entry only
# delivers the plugin bundle (manifest+icon) via plugin_deploy.py, not the
# underlying program. Without this, enabling e.g. Writer on a freshly
# imaged machine hits the exact "greyed out, package not installed" failure
# found on real hardware building the Calc/Impress tiles (see CLAUDE.md).
# gcompris-qt: trixie only ships the Qt/QML rewrite, not classic "gcompris"
# (see the KILL_LIST comment in process_manager.py/recovery.sh for the same
# correction). luanti-game-minetest: the default subgame — Luanti alone has
# no content to play. Package names confirmed against a real Debian trixie
# install, not assumed.
apt-get install -y \
    tuxpaint tuxtype tuxmath gcompris-qt cheese \
    libreoffice-writer \
    luanti luanti-game-minetest

echo "== [2/15] Creating $KIOSK_USER user =="
if ! id -u "$KIOSK_USER" >/dev/null 2>&1; then
    # Groups mirror what `adduser` grants a normal Debian desktop user by
    # default (checked against the dev account on this same image) — audio
    # is what actually matters for the bar's volume slider, in case logind's
    # seat ACL doesn't cover it.
    useradd --create-home --shell /bin/bash --groups audio,video,plugdev "$KIOSK_USER"
fi

echo "== [3/15] Deploying repo to $DEPLOY_DIR =="
mkdir -p "$DEPLOY_DIR"
# machine_id/server_url/config_cache.json are runtime-generated, not part of
# the repo — --delete would wipe them on every re-run otherwise (machine_id
# gets rewritten in step 11 and server_url in step 13 anyway if their env vars
# are set, but config_cache.json has no such regeneration step, and any of
# the three set via an env var not present on a given re-run would otherwise
# just vanish, contradicting this script's "safe to re-run" claim). documents/
# is the same shape of problem at a much larger blast radius: it holds
# children's saved word processor stories (plugins/wordprocessor/app/documents.py),
# lives outside the repo entirely, and --delete would wipe every saved story
# on the next re-run without this exclude.
rsync -a --delete \
    --exclude='.git' --exclude='.claude' \
    --exclude='tests' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='machine_id' --exclude='server_url' --exclude='config_cache.json' \
    --exclude='documents' \
    "$REPO_DIR"/ "$DEPLOY_DIR"/
chown -R "$KIOSK_USER":"$KIOSK_USER" "$DEPLOY_DIR"

echo "== [4/15] Configuring unattended security updates =="
# These are unsupervised classroom kiosks with no admin regularly checking
# in on them (same reasoning as the plugin-deploy timer below) — a missed
# Debian security update just sits there forever unless something applies
# it on its own. Deliberately scoped to security updates only, not all
# package updates: the apt-get install in
# step 1 already pulled in unattended-upgrades, and its own
# package-provided /etc/apt/apt.conf.d/50unattended-upgrades (a conffile —
# never edited by this script, same "don't touch package-owned config"
# rule as GRUB below) ships with only the Debian-Security origin
# uncommented by default, not the plain Debian origin. See
# system/apt/apt.conf.d/ for why the two drop-ins below don't try to
# override that. Runs after the repo deploy above since both drop-ins are
# checked-in files copied from $DEPLOY_DIR, same pattern as the Chromium
# policy and X11 drop-in below.
mkdir -p /etc/apt/apt.conf.d
cp "$DEPLOY_DIR/system/apt/apt.conf.d/20auto-upgrades" \
    /etc/apt/apt.conf.d/20auto-upgrades
cp "$DEPLOY_DIR/system/apt/apt.conf.d/52classpad-auto-reboot" \
    /etc/apt/apt.conf.d/52classpad-auto-reboot
# Package postinst already enables this, but doing it explicitly matches
# this script's existing pattern of not assuming a package default holds
# (e.g. the plugin-deploy timer below is also explicitly enabled) and
# keeps this step idempotent on its own.
enable_and_start_if_live unattended-upgrades.service

echo "== [5/15] Installing Chromium managed policy =="
mkdir -p /etc/chromium/policies/managed
cp "$DEPLOY_DIR/system/chromium/policies/managed/classpad-policy.json" \
    /etc/chromium/policies/managed/classpad-policy.json

echo "== [6/15] Silencing GRUB menu (always boot the current kernel) =="
# Kiosk machines have no one present to pick an entry, and a raw whole-disk
# clone of the golden image carries /etc/default/grub and the generated
# /boot/grub/grub.cfg forward unchanged onto every machine it's imaged onto —
# so this only needs setting once, here, rather than per-machine.
# GRUB_DEFAULT=0 + GRUB_TIMEOUT=0 + GRUB_TIMEOUT_STYLE=hidden together mean no
# menu is ever drawn and the top entry boots immediately with no delay; on a
# single-OS box the top entry is always the newest installed kernel, so this
# stays "the right one" automatically across `apt upgrade`s that add new
# kernel entries. Deliberately not touching os-prober/submenu settings beyond
# this — GRUB_TIMEOUT_STYLE=hidden already suppresses the menu screen
# entirely, and holding Shift during boot still force-shows it (Debian
# grub-pc default), which is kept on purpose as the same kind of
# hidden-until-needed recovery path as the Ctrl+Alt+Shift+Escape hotkey
# elsewhere in this project — not stripped further.
if [ -f /etc/default/grub ]; then
    set_grub_default() {
        local key="$1" value="$2"
        if grep -qE "^${key}=" /etc/default/grub; then
            sed -i "s/^${key}=.*/${key}=${value}/" /etc/default/grub
        elif grep -qE "^#${key}=" /etc/default/grub; then
            sed -i "s/^#${key}=.*/${key}=${value}/" /etc/default/grub
        else
            echo "${key}=${value}" >> /etc/default/grub
        fi
    }
    set_grub_default GRUB_DEFAULT 0
    set_grub_default GRUB_TIMEOUT 0
    set_grub_default GRUB_TIMEOUT_STYLE hidden
    if command -v update-grub >/dev/null 2>&1; then
        update-grub
    else
        echo "install.sh: WARNING update-grub not found; /etc/default/grub was updated but grub.cfg was not regenerated" >&2
    fi
else
    echo "install.sh: WARNING /etc/default/grub not found (non-GRUB bootloader?); skipping silent-boot config" >&2
fi

echo "== [7/15] Disabling Ctrl+Alt+Fn VT switching =="
# See system/X11/xorg.conf.d/10-classpad-no-vtswitch.conf.
mkdir -p /etc/X11/xorg.conf.d
cp "$DEPLOY_DIR/system/X11/xorg.conf.d/10-classpad-no-vtswitch.conf" \
    /etc/X11/xorg.conf.d/10-classpad-no-vtswitch.conf

echo "== [8/15] Installing systemd user units and openbox autostart =="
mkdir -p /etc/systemd/user
cp "$DEPLOY_DIR/system/systemd/classpad-launcher.service" \
    /etc/systemd/user/classpad-launcher.service
cp "$DEPLOY_DIR/system/systemd/classpad-bar.service" \
    /etc/systemd/user/classpad-bar.service

# `install -d` only chowns the leaf directory it's given, not any parent
# directories it has to create along the way — found on real hardware:
# calling it with just ".config/openbox" left ".config" itself owned by
# root (created as a side effect, default mkdir ownership), which broke
# every app that needs to write under ~/.config for the classpad user.
# Chromium was the one that surfaced it loudly: it couldn't create
# ~/.config/chromium/Crash\ Reports for its crash database, and treats
# that as fatal — every website-type plugin failed to open at all.
# Explicitly chowning .config itself first, before the leaf, fixes this
# regardless of whether .config already existed going in.
install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "/home/$KIOSK_USER/.config"
install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "/home/$KIOSK_USER/.config/openbox"
install -o "$KIOSK_USER" -g "$KIOSK_USER" -m 755 \
    "$DEPLOY_DIR/system/openbox/autostart" \
    "/home/$KIOSK_USER/.config/openbox/autostart"

echo "== [9/15] Installing plugin deployment timer (Phase 10) =="
# System-level (/etc/systemd/system/), not the user-level unit dir above —
# runs as root deliberately: plugin install.sh scripts are expected to run
# with elevated privileges (see CLAUDE.md's Plugin System trust model), so
# this stays a separate, root-owned background job rather than living
# inside the unprivileged, always-on classpad-launcher.service.
cp "$DEPLOY_DIR/system/systemd/classpad-plugin-deploy.service" \
    /etc/systemd/system/classpad-plugin-deploy.service
cp "$DEPLOY_DIR/system/systemd/classpad-plugin-deploy.timer" \
    /etc/systemd/system/classpad-plugin-deploy.timer
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
fi
enable_and_start_if_live classpad-plugin-deploy.timer

echo "== [10/15] Setting default volume =="
# Boots muted at 0% on this hardware with no visible error (found on real
# hardware, see CLAUDE.md) — 70% matches the bar's default slider position.
amixer sset Master 70% unmute >/dev/null

echo "== [11/15] Setting hostname from serial number =="
SERIAL="$(dmidecode -s system-serial-number 2>/dev/null | tr -d '[:space:]')"
if [ -z "$SERIAL" ] || [ "$SERIAL" = "None" ]; then
    echo "install.sh: WARNING dmidecode returned no usable serial number; leaving hostname and machine_id untouched" >&2
else
    MACHINE_ID="CPad$SERIAL"
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

echo "== [12/15] WiFi (WPA2-Enterprise PEAP/MSCHAPv2) =="
# Logic lives in wifi-configure.sh (extracted 2026-08-06) so there's one
# implementation of the nmcli connection-add call, not this step's own copy.
bash "$DEPLOY_DIR/scripts/wifi-configure.sh"

echo "== [13/15] Server URL override (Phase 9 polling) =="
# Optional — launcher/config.py defaults to http://classpad-admin:5000
# (DEFAULT_SERVER_URL) if nothing overrides it, relying on this site's local
# DNS to resolve that name. Only set CLASSPAD_SERVER_URL for testing or a
# site using a different naming convention.
if [ -n "${CLASSPAD_SERVER_URL:-}" ]; then
    # Written once here rather than baked into the tracked systemd unit file
    # (which would force this script to template a repo file) — same
    # file-based pattern as machine_id above. launcher/config.py reads this
    # unless CLASSPAD_SERVER_URL is set directly in its own environment,
    # which takes priority (test/dev override).
    printf '%s' "$CLASSPAD_SERVER_URL" > "$DEPLOY_DIR/server_url"
    chown "$KIOSK_USER":"$KIOSK_USER" "$DEPLOY_DIR/server_url"
else
    echo "install.sh: CLASSPAD_SERVER_URL not set, using the classpad-admin default" >&2
fi

echo "== [14/15] Enabling autologin =="
mkdir -p /etc/lightdm/lightdm.conf.d
cp "$DEPLOY_DIR/system/lightdm/lightdm.conf" /etc/lightdm/lightdm.conf.d/50-classpad.conf
# A minimal `tasksel standard`-only base install (no desktop task) leaves
# the system's default systemd target at multi-user.target — installing the
# lightdm *package* alone doesn't change that, it only enables
# lightdm.service *within* graphical.target's dependency tree, which is
# never reached unless graphical.target is actually the boot target.
# `set-default` is an offline, symlink-only operation — safe and cheap to
# run unconditionally here regardless of how minimal the underlying OS
# install was. Idempotent: a no-op if already set.
systemctl set-default graphical.target

echo "== [15/15] Rebooting =="
# A reboot is genuinely required, not just a nicety: the hostname change
# (step 11/15), the GRUB/VT-switch config (steps 6-7/15), and the LightDM
# autologin drop-in just written above only take effect on a fresh boot.
# Leaving this to whoever runs install.sh (a teacher or IT volunteer, not
# necessarily anyone technical — see the "no admin regularly checking in"
# reasoning elsewhere in this file) risks the machine sitting half-applied
# indefinitely, so this reboots automatically rather than just printing a
# reminder.
#
# CLASSPAD_SKIP_REBOOT opts out, for the test suite / anyone re-running
# this by hand who wants to inspect the result before rebooting.
if [ -n "${CLASSPAD_SKIP_REBOOT:-}" ]; then
    echo "install.sh: done. CLASSPAD_SKIP_REBOOT is set, skipping automatic reboot — reboot manually to apply the hostname change and log in as $KIOSK_USER."
else
    echo "install.sh: done. Rebooting now to apply the hostname change and log in as $KIOSK_USER."
    reboot
fi
