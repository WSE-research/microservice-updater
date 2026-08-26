"""Unit tests for tasks/update_service.py's stop_service helper."""
import os
import sys
from unittest import mock

import pytest
from docker.errors import NotFound

# tasks/update_service.py imports its sibling module start_service directly
# (it runs as a script), so the tasks directory itself has to be importable.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "tasks"))

import update_service  # noqa: E402


@pytest.fixture
def docker_client():
    with mock.patch.object(update_service.docker, "from_env") as from_env:
        yield from_env.return_value


@pytest.mark.parametrize("mode", ["docker", "dockerfile"])
def test_stop_service_removes_container(docker_client, mode):
    container = docker_client.containers.get.return_value

    update_service.stop_service(mode, "svc")

    docker_client.containers.get.assert_called_once_with("svc")
    container.stop.assert_called_once()
    container.remove.assert_called_once()


def test_stop_service_ignores_missing_container(docker_client):
    docker_client.containers.get.side_effect = NotFound("no such container")

    update_service.stop_service("docker", "svc")  # must not raise


def test_stop_service_docker_compose_uses_subprocess(docker_client):
    with mock.patch.object(update_service.subprocess, "run") as run:
        update_service.stop_service("docker-compose", "svc")

    run.assert_called_once_with(["docker-compose", "down"])
    docker_client.containers.get.assert_not_called()


def test_stop_service_unknown_mode_does_nothing(docker_client):
    with mock.patch.object(update_service.subprocess, "run") as run:
        update_service.stop_service("unknown-mode", "svc")

    run.assert_not_called()
    docker_client.containers.get.assert_not_called()


@pytest.fixture
def proxy_env(tmp_path, monkeypatch):
    """Enable the reverse-proxy profile with an isolated config directory."""
    monkeypatch.setenv("PROXY_ENABLED", "true")
    monkeypatch.setenv("PROXY_CONF_DIR", str(tmp_path / "conf.d"))
    monkeypatch.setenv("PROXY_CONTAINER", "microservice-proxy")
    return tmp_path / "conf.d"


def test_stop_service_removes_colored_containers_and_route(docker_client,
                                                           proxy_env):
    update_service.proxy.write_config("svc", "server {}")
    docker_client.containers.get.return_value.exec_run.return_value = (0, b"")

    update_service.stop_service("docker", "svc")

    looked_up = [call.args[0]
                 for call in docker_client.containers.get.call_args_list]
    assert looked_up == ["svc", "svc-blue", "svc-green", "microservice-proxy"]
    assert not (proxy_env / "svc.conf").exists()


def test_stop_service_without_the_profile_keeps_the_plain_lookup(docker_client,
                                                                 monkeypatch):
    monkeypatch.delenv("PROXY_ENABLED", raising=False)

    update_service.stop_service("docker", "svc")

    docker_client.containers.get.assert_called_once_with("svc")
