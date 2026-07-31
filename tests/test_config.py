import http.server
import itertools
import json
import queue
import threading

import pytest

from launcher import button, config, process_manager
from launcher.config import build_button_grid, compute_grid_dimensions


class DummyPlugin:
    def __init__(self, i):
        self.id = f"plugin-{i}"
        self.name = f"Plugin {i}"


AREA_WIDTH = 1366
AREA_HEIGHT = 713


@pytest.mark.parametrize("count", range(1, 13))
def test_grid_has_no_overlapping_tiles(count):
    plugins = [DummyPlugin(i) for i in range(count)]

    layout = build_button_grid(plugins, AREA_WIDTH, AREA_HEIGHT)

    assert len(layout) == count

    def as_bounds(rect):
        x, y, w, h = rect
        return x, y, x + w, y + h

    for (plugin_a, rect_a), (plugin_b, rect_b) in itertools.combinations(layout, 2):
        ax1, ay1, ax2, ay2 = as_bounds(rect_a)
        bx1, by1, bx2, by2 = as_bounds(rect_b)
        overlaps = ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2
        assert not overlaps, f"{plugin_a.id} and {plugin_b.id} tiles overlap"


@pytest.mark.parametrize("count", range(1, 13))
def test_all_tiles_within_area(count):
    plugins = [DummyPlugin(i) for i in range(count)]

    layout = build_button_grid(plugins, AREA_WIDTH, AREA_HEIGHT)

    for _, (x, y, w, h) in layout:
        assert x >= 0
        assert y >= 0
        assert x + w <= AREA_WIDTH
        assert y + h <= AREA_HEIGHT


def test_rendered_icon_meets_minimum_size():
    assert button.ICON_SIZE >= 96


@pytest.mark.parametrize("count", range(1, 13))
def test_tiles_fit_the_rendered_icon(count):
    plugins = [DummyPlugin(i) for i in range(count)]

    layout = build_button_grid(plugins, AREA_WIDTH, AREA_HEIGHT)

    for _, (_, _, w, h) in layout:
        assert w >= button.ICON_SIZE
        assert h >= button.ICON_SIZE


def test_build_button_grid_empty_plugin_list():
    assert build_button_grid([], AREA_WIDTH, AREA_HEIGHT) == []


def test_compute_grid_dimensions_covers_all_items():
    for count in range(1, 13):
        cols, rows = compute_grid_dimensions(count, AREA_WIDTH, AREA_HEIGHT)
        assert cols * rows >= count


# --- Phase 9: server polling ------------------------------------------------


def test_reconcile_plugins_orders_by_server_list():
    local = [DummyPlugin(2), DummyPlugin(0), DummyPlugin(1)]
    server_plugins = [{"id": "plugin-0"}, {"id": "plugin-1"}, {"id": "plugin-2"}]

    reconciled = config.reconcile_plugins(server_plugins, local)

    assert [p.id for p in reconciled] == ["plugin-0", "plugin-1", "plugin-2"]


def test_reconcile_plugins_skips_ids_not_installed_locally():
    local = [DummyPlugin(0)]
    server_plugins = [{"id": "plugin-0"}, {"id": "plugin-99"}]

    reconciled = config.reconcile_plugins(server_plugins, local)

    assert [p.id for p in reconciled] == ["plugin-0"]


def test_reconcile_plugins_empty_server_list_returns_empty():
    assert config.reconcile_plugins([], [DummyPlugin(0)]) == []


def test_maybe_enqueue_skips_empty_list_to_avoid_blanking_grid():
    q = queue.Queue(maxsize=1)

    result = config._maybe_enqueue(q, [], last_applied_ids=("plugin-0",))

    assert result == ("plugin-0",)
    assert q.empty()


def test_maybe_enqueue_skips_unchanged_list():
    q = queue.Queue(maxsize=1)
    plugins = [DummyPlugin(0), DummyPlugin(1)]
    last_ids = ("plugin-0", "plugin-1")

    result = config._maybe_enqueue(q, plugins, last_ids)

    assert result == last_ids
    assert q.empty()


def test_maybe_enqueue_pushes_changed_list_and_returns_new_ids():
    q = queue.Queue(maxsize=1)
    plugins = [DummyPlugin(0), DummyPlugin(1)]

    result = config._maybe_enqueue(q, plugins, last_applied_ids=("plugin-9",))

    assert result == ("plugin-0", "plugin-1")
    assert q.get_nowait() is plugins


def test_maybe_enqueue_replaces_a_stale_unconsumed_update():
    q = queue.Queue(maxsize=1)
    q.put_nowait("stale")
    plugins = [DummyPlugin(0)]

    config._maybe_enqueue(q, plugins, last_applied_ids=None)

    assert q.get_nowait() is plugins
    assert q.empty()


def test_get_server_url_env_var_takes_priority(monkeypatch, tmp_path):
    server_url_file = tmp_path / "server_url"
    server_url_file.write_text("http://file-configured:5000")
    monkeypatch.setattr(config, "SERVER_URL_FILE", server_url_file)
    monkeypatch.setenv(config.SERVER_URL_ENV_VAR, "http://env-configured:5000/")

    assert config.get_server_url() == "http://env-configured:5000"


def test_get_server_url_falls_back_to_file(monkeypatch, tmp_path):
    monkeypatch.delenv(config.SERVER_URL_ENV_VAR, raising=False)
    server_url_file = tmp_path / "server_url"
    server_url_file.write_text("http://file-configured:5000/\n")
    monkeypatch.setattr(config, "SERVER_URL_FILE", server_url_file)

    assert config.get_server_url() == "http://file-configured:5000"


def test_get_server_url_none_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.delenv(config.SERVER_URL_ENV_VAR, raising=False)
    monkeypatch.setattr(config, "SERVER_URL_FILE", tmp_path / "does-not-exist")

    assert config.get_server_url() is None


def test_save_and_load_cached_config_round_trip(tmp_path):
    cache_path = tmp_path / "config_cache.json"
    data = {"machine_id": "11e-TEST", "plugins": [{"id": "tuxpaint", "version": "1.0.0"}], "force_home": False}

    config.save_cached_config(data, cache_path)

    assert config.load_cached_config(cache_path) == data


def test_load_cached_config_missing_file_returns_none(tmp_path):
    assert config.load_cached_config(tmp_path / "missing.json") is None


def test_load_cached_config_corrupt_json_returns_none(tmp_path):
    cache_path = tmp_path / "config_cache.json"
    cache_path.write_text("not json{{{")

    assert config.load_cached_config(cache_path) is None


def test_save_cached_config_uses_module_default_path_when_monkeypatched(monkeypatch, tmp_path):
    # Guards against the def-time-default-binding gotcha: run_poller calls
    # save_cached_config()/load_cached_config() with no explicit path, relying
    # on them reading the module-level CONFIG_CACHE_FILE fresh at call time.
    fake_path = tmp_path / "config_cache.json"
    monkeypatch.setattr(config, "CONFIG_CACHE_FILE", fake_path)

    config.save_cached_config({"ok": True})

    assert fake_path.exists()
    assert config.load_cached_config() == {"ok": True}


class _StubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # keep test output quiet

    def _send_json(self, body):
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path.startswith("/config/"):
            self._send_json(self.server.config_response)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        self.server.telemetry_calls.append(body)
        self._send_json({"ok": True})


@pytest.fixture
def stub_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _StubHandler)
    server.config_response = {"machine_id": "x", "plugins": [], "force_home": False}
    server.telemetry_calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join()


@pytest.fixture
def stub_server_url(stub_server):
    return f"http://127.0.0.1:{stub_server.server_port}"


def test_fetch_config_returns_parsed_json(stub_server, stub_server_url):
    stub_server.config_response = {
        "machine_id": "11e-TEST",
        "plugins": [{"id": "tuxpaint", "version": "1.0.0"}],
        "force_home": False,
    }

    assert config.fetch_config(stub_server_url, "11e-TEST") == stub_server.config_response


def test_post_telemetry_sends_expected_body_and_returns_response(stub_server, stub_server_url):
    result = config.post_telemetry(
        stub_server_url, "11e-TEST", current_activity="TuxPaint", error=None, force_home_ack=False
    )

    assert result == {"ok": True}
    assert stub_server.telemetry_calls == [{"current_activity": "TuxPaint", "error": None, "force_home_ack": False}]


def test_fetch_config_raises_on_connection_failure():
    with pytest.raises(Exception):
        config.fetch_config("http://127.0.0.1:1", "11e-TEST", timeout=1)


class _PollerHarness:
    """Runs run_poller on a real thread against a real local stub server, with
    fast tick/poll/telemetry intervals so gate tests finish in well under a
    second rather than needing the real 30s/300s production intervals.
    """

    def __init__(self, monkeypatch, tmp_path, stub_server, stub_server_url):
        self.stub_server = stub_server
        monkeypatch.setattr(config, "SERVER_URL_FILE", tmp_path / "server_url")
        monkeypatch.setenv(config.SERVER_URL_ENV_VAR, stub_server_url)
        monkeypatch.setattr(config, "CONFIG_CACHE_FILE", tmp_path / "config_cache.json")
        monkeypatch.setattr(process_manager, "ACTIVITY_FILE", str(tmp_path / "classpad_activity"))
        self.kill_all_calls = []
        monkeypatch.setattr(process_manager, "kill_all", lambda: self.kill_all_calls.append(True))
        self.update_queue = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()

    def run_briefly(self, local_plugins, monkeypatch, seconds=0.3):
        monkeypatch.setattr(config, "scan_plugins", lambda: local_plugins)
        thread = threading.Thread(
            target=config.run_poller,
            args=(self.update_queue, self.stop_event),
            kwargs=dict(poll_interval=0.05, telemetry_interval=0.05, tick=0.02),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=seconds)
        self.stop_event.set()
        thread.join(timeout=1)


@pytest.fixture
def poller_harness(monkeypatch, tmp_path, stub_server, stub_server_url):
    return _PollerHarness(monkeypatch, tmp_path, stub_server, stub_server_url)


def test_run_poller_applies_reconciled_config_to_queue(monkeypatch, poller_harness, stub_server):
    stub_server.config_response = {
        "machine_id": "11e-TEST",
        "plugins": [{"id": "plugin-0", "version": "1.0.0"}],
        "force_home": False,
    }
    local_plugins = [DummyPlugin(0)]

    poller_harness.run_briefly(local_plugins, monkeypatch)

    applied = poller_harness.update_queue.get_nowait()
    assert [p.id for p in applied] == ["plugin-0"]


def test_run_poller_caches_the_raw_server_response(monkeypatch, poller_harness, stub_server):
    stub_server.config_response = {
        "machine_id": "11e-TEST",
        "plugins": [{"id": "plugin-0", "version": "9.9.9"}],
        "force_home": False,
    }

    poller_harness.run_briefly([DummyPlugin(0)], monkeypatch)

    cached = config.load_cached_config()
    assert cached == stub_server.config_response


def test_run_poller_force_home_kills_and_acks(monkeypatch, poller_harness, stub_server):
    stub_server.config_response = {"machine_id": "11e-TEST", "plugins": [], "force_home": True}

    poller_harness.run_briefly([], monkeypatch)

    assert poller_harness.kill_all_calls
    acks = [call for call in stub_server.telemetry_calls if call.get("force_home_ack")]
    assert acks


def test_run_poller_falls_back_to_cache_when_server_unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SERVER_URL_FILE", tmp_path / "server_url")
    monkeypatch.setenv(config.SERVER_URL_ENV_VAR, "http://127.0.0.1:1")
    cache_path = tmp_path / "config_cache.json"
    monkeypatch.setattr(config, "CONFIG_CACHE_FILE", cache_path)
    monkeypatch.setattr(process_manager, "ACTIVITY_FILE", str(tmp_path / "classpad_activity"))
    config.save_cached_config({"machine_id": "11e-TEST", "plugins": [{"id": "plugin-0"}], "force_home": False})
    monkeypatch.setattr(config, "scan_plugins", lambda: [DummyPlugin(0)])

    update_queue = queue.Queue(maxsize=1)
    stop_event = threading.Event()
    thread = threading.Thread(
        target=config.run_poller,
        args=(update_queue, stop_event),
        kwargs=dict(poll_interval=0.05, telemetry_interval=0.05, tick=0.02),
        daemon=True,
    )
    thread.start()
    thread.join(timeout=0.3)
    stop_event.set()
    thread.join(timeout=1)

    applied = update_queue.get_nowait()
    assert [p.id for p in applied] == ["plugin-0"]


def test_run_poller_never_blanks_grid_on_empty_reconciled_result(monkeypatch, poller_harness, stub_server):
    stub_server.config_response = {
        "machine_id": "11e-TEST",
        "plugins": [{"id": "not-installed-locally"}],
        "force_home": False,
    }

    poller_harness.run_briefly([DummyPlugin(0)], monkeypatch)

    assert poller_harness.update_queue.empty()
