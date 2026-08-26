"""Unit tests for tasks/start_service.py's start_service (docker stubbed).

start_service runs inside the service's docker_root directory and writes
error.txt there, so every test chdir's into a temp dir first.
"""
import os
import subprocess
import sys
from unittest import mock

import pytest
from docker.errors import APIError, BuildError, ImageNotFound

# the tasks scripts import each other as top-level modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "tasks"))

import start_service as start_service_module  # noqa: E402


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def db_and_cursor():
    return mock.Mock(), mock.Mock()


@pytest.fixture
def docker_client():
    client = mock.Mock()
    client.images.build.return_value = (mock.Mock(), iter([]))
    with mock.patch.object(start_service_module.docker, "from_env",
                           return_value=client):
        yield client


def read_error_file():
    with open("error.txt") as f:
        return f.read()


def test_docker_mode_builds_and_runs(workspace, db_and_cursor, docker_client):
    db, cursor = db_and_cursor

    start_service_module.start_service("svc", "docker", db, cursor,
                                       "8080:80,8443:443", None, None, [])

    docker_client.images.build.assert_called_once_with(path=".", tag="svc",
                                                       rm=True)
    run_call = docker_client.containers.run.call_args
    assert run_call.args[0] == "svc:latest"
    assert run_call.kwargs["ports"] == {"80": "8080", "443": "8443"}
    assert run_call.kwargs["name"] == "svc"
    cursor.execute.assert_called_once_with(
        "UPDATE repos SET state = 'RUNNING' WHERE id = ?", ("svc",))
    assert read_error_file() == ""


def test_docker_mode_reads_env_file(workspace, db_and_cursor, docker_client):
    db, cursor = db_and_cursor
    with open(".env", "w") as f:
        f.write("FOO=bar\n")

    start_service_module.start_service("svc", "docker", db, cursor,
                                       "8080:80", None, None, [])

    assert docker_client.containers.run.call_args.kwargs["environment"] == ["FOO=bar"]


def test_docker_mode_build_error_reports_failure(workspace, db_and_cursor,
                                                 docker_client):
    db, cursor = db_and_cursor
    docker_client.images.build.side_effect = BuildError("the build exploded",
                                                        build_log=[])

    start_service_module.start_service("svc", "docker", db, cursor,
                                       "8080:80", None, None, [])

    assert read_error_file() == "the build exploded"
    cursor.execute.assert_called_once_with(
        "UPDATE repos SET state = 'BUILD FAILED' WHERE id = ?", ("svc",))


def test_docker_mode_api_error_reports_failure(workspace, db_and_cursor,
                                               docker_client):
    # regression for issue #147: an APIError crashed the handler because
    # `e is APIError` is never true and APIError has no .msg attribute
    db, cursor = db_and_cursor
    docker_client.images.build.side_effect = APIError(
        "500 Server Error", explanation="no space left on device")

    start_service_module.start_service("svc", "docker", db, cursor,
                                       "8080:80", None, None, [])

    assert read_error_file() == "no space left on device"
    cursor.execute.assert_called_once_with(
        "UPDATE repos SET state = 'BUILD FAILED' WHERE id = ?", ("svc",))


def test_dockerfile_mode_pulls_and_runs(workspace, db_and_cursor, docker_client):
    db, cursor = db_and_cursor

    start_service_module.start_service("svc", "dockerfile", db, cursor,
                                       "8080:80", "nginx", "alpine",
                                       ["/data:/var/data"])

    docker_client.images.pull.assert_called_once_with("nginx", "alpine")
    run_call = docker_client.containers.run.call_args
    assert run_call.args[0] == "nginx:alpine"
    assert run_call.kwargs["ports"] == {"80": "8080"}
    assert run_call.kwargs["volumes"] == ["/data:/var/data"]
    cursor.execute.assert_called_once_with(
        "UPDATE repos SET state = 'RUNNING' WHERE id = ?", ("svc",))
    assert read_error_file() == ""


def test_dockerfile_mode_pull_failure_without_explanation(workspace,
                                                          db_and_cursor,
                                                          docker_client):
    # regression for issue #147: explanation=None crashed f.write with TypeError
    db, cursor = db_and_cursor
    docker_client.images.pull.side_effect = ImageNotFound("pull access denied")

    start_service_module.start_service("svc", "dockerfile", db, cursor,
                                       "8080:80", "nginx", "alpine", [])

    assert "pull access denied" in read_error_file()
    cursor.execute.assert_called_once_with(
        "UPDATE repos SET state = 'BUILD FAILED' WHERE id = ?", ("svc",))


def test_docker_compose_mode_builds_and_starts(workspace, db_and_cursor,
                                               docker_client):
    db, cursor = db_and_cursor

    with mock.patch.object(start_service_module.subprocess, "run") as run:
        start_service_module.start_service("svc", "docker-compose", db, cursor,
                                           ".", None, None, [])

    assert run.call_args_list[0].args[0] == ["docker-compose", "build"]
    assert run.call_args_list[1].args[0] == ["docker-compose", "up", "-d"]
    cursor.execute.assert_called_once_with(
        "UPDATE repos SET state = 'RUNNING' WHERE id = ?", ("svc",))
    assert read_error_file() == ""


def test_docker_compose_mode_build_failure(workspace, db_and_cursor,
                                           docker_client):
    db, cursor = db_and_cursor
    error = subprocess.CalledProcessError(1, ["docker-compose", "build"],
                                          stderr=b"compose build failed")

    with mock.patch.object(start_service_module.subprocess, "run",
                           side_effect=error):
        start_service_module.start_service("svc", "docker-compose", db, cursor,
                                           ".", None, None, [])

    assert "compose build failed" in read_error_file()
    cursor.execute.assert_called_once_with(
        "UPDATE repos SET state = 'BUILD FAILED' WHERE id = ?", ("svc",))

@pytest.fixture
def proxy_env(workspace, monkeypatch):
    """Enable the reverse-proxy profile with an isolated config directory."""
    monkeypatch.setenv("PROXY_ENABLED", "true")
    monkeypatch.setenv("PROXY_CONF_DIR", str(workspace / "conf.d"))
    monkeypatch.setenv("PROXY_CONTAINER", "microservice-proxy")
    return workspace / "conf.d"


def started_container(host_ports):
    container = mock.Mock()
    container.name = "svc-blue"
    container.attrs = {"NetworkSettings": {"Ports": {
        f"{port}/tcp": [{"HostIp": "127.0.0.1", "HostPort": host_port}]
        for port, host_port in host_ports.items()}}}
    return container


class TestProxyProfile:
    def test_first_version_starts_as_blue_behind_the_proxy(self, db_and_cursor,
                                                           docker_client,
                                                           proxy_env):
        db, cursor = db_and_cursor
        docker_client.containers.run.return_value = \
            started_container({"80": "49153"})
        docker_client.containers.get.return_value.exec_run.return_value = (0, b"")

        start_service_module.start_service("svc", "docker", db, cursor,
                                           "8080:80", None, None, [])

        run_call = docker_client.containers.run.call_args
        assert run_call.kwargs["name"] == "svc-blue"
        # the registered port belongs to the proxy now
        assert run_call.kwargs["ports"] == {"80": ("127.0.0.1", None)}
        config = (proxy_env / "svc.conf").read_text()
        assert "listen 8080;" in config
        assert "proxy_pass 127.0.0.1:49153;" in config
        assert read_error_file() == ""

    def test_dockerfile_mode_is_routed_as_well(self, db_and_cursor,
                                               docker_client, proxy_env):
        db, cursor = db_and_cursor
        docker_client.containers.run.return_value = \
            started_container({"80": "49153"})
        docker_client.containers.get.return_value.exec_run.return_value = (0, b"")

        start_service_module.start_service("svc", "dockerfile", db, cursor,
                                           "8080:80", "nginx", "alpine", [])

        assert docker_client.containers.run.call_args.kwargs["name"] == "svc-blue"
        assert "proxy_pass 127.0.0.1:49153;" in (proxy_env / "svc.conf").read_text()

    def test_unroutable_container_is_removed_and_reported(self, db_and_cursor,
                                                          docker_client,
                                                          proxy_env):
        db, cursor = db_and_cursor
        container = started_container({"80": "49153"})
        docker_client.containers.run.return_value = container
        docker_client.containers.get.return_value.exec_run.return_value = \
            (1, b"bad config")

        start_service_module.start_service("svc", "docker", db, cursor,
                                           "8080:80", None, None, [])

        assert "nginx reload failed" in read_error_file()
        cursor.execute.assert_called_once_with(
            "UPDATE repos SET state = 'BUILD FAILED' WHERE id = ?", ("svc",))
        assert not (proxy_env / "svc.conf").exists()

    def test_compose_mode_is_untouched_by_the_profile(self, db_and_cursor,
                                                      docker_client, proxy_env):
        db, cursor = db_and_cursor

        with mock.patch.object(start_service_module.subprocess, "run"):
            start_service_module.start_service("svc", "docker-compose", db,
                                               cursor, ".", None, None, [])

        assert not (proxy_env / "svc.conf").exists()
