"""Tests for tasks.init_repo.load_repository.

The 'dockerfile' mode needs no git clone (it only creates a directory), so we
can exercise the directory creation, optional-file writing, duplicate guard and
the SQLite registration without touching the network.
"""
import os
import sqlite3

import pytest

from tasks.exceptions import RepositoryAlreadyExistsException
from tasks.init_repo import load_repository


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.mkdir("services")
    with sqlite3.connect(os.path.join("services", "services.db")) as db:
        db.execute(
            "CREATE TABLE repos(id TEXT PRIMARY KEY, url TEXT, mode TEXT, state TEXT,"
            " port TEXT, docker_root TEXT, image TEXT, tag TEXT)"
        )
        db.commit()
    return tmp_path


def test_load_repository_dockerfile_mode_creates_dir_and_row(workspace):
    service_id = load_repository(
        url="", mode="dockerfile", port="8080:80", docker_root=".",
        dockerfile="myorg/myimage", tag="1.0",
        files={"config/extra.txt": "hello"},
    )
    assert service_id == "myorg-myimage"
    # directory + optional file were created
    assert os.path.isdir(os.path.join("services", service_id))
    with open(os.path.join("services", service_id, "config", "extra.txt")) as f:
        assert f.read() == "hello"
    # row was registered with the INITIALIZING state
    with sqlite3.connect(os.path.join("services", "services.db")) as db:
        row = db.execute("SELECT id, mode, state, port FROM repos WHERE id = ?", (service_id,)).fetchone()
    assert row == (service_id, "dockerfile", "INITIALIZING", "8080:80")


def test_load_repository_rejects_existing_repository(workspace):
    os.makedirs(os.path.join("services", "myorg-myimage"))
    with pytest.raises(RepositoryAlreadyExistsException):
        load_repository(
            url="", mode="dockerfile", port="", docker_root=".",
            dockerfile="myorg/myimage", tag="1.0",
        )
