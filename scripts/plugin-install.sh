#!/usr/bin/env bash
set -euo pipefail

PLUGINS_DIR="${CLASSPAD_PLUGINS_DIR:-/opt/classpad/plugins}"

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <plugin-zip-path>" >&2
    exit 1
fi

ZIP_PATH="$1"

if [ ! -f "$ZIP_PATH" ]; then
    echo "error: no such file: $ZIP_PATH" >&2
    exit 1
fi

STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGING_DIR"' EXIT

# Validate every archive entry and extract to a staging dir in one interpreter,
# so a rejected zip never touches anything outside $STAGING_DIR. The plugin id
# is read from the manifest here too, and validated before it is ever used to
# build a filesystem path in this script (a malicious id, e.g. "../../etc", is
# a second path-traversal vector independent of the archive entry names).
RESULT="$(python3 - "$ZIP_PATH" "$STAGING_DIR" <<'PYEOF'
import json
import re
import sys
import zipfile
from pathlib import Path

zip_path, staging_dir = sys.argv[1], sys.argv[2]
staging_dir = Path(staging_dir)

with zipfile.ZipFile(zip_path) as zf:
    for name in zf.namelist():
        normalized = name.replace("\\", "/")
        if normalized.startswith("/"):
            sys.exit(f"error: archive entry has an absolute path: {name}")
        if ".." in normalized.split("/"):
            sys.exit(f"error: archive entry attempts path traversal: {name}")

    zf.extractall(staging_dir)

manifest_paths = sorted(staging_dir.rglob("manifest.json"), key=lambda p: len(p.parts))
if not manifest_paths:
    sys.exit("error: no manifest.json found in archive")

plugin_root = manifest_paths[0].parent

try:
    manifest = json.loads(manifest_paths[0].read_text())
except json.JSONDecodeError as e:
    sys.exit(f"error: malformed manifest.json: {e}")

plugin_id = manifest.get("id", "")
if not re.match(r"^[a-z0-9][a-z0-9-]*$", plugin_id):
    sys.exit(f"error: invalid or missing plugin id in manifest: {plugin_id!r}")

print(plugin_id)
print(plugin_root)
PYEOF
)" || exit 1

PLUGIN_ID="$(echo "$RESULT" | sed -n '1p')"
PLUGIN_ROOT="$(echo "$RESULT" | sed -n '2p')"

if [ -z "$PLUGIN_ID" ] || [ -z "$PLUGIN_ROOT" ]; then
    echo "error: failed to determine plugin id or root from archive" >&2
    exit 1
fi

TARGET_DIR="$PLUGINS_DIR/$PLUGIN_ID"

mkdir -p "$PLUGINS_DIR"
rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"
cp -a "$PLUGIN_ROOT/." "$TARGET_DIR/"

INSTALL_SCRIPT="$TARGET_DIR/install.sh"
if [ -f "$INSTALL_SCRIPT" ]; then
    chmod +x "$INSTALL_SCRIPT"
    "$INSTALL_SCRIPT"
fi

echo "installed plugin '$PLUGIN_ID' to $TARGET_DIR"
