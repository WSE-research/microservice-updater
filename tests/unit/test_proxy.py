"""Unit tests for tasks/proxy.py — the reverse-proxy blue-green support
(issue #149, phase 2). All docker interactions are stubbed."""
import os
import sys
from unittest import mock

import pytest
from docker.errors import APIError, NotFound

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "tasks"))

import proxy  # noqa: E402


@pytest.fixture
def conf_dir(tmp_path, monkeypatch):
    directory = tmp_path / "conf.d"
    monkeypatch.setenv("PROXY_CONF_DIR", str(directory))
    return directory


@pytest.fixture
def docker_client():
    client = mock.Mock()
    client.containers.get.return_value.exec_run.return_value = (0, b"")
    return client


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
    ("", False), ("false", False), ("0", False), ("no", False),
])
def test_proxy_enabled_reads_environment(monkeypatch, value, expected):
    monkeypatch.setenv("PROXY_ENABLED", value)

    assert proxy.proxy_enabled() is expected


def test_proxy_enabled_defaults_to_false(monkeypatch):
    monkeypatch.delenv("PROXY_ENABLED", raising=False)

    assert proxy.proxy_enabled() is False


def test_relative_conf_dir_is_resolved_against_the_start_directory(monkeypatch,
                                                                   tmp_path):
    # the rollout chdir's into the service directory, so a relative path must
    # not depend on the working directory at call time
    monkeypatch.setenv("PROXY_CONF_DIR", "proxy/conf.d")
    monkeypatch.chdir(tmp_path)

    assert proxy.proxy_conf_dir() == os.path.join(proxy._BASE_DIR, "proxy/conf.d")


def test_default_conf_dir(monkeypatch):
    monkeypatch.delenv("PROXY_CONF_DIR", raising=False)

    assert proxy.proxy_conf_dir() == os.path.join(proxy._BASE_DIR, "proxy",
                                                  "conf.d")


def test_drain_seconds_falls_back_on_invalid_values(monkeypatch):
    monkeypatch.setenv("PROXY_DRAIN_SECONDS", "not-a-number")

    assert proxy.drain_seconds() == proxy.DEFAULT_DRAIN_SECONDS

    monkeypatch.setenv("PROXY_DRAIN_SECONDS", "0")
    assert proxy.drain_seconds() == 0


def test_colors_alternate():
    assert proxy.next_color("blue") == "green"
    assert proxy.next_color("green") == "blue"
    # the very first rollout of a service starts with blue
    assert proxy.next_color(None) == "blue"
    assert proxy.container_name("svc", "green") == "svc-green"


class TestContainerLookup:
    def test_active_color_finds_the_running_container(self, docker_client):
        docker_client.containers.get.side_effect = [NotFound("no blue"),
                                                    mock.Mock()]

        assert proxy.active_color(docker_client, "svc") == "green"
        assert [call.args[0] for call in docker_client.containers.get.call_args_list] \
            == ["svc-blue", "svc-green"]

    def test_active_color_without_containers(self, docker_client):
        docker_client.containers.get.side_effect = NotFound("nothing")

        assert proxy.active_color(docker_client, "svc") is None

    def test_find_container_prefers_the_plain_name(self, docker_client):
        container = proxy.find_container(docker_client, "svc")

        docker_client.containers.get.assert_called_once_with("svc")
        assert container is docker_client.containers.get.return_value

    def test_find_container_falls_back_to_colors(self, docker_client):
        green = mock.Mock()
        docker_client.containers.get.side_effect = [NotFound("plain"),
                                                    NotFound("blue"), green]

        assert proxy.find_container(docker_client, "svc") is green

    def test_find_container_raises_when_nothing_exists(self, docker_client):
        docker_client.containers.get.side_effect = NotFound("nothing")

        with pytest.raises(NotFound):
            proxy.find_container(docker_client, "svc")


class TestRemoveContainer:
    def test_removes_existing_container(self, docker_client):
        container = docker_client.containers.get.return_value

        assert proxy.remove_container(docker_client, "svc-blue") is True

        container.stop.assert_called_once()
        container.remove.assert_called_once()

    def test_missing_container_is_not_an_error(self, docker_client):
        docker_client.containers.get.side_effect = NotFound("gone")

        assert proxy.remove_container(docker_client, "svc-blue") is False

    def test_api_error_is_swallowed(self, docker_client):
        docker_client.containers.get.return_value.stop.side_effect = \
            APIError("busy")

        assert proxy.remove_container(docker_client, "svc-blue") is False


class TestPortHandling:
    def test_publish_spec_uses_ephemeral_loopback_ports(self, monkeypatch):
        monkeypatch.delenv("PROXY_BACKEND_HOST", raising=False)

        # the registered external ports belong to the proxy, not the container
        assert proxy.publish_spec("8080:80,8443:443") == {
            "80": ("127.0.0.1", None),
            "443": ("127.0.0.1", None),
        }

    def test_published_ports_reads_the_assigned_host_ports(self):
        container = mock.Mock()
        container.attrs = {"NetworkSettings": {"Ports": {
            "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49153"}],
            "443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49154"}],
            "9000/tcp": None,
        }}}

        assert proxy.published_ports(container) == {"80": "49153",
                                                    "443": "49154"}
        container.reload.assert_called_once()


class TestRenderConfig:
    def test_one_listener_per_port_mapping(self, conf_dir):
        config = proxy.render_config("svc", "8080:80,8443:443",
                                     {"80": "49153", "443": "49154"})

        assert "listen 8080;" in config
        assert "proxy_pass 127.0.0.1:49153;" in config
        assert "listen 8443;" in config
        assert "proxy_pass 127.0.0.1:49154;" in config

    def test_unpublished_port_is_rejected(self, conf_dir):
        with pytest.raises(proxy.ProxyConfigurationException):
            proxy.render_config("svc", "8080:80", {})


class TestConfigFiles:
    def test_write_read_and_remove(self, conf_dir):
        assert proxy.read_config("svc") is None

        proxy.write_config("svc", "server {}")

        assert (conf_dir / "svc.conf").read_text() == "server {}"
        assert proxy.read_config("svc") == "server {}"
        assert proxy.remove_config("svc") is True
        assert proxy.remove_config("svc") is False


class TestReloadProxy:
    def test_successful_reload(self, docker_client, monkeypatch):
        monkeypatch.setenv("PROXY_CONTAINER", "my-proxy")

        proxy.reload_proxy(docker_client)

        docker_client.containers.get.assert_called_once_with("my-proxy")
        docker_client.containers.get.return_value.exec_run \
            .assert_called_once_with(["nginx", "-s", "reload"])

    def test_missing_proxy_container(self, docker_client):
        docker_client.containers.get.side_effect = NotFound("no proxy")

        with pytest.raises(proxy.ProxyUnavailableException):
            proxy.reload_proxy(docker_client)

    def test_failed_reload_reports_nginx_output(self, docker_client):
        docker_client.containers.get.return_value.exec_run.return_value = \
            (1, b"invalid port")

        with pytest.raises(proxy.ProxyUnavailableException) as error:
            proxy.reload_proxy(docker_client)

        assert "invalid port" in error.value.message

    def test_api_error_is_translated(self, docker_client):
        docker_client.containers.get.return_value.exec_run.side_effect = \
            APIError("boom", explanation="daemon gone")

        with pytest.raises(proxy.ProxyUnavailableException) as error:
            proxy.reload_proxy(docker_client)

        assert error.value.message == "daemon gone"


class TestSwitchRoute:
    def test_writes_config_and_reloads(self, docker_client, conf_dir):
        proxy.switch_route(docker_client, "svc", "8080:80", {"80": "49153"})

        assert "proxy_pass 127.0.0.1:49153;" in (conf_dir / "svc.conf").read_text()
        docker_client.containers.get.return_value.exec_run.assert_called_once()

    def test_failed_reload_restores_the_previous_route(self, docker_client,
                                                       conf_dir):
        proxy.write_config("svc", "previous route")
        docker_client.containers.get.return_value.exec_run.return_value = \
            (1, b"broken")

        with pytest.raises(proxy.ProxyUnavailableException):
            proxy.switch_route(docker_client, "svc", "8080:80", {"80": "49153"})

        # the old version keeps its route, so it keeps serving
        assert (conf_dir / "svc.conf").read_text() == "previous route"

    def test_failed_first_route_is_removed_again(self, docker_client, conf_dir):
        docker_client.containers.get.return_value.exec_run.return_value = \
            (1, b"broken")

        with pytest.raises(proxy.ProxyUnavailableException):
            proxy.switch_route(docker_client, "svc", "8080:80", {"80": "49153"})

        assert not (conf_dir / "svc.conf").exists()


class TestRemoveService:
    def test_drops_containers_route_and_reloads(self, docker_client, conf_dir):
        proxy.write_config("svc", "server {}")

        assert proxy.remove_service(docker_client, "svc") is True

        removed = [call.args[0]
                   for call in docker_client.containers.get.call_args_list]
        assert "svc-blue" in removed and "svc-green" in removed
        assert not (conf_dir / "svc.conf").exists()
        docker_client.containers.get.return_value.exec_run.assert_called_once()

    def test_without_config_no_reload_happens(self, docker_client, conf_dir):
        docker_client.containers.get.side_effect = NotFound("nothing")

        assert proxy.remove_service(docker_client, "svc") is False

    def test_failed_reload_does_not_raise(self, docker_client, conf_dir):
        proxy.write_config("svc", "server {}")
        docker_client.containers.get.return_value.exec_run.return_value = \
            (1, b"broken")

        assert proxy.remove_service(docker_client, "svc") is True
