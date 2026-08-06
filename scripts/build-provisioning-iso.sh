#!/usr/bin/env bash
# Builds the classpad provisioning USB/ISO: runs the vendored
# debian-preseed-iso-generator (https://github.com/bergmann-max/debian-preseed-iso-generator)
# to produce a preseeded Debian netinst ISO, then re-opens that ISO and
# injects a full copy of this repo (pinned to the commit checked out when
# this script runs) as /classpad-src on the disc itself.
#
# Why a second extract/repack pass instead of patching the vendored
# generator: the generator has no "extra files" option, and forking it
# means tracking upstream by hand forever instead of just re-cloning.
# Leaves scripts/vendor/debian-preseed-iso-generator/ untouched and does
# its own xorriso pass afterward, mirroring the exact BIOS+UEFI mkisofs
# invocation the generator itself uses so the result boots the same way.
# Only handles amd64 (BIOS+UEFI hybrid) -- the only arch this project
# targets (11e ThinkPads, x86_64); the generator's arm64 (UEFI-only)
# branch isn't replicated here.
#
# Result: preseed/late_command can `cp -a /cdrom/classpad-src ...` and run
# scripts/install.sh with zero dependency on classpad-admin or GitHub being
# reachable during provisioning -- see preseeds/classpad/preseed.cfg and
# CLAUDE.md's Central Server section for why that matters.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GENERATOR_DIR="$REPO_ROOT/scripts/vendor/debian-preseed-iso-generator"
GENERATOR_URL="https://github.com/bergmann-max/debian-preseed-iso-generator"
PRESEED_NAME="${1:-classpad}"
ARCH="${CLASSPAD_ISO_ARCH:-amd64}"
OUT_DIR="$REPO_ROOT/out"

if [ "$ARCH" != "amd64" ]; then
    echo "build-provisioning-iso.sh: only amd64 is supported (got '$ARCH')" >&2
    exit 1
fi

for cmd in git xorriso find md5sum tar; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "build-provisioning-iso.sh: required tool '$cmd' not found" >&2; exit 1; }
done

if [ ! -d "$GENERATOR_DIR" ]; then
    echo "build-provisioning-iso.sh: cloning debian-preseed-iso-generator into $GENERATOR_DIR" >&2
    git clone --depth 1 "$GENERATOR_URL" "$GENERATOR_DIR"
else
    echo "build-provisioning-iso.sh: using existing checkout at $GENERATOR_DIR (git pull it yourself to update)" >&2
fi

if [ ! -f "$REPO_ROOT/preseeds/$PRESEED_NAME/preseed.cfg" ]; then
    echo "build-provisioning-iso.sh: no such preseed 'preseeds/$PRESEED_NAME/preseed.cfg'" >&2
    exit 1
fi

echo "== 1/3: Running debian-preseed-iso-generator (preseed=$PRESEED_NAME, arch=$ARCH) =="
# The generator looks for preseeds/<name>/preseed.cfg relative to its own
# directory -- symlink ours in rather than duplicating it or teaching the
# generator about a second preseeds/ location.
mkdir -p "$GENERATOR_DIR/preseeds"
ln -sfn "$REPO_ROOT/preseeds/$PRESEED_NAME" "$GENERATOR_DIR/preseeds/$PRESEED_NAME"
( cd "$GENERATOR_DIR" && ./debian-preseed-iso-generator.sh -p "$PRESEED_NAME" -a "$ARCH" -o "$OUT_DIR" )

BASE_ISO="$OUT_DIR/${PRESEED_NAME}-preseed-debian-${ARCH}-netinst.iso"
if [ ! -f "$BASE_ISO" ]; then
    echo "build-provisioning-iso.sh: expected ISO not found: $BASE_ISO" >&2
    exit 1
fi
FINAL_ISO="$OUT_DIR/${PRESEED_NAME}-preseed-debian-${ARCH}-netinst-with-src.iso"

REPO_COMMIT="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
if ! git -C "$REPO_ROOT" diff --quiet HEAD 2>/dev/null; then
    echo "build-provisioning-iso.sh: WARNING working tree has uncommitted changes -- bundling committed HEAD ($REPO_COMMIT) only, not your local edits" >&2
fi

echo "== 2/3: Staging repo content to inject (pinned at $REPO_COMMIT) =="
STAGE=$(mktemp -d -t classpad-src-XXXXXX)
ISODIR=$(mktemp -d -t classpad-isofiles-XXXXXX)
cleanup() { rm -rf "$STAGE" "$ISODIR"; }
trap cleanup EXIT

mkdir -p "$STAGE/classpad-src"
git -C "$REPO_ROOT" archive HEAD | tar -x -C "$STAGE/classpad-src"

echo "== 3/3: Re-opening ISO to inject classpad-src =="
xorriso -osirrox on -indev "$BASE_ISO" -extract / "$ISODIR"
chmod -R u+w "$ISODIR"
cp -a "$STAGE/classpad-src" "$ISODIR/classpad-src"
( cd "$ISODIR" && find . -type f -print0 | xargs -0 md5sum > md5sum.txt )

ISOHDPFX=""
for candidate in "$ISODIR"/isolinux/isohdpfx.bin \
                 /usr/lib/ISOLINUX/isohdpfx.bin \
                 /usr/lib/syslinux/bios/isohdpfx.bin \
                 /usr/lib/syslinux/mbr/isohdpfx.bin \
                 /usr/share/syslinux/isohdpfx.bin; do
    [ -f "$candidate" ] && { ISOHDPFX="$candidate"; break; }
done
if [ -z "$ISOHDPFX" ]; then
    echo "build-provisioning-iso.sh: isohdpfx.bin not found (install isolinux or syslinux)" >&2
    exit 1
fi

xorriso -as mkisofs \
    -r -V "DEBIAN-PRESEED" \
    -o "$FINAL_ISO" \
    -J -joliet-long \
    -isohybrid-mbr "$ISOHDPFX" \
    -b isolinux/isolinux.bin \
    -c isolinux/boot.cat \
    -boot-load-size 4 -boot-info-table -no-emul-boot \
    -eltorito-alt-boot \
    -e boot/grub/efi.img \
    -no-emul-boot -isohybrid-gpt-basdat \
    "$ISODIR"

echo "build-provisioning-iso.sh: done -> $FINAL_ISO"
echo "build-provisioning-iso.sh: base (pre-injection) ISO also left at $BASE_ISO"
