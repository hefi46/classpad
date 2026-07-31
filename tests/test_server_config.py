from server import models

def test_unknown_machine_gets_empty_config(client):
    resp = client.get("/config/11e-brandnew")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "machine_id": "11e-brandnew",
        "plugins": [],
        "force_home": False,
    }


def test_config_returns_profile_plugins_in_order(app, client):
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint",
            name="TuxPaint",
            version="1.0.0",
            type="app",
            description="Drawing",
            zip_filename="tuxpaint.zip",
        )
        models.upsert_plugin(
            "tuxmath",
            name="TuxMath",
            version="2.0.0",
            type="app",
            description="Maths",
            zip_filename="tuxmath.zip",
        )
        models.set_profile(["tuxmath", "tuxpaint"])

    data = client.get("/config/11e-abc123").get_json()
    assert data["plugins"] == [
        {"id": "tuxmath", "version": "2.0.0"},
        {"id": "tuxpaint", "version": "1.0.0"},
    ]
    assert data["force_home"] is False


def test_config_profile_is_shared_across_machines(app, client):
    with app.app_context():
        models.upsert_plugin(
            "tuxpaint",
            name="TuxPaint",
            version="1.0.0",
            type="app",
            description="Drawing",
            zip_filename="tuxpaint.zip",
        )
        models.set_profile(["tuxpaint"])

    data_a = client.get("/config/11e-aaa111").get_json()
    data_b = client.get("/config/11e-bbb222").get_json()
    assert data_a["plugins"] == data_b["plugins"] == [
        {"id": "tuxpaint", "version": "1.0.0"}
    ]


def test_display_name_does_not_affect_config(app, client):
    with app.app_context():
        models.set_display_name("11e-abc123", "blue-3")

    data = client.get("/config/11e-abc123").get_json()
    assert "display_name" not in data

    with app.app_context():
        machine = models.get_machine("11e-abc123")
    assert machine.display_name == "blue-3"


def test_force_home_flag_surfaces_in_config(app, client):
    with app.app_context():
        models.set_force_home("11e-abc123", True)

    assert client.get("/config/11e-abc123").get_json()["force_home"] is True


def test_telemetry_updates_activity(app, client):
    resp = client.post(
        "/telemetry/11e-abc123",
        json={"current_activity": "tuxpaint", "error": None},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}

    with app.app_context():
        machine = models.get_machine("11e-abc123")
    assert machine.current_activity == "tuxpaint"
    assert machine.last_seen is not None


def test_telemetry_force_home_ack_clears_flag(app, client):
    with app.app_context():
        models.set_force_home("11e-abc123", True)

    client.post("/telemetry/11e-abc123", json={"force_home_ack": True})

    with app.app_context():
        machine = models.get_machine("11e-abc123")
    assert machine.force_home is False


def test_telemetry_without_body_does_not_error(client):
    resp = client.post("/telemetry/11e-abc123")
    assert resp.status_code == 200
