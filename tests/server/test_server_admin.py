import json
import re
import zipfile
from io import BytesIO

from server import models
from tests.server.conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME


def _extract_csrf(html: bytes) -> str:
    match = re.search(rb'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf_token field found in response"
    return match.group(1).decode()


def _login(client) -> str:
    """Log in and return a CSRF token good for this session."""
    resp = client.post(
        "/admin/login",
        data={"username": TEST_ADMIN_USERNAME, "password": TEST_ADMIN_PASSWORD},
    )
    assert resp.status_code == 302
    page = client.get("/admin/machines")
    return _extract_csrf(page.data)


def _plugin_zip(manifest: dict, *, manifest_prefix="plugin/", extra_entries=None) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{manifest_prefix}manifest.json", json.dumps(manifest))
        zf.writestr(f"{manifest_prefix}icon.png", b"not-a-real-png-but-a-file")
        for name, content in (extra_entries or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


WEBSITE_MANIFEST = {
    "id": "phonics-site",
    "name": "Phonics Game",
    "version": "1.0.0",
    "type": "website",
    "url": "https://example.com/phonics",
    "icon": "icon.png",
    "description": "Curated phonics practice website",
}


def test_admin_routes_require_login(client):
    resp = client.get("/admin/machines")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_login_wrong_password_rejected(client):
    resp = client.post("/admin/login", data={"username": TEST_ADMIN_USERNAME, "password": "nope"})
    assert resp.status_code == 401
    # still logged out
    assert client.get("/admin/machines").status_code == 302


def test_login_correct_credentials_grants_access(client):
    _login(client)
    assert client.get("/admin/machines").status_code == 200


def test_logout_clears_session(app, client):
    csrf = _login(client)
    client.post("/admin/logout", data={"csrf_token": csrf})
    assert client.get("/admin/machines").status_code == 302


def test_post_without_csrf_token_rejected(app, client):
    _login(client)
    with app.app_context():
        models.upsert_machine("11e-abc123")
    resp = client.post(
        "/admin/machines/11e-abc123/force-home", data={}
    )
    assert resp.status_code == 403
    with app.app_context():
        assert models.get_machine("11e-abc123").force_home is False


def test_set_display_name(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_machine("11e-abc123")
    client.post(
        "/admin/machines/11e-abc123/display-name",
        data={"csrf_token": csrf, "display_name": "blue-3"},
    )
    with app.app_context():
        assert models.get_machine("11e-abc123").display_name == "blue-3"


def test_force_home_button_sets_flag(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_machine("11e-abc123")
    resp = client.post(
        "/admin/machines/11e-abc123/force-home", data={"csrf_token": csrf}
    )
    assert resp.status_code == 302
    with app.app_context():
        assert models.get_machine("11e-abc123").force_home is True


def test_toggle_plugin(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="Drawing", zip_filename="tuxpaint.zip",
        )
    client.post("/admin/plugins/tuxpaint/toggle", data={"csrf_token": csrf})
    with app.app_context():
        plugin = models.get_plugin("tuxpaint")
    assert plugin.enabled is True
    assert plugin.position == 0

    client.post("/admin/plugins/tuxpaint/toggle", data={"csrf_token": csrf})
    with app.app_context():
        plugin = models.get_plugin("tuxpaint")
    assert plugin.enabled is False
    assert plugin.position is None


def test_move_plugin_up_and_down(app, client):
    csrf = _login(client)
    with app.app_context():
        for pid in ("tuxpaint", "tuxmath"):
            models.upsert_plugin(
                pid, name=pid, version="1.0.0", type="app",
                description="d", zip_filename=f"{pid}.zip",
            )
        models.set_profile(["tuxpaint", "tuxmath"])

    client.post("/admin/plugins/tuxmath/move", data={"csrf_token": csrf, "direction": "up"})
    with app.app_context():
        assert models.get_plugin("tuxmath").position == 0
        assert models.get_plugin("tuxpaint").position == 1


def test_delete_website_plugin_removes_it_from_catalogue_and_disk(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "phonics-site", name="Phonics Game", version="1.0.0", type="website",
            description="d", zip_filename="phonics-site.zip",
        )
        (models.plugins_dir() / "phonics-site.zip").write_bytes(b"zip-bytes")

    resp = client.post("/admin/plugins/phonics-site/delete", data={"csrf_token": csrf})
    assert resp.status_code == 302
    with app.app_context():
        assert models.get_plugin("phonics-site") is None
        assert not (models.plugins_dir() / "phonics-site.zip").exists()


def test_delete_website_plugin_renumbers_remaining_enabled_plugins(app, client):
    csrf = _login(client)
    with app.app_context():
        for pid, ptype in (("phonics-site", "website"), ("rhymes-site", "website")):
            models.upsert_plugin(
                pid, name=pid, version="1.0.0", type=ptype,
                description="d", zip_filename=f"{pid}.zip",
            )
        models.set_profile(["phonics-site", "rhymes-site"])

    client.post("/admin/plugins/phonics-site/delete", data={"csrf_token": csrf})
    with app.app_context():
        assert models.get_plugin("rhymes-site").position == 0


def test_delete_rejects_non_website_plugin_type(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="d", zip_filename="tuxpaint.zip",
        )
    client.post("/admin/plugins/tuxpaint/delete", data={"csrf_token": csrf})
    with app.app_context():
        assert models.get_plugin("tuxpaint") is not None


def test_delete_nonexistent_plugin_is_a_no_op(app, client):
    csrf = _login(client)
    resp = client.post("/admin/plugins/does-not-exist/delete", data={"csrf_token": csrf})
    assert resp.status_code == 302


def test_upload_valid_website_plugin(app, client):
    csrf = _login(client)
    zip_bytes = _plugin_zip(WEBSITE_MANIFEST)
    resp = client.post(
        "/admin/plugins/upload",
        data={
            "csrf_token": csrf,
            "plugin_zip": (BytesIO(zip_bytes), "phonics.zip"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        plugin = models.get_plugin("phonics-site")
        assert plugin is not None
        assert plugin.version == "1.0.0"
        assert plugin.enabled is False  # uploaded, not yet enabled in the profile
        zip_on_disk = models.plugins_dir() / "phonics-site.zip"
    assert zip_on_disk.read_bytes() == zip_bytes


def test_upload_rejects_path_traversal_entry(app, client):
    csrf = _login(client)
    zip_bytes = _plugin_zip(
        WEBSITE_MANIFEST, extra_entries={"../../etc/cron.d/evil": "bad"}
    )
    client.post(
        "/admin/plugins/upload",
        data={"csrf_token": csrf, "plugin_zip": (BytesIO(zip_bytes), "evil.zip")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        assert models.get_plugin("phonics-site") is None


def test_upload_rejects_invalid_manifest(app, client):
    csrf = _login(client)
    bad_manifest = dict(WEBSITE_MANIFEST)
    del bad_manifest["description"]
    zip_bytes = _plugin_zip(bad_manifest)
    client.post(
        "/admin/plugins/upload",
        data={"csrf_token": csrf, "plugin_zip": (BytesIO(zip_bytes), "bad.zip")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        assert models.get_plugin("phonics-site") is None


def test_upload_rejects_non_zip_file(app, client):
    csrf = _login(client)
    resp = client.post(
        "/admin/plugins/upload",
        data={"csrf_token": csrf, "plugin_zip": (BytesIO(b"not a zip"), "junk.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302


def test_create_website_plugin_builds_a_valid_zip(app, client):
    csrf = _login(client)
    resp = client.post(
        "/admin/plugins/create-website",
        data={
            "csrf_token": csrf,
            "name": "Cool Math Games",
            "url": "https://www.coolmathgames.com/",
            "icon": (BytesIO(b"fake-icon-bytes"), "icon.png"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        plugin = models.get_plugin("cool-math-games")
        assert plugin is not None
        assert plugin.type == "website"
        assert plugin.enabled is False  # no enable_now checkbox ticked
        zip_path = models.plugins_dir() / "cool-math-games.zip"
    with zipfile.ZipFile(zip_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["url"] == "https://www.coolmathgames.com/"
        assert zf.read("icon.png") == b"fake-icon-bytes"


def test_create_website_plugin_enable_now_adds_it_to_the_profile(app, client):
    csrf = _login(client)
    client.post(
        "/admin/plugins/create-website",
        data={
            "csrf_token": csrf,
            "name": "Cool Math Games",
            "url": "https://www.coolmathgames.com/",
            "icon": (BytesIO(b"fake-icon-bytes"), "icon.png"),
            "enable_now": "on",
        },
        content_type="multipart/form-data",
    )
    with app.app_context():
        plugin = models.get_plugin("cool-math-games")
    assert plugin.enabled is True
    assert plugin.position == 0


def test_create_website_plugin_dedupes_id_on_name_collision(app, client):
    csrf = _login(client)
    for _ in range(2):
        client.post(
            "/admin/plugins/create-website",
            data={
                "csrf_token": csrf,
                "name": "Cool Math Games",
                "url": "https://www.coolmathgames.com/",
                "icon": (BytesIO(b"fake-icon-bytes"), "icon.png"),
            },
            content_type="multipart/form-data",
        )
    with app.app_context():
        ids = {p.id for p in models.list_plugins()}
    assert ids == {"cool-math-games", "cool-math-games-2"}


def test_create_website_plugin_rejects_bad_url(app, client):
    csrf = _login(client)
    client.post(
        "/admin/plugins/create-website",
        data={
            "csrf_token": csrf,
            "name": "Sketchy",
            "url": "javascript:alert(1)",
            "icon": (BytesIO(b"fake-icon-bytes"), "icon.png"),
        },
        content_type="multipart/form-data",
    )
    with app.app_context():
        assert models.list_plugins() == []


def test_create_website_plugin_requires_icon(app, client):
    csrf = _login(client)
    client.post(
        "/admin/plugins/create-website",
        data={"csrf_token": csrf, "name": "Cool Math Games", "url": "https://example.com/"},
        content_type="multipart/form-data",
    )
    with app.app_context():
        assert models.list_plugins() == []


def test_set_background_color(app, client):
    csrf = _login(client)
    client.post("/admin/background/color", data={"csrf_token": csrf, "theme": "Mint"})
    with app.app_context():
        assert models.get_settings()["background_color"] == models.BACKGROUND_THEMES["Mint"]


def test_set_background_color_rejects_unknown_theme(app, client):
    csrf = _login(client)
    client.post("/admin/background/color", data={"csrf_token": csrf, "theme": "not-a-theme"})
    with app.app_context():
        assert models.get_settings()["background_color"] == "#DCEEF7"  # unchanged default


def test_upload_background_image_stores_file_and_updates_settings(app, client):
    csrf = _login(client)
    resp = client.post(
        "/admin/background/image",
        data={"csrf_token": csrf, "background_image": (BytesIO(b"fake-bg-bytes"), "bg.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        filename = models.get_settings()["background_image_filename"]
        assert filename is not None
        assert (models.background_dir() / filename).read_bytes() == b"fake-bg-bytes"


def test_upload_background_image_replaces_and_removes_old_file(app, client):
    csrf = _login(client)
    client.post(
        "/admin/background/image",
        data={"csrf_token": csrf, "background_image": (BytesIO(b"first"), "bg1.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        first_filename = models.get_settings()["background_image_filename"]
        first_path = models.background_dir() / first_filename

    client.post(
        "/admin/background/image",
        data={"csrf_token": csrf, "background_image": (BytesIO(b"second"), "bg2.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        second_filename = models.get_settings()["background_image_filename"]
    assert second_filename != first_filename
    assert not first_path.exists()


def test_clear_background_image_falls_back_to_no_image(app, client):
    csrf = _login(client)
    client.post(
        "/admin/background/image",
        data={"csrf_token": csrf, "background_image": (BytesIO(b"first"), "bg1.png")},
        content_type="multipart/form-data",
    )
    with app.app_context():
        filename = models.get_settings()["background_image_filename"]
        path = models.background_dir() / filename

    client.post("/admin/background/image/clear", data={"csrf_token": csrf})

    with app.app_context():
        assert models.get_settings()["background_image_filename"] is None
    assert not path.exists()


def test_set_lock_to_app_requires_enabled_plugin(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="d", zip_filename="tuxpaint.zip",
        )
        models.set_profile(["tuxpaint"])

    resp = client.post("/admin/lock-to-app", data={"csrf_token": csrf, "plugin_id": "tuxpaint"})
    assert resp.status_code == 302
    with app.app_context():
        assert models.get_settings()["lock_to_app"] == "tuxpaint"


def test_set_lock_to_app_with_duration_stores_an_expiry(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="d", zip_filename="tuxpaint.zip",
        )
        models.set_profile(["tuxpaint"])

    client.post(
        "/admin/lock-to-app",
        data={"csrf_token": csrf, "plugin_id": "tuxpaint", "duration": "2h"},
    )
    with app.app_context():
        assert models.get_settings()["lock_to_app_expires_at"] is not None


def test_set_lock_to_app_omitting_duration_defaults_to_indefinite(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="d", zip_filename="tuxpaint.zip",
        )
        models.set_profile(["tuxpaint"])

    client.post("/admin/lock-to-app", data={"csrf_token": csrf, "plugin_id": "tuxpaint"})
    with app.app_context():
        assert models.get_settings()["lock_to_app_expires_at"] is None


def test_set_lock_to_app_rejects_unknown_duration(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="d", zip_filename="tuxpaint.zip",
        )
        models.set_profile(["tuxpaint"])

    client.post(
        "/admin/lock-to-app",
        data={"csrf_token": csrf, "plugin_id": "tuxpaint", "duration": "99h"},
    )
    with app.app_context():
        assert models.get_settings()["lock_to_app"] is None  # rejected, never locked


def test_set_lock_to_app_rejects_disabled_plugin(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="d", zip_filename="tuxpaint.zip",
        )
    client.post("/admin/lock-to-app", data={"csrf_token": csrf, "plugin_id": "tuxpaint"})
    with app.app_context():
        assert models.get_settings()["lock_to_app"] is None


def test_set_lock_to_app_rejects_nonexistent_plugin(app, client):
    csrf = _login(client)
    client.post("/admin/lock-to-app", data={"csrf_token": csrf, "plugin_id": "does-not-exist"})
    with app.app_context():
        assert models.get_settings()["lock_to_app"] is None


def test_clear_lock_to_app(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="d", zip_filename="tuxpaint.zip",
        )
        models.set_profile(["tuxpaint"])
    client.post("/admin/lock-to-app", data={"csrf_token": csrf, "plugin_id": "tuxpaint"})

    client.post("/admin/lock-to-app/clear", data={"csrf_token": csrf})
    with app.app_context():
        assert models.get_settings()["lock_to_app"] is None


def test_deleting_locked_website_plugin_also_clears_lock(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "phonics-site", name="Phonics Game", version="1.0.0", type="website",
            description="d", zip_filename="phonics-site.zip",
        )
        models.set_profile(["phonics-site"])
    client.post("/admin/lock-to-app", data={"csrf_token": csrf, "plugin_id": "phonics-site"})

    client.post("/admin/plugins/phonics-site/delete", data={"csrf_token": csrf})
    with app.app_context():
        assert models.get_settings()["lock_to_app"] is None


def test_machines_page_shows_no_lock_banner_when_unlocked(app, client):
    _login(client)
    page = client.get("/admin/machines")
    assert b"lock-banner" not in page.data


def test_machines_page_shows_lock_banner_with_plugin_name(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="d", zip_filename="tuxpaint.zip",
        )
        models.set_profile(["tuxpaint"])
    client.post("/admin/lock-to-app", data={"csrf_token": csrf, "plugin_id": "tuxpaint"})

    page = client.get("/admin/machines")
    assert b"lock-banner" in page.data
    assert b"TuxPaint" in page.data


def test_machines_page_lock_banner_handles_deleted_locked_plugin(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "phonics-site", name="Phonics Game", version="1.0.0", type="website",
            description="d", zip_filename="phonics-site.zip",
        )
        models.set_profile(["phonics-site"])
        # Bypass the route's own guard (which clears the lock on delete) to
        # exercise the banner's fallback for an id that no longer resolves
        # to a real plugin — same defensive case the Config-page section
        # already handles.
        models.set_lock_to_app("phonics-site")
        models.delete_plugin("phonics-site")

    page = client.get("/admin/machines")
    assert b"lock-banner" in page.data
    assert b"phonics-site" in page.data
    assert b"no longer exists" in page.data


def test_unlock_from_machines_page_redirects_back_to_machines(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="d", zip_filename="tuxpaint.zip",
        )
        models.set_profile(["tuxpaint"])
    client.post("/admin/lock-to-app", data={"csrf_token": csrf, "plugin_id": "tuxpaint"})

    resp = client.post(
        "/admin/lock-to-app/clear",
        data={"csrf_token": csrf},
        headers={"Referer": "http://localhost/admin/machines"},
    )
    assert resp.headers["Location"].endswith("/admin/machines")


def test_unlock_from_config_page_redirects_back_to_config(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="d", zip_filename="tuxpaint.zip",
        )
        models.set_profile(["tuxpaint"])
    client.post("/admin/lock-to-app", data={"csrf_token": csrf, "plugin_id": "tuxpaint"})

    resp = client.post(
        "/admin/lock-to-app/clear",
        data={"csrf_token": csrf},
        headers={"Referer": "http://localhost/admin/plugins"},
    )
    assert resp.headers["Location"].endswith("/admin/plugins")


def test_disabling_locked_plugin_also_clears_lock(app, client):
    csrf = _login(client)
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint", name="TuxPaint", version="1.0.0", type="app",
            description="d", zip_filename="tuxpaint.zip",
        )
        models.set_profile(["tuxpaint"])
    client.post("/admin/lock-to-app", data={"csrf_token": csrf, "plugin_id": "tuxpaint"})

    client.post("/admin/plugins/tuxpaint/toggle", data={"csrf_token": csrf})
    with app.app_context():
        assert models.get_settings()["lock_to_app"] is None
        assert models.get_plugin("tuxpaint").enabled is False
