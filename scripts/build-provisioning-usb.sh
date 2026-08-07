#!/usr/bin/env bash
# Preps an already-written Debian netinst USB stick for unattended classpad
# provisioning: copies preseeds/<name>/preseed.cfg onto it (with the
# placeholder password substituted), rewrites the BIOS (isolinux) and UEFI
# (grub) boot menus so the stick boots straight into it with no operator
# interaction, and bundles a full copy of this repo as classpad-src/ so
# scripts/install.sh runs with zero dependency on classpad-admin or GitHub
# being reachable. See CLAUDE.md's "Provisioning / Imaging" section.
#
# Assumes: the target is a Debian netinst ISO already written to a USB
# stick with Rufus in "ISO Image mode" (or equivalent — anything that
# extracts the ISO's files onto a real, writable FAT32/exFAT filesystem
# rather than dd'ing the raw ISO9660 image). "DD Image mode" produces a
# read-only ISO9660 stick this script cannot write to at all — see the
# filesystem check below, which exists specifically to catch that mistake
# with a clear message instead of a confusing write failure partway
# through.
#
# Deliberately NOT a rebuilt ISO and NOT the vendored
# debian-preseed-iso-generator this project used before (see git history,
# commit 6832d69, reverted 911da1d) — this is the "traditional methods"
# approach: plain file copy plus a hand-edited boot menu, so there's
# nothing to vendor or keep in sync with an upstream tool.
#
# Usage:
#   CLASSPAD_USER_PASSWORD_CRYPTED='$6$...' scripts/build-provisioning-usb.sh /media/you/DEBIAN_STICK [preseed-name]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-}"
PRESEED_NAME="${2:-classpad}"

if [ -z "$TARGET" ]; then
    echo "usage: CLASSPAD_USER_PASSWORD_CRYPTED='...' $0 <usb-mount-point> [preseed-name]" >&2
    exit 1
fi
if [ ! -d "$TARGET" ]; then
    echo "build-provisioning-usb.sh: no such directory: $TARGET" >&2
    exit 1
fi

PRESEED_TEMPLATE="$REPO_ROOT/preseeds/$PRESEED_NAME/preseed.cfg"
if [ ! -f "$PRESEED_TEMPLATE" ]; then
    echo "build-provisioning-usb.sh: no such preseed 'preseeds/$PRESEED_NAME/preseed.cfg'" >&2
    exit 1
fi

# The whole point of this substitution: the repo's own copy of preseed.cfg
# is a template and must never carry a real crypted password (see the
# header comment in preseeds/classpad/preseed.cfg). Generate one with:
# mkpasswd -m sha-512
if [ -z "${CLASSPAD_USER_PASSWORD_CRYPTED:-}" ]; then
    echo "build-provisioning-usb.sh: CLASSPAD_USER_PASSWORD_CRYPTED is not set." >&2
    echo "  Generate one with: mkpasswd -m sha-512" >&2
    exit 1
fi

for cmd in findmnt git tar; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "build-provisioning-usb.sh: required tool '$cmd' not found" >&2; exit 1; }
done

# Reject a DD-mode (ISO9660/UDF, read-only) stick with a clear message
# rather than letting every write below fail confusingly one at a time.
FSTYPE="$(findmnt -no FSTYPE --target "$TARGET" 2>/dev/null || true)"
case "$FSTYPE" in
    vfat|exfat) ;;
    "")
        echo "build-provisioning-usb.sh: could not determine filesystem type of $TARGET (is it mounted?)" >&2
        exit 1
        ;;
    *)
        echo "build-provisioning-usb.sh: $TARGET is $FSTYPE, not FAT32/exFAT." >&2
        echo "  This looks like a stick written with Rufus 'DD Image mode' (read-only ISO9660)." >&2
        echo "  Re-write it with Rufus using 'ISO Image mode' instead, then re-run this script." >&2
        exit 1
        ;;
esac

# Confirm this actually looks like a Debian installer tree before writing
# anything into it, and find the kernel/initrd path (install.amd on 11e's
# x86_64 target, but don't hardcode the arch directory name).
if [ ! -f "$TARGET/isolinux/isolinux.cfg" ]; then
    echo "build-provisioning-usb.sh: $TARGET/isolinux/isolinux.cfg not found — is this really a Debian netinst stick?" >&2
    exit 1
fi
mapfile -t VMLINUZ_CANDIDATES < <(find "$TARGET" -maxdepth 2 -path "$TARGET/install.*/vmlinuz" 2>/dev/null)
if [ "${#VMLINUZ_CANDIDATES[@]}" -eq 0 ]; then
    echo "build-provisioning-usb.sh: no install.*/vmlinuz found under $TARGET" >&2
    exit 1
elif [ "${#VMLINUZ_CANDIDATES[@]}" -gt 1 ]; then
    echo "build-provisioning-usb.sh: multiple install.*/vmlinuz found under $TARGET, expected exactly one: ${VMLINUZ_CANDIDATES[*]}" >&2
    exit 1
fi
INSTALL_DIR="$(basename "$(dirname "${VMLINUZ_CANDIDATES[0]}")")"
KERNEL_PATH="/$INSTALL_DIR/vmlinuz"
INITRD_PATH="/$INSTALL_DIR/initrd.gz"

echo "== 1/4: Writing preseed.cfg (preseed=$PRESEED_NAME) =="
# `|` as the sed delimiter, not `/` -- a sha-512-crypt hash's alphabet
# (./0-9A-Za-z$) can legally contain `/`, which would otherwise corrupt
# the substitution. `&` and backslashes (also special in sed's
# replacement text) never appear in that alphabet, so this is safe.
sed "s|@@USER_PASSWORD_CRYPTED@@|$CLASSPAD_USER_PASSWORD_CRYPTED|" "$PRESEED_TEMPLATE" > "$TARGET/preseed.cfg"

echo "== 2/4: Staging repo content (classpad-src/) =="
REPO_COMMIT="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
if ! git -C "$REPO_ROOT" diff --quiet HEAD 2>/dev/null; then
    echo "build-provisioning-usb.sh: WARNING working tree has uncommitted changes -- bundling committed HEAD ($REPO_COMMIT) only, not your local edits" >&2
fi
rm -rf "$TARGET/classpad-src"
mkdir -p "$TARGET/classpad-src"
git -C "$REPO_ROOT" archive HEAD | tar -x -C "$TARGET/classpad-src"

# Boot-parameter order matters: everything preseed-related must come
# before the `---` separator (params after it go to the installed
# system's own kernel cmdline, not the installer). file=/cdrom/preseed.cfg
# relies on d-i mounting the boot medium at /cdrom regardless of the
# underlying filesystem (FAT32 here, not actually a CD) -- this is
# standard d-i behaviour for any boot device, not specific to real optical
# media.
APPEND_LINE="auto=true priority=critical file=/cdrom/preseed.cfg vga=788 --- quiet"

echo "== 3/4: Rewriting isolinux.cfg (BIOS boot) for auto-preseed =="
[ -f "$TARGET/isolinux/isolinux.cfg.orig" ] || cp "$TARGET/isolinux/isolinux.cfg" "$TARGET/isolinux/isolinux.cfg.orig"
# prompt 1 + a real (not near-instant) timeout: this stick reformats
# whatever disk it boots against with zero further confirmation
# (partman/confirm_nooverwrite is preseeded true). If it's left inserted
# and the machine reboots with USB ahead of the internal disk in boot
# order -- either at the end of this same install, or any time later --
# it will silently re-provision itself. A near-zero timeout gives no
# realistic window to notice and interrupt; 10s at least gives a bench
# operator a chance. There is no substitute for actually removing the
# stick once the install starts -- see this script's final printed
# reminder.
cat > "$TARGET/isolinux/isolinux.cfg" <<EOF
prompt 1
default classpad_auto
timeout 100
label classpad_auto
  menu label ^Automated Classpad install
  kernel $KERNEL_PATH
  append initrd=$INITRD_PATH $APPEND_LINE
EOF

echo "== 4/4: Rewriting boot/grub/grub.cfg (UEFI boot) for auto-preseed =="
if [ -f "$TARGET/boot/grub/grub.cfg" ]; then
    [ -f "$TARGET/boot/grub/grub.cfg.orig" ] || cp "$TARGET/boot/grub/grub.cfg" "$TARGET/boot/grub/grub.cfg.orig"
    cat > "$TARGET/boot/grub/grub.cfg" <<EOF
set default=0
set timeout=10
menuentry 'Automated Classpad install' {
    linux  $KERNEL_PATH $APPEND_LINE
    initrd $INITRD_PATH
}
EOF
else
    echo "build-provisioning-usb.sh: WARNING no boot/grub/grub.cfg found -- UEFI boot won't auto-preseed (BIOS/isolinux path still does)" >&2
fi

# The stick's own md5sum.txt (from the original ISO) is now stale, since
# we just edited two of the files it covers. Harmless in this flow -- it
# only affects the installer's optional "Check disc for defects" menu
# item, which nothing here uses -- but left as-is rather than
# regenerated, so a future reader isn't puzzled by a missing step.
echo "build-provisioning-usb.sh: done. $TARGET/md5sum.txt is now stale (harmless -- see comment in this script)."
echo "build-provisioning-usb.sh: USB ready at $TARGET (preseed=$PRESEED_NAME, repo pinned at $REPO_COMMIT)."
echo "build-provisioning-usb.sh: WARNING -- remove the USB stick once the install starts booting." >&2
echo "  This preseed reformats whatever disk it boots against with no further confirmation." >&2
echo "  Leaving the stick in for the post-install reboot re-provisions the machine again." >&2
