"""Unit tests for service_config/config.py (port-mapping validation + REGEXP)."""
import sqlite3

import pytest

from service_config.config import (
    InvalidPortMappingException,
    PortAlreadyUsedException,
    check_ports,
    modes,
    regexp,
)


@pytest.fixture
def cursor():
    db = sqlite3.connect(":memory:")
    db.create_function("REGEXP", 2, regexp)
    db.execute(
        "CREATE TABLE repos(id TEXT PRIMARY KEY, url TEXT, mode TEXT, state TEXT,"
        " port TEXT, docker_root TEXT, image TEXT, tag TEXT)"
    )
    yield db.cursor()
    db.close()


def test_modes_contains_expected_values():
    assert set(modes) == {"docker", "docker-compose", "dockerfile"}


def test_regexp_matches_and_rejects():
    assert regexp(r"^\d+$", "123") is True
    assert regexp(r"^\d+$", "12a") is False


def test_check_ports_accepts_valid_mapping(cursor):
    assert check_ports("8080:80", cursor) is True


def test_check_ports_accepts_multiple_mappings(cursor):
    assert check_ports("8080:80,9090:90", cursor) is True


def test_check_ports_rejects_invalid_format(cursor):
    with pytest.raises(InvalidPortMappingException) as exc:
        check_ports("not-a-port", cursor)
    assert "Invalid port mapping" in exc.value.message


def test_check_ports_detects_already_used_port(cursor):
    cursor.execute(
        "INSERT INTO repos VALUES ('svc', 'url', 'docker', 'RUNNING',"
        " '8080:80', '.', '', '')"
    )
    with pytest.raises(PortAlreadyUsedException) as exc:
        check_ports("8080:81", cursor)
    assert "8080" in exc.value.message
