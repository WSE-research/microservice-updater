"""Endpoint tests for the Flask service-management API in app.py.

Importing app.py creates `services/`, an SQLite DB and an API-key file relative
to the current working directory, so the fixture first chdir's into a temp dir
and seeds a known API key. We exercise only the validation / routing branches
that don't shell out to git or docker.
"""
import importlib
import json
import os
import sys

import pytest

API_KEY = "test-key"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.mkdir("services")
    # a placeholder file so the GET /service listing (which strips .gitkeep) works
    open(os.path.join("services", ".gitkeep"), "w").close()
    with open(os.path.join("services", "api-keys.json"), "w") as f:
        json.dump([API_KEY], f)

    # ensure a fresh import so module-level setup runs against this temp dir
    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


def test_check_volumes_validation():
    sys.modules.pop("app", None)
    import app as app_module
    from tasks.exceptions import InvalidVolumeMappingException

    # valid mapping passes silently
    app_module.check_volumes(["data:/data"])
    with pytest.raises(InvalidVolumeMappingException):
        app_module.check_volumes("not-a-list")
    with pytest.raises(InvalidVolumeMappingException):
        app_module.check_volumes(["missing-colon"])


def test_list_services_empty(client):
    resp = client.get("/service")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_register_requires_json_content_type(client):
    resp = client.post("/service", data="x", content_type="text/plain")
    assert resp.status_code == 400
    assert b"Json required" in resp.data


def test_register_rejects_invalid_api_key(client):
    resp = client.post("/service", json={"API-KEY": "wrong", "mode": "docker"})
    assert resp.status_code == 400
    assert b"valid API-KEY required" in resp.data


def test_register_missing_mode_is_bad_request(client):
    resp = client.post("/service", json={"API-KEY": API_KEY})
    assert resp.status_code == 400
    assert b"Missing argument" in resp.data


def test_register_unsupported_mode(client):
    resp = client.post("/service", json={"API-KEY": API_KEY, "mode": "bogus"})
    assert resp.status_code == 400
    assert b"unsupported mode" in resp.data


def test_register_invalid_volume_mapping(client):
    resp = client.post(
        "/service",
        json={"API-KEY": API_KEY, "mode": "docker-compose", "volumes": ["bad-mapping"]},
    )
    assert resp.status_code == 400
    assert b"Volume" in resp.data or b"mapping" in resp.data


def test_get_unknown_service_returns_404(client):
    resp = client.get("/service/does-not-exist")
    assert resp.status_code == 404
    assert b"not found" in resp.data


def test_update_unknown_service_requires_json(client):
    resp = client.post("/service/whatever", data="x", content_type="text/plain")
    assert resp.status_code == 400
    assert b"JSON payload expected" in resp.data


def test_update_requires_api_key(client):
    resp = client.post("/service/whatever", json={"no": "key"})
    assert resp.status_code == 400
    assert b"valid API-KEY required" in resp.data


def test_service_trailing_slash_redirects(client):
    resp = client.get("/service/", follow_redirects=False)
    assert resp.status_code == 307


@pytest.fixture
def registered(tmp_path, monkeypatch):
    """A booted app with one service row + workspace, plus the app module so
    tests can stub out subprocess/docker side effects."""
    import sqlite3

    monkeypatch.chdir(tmp_path)
    os.mkdir("services")
    open(os.path.join("services", ".gitkeep"), "w").close()
    with open(os.path.join("services", "api-keys.json"), "w") as f:
        json.dump([API_KEY], f)

    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")
    app_module.app.config.update(TESTING=True)

    # register a service directly in the DB
    os.makedirs(os.path.join("services", "svc1"), exist_ok=True)
    with open(os.path.join("services", "svc1", "error.txt"), "w") as f:
        f.write("no errors")
    with sqlite3.connect(os.path.join("services", "services.db")) as db:
        db.execute(
            "INSERT INTO repos VALUES ('svc1', 'http://x/y.git', 'docker',"
            " 'RUNNING', '8080:80', '.', 'img', 'tag')"
        )
        db.commit()

    return app_module, app_module.app.test_client()


def test_post_update_initiates_background_task(registered, monkeypatch):
    app_module, client = registered
    calls = []
    monkeypatch.setattr(app_module.subprocess, "Popen", lambda *a, **k: calls.append(a))
    resp = client.post("/service/svc1", json={"API-KEY": API_KEY, "volumes": ["data:/data", ""]})
    assert resp.status_code == 200
    assert b"Update initiated" in resp.data
    assert calls  # background update was spawned


def test_post_update_rejects_invalid_volumes(registered, monkeypatch):
    app_module, client = registered
    monkeypatch.setattr(app_module.subprocess, "Popen", lambda *a, **k: None)
    resp = client.post("/service/svc1", json={"API-KEY": API_KEY, "volumes": ["bad"]})
    assert resp.status_code == 400


def test_patch_updates_port_and_restarts(registered, monkeypatch):
    app_module, client = registered
    monkeypatch.setattr(app_module.subprocess, "Popen", lambda *a, **k: None)
    resp = client.patch("/service/svc1", json={"API-KEY": API_KEY, "port": "9090:90"})
    assert resp.status_code == 200
    assert b"patched and restarted" in resp.data


def test_delete_service(registered, monkeypatch):
    app_module, client = registered

    class _Done:
        returncode = 0

    monkeypatch.setattr(app_module.subprocess, "run", lambda *a, **k: _Done())
    resp = client.delete("/service/svc1", json={"API-KEY": API_KEY})
    assert resp.status_code == 200
    assert b"removed" in resp.data


def test_get_service_state_build_failed(registered, monkeypatch):
    app_module, client = registered

    class _FakeClient:
        class containers:
            @staticmethod
            def get(_id):
                from docker.errors import NotFound
                raise NotFound("missing")

    monkeypatch.setattr(app_module.docker, "from_env", lambda: _FakeClient())
    resp = client.get("/service/svc1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["state"] == "BUILD FAILED"
    assert data["id"] == "svc1"
