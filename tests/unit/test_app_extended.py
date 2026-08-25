"""Extended endpoint tests for app.py beyond the baseline suite (issue #139).

Covers the registration success paths (subprocess/docker/git stubbed), port
conflicts through the API, argument forwarding to the background tasks, PATCH
persistence, the DELETE failure path and the GET state of a running container.
"""
import importlib
import json
import os
import sqlite3
import sys
from unittest import mock

import pytest
from git import GitCommandError

API_KEY = "test-key"


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """A freshly imported app in a temp working dir: (app_module, client)."""
    monkeypatch.chdir(tmp_path)
    os.mkdir("services")
    open(os.path.join("services", ".gitkeep"), "w").close()
    with open(os.path.join("services", "api-keys.json"), "w") as f:
        json.dump([API_KEY], f)

    sys.modules.pop("app", None)
    app_module = importlib.import_module("app")
    app_module.app.config.update(TESTING=True)
    return app_module, app_module.app.test_client()


def register_service(service_id, url="", mode="dockerfile", state="RUNNING",
                     port="8080:80", docker_root=".", image="nginx",
                     tag="alpine", errors=""):
    """Insert a service row + workspace as the background tasks would."""
    os.makedirs(os.path.join("services", service_id), exist_ok=True)
    with open(os.path.join("services", service_id, "error.txt"), "w") as f:
        f.write(errors)
    with sqlite3.connect(os.path.join("services", "services.db")) as db:
        db.execute("INSERT INTO repos VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (service_id, url, mode, state, port, docker_root, image, tag))
        db.commit()


def service_row(service_id):
    with sqlite3.connect(os.path.join("services", "services.db")) as db:
        return db.execute("SELECT * FROM repos WHERE id = ?",
                          (service_id,)).fetchone()


def post_service(client, **payload):
    payload.setdefault("API-KEY", API_KEY)
    return client.post("/service", json=payload)


def test_list_services_is_sorted(app_env):
    _, client = app_env
    os.mkdir(os.path.join("services", "zeta"))
    os.mkdir(os.path.join("services", "alpha"))

    resp = client.get("/service")

    assert resp.status_code == 200
    assert resp.get_json() == ["alpha", "zeta"]


def test_register_dockerfile_service(app_env):
    app_module, client = app_env
    with mock.patch.object(app_module.subprocess, "Popen") as popen:
        resp = post_service(client, mode="dockerfile", image="nginx",
                            tag="alpine", port="8080:80")

    assert resp.status_code == 200
    assert resp.get_json() == {"id": "nginx", "state": "CREATED"}
    assert os.path.isdir(os.path.join("services", "nginx"))
    assert service_row("nginx") == ("nginx", "", "dockerfile", "INITIALIZING",
                                    "8080:80", ".", "nginx", "alpine")
    popen.assert_called_once_with(
        ["python", "tasks/start_service.py", "nginx", "dockerfile", ".",
         "8080:80", "nginx", "alpine", "[]"])


def test_register_duplicate_service_rejected(app_env):
    app_module, client = app_env
    with mock.patch.object(app_module.subprocess, "Popen"):
        first = post_service(client, mode="dockerfile", image="nginx",
                             tag="alpine", port="8080:8080")
        second = post_service(client, mode="dockerfile", image="nginx",
                              tag="alpine", port="9090:9090")

    assert first.status_code == 200
    assert second.status_code == 400
    assert b"Service already existing" in second.data


def test_register_rejects_used_port(app_env):
    _, client = app_env
    register_service("existing", port="8080:80")

    resp = post_service(client, mode="dockerfile", image="nginx",
                        tag="alpine", port="8080:80")

    assert resp.status_code == 400
    assert b"Port 8080 already used" in resp.data


def test_register_rejects_invalid_port_mapping(app_env):
    _, client = app_env
    resp = post_service(client, mode="dockerfile", image="nginx",
                        tag="alpine", port="not-a-port")

    assert resp.status_code == 400
    assert b"Invalid port mapping provided" in resp.data


def test_register_docker_mode_requires_port(app_env):
    _, client = app_env
    resp = post_service(client, mode="docker", url="https://example.org/a/b.git")

    assert resp.status_code == 400
    assert b"missing parameters" in resp.data


@pytest.mark.parametrize("payload", [
    {"port": "8080:80", "tag": "alpine"},               # image missing
    {"port": "8080:80", "image": "nginx"},              # tag missing
    {"image": "nginx", "tag": "alpine"},                # port missing
    {"port": "8080:80", "image": "", "tag": "alpine"},  # empty image
    {"port": "8080:80", "image": "nginx", "tag": ""},   # empty tag
])
def test_register_dockerfile_mode_requires_port_image_and_tag(app_env, payload):
    _, client = app_env
    resp = post_service(client, mode="dockerfile", **payload)

    assert resp.status_code == 400
    assert b"missing parameters" in resp.data


def test_register_rejects_non_list_volumes(app_env):
    _, client = app_env
    resp = post_service(client, mode="dockerfile", image="nginx", tag="alpine",
                        port="8080:80", volumes={"host": "container"})

    assert resp.status_code == 400
    assert b"Volume mapping list expected" in resp.data


def test_register_drops_empty_volume_entries(app_env):
    app_module, client = app_env
    with mock.patch.object(app_module.subprocess, "Popen") as popen:
        resp = post_service(client, mode="dockerfile", image="nginx",
                            tag="alpine", port="8080:80", volumes=[""])

    assert resp.status_code == 200
    assert popen.call_args.args[0][-1] == "[]"


def test_register_forwards_volumes_to_start_task(app_env):
    app_module, client = app_env
    with mock.patch.object(app_module.subprocess, "Popen") as popen:
        resp = post_service(client, mode="dockerfile", image="nginx",
                            tag="alpine", port="8080:80",
                            volumes=["/data:/var/data"])

    assert resp.status_code == 200
    assert popen.call_args.args[0][-1] == json.dumps(["/data:/var/data"])


def test_register_docker_mode_uses_cloned_repository(app_env):
    app_module, client = app_env
    with mock.patch.object(app_module.subprocess, "Popen"), \
            mock.patch.object(app_module, "load_repository",
                              return_value="example-org-repo") as load:
        resp = post_service(client, mode="docker", port="8080:80",
                            url="https://example.org/org/repo.git")

    assert resp.status_code == 200
    assert resp.get_json() == {"id": "example-org-repo", "state": "CREATED"}
    load.assert_called_once_with("https://example.org/org/repo.git", "docker",
                                 "8080:80", ".", "", "", {})


def test_register_reports_failed_clone(app_env):
    app_module, client = app_env
    error = GitCommandError(["git", "clone"], 128, b"fatal: repository not found")
    with mock.patch.object(app_module.subprocess, "Popen") as popen, \
            mock.patch.object(app_module, "load_repository", side_effect=error):
        resp = post_service(client, mode="docker-compose",
                            url="https://example.org/org/missing.git")

    assert resp.status_code == 400
    assert "repository not found" in resp.get_json()["error"]
    popen.assert_not_called()


def test_get_state_of_running_container(app_env):
    app_module, client = app_env
    register_service("svc", port="8080:80", image="nginx", tag="alpine")

    with mock.patch.object(app_module.docker, "from_env") as from_env:
        from_env.return_value.containers.get.return_value.status = "running"
        resp = client.get("/service/svc")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "id": "svc",
        "state": "RUNNING",
        "errors": "",
        "image": "nginx",
        "tag": "alpine",
        "port": "8080:80",
    }
    from_env.return_value.containers.get.assert_called_once_with("svc")


def test_update_forwards_files_and_volumes(app_env):
    app_module, client = app_env
    register_service("svc")
    files = {"config/app.conf": "DEBUG=1"}

    with mock.patch.object(app_module.subprocess, "Popen") as popen:
        resp = client.post("/service/svc",
                           json={"API-KEY": API_KEY, "files": files,
                                 "volumes": ["/data:/var/data", ""]})

    assert resp.status_code == 200
    args = popen.call_args.args[0]
    assert args[:3] == ["python", "tasks/update_service.py", "svc"]
    assert json.loads(args[3]) == files
    assert json.loads(args[4]) == ["/data:/var/data"]


def test_delete_failure_returns_500(app_env):
    app_module, client = app_env
    register_service("svc")

    with mock.patch.object(app_module.subprocess, "run",
                           return_value=mock.Mock(returncode=1)):
        resp = client.delete("/service/svc", json={"API-KEY": API_KEY})

    assert resp.status_code == 500
    assert b"deletion not completed" in resp.data


def test_patch_persists_tag_and_port(app_env):
    app_module, client = app_env
    register_service("svc", port="8080:80", tag="alpine")

    with mock.patch.object(app_module.subprocess, "Popen") as popen:
        resp = client.patch("/service/svc",
                            json={"API-KEY": API_KEY, "port": "9090:90",
                                  "tag": "nightly"})

    assert resp.status_code == 200
    row = service_row("svc")
    assert row[4] == "9090:90"
    assert row[7] == "nightly"
    popen.assert_called_once_with(
        ["python", "tasks/update_service.py", "svc", "{}", "[]"])


def test_patch_rejects_invalid_port_and_keeps_db(app_env):
    app_module, client = app_env
    register_service("svc", port="8080:80")

    with mock.patch.object(app_module.subprocess, "Popen") as popen:
        resp = client.patch("/service/svc",
                            json={"API-KEY": API_KEY, "port": "invalid"})

    assert resp.status_code == 400
    assert service_row("svc")[4] == "8080:80"
    popen.assert_not_called()


def test_patch_rejects_port_of_other_service(app_env):
    app_module, client = app_env
    register_service("svc", port="8080:80")
    register_service("other", port="9090:90")

    with mock.patch.object(app_module.subprocess, "Popen"):
        resp = client.patch("/service/svc",
                            json={"API-KEY": API_KEY, "port": "9090:90"})

    assert resp.status_code == 400
    assert b"Port 9090 already used" in resp.data
    assert service_row("svc")[4] == "8080:80"


def test_patch_with_empty_tag_keeps_current_tag(app_env):
    app_module, client = app_env
    register_service("svc", tag="alpine")

    with mock.patch.object(app_module.subprocess, "Popen"):
        resp = client.patch("/service/svc",
                            json={"API-KEY": API_KEY, "tag": ""})

    assert resp.status_code == 200
    assert service_row("svc")[7] == "alpine"
