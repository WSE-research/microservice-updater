"""Unit tests for tasks/rollout.py — the low-downtime update flow (issue #149).

All docker interactions are stubbed; every test runs in a temp directory
because the rollout writes error.txt into the service's docker_root.
"""
import itertools
import os
import sqlite3
import subprocess
import sys
from unittest import mock

import pytest
import requests
from docker.errors import APIError, BuildError, ImageNotFound, NotFound

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "tasks"))

import rollout  # noqa: E402


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def db_env(workspace):
    """Real in-memory-style DB so state transitions can be asserted."""
    db = sqlite3.connect("services.db")
    db.execute("CREATE TABLE repos(id TEXT PRIMARY KEY, url TEXT, mode TEXT,"
               " state TEXT, port TEXT, docker_root TEXT, image TEXT, tag TEXT,"
               " health_path TEXT DEFAULT '')")
    db.execute("INSERT INTO repos VALUES ('svc', '', 'docker', 'UPDATING',"
               " '8080:80', '.', '', '', '')")
    db.commit()
    yield db, db.cursor()
    db.close()


def service_state(db):
    return db.execute("SELECT state FROM repos WHERE id = 'svc'").fetchone()[0]


def read_error_file():
    with open("error.txt") as f:
        return f.read()


@pytest.fixture
def docker_client():
    client = mock.Mock()
    client.images.build.return_value = (mock.Mock(), iter([]))
    client.containers.get.return_value.image.id = "sha256:old-image"
    return client


@pytest.fixture
def instant_clock():
    """Make wait_until_ready iterate without real sleeping."""
    with mock.patch.object(rollout.time, "sleep"), \
            mock.patch.object(rollout.time, "monotonic",
                              side_effect=itertools.count(0.0, 1.0)):
        yield


def test_parse_ports():
    assert rollout.parse_ports("8080:80") == {"80": "8080"}
    assert rollout.parse_ports("8080:80,8443:443") == {"80": "8080",
                                                       "443": "8443"}


class TestWaitUntilReady:
    def test_running_without_health_path_is_ready(self, instant_clock):
        container = mock.Mock(status="running")

        assert rollout.wait_until_ready(container, "8080", "80", "") is True

    def test_exited_container_is_not_ready(self, instant_clock):
        container = mock.Mock(status="exited")

        assert rollout.wait_until_ready(container, "8080", "80", "") is False

    def test_health_path_polls_until_200(self, instant_clock):
        container = mock.Mock(status="running")
        container.attrs = {"NetworkSettings": {"IPAddress": "172.17.0.5"}}
        responses = [mock.Mock(status_code=503), mock.Mock(status_code=200)]

        with mock.patch.object(rollout.requests, "get",
                               side_effect=responses) as get:
            assert rollout.wait_until_ready(container, "8080", "80",
                                            "/health") is True

        assert get.call_args_list[0].args[0] == "http://172.17.0.5:80/health"

    def test_health_path_falls_back_to_mapped_port(self, instant_clock):
        container = mock.Mock(status="running")
        container.attrs = {"NetworkSettings": {"IPAddress": "172.17.0.5"}}
        responses = [requests.ConnectionError(), mock.Mock(status_code=200)]

        with mock.patch.object(rollout.requests, "get",
                               side_effect=responses) as get:
            assert rollout.wait_until_ready(container, "8080", "80",
                                            "/health") is True

        assert get.call_args_list[1].args[0] == "http://localhost:8080/health"

    def test_health_path_times_out(self, instant_clock):
        container = mock.Mock(status="running")
        container.attrs = {"NetworkSettings": {"IPAddress": "172.17.0.5"}}

        with mock.patch.object(rollout.requests, "get",
                               side_effect=requests.ConnectionError()):
            assert rollout.wait_until_ready(container, "8080", "80", "/health",
                                            timeout=3) is False


class TestUpdateSingleContainerService:
    def run_update(self, docker_client, db_env, mode="docker", image="",
                   tag="", health_path="", volumes=None):
        db, cursor = db_env
        return rollout.update_single_container_service(
            docker_client, "svc", mode, db, cursor, "8080:80", image, tag,
            health_path, volumes or [])

    def test_successful_docker_update(self, docker_client, db_env,
                                      instant_clock):
        db, _ = db_env
        docker_client.containers.run.return_value.status = "running"
        old_container = docker_client.containers.get.return_value

        assert self.run_update(docker_client, db_env) is True

        # the new image is staged, promoted and started
        docker_client.images.build.assert_called_once_with(path=".",
                                                           tag="svc:next",
                                                           rm=True)
        docker_client.images.get.assert_called_once_with("svc:next")
        docker_client.images.get.return_value.tag.assert_called_once_with(
            "svc", "latest")
        assert docker_client.containers.run.call_args.args[0] == "svc:latest"
        old_container.stop.assert_called_once()
        old_container.remove.assert_called_once()
        assert service_state(db) == "RUNNING"
        assert read_error_file() == ""

    def test_build_happens_before_old_container_is_stopped(self, docker_client,
                                                           db_env,
                                                           instant_clock):
        # the phase-1 property: a build failure must leave the old
        # container untouched
        docker_client.containers.run.return_value.status = "running"
        calls = []
        docker_client.images.build.side_effect = \
            lambda **kw: calls.append("build") or (mock.Mock(), iter([]))
        docker_client.containers.get.return_value.stop.side_effect = \
            lambda **kw: calls.append("stop")

        self.run_update(docker_client, db_env)

        assert calls.index("build") < calls.index("stop")

    def test_failed_build_keeps_old_container_serving(self, docker_client,
                                                      db_env):
        db, _ = db_env
        docker_client.images.build.side_effect = BuildError("broken Dockerfile",
                                                            build_log=[])

        assert self.run_update(docker_client, db_env) is False

        docker_client.containers.get.return_value.stop.assert_not_called()
        docker_client.containers.run.assert_not_called()
        assert service_state(db) == "UPDATE FAILED"
        assert read_error_file() == "broken Dockerfile"

    def test_failed_pull_keeps_old_container_serving(self, docker_client,
                                                     db_env):
        db, _ = db_env
        docker_client.images.pull.side_effect = ImageNotFound("no such image")

        assert self.run_update(docker_client, db_env, mode="dockerfile",
                               image="nginx", tag="broken") is False

        docker_client.containers.get.return_value.stop.assert_not_called()
        assert service_state(db) == "UPDATE FAILED"
        assert "no such image" in read_error_file()

    def test_unready_container_rolls_back_to_old_image(self, docker_client,
                                                       db_env, instant_clock):
        db, _ = db_env
        new_container = mock.Mock(status="running")
        new_container.attrs = {"NetworkSettings": {"IPAddress": ""}}
        docker_client.containers.run.side_effect = [new_container,
                                                    mock.Mock()]

        with mock.patch.object(rollout.requests, "get",
                               side_effect=requests.ConnectionError()), \
                mock.patch.object(rollout, "READINESS_TIMEOUT", 3):
            result = self.run_update(docker_client, db_env,
                                     health_path="/health")

        assert result is False
        # the failed container is removed, the old image restarted
        new_container.stop.assert_called_once()
        new_container.remove.assert_called_once()
        assert docker_client.containers.run.call_count == 2
        rollback_call = docker_client.containers.run.call_args_list[1]
        assert rollback_call.args[0] == "sha256:old-image"
        assert service_state(db) == "UPDATE FAILED"
        assert "did not become ready" in read_error_file()

    def test_failed_start_rolls_back_to_old_image(self, docker_client, db_env):
        db, _ = db_env
        docker_client.containers.run.side_effect = [
            APIError("conflict", explanation="port already allocated"),
            mock.Mock(),
        ]

        assert self.run_update(docker_client, db_env) is False

        rollback_call = docker_client.containers.run.call_args_list[1]
        assert rollback_call.args[0] == "sha256:old-image"
        assert service_state(db) == "UPDATE FAILED"
        assert read_error_file() == "port already allocated"

    def test_first_start_without_old_container(self, docker_client, db_env,
                                               instant_clock):
        db, _ = db_env
        docker_client.containers.get.side_effect = NotFound("no container")
        docker_client.containers.run.return_value.status = "running"

        assert self.run_update(docker_client, db_env) is True
        assert service_state(db) == "RUNNING"

    def test_unready_without_old_container_reports_failure(self, docker_client,
                                                           db_env,
                                                           instant_clock):
        db, _ = db_env
        docker_client.containers.get.side_effect = NotFound("no container")
        new_container = mock.Mock(status="exited")
        docker_client.containers.run.return_value = new_container

        assert self.run_update(docker_client, db_env) is False

        # no rollback anchor: only the failed container is cleaned up
        assert docker_client.containers.run.call_count == 1
        assert service_state(db) == "UPDATE FAILED"

    def test_dockerfile_mode_runs_pulled_image(self, docker_client, db_env,
                                               instant_clock):
        db, _ = db_env
        docker_client.containers.run.return_value.status = "running"

        assert self.run_update(docker_client, db_env, mode="dockerfile",
                               image="nginx", tag="alpine") is True

        docker_client.images.pull.assert_called_once_with("nginx", "alpine")
        run_call = docker_client.containers.run.call_args
        assert run_call.args[0] == "nginx:alpine"
        assert run_call.kwargs["tty"] is True
        assert service_state(db) == "RUNNING"


class TestUpdateComposeService:
    def test_build_runs_before_recreation(self, db_env):
        db, cursor = db_env

        with mock.patch.object(rollout.subprocess, "run") as run:
            assert rollout.update_compose_service("svc", db, cursor) is True

        assert run.call_args_list[0].args[0] == ["docker-compose", "build"]
        assert run.call_args_list[1].args[0] == ["docker-compose", "up", "-d"]
        # no 'docker-compose down' anywhere: containers are recreated in place
        commands = [call.args[0] for call in run.call_args_list]
        assert ["docker-compose", "down"] not in commands
        assert service_state(db) == "RUNNING"

    def test_failed_build_keeps_old_containers(self, db_env):
        db, cursor = db_env
        error = subprocess.CalledProcessError(1, ["docker-compose", "build"],
                                              stderr=b"compose build failed")

        with mock.patch.object(rollout.subprocess, "run",
                               side_effect=error) as run:
            assert rollout.update_compose_service("svc", db, cursor) is False

        assert run.call_count == 1  # 'up -d' is never reached
        assert service_state(db) == "UPDATE FAILED"
        assert "compose build failed" in read_error_file()