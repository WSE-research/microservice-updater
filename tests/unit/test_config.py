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


@pytest.mark.parametrize("ports", [
    "8080",          # no internal port
    "8080:",         # empty internal port
    ":80",           # empty external port
    "abc:80",        # non-numeric external port
    "80:def",        # non-numeric internal port
    "8080:80:443",   # too many parts
    "",              # empty string
    "8080-80",       # wrong separator
    "8080:80,",      # trailing comma yields an empty mapping
    "8080:80, 8443:443",  # whitespace after the comma
])
def test_check_ports_rejects_malformed_mappings(cursor, ports):
    with pytest.raises(InvalidPortMappingException):
        check_ports(ports, cursor)


def test_check_ports_detects_conflict_in_second_mapping(cursor):
    cursor.execute(
        "INSERT INTO repos VALUES ('svc', 'url', 'docker', 'RUNNING',"
        " '8080:80', '.', '', '')"
    )
    with pytest.raises(PortAlreadyUsedException):
        check_ports("9090:90,8080:80", cursor)


def test_check_ports_detects_conflict_in_multi_mapping_service(cursor):
    cursor.execute(
        "INSERT INTO repos VALUES ('svc', 'url', 'docker', 'RUNNING',"
        " '8080:80,8443:443', '.', '', '')"
    )
    with pytest.raises(PortAlreadyUsedException):
        check_ports("8443:443", cursor)


def test_check_ports_accepts_distinct_external_port(cursor):
    cursor.execute(
        "INSERT INTO repos VALUES ('svc', 'url', 'docker', 'RUNNING',"
        " '9090:90', '.', '', '')"
    )
    assert check_ports("8080:80", cursor) is True
