import pytest

from server import models
from server.app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(data_dir=tmp_path)


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_plugin(app, plugin_id="tuxpaint", zip_bytes=b"fake zip contents"):
    with app.app_context():
        (models.plugins_dir() / f"{plugin_id}.zip").write_bytes(zip_bytes)
        models.upsert_plugin(
            plugin_id,
            name="TuxPaint",
            version="1.0.0",
            type="app",
            description="Drawing",
            zip_filename=f"{plugin_id}.zip",
        )


def test_catalogue_lists_plugins(app, client):
    _seed_plugin(app)
    resp = client.get("/plugins/")
    assert resp.status_code == 200
    assert resp.get_json() == [
        {
            "id": "tuxpaint",
            "name": "TuxPaint",
            "version": "1.0.0",
            "type": "app",
            "description": "Drawing",
        }
    ]


def test_catalogue_empty_when_no_plugins(client):
    assert client.get("/plugins/").get_json() == []


def test_download_returns_zip_bytes(app, client):
    _seed_plugin(app, zip_bytes=b"real zip data")
    resp = client.get("/plugins/tuxpaint/download")
    assert resp.status_code == 200
    assert resp.data == b"real zip data"


def test_download_unknown_plugin_404s(client):
    resp = client.get("/plugins/nonexistent/download")
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "malicious_id", ["..", "UPPERCASE", "-leading-hyphen", "has space", "a.b"]
)
def test_download_rejects_ids_failing_pattern(app, client, malicious_id):
    # Even if a plugin row somehow existed under this id, the pattern check
    # must reject it before any filesystem access — same rule as
    # plugin-install.sh and plugin_manager.py's ID_PATTERN.
    resp = client.get(f"/plugins/{malicious_id}/download")
    assert resp.status_code == 404
