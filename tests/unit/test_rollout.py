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

class TestProbeUrls:
    def test_user_defined_networks_are_probed(self):
        container = mock.Mock()
        container.attrs = {"NetworkSettings": {
            "IPAddress": "",
            "Networks": {"bridge": {"IPAddress": "172.17.0.9"}},
        }}

        assert rollout.probe_urls(container, "", "80", "/health") == \
            ["http://172.17.0.9:80/health"]

    def test_duplicate_addresses_are_probed_once(self):
        container = mock.Mock()
        container.attrs = {"NetworkSettings": {
            "IPAddress": "172.17.0.9",
            "Networks": {"bridge": {"IPAddress": "172.17.0.9"}},
        }}

        assert rollout.probe_urls(container, "8080", "80", "/health") == [
            "http://172.17.0.9:80/health",
            "http://localhost:8080/health",
        ]

    def test_container_without_addresses_falls_back_to_the_mapped_port(self):
        container = mock.Mock()
        container.attrs = {"NetworkSettings": {}}

        assert rollout.probe_urls(container, "8080", "80", "/health") == \
            ["http://localhost:8080/health"]


@pytest.fixture
def proxy_env(workspace, monkeypatch):
    """Enable the reverse-proxy profile with an isolated config directory."""
    monkeypatch.setenv("PROXY_ENABLED", "true")
    monkeypatch.setenv("PROXY_CONF_DIR", str(workspace / "conf.d"))
    monkeypatch.setenv("PROXY_CONTAINER", "microservice-proxy")
    monkeypatch.setenv("PROXY_DRAIN_SECONDS", "0")
    return workspace / "conf.d"


def register_containers(docker_client, names):
    """Let the stubbed client only know about the given container names."""
    containers = {}

    for name in names:
        container = mock.Mock()
        container.name = name
        containers[name] = container

    proxy_container = mock.Mock()
    proxy_container.exec_run.return_value = (0, b"")
    containers["microservice-proxy"] = proxy_container

    def get(name):
        if name in containers:
            return containers[name]
        raise NotFound(f"no container {name}")

    docker_client.containers.get.side_effect = get
    return containers


def run_registers(docker_client, containers, new_container):
    """Make the started container discoverable under the name it was given."""
    def run(*args, **kwargs):
        containers[kwargs["name"]] = new_container
        return new_container

    docker_client.containers.run.side_effect = run


def started_container(host_ports=None, status="running"):
    container = mock.Mock(status=status)
    bindings = {f"{port}/tcp": [{"HostIp": "127.0.0.1", "HostPort": host_port}]
                for port, host_port in (host_ports or {"80": "49153"}).items()}
    container.attrs = {"NetworkSettings": {"Ports": bindings,
                                           "IPAddress": "172.17.0.9"}}
    return container


class TestUpdateProxiedService:
    def run_update(self, docker_client, db_env, mode="docker", image="",
                   tag="", health_path="", volumes=None, port="8080:80"):
        db, cursor = db_env
        return rollout.update_proxied_service(
            docker_client, "svc", mode, db, cursor, port, image, tag,
            health_path, volumes or [])

    def test_new_version_starts_next_to_the_old_one(self, docker_client, db_env,
                                                    proxy_env, instant_clock):
        db, _ = db_env
        containers = register_containers(docker_client, ["svc-blue"])
        new_container = started_container()
        docker_client.containers.run.return_value = new_container

        assert self.run_update(docker_client, db_env) is True

        run_call = docker_client.containers.run.call_args
        # the new container gets the other color and no registered port
        assert run_call.kwargs["name"] == "svc-green"
        assert run_call.kwargs["ports"] == {"80": ("127.0.0.1", None)}
        # the old container is only removed after the route has been switched
        containers["svc-blue"].stop.assert_called_once()
        assert (proxy_env / "svc.conf").read_text().count("listen 8080;") == 1
        assert "proxy_pass 127.0.0.1:49153;" in (proxy_env / "svc.conf").read_text()
        assert service_state(db) == "RUNNING"
        assert read_error_file() == ""

    def test_route_switches_only_after_readiness(self, docker_client, db_env,
                                                 proxy_env, instant_clock):
        register_containers(docker_client, ["svc-blue"])
        new_container = started_container()
        new_container.attrs["NetworkSettings"]["IPAddress"] = "172.17.0.9"
        docker_client.containers.run.return_value = new_container

        order = []
        new_container.reload.side_effect = lambda: order.append("probe")
        with mock.patch.object(rollout.requests, "get",
                               return_value=mock.Mock(status_code=200)), \
                mock.patch.object(rollout.proxy, "switch_route",
                                  side_effect=lambda *a, **kw: order.append("switch")):
            assert self.run_update(docker_client, db_env,
                                   health_path="/health") is True

        assert order.index("probe") < order.index("switch")

    def test_first_rollout_uses_blue(self, docker_client, db_env, proxy_env,
                                     instant_clock):
        db, _ = db_env
        register_containers(docker_client, [])
        docker_client.containers.run.return_value = started_container()

        assert self.run_update(docker_client, db_env) is True

        assert docker_client.containers.run.call_args.kwargs["name"] == "svc-blue"
        assert service_state(db) == "RUNNING"

    def test_failed_build_never_touches_the_running_version(self, docker_client,
                                                            db_env, proxy_env):
        db, _ = db_env
        containers = register_containers(docker_client, ["svc-blue"])
        docker_client.images.build.side_effect = BuildError("broken Dockerfile",
                                                            build_log=[])

        assert self.run_update(docker_client, db_env) is False

        docker_client.containers.run.assert_not_called()
        containers["svc-blue"].stop.assert_not_called()
        assert not (proxy_env / "svc.conf").exists()
        assert service_state(db) == "UPDATE FAILED"
        assert read_error_file() == "broken Dockerfile"

    def test_unready_container_keeps_the_old_route(self, docker_client, db_env,
                                                   proxy_env, instant_clock):
        db, _ = db_env
        containers = register_containers(docker_client, ["svc-blue"])
        new_container = started_container(status="exited")
        # the rollout looks the new container up again to remove it
        run_registers(docker_client, containers, new_container)

        assert self.run_update(docker_client, db_env,
                               health_path="/health") is False

        new_container.stop.assert_called_once()
        new_container.remove.assert_called_once()
        containers["svc-blue"].stop.assert_not_called()
        assert not (proxy_env / "svc.conf").exists()
        assert service_state(db) == "UPDATE FAILED"
        assert "did not become ready" in read_error_file()

    def test_failed_start_leaves_the_old_version_serving(self, docker_client,
                                                         db_env, proxy_env):
        db, _ = db_env
        containers = register_containers(docker_client, ["svc-blue"])
        docker_client.containers.run.side_effect = \
            APIError("conflict", explanation="port already allocated")

        assert self.run_update(docker_client, db_env) is False

        containers["svc-blue"].stop.assert_not_called()
        assert service_state(db) == "UPDATE FAILED"
        assert read_error_file() == "port already allocated"

    def test_failed_proxy_reload_rolls_the_rollout_back(self, docker_client,
                                                        db_env, proxy_env,
                                                        instant_clock):
        db, _ = db_env
        containers = register_containers(docker_client, ["svc-blue"])
        new_container = started_container()
        run_registers(docker_client, containers, new_container)
        containers["microservice-proxy"].exec_run.return_value = (1, b"bad config")

        assert self.run_update(docker_client, db_env) is False

        # the new container is dropped, the old one keeps its route
        new_container.remove.assert_called_once()
        containers["svc-blue"].stop.assert_not_called()
        assert not (proxy_env / "svc.conf").exists()
        assert service_state(db) == "UPDATE FAILED"
        assert "nginx reload failed" in read_error_file()

    def test_stale_container_of_the_target_color_is_replaced(self, docker_client,
                                                             db_env, proxy_env,
                                                             instant_clock):
        containers = register_containers(docker_client, ["svc-blue", "svc-green"])
        docker_client.containers.run.return_value = started_container()

        assert self.run_update(docker_client, db_env) is True

        # a leftover green container from a failed rollout is removed first
        containers["svc-green"].remove.assert_called_once()

    def test_container_from_a_pre_proxy_deployment_is_replaced(self, docker_client,
                                                               db_env, proxy_env,
                                                               instant_clock):
        containers = register_containers(docker_client, ["svc"])
        docker_client.containers.run.return_value = started_container()

        assert self.run_update(docker_client, db_env) is True

        # it holds the registered port the proxy needs to listen on
        containers["svc"].stop.assert_called_once()
        assert docker_client.containers.run.call_args.kwargs["name"] == "svc-blue"

    def test_dockerfile_mode_pulls_and_publishes_all_ports(self, docker_client,
                                                           db_env, proxy_env,
                                                           instant_clock):
        register_containers(docker_client, ["svc-blue"])
        docker_client.containers.run.return_value = \
            started_container({"80": "49153", "443": "49154"})

        assert self.run_update(docker_client, db_env, mode="dockerfile",
                               image="nginx", tag="alpine",
                               port="8080:80,8443:443") is True

        docker_client.images.pull.assert_called_once_with("nginx", "alpine")
        run_call = docker_client.containers.run.call_args
        assert run_call.args[0] == "nginx:alpine"
        assert run_call.kwargs["tty"] is True
        config = (proxy_env / "svc.conf").read_text()
        assert "listen 8080;" in config and "proxy_pass 127.0.0.1:49153;" in config
        assert "listen 8443;" in config and "proxy_pass 127.0.0.1:49154;" in config


class TestUpdateServiceContainers:
    def dispatch(self, docker_client, db_env, mode="docker"):
        db, cursor = db_env
        return rollout.update_service_containers(docker_client, "svc", mode, db,
                                                 cursor, "8080:80", "", "", "",
                                                 [])

    def test_compose_mode_ignores_the_proxy(self, docker_client, db_env,
                                            proxy_env):
        with mock.patch.object(rollout, "update_compose_service",
                               return_value=True) as compose:
            assert self.dispatch(docker_client, db_env,
                                 mode="docker-compose") is True

        compose.assert_called_once()

    def test_proxy_profile_selects_the_blue_green_rollout(self, docker_client,
                                                          db_env, proxy_env):
        with mock.patch.object(rollout, "update_proxied_service",
                               return_value=True) as proxied:
            assert self.dispatch(docker_client, db_env) is True

        proxied.assert_called_once()

    def test_without_the_proxy_the_phase_one_flow_runs(self, docker_client,
                                                       db_env, monkeypatch):
        monkeypatch.delenv("PROXY_ENABLED", raising=False)

        with mock.patch.object(rollout, "update_single_container_service",
                               return_value=True) as single:
            assert self.dispatch(docker_client, db_env) is True

        single.assert_called_once()
