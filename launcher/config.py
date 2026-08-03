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
from launcher.button import ICON_SIZE
from launcher.plugin_manager import scan_plugins

GRID_MARGIN = 40
TILE_PADDING = 24
MAX_TILE_SIZE = 260

# Reserved zones for the pager (launcher/pager.py) — left/right strips for the
# prev/next arrows, a band at the bottom for the numbered page indicators.
# Defined here (not in pager.py) because build_button_grid needs them to keep
# the tile grid from overlapping the pager chrome; pager.py imports these back
# for its own layout so the two stay in sync by construction, not by hand.
ARROW_ZONE_WIDTH = 70
PAGE_INDICATOR_HEIGHT = 60

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

# Matches server/models.py's settings table default — used before the first
# successful poll (and if a server response is somehow missing the
# "background" key, e.g. an older server during a rolling upgrade).
DEFAULT_BACKGROUND_COLOR = "#add8f0"
# Cached alongside config_cache.json rather than in it — this is binary
# image data, not JSON. Only ever (re)written when image_version changes,
# so it survives server outages the same way config_cache.json does.
BACKGROUND_IMAGE_CACHE = Path("/opt/classpad/background_image.png")

# Cross-process signalling with bar/bar.py's info panel — bar.py and the
# launcher are separate processes, so this follows the same shared-file
# convention as process_manager.ACTIVITY_FILE rather than adding real IPC.
SERVER_STATUS_FILE = Path("/tmp/classpad_server_status")
REFRESH_REQUEST_FILE = Path("/tmp/classpad_refresh_requested")

POLL_INTERVAL_SECONDS = 30
TELEMETRY_INTERVAL_SECONDS = 300
POLLER_TICK_SECONDS = 1
HTTP_TIMEOUT_SECONDS = 5


def _grid_area(area_width, area_height):
    """The screen area available for tiles once the pager's arrow strips
    (left/right) and page-indicator band (bottom) are reserved.
    """
    left = GRID_MARGIN + ARROW_ZONE_WIDTH
    top = GRID_MARGIN
    width = max(0, area_width - 2 * GRID_MARGIN - 2 * ARROW_ZONE_WIDTH)
    height = max(0, area_height - 2 * GRID_MARGIN - PAGE_INDICATOR_HEIGHT)
    return left, top, width, height


def compute_page_grid(area_width, area_height):
    """Pick a fixed (cols, rows, tile_size) for one page of tiles.

    Derived from the screen area, not from how many plugins are installed —
    that's what keeps tile size constant across every page (build_button_grid
    below) instead of shrinking further every time a plugin is added, like
    the old single-screen grid did. Starts at MAX_TILE_SIZE and only shrinks
    (down to button.ICON_SIZE — smaller and the icon itself stops reading
    clearly) if the screen is too small to fit even one MAX_TILE_SIZE tile
    per row/column, so this stays sane across different 11e panel
    resolutions rather than assuming one fixed number of tiles per page.
    """
    _, _, grid_width, grid_height = _grid_area(area_width, area_height)

    tile_size = MAX_TILE_SIZE
    while tile_size > ICON_SIZE:
        cols = int(grid_width // (tile_size + TILE_PADDING))
        rows = int(grid_height // (tile_size + TILE_PADDING))
        if cols >= 1 and rows >= 1:
            break
        tile_size -= 10

    cols = max(1, int(grid_width // (tile_size + TILE_PADDING)))
    rows = max(1, int(grid_height // (tile_size + TILE_PADDING)))
    return cols, rows, tile_size


def tiles_per_page(area_width, area_height):
    cols, rows, _ = compute_page_grid(area_width, area_height)
    return cols * rows


def page_count(num_plugins, area_width, area_height):
    per_page = tiles_per_page(area_width, area_height)
    if num_plugins == 0:
        return 1
    return math.ceil(num_plugins / per_page)


def build_button_grid(plugins, area_width, area_height, page=0):
    """Lay out one page's worth of tiles (button.ICON_SIZE-floored,
    MAX_TILE_SIZE-capped, see compute_page_grid) in a fixed grid.

    Only plugins[page * tiles_per_page : (page + 1) * tiles_per_page] are
    positioned — pagination (launcher/pager.py) is what lets the rest be
    reached, not a shrink-to-fit grid.
    """
    cols, rows, tile_size = compute_page_grid(area_width, area_height)
    left, top, grid_width, grid_height = _grid_area(area_width, area_height)

    per_page = cols * rows
    page_plugins = plugins[page * per_page : (page + 1) * per_page]
    if not page_plugins:
        return []

    cell_width = grid_width / cols
    cell_height = grid_height / rows

    layout = []
    for index, plugin in enumerate(page_plugins):
        row, col = divmod(index, cols)
        cell_x = left + col * cell_width
        cell_y = top + row * cell_height
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


def fetch_background_image(server_url, timeout=HTTP_TIMEOUT_SECONDS):
    url = f"{server_url}/background/image"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


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


def _apply_background(response, last_state, background_queue, server_url):
    """Detect a background colour/image change and, if there is one, hand it
    to main.py via background_queue (same maxsize=1 handoff convention as
    update_queue — pygame surface loading is main-thread-only, so this just
    downloads the raw bytes and lets main.py turn them into a Surface).

    background_queue is None in callers/tests that don't care about
    background at all (kept optional so existing run_poller callers don't
    need to change). `response` may be a live server reply or a cached one
    (see the exception branch below) — either shape has the same
    "background" key since save_cached_config stores the raw response.
    """
    if background_queue is None:
        return last_state
    bg = response.get("background") or {}
    color = bg.get("color") or DEFAULT_BACKGROUND_COLOR
    image_version = bg.get("image_version")
    state = (color, image_version)
    if state == last_state:
        return last_state

    if image_version:
        try:
            image_bytes = fetch_background_image(server_url)
        except Exception as e:
            print(f"warning: background image download failed: {e}", file=sys.stderr)
            return last_state  # retry next poll rather than applying a half-updated state
        BACKGROUND_IMAGE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = BACKGROUND_IMAGE_CACHE.with_suffix(".tmp")
        tmp_path.write_bytes(image_bytes)
        tmp_path.replace(BACKGROUND_IMAGE_CACHE)  # atomic swap, no half-written file ever visible
    elif BACKGROUND_IMAGE_CACHE.exists():
        BACKGROUND_IMAGE_CACHE.unlink()

    try:
        background_queue.get_nowait()
    except queue.Empty:
        pass
    background_queue.put_nowait({"color": color, "has_image": bool(image_version)})
    return state


def run_poller(
    update_queue,
    stop_event,
    background_queue=None,
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
    last_background_state = None

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
                last_background_state = _apply_background(
                    response, last_background_state, background_queue, server_url
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
                    last_background_state = _apply_background(
                        cached, last_background_state, background_queue, server_url
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
