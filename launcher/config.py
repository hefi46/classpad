import json
import math
import os
import queue
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from launcher import process_manager
from launcher.plugin_manager import scan_plugins

GRID_MARGIN = 40
TILE_PADDING = 24
MAX_TILE_SIZE = 260

# CLASSPAD_SERVER_URL takes priority (matches the CLASSPAD_PLUGINS_DIR /
# CLASSPAD_ACTIVITY_FILE test-override convention); the file is the real
# production mechanism, written once by install.sh from the same env var —
# mirroring how machine_id is derived once at install time rather than
# re-read live on every boot.
SERVER_URL_ENV_VAR = "CLASSPAD_SERVER_URL"
SERVER_URL_FILE = Path("/opt/classpad/server_url")
# Hardcoded fallback rather than requiring explicit configuration on every
# machine — each deployment site runs its own site-local DNS with an entry
# for the server (CLAUDE.md's Network & Deployment Context), so a fresh
# machine just needs "classpad-admin" to resolve there and everything else
# works with zero configuration. The env var / file above stay as an
# override for testing or a site using a different naming convention.
DEFAULT_SERVER_URL = "http://classpad-admin:5000"
CONFIG_CACHE_FILE = Path("/opt/classpad/config_cache.json")

# Cross-process signalling with bar/bar.py's info panel — bar.py and the
# launcher are separate processes, so this follows the same shared-file
# convention as process_manager.ACTIVITY_FILE rather than adding real IPC.
SERVER_STATUS_FILE = Path("/tmp/classpad_server_status")
REFRESH_REQUEST_FILE = Path("/tmp/classpad_refresh_requested")

POLL_INTERVAL_SECONDS = 30
TELEMETRY_INTERVAL_SECONDS = 300
POLLER_TICK_SECONDS = 1
HTTP_TIMEOUT_SECONDS = 5


def compute_grid_dimensions(count, area_width, area_height):
    best_cols, best_rows, best_tile_size = 1, count, 0
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        tile_size = min(area_width / cols, area_height / rows)
        if tile_size > best_tile_size:
            best_cols, best_rows, best_tile_size = cols, rows, tile_size
    return best_cols, best_rows


def build_button_grid(plugins, area_width, area_height):
    count = len(plugins)
    if count == 0:
        return []

    grid_width = area_width - 2 * GRID_MARGIN
    grid_height = area_height - 2 * GRID_MARGIN

    cols, rows = compute_grid_dimensions(count, grid_width, grid_height)

    cell_width = grid_width / cols
    cell_height = grid_height / rows
    tile_size = int(min(cell_width, cell_height, MAX_TILE_SIZE + TILE_PADDING) - TILE_PADDING)

    layout = []
    for index, plugin in enumerate(plugins):
        row, col = divmod(index, cols)
        cell_x = GRID_MARGIN + col * cell_width
        cell_y = GRID_MARGIN + row * cell_height
        tile_x = int(cell_x + (cell_width - tile_size) / 2)
        tile_y = int(cell_y + (cell_height - tile_size) / 2)
        layout.append((plugin, (tile_x, tile_y, tile_size, tile_size)))

    return layout


# --- Server polling (Phase 9) ---------------------------------------------
#
# The main pygame loop blocks synchronously in process_manager.wait_for_exit()
# for however long a child app runs (potentially the whole school day), so
# polling/telemetry has to live on its own daemon thread (run_poller, started
# by launcher/main.py) rather than in the render loop. The only handoff back
# to the main thread is `update_queue`: the poller never touches pygame or
# constructs Button objects itself (that's SDL surface work — main-thread
# only), it just puts an ordered list of already-locally-installed Plugin
# objects on the queue for main.py to turn into Buttons when convenient.


def get_machine_id():
    return socket.gethostname()


def get_server_url():
    env_value = os.environ.get(SERVER_URL_ENV_VAR)
    if env_value:
        return env_value.rstrip("/")
    try:
        file_value = SERVER_URL_FILE.read_text().strip().rstrip("/")
        if file_value:
            return file_value
    except FileNotFoundError:
        pass
    return DEFAULT_SERVER_URL


def fetch_config(server_url, machine_id, timeout=HTTP_TIMEOUT_SECONDS):
    url = f"{server_url}/config/{machine_id}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def post_telemetry(server_url, machine_id, *, current_activity, error, force_home_ack, timeout=HTTP_TIMEOUT_SECONDS):
    url = f"{server_url}/telemetry/{machine_id}"
    body = json.dumps(
        {
            "current_activity": current_activity,
            "error": error,
            "force_home_ack": force_home_ack,
        }
    ).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def load_cached_config(cache_path=None):
    # cache_path=None rather than defaulting straight to CONFIG_CACHE_FILE:
    # a default *parameter* value is bound once at def time, so a test (or
    # run_poller's own no-argument calls) monkeypatching the module-level
    # CONFIG_CACHE_FILE constant wouldn't otherwise be picked up here.
    cache_path = Path(cache_path) if cache_path is not None else CONFIG_CACHE_FILE
    try:
        return json.loads(cache_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_cached_config(data, cache_path=None):
    cache_path = Path(cache_path) if cache_path is not None else CONFIG_CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data))


def reconcile_plugins(server_plugins, local_plugins):
    """Order the locally-installed plugins to match the server's enabled list.

    Only returns plugins already installed locally — an id the server has
    enabled but this machine hasn't downloaded yet is Phase 10's job, not
    this one. Deliberately returns an empty list rather than raising or
    filtering some other way when nothing matches; callers must not treat an
    empty result as "clear the grid" (see _maybe_enqueue) since server plugin
    lists this trivially/differently populated (a fresh/misconfigured server
    profile) are indistinguishable on the wire from "admin disabled
    everything", and blanking a kiosk screen a child is looking at is worse
    than briefly showing a stale grid.
    """
    local_by_id = {plugin.id: plugin for plugin in local_plugins}
    return [local_by_id[entry["id"]] for entry in server_plugins if entry["id"] in local_by_id]


def _write_server_status(ok):
    # Best-effort — found on real hardware that a stray root/other-user-owned
    # leftover at this path raises PermissionError, and this call site inside
    # the `except` branch of run_poller's poll attempt wasn't itself wrapped,
    # so a failure here crashed the entire poller thread (silently, since
    # it's a daemon thread) instead of just skipping the status update.
    try:
        SERVER_STATUS_FILE.write_text(json.dumps({"ok": ok, "checked_at": time.time()}))
    except OSError as e:
        print(f"warning: failed to write server status: {e}", file=sys.stderr)


def refresh_requested():
    """The info panel's "refresh now" button touches REFRESH_REQUEST_FILE from
    bar.py (a separate process) rather than calling into the poller directly.
    Consumed (deleted) here so a stale request doesn't keep re-firing.
    """
    if REFRESH_REQUEST_FILE.exists():
        REFRESH_REQUEST_FILE.unlink()
        return True
    return False


def _read_current_activity():
    try:
        activity = Path(process_manager.ACTIVITY_FILE).read_text().strip()
    except FileNotFoundError:
        return None
    return activity or None


def _maybe_enqueue(update_queue, plugins, last_applied_ids):
    """Push a reconciled plugin list to the main thread, unless it's empty or
    unchanged from what's already showing (avoids empty grids and avoids
    needless Button/icon rebuilds every poll when nothing actually changed).
    """
    if not plugins:
        return last_applied_ids
    ids = tuple(plugin.id for plugin in plugins)
    if ids == last_applied_ids:
        return last_applied_ids
    try:
        update_queue.get_nowait()
    except queue.Empty:
        pass
    update_queue.put_nowait(plugins)
    return ids


def run_poller(
    update_queue,
    stop_event,
    poll_interval=POLL_INTERVAL_SECONDS,
    telemetry_interval=TELEMETRY_INTERVAL_SECONDS,
    tick=POLLER_TICK_SECONDS,
):
    """Background loop: polls /config, applies force_home, sends telemetry.

    Runs until stop_event is set. Every network call is wrapped broadly —
    an unhandled exception here would silently kill polling for the rest of
    the session with no visible symptom, which is exactly the failure shape
    this project has repeatedly found on real hardware elsewhere.
    """
    machine_id = get_machine_id()
    last_poll = 0.0
    last_telemetry = 0.0
    last_activity_sent = object()  # sentinel, guaranteed != any real activity value
    last_applied_ids = None

    while not stop_event.is_set():
        server_url = get_server_url()
        now = time.monotonic()

        if refresh_requested():
            last_poll = 0.0  # forces the poll below to run this tick

        if server_url and now - last_poll >= poll_interval:
            last_poll = now
            try:
                response = fetch_config(server_url, machine_id)
                _write_server_status(ok=True)
                save_cached_config(response)
                last_applied_ids = _maybe_enqueue(
                    update_queue, reconcile_plugins(response.get("plugins", []), scan_plugins()), last_applied_ids
                )
                if response.get("force_home"):
                    # process_manager.kill_all() has the exact same effect as
                    # scripts/recovery.sh (both are kept in sync against the
                    # same KILL_LIST — see the "must match" comments on each)
                    # but is called in-process here rather than shelled out,
                    # since this thread isn't stuck (it's the one polling).
                    process_manager.kill_all()
                    try:
                        post_telemetry(
                            server_url,
                            machine_id,
                            current_activity=_read_current_activity(),
                            error=process_manager.pop_last_error(),
                            force_home_ack=True,
                        )
                        last_telemetry = time.monotonic()
                        last_activity_sent = _read_current_activity()
                    except Exception as e:
                        # Harmless if this fails: force_home is still True
                        # server-side, so the next poll just re-applies
                        # kill_all() (idempotent) and retries the ack.
                        print(f"warning: force_home ack failed: {e}", file=sys.stderr)
            except Exception as e:
                print(f"warning: config poll failed: {e}", file=sys.stderr)
                _write_server_status(ok=False)
                cached = load_cached_config()
                if cached is not None:
                    last_applied_ids = _maybe_enqueue(
                        update_queue, reconcile_plugins(cached.get("plugins", []), scan_plugins()), last_applied_ids
                    )

        current_activity = _read_current_activity()
        if server_url and (now - last_telemetry >= telemetry_interval or current_activity != last_activity_sent):
            try:
                post_telemetry(
                    server_url,
                    machine_id,
                    current_activity=current_activity,
                    error=process_manager.pop_last_error(),
                    force_home_ack=False,
                )
                last_telemetry = now
                last_activity_sent = current_activity
            except Exception as e:
                print(f"warning: telemetry send failed: {e}", file=sys.stderr)

        stop_event.wait(tick)
