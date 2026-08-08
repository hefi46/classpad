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
# Step 5/5 re-reads every file this script just wrote and checks it for
# the expected content (see check()/file_has()/file_lacks() below),
# reporting [OK]/[FAIL] per check and exiting non-zero if anything
# failed — a write that silently produced wrong content is not reported
# as done. The Windows counterpart (build-provisioning-usb.ps1) mirrors
# this step-for-step.
#
# Usage:
#   CLASSPAD_USER_PASSWORD_CRYPTED='$6$...' scripts/build-provisioning-usb.sh /media/you/DEBIAN_STICK [preseed-name]
# or, to have this script hash a plaintext password for you (openssl passwd -6):
#   CLASSPAD_USER_PASSWORD='plaintext'      scripts/build-provisioning-usb.sh /media/you/DEBIAN_STICK [preseed-name]

set -euo pipefail

# Tiny check framework for the verification pass at the end (step 5/5) --
# every file this script writes gets checked back for what actually landed,
# not just assumed correct because the write command didn't error. Defined
# up top since bash functions must exist before first use, but only
# exercised at the very end.
CHECKS_TOTAL=0
CHECKS_FAILED=0
check() {
    # check <description> <command...> -- runs the command, prints OK/FAIL,
    # and tracks pass/fail counts for the final summary. Never trips
    # set -e itself: the command's exit status is only ever consumed by
    # this function's own `if`, never propagated raw.
    local desc="$1"; shift
    CHECKS_TOTAL=$((CHECKS_TOTAL + 1))
    if "$@" >/dev/null 2>&1; then
        echo "  [OK]   $desc"
    else
        echo "  [FAIL] $desc" >&2
        CHECKS_FAILED=$((CHECKS_FAILED + 1))
    fi
}
file_has() { grep -qF -- "$2" "$1"; }
file_lacks() { ! grep -qF -- "$2" "$1"; }

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
# header comment in preseeds/classpad/preseed.cfg). Provide either:
#   CLASSPAD_USER_PASSWORD_CRYPTED   a pre-hashed SHA-512 crypt string,
#                                    e.g. from `mkpasswd -m sha-512`
#   CLASSPAD_USER_PASSWORD           a plaintext password -- hashed below
#                                    with `openssl passwd -6` (same
#                                    SHA-512 crypt format) so you don't
#                                    need the `whois` package (mkpasswd)
#                                    installed just for this.
if [ -z "${CLASSPAD_USER_PASSWORD_CRYPTED:-}" ]; then
    if [ -n "${CLASSPAD_USER_PASSWORD:-}" ]; then
        command -v openssl >/dev/null 2>&1 || {
            echo "build-provisioning-usb.sh: CLASSPAD_USER_PASSWORD was set but 'openssl' is not available to hash it." >&2
            echo "  Install openssl, or pre-hash it yourself and set CLASSPAD_USER_PASSWORD_CRYPTED instead: mkpasswd -m sha-512" >&2
            exit 1
        }
        # -stdin (not a CLI arg) so the plaintext password never appears in
        # `ps`/process listings. Fresh random salt per run -- crypt(3)
        # embeds the salt in the output itself, so the hash stays
        # self-describing without needing to remember the salt separately.
        CLASSPAD_USER_PASSWORD_CRYPTED="$(openssl passwd -6 -salt "$(openssl rand -hex 8)" -stdin <<<"$CLASSPAD_USER_PASSWORD")"
        unset CLASSPAD_USER_PASSWORD
    else
        echo "build-provisioning-usb.sh: set either CLASSPAD_USER_PASSWORD_CRYPTED (pre-hashed) or CLASSPAD_USER_PASSWORD (plaintext -- hashed automatically)." >&2
        echo "  To pre-hash yourself instead: mkpasswd -m sha-512" >&2
        exit 1
    fi
fi

for cmd in findmnt git tar; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "build-provisioning-usb.sh: required tool '$cmd' not found" >&2; exit 1; }
done

# Reject a DD-mode (ISO9660/UDF, read-only) stick with a clear message
# rather than letting every write below fail confusingly one at a time.
#
# Under WSL2, a USB stick that already has a Windows drive letter (e.g. a
# Rufus-written stick showing up as E:) is normally reachable here for
# free at /mnt/e via DrvFs -- no usbipd/device-passthrough needed, since
# this script only needs file-level access, not the raw block device.
# But findmnt reports every DrvFs mount as fstype "9p" regardless of
# what's actually on the drive, which would otherwise trip the check
# below on a perfectly good FAT32 stick. When that happens, ask Windows
# directly (via powershell.exe, which WSL can always shell out to) what
# the drive letter's real filesystem is.
# -f/--first-only: if the same path has been mounted more than once (seen
# in practice on WSL2 -- a stale DrvFs mount left behind after the drive
# was reattached, with a fresh one stacked on top by a manual remount),
# plain `findmnt --target` prints one line per match, which would
# otherwise turn $FSTYPE into a multi-line string and corrupt every
# comparison below it.
FSTYPE="$(findmnt -f -no FSTYPE --target "$TARGET" 2>/dev/null || true)"
if [ "$FSTYPE" = "9p" ] || [ "$FSTYPE" = "drvfs" ]; then
    DRIVE_LETTER=""
    case "$TARGET" in
        */mnt/?|*/mnt/?/*) DRIVE_LETTER="${TARGET##*/mnt/}"; DRIVE_LETTER="${DRIVE_LETTER%%/*}" ;;
    esac
    if [ -n "$DRIVE_LETTER" ] && command -v powershell.exe >/dev/null 2>&1; then
        WIN_FSTYPE="$(powershell.exe -NoProfile -Command "(Get-Volume -DriveLetter '$DRIVE_LETTER').FileSystem" 2>/dev/null | tr -d '\r\n')"
        case "$WIN_FSTYPE" in
            FAT32) FSTYPE="vfat" ;;
            exFAT) FSTYPE="exfat" ;;
            "") ;;  # couldn't ask Windows -- fall through to the generic error below
            *) FSTYPE="$WIN_FSTYPE" ;;  # e.g. NTFS -- rejected below with a clear message
        esac
    fi
fi
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

echo "== 1/5: Writing preseed.cfg (preseed=$PRESEED_NAME) =="
# `|` as the sed delimiter, not `/` -- a sha-512-crypt hash's alphabet
# (./0-9A-Za-z$) can legally contain `/`, which would otherwise corrupt
# the substitution. `&` and backslashes (also special in sed's
# replacement text) never appear in that alphabet, so this is safe.
sed "s|@@USER_PASSWORD_CRYPTED@@|$CLASSPAD_USER_PASSWORD_CRYPTED|" "$PRESEED_TEMPLATE" > "$TARGET/preseed.cfg"

echo "== 2/5: Staging repo content (classpad-src/) =="
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

echo "== 3/5: Rewriting isolinux.cfg (BIOS boot) for auto-preseed =="
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

echo "== 4/5: Rewriting boot/grub/grub.cfg (UEFI boot) for auto-preseed =="
GRUB_PRESENT=0
if [ -f "$TARGET/boot/grub/grub.cfg" ]; then
    GRUB_PRESENT=1
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

echo "== 5/5: Verifying written files =="
check "preseed.cfg: exists and non-empty" test -s "$TARGET/preseed.cfg"
check "preseed.cfg: password placeholder was substituted" file_lacks "$TARGET/preseed.cfg" "@@USER_PASSWORD_CRYPTED@@"
check "preseed.cfg: crypted password hash present" file_has "$TARGET/preseed.cfg" "$CLASSPAD_USER_PASSWORD_CRYPTED"

# git ls-tree, not `git archive ... | tar -t` -- same file set (verified:
# no .gitattributes export-ignore rules exist in this repo to make them
# diverge), but avoids a second tar invocation and the pipefail footgun of
# piping through `grep -c` (which exits 1, not 0, when it selects no lines).
EXPECTED_FILE_COUNT="$(git -C "$REPO_ROOT" ls-tree -r --name-only HEAD | wc -l)"
ACTUAL_FILE_COUNT="$(find "$TARGET/classpad-src" -type f | wc -l)"
check "classpad-src: extracted file count matches repo archive ($EXPECTED_FILE_COUNT files)" \
    test "$EXPECTED_FILE_COUNT" -eq "$ACTUAL_FILE_COUNT"
check "classpad-src: scripts/install.sh present" test -f "$TARGET/classpad-src/scripts/install.sh"

check "isolinux.cfg: auto-boot label present" file_has "$TARGET/isolinux/isolinux.cfg" "label classpad_auto"
check "isolinux.cfg: kernel path correct" file_has "$TARGET/isolinux/isolinux.cfg" "kernel $KERNEL_PATH"
check "isolinux.cfg: append line correct" file_has "$TARGET/isolinux/isolinux.cfg" "$APPEND_LINE"
check "isolinux.cfg: original backed up" test -s "$TARGET/isolinux/isolinux.cfg.orig"

if [ "$GRUB_PRESENT" -eq 1 ]; then
    check "grub.cfg: auto-boot entry present" file_has "$TARGET/boot/grub/grub.cfg" "Automated Classpad install"
    check "grub.cfg: kernel+append line correct" file_has "$TARGET/boot/grub/grub.cfg" "linux  $KERNEL_PATH $APPEND_LINE"
    check "grub.cfg: initrd line correct" file_has "$TARGET/boot/grub/grub.cfg" "initrd $INITRD_PATH"
    check "grub.cfg: original backed up" test -s "$TARGET/boot/grub/grub.cfg.orig"
fi

echo
if [ "$CHECKS_FAILED" -ne 0 ]; then
    echo "build-provisioning-usb.sh: verification FAILED ($CHECKS_FAILED of $CHECKS_TOTAL checks) -- USB stick is NOT ready, do not use it." >&2
    exit 1
fi
echo "build-provisioning-usb.sh: verification passed ($CHECKS_TOTAL/$CHECKS_TOTAL checks)."

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
