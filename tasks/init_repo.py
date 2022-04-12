from git import Repo
import sqlite3
from tasks.exceptions import RepositoryAlreadyExistsException
import os


def load_repository(url: str, mode: str, port: str, docker_root: str):
    """
    Clone a repository and store configuration into database

    :param port: Port Mapping for Dockerfile setups
    :param url: Git Clone URL
    :param mode: mode of docker execution
    :param docker_root: directory of repo with Dockerfile/docker-compose.yml
    :raises RepositoryAlreadyExistsException
    :return: id of the created repository
    """
    link = '-'.join(url.lower().replace('//', '').split('/')[1:]).replace('.git', '')

    repo_path = f'services/{link}'

    # repository already exists
    if os.path.exists(repo_path):
        raise RepositoryAlreadyExistsException()

    # clone repository
    Repo.clone_from(url, repo_path)

    with sqlite3.connect('services.db') as db:
        cursor = db.cursor()

        # store configuration in SQLite db
        cursor.execute('INSERT INTO repos VALUES (?, ?, ?, "INITIALIZING", ?, ?)',
                       (link, url, mode, port, docker_root))
        db.commit()
        cursor.close()

    return link
