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
