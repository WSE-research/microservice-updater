"""Tests for tasks.init_repo.load_repository.

The 'dockerfile' mode needs no git clone (it only creates a directory), so we
can exercise the directory creation, optional-file writing, duplicate guard and
the SQLite registration without touching the network.
"""
import os
import sqlite3
from unittest import mock

import pytest

from tasks import init_repo
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


def test_load_repository_custom_file_cannot_escape_service_dir(workspace):
    load_repository(
        url="", mode="dockerfile", port="8080:80", docker_root=".",
        dockerfile="myimage", tag="1.0", files={"../escape.txt": "pwned"},
    )
    # the ".." is neutralized, so the file lands inside the service directory
    assert not os.path.exists(os.path.join("services", "escape.txt"))
    assert os.path.exists(os.path.join("services", "myimage", "escape.txt"))


def test_load_repository_git_mode_derives_id_from_url(workspace):
    url = "https://github.com/WSE-research/Demo-Service.git"
    with mock.patch.object(init_repo, "Repo") as repo_mock:
        repo_mock.clone_from.return_value.submodules = []
        service_id = load_repository(
            url=url, mode="docker", port="8080:80", docker_root=".",
            dockerfile="", tag="",
        )

    assert service_id == "wse-research-demo-service"
    repo_mock.clone_from.assert_called_once_with(
        url, os.path.join("services", "wse-research-demo-service"))
    with sqlite3.connect(os.path.join("services", "services.db")) as db:
        row = db.execute("SELECT id, url, mode, state FROM repos WHERE id = ?",
                         (service_id,)).fetchone()
    assert row == (service_id, url, "docker", "INITIALIZING")


def test_load_repository_git_mode_initializes_submodules(workspace):
    submodule = mock.Mock()
    with mock.patch.object(init_repo, "Repo") as repo_mock:
        repo_mock.clone_from.return_value.submodules = [submodule]
        load_repository(
            url="https://example.org/group/repo.git", mode="docker-compose",
            port="", docker_root=".", dockerfile="", tag="",
        )

    submodule.update.assert_called_once()
