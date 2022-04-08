from git import Repo
from hashlib import sha256
import sqlite3
from tasks.exceptions import RepositoryAlreadyExistsException
import os


def load_repository(url: str, mode: str):
    """
    Clone a repository and store configuration into database

    :param url: Git Clone URL
    :param mode: mode of docker execution
    :raises RepositoryAlreadyExistsException
    :return: id of the created repository
    """
    link = sha256(url.encode()).hexdigest()

    repo_path = f'services/{link}'

    if os.path.exists(repo_path):
        raise RepositoryAlreadyExistsException()

    Repo.clone_from(url, repo_path)
    repo = Repo(f'{repo_path}/.git')
    head = repo.head.commit.hexsha

    with sqlite3.connect('services.db') as db:
        cursor = db.cursor()

        cursor.execute('INSERT INTO repos VALUES (?, ?, ?, "INITIALIZING")', (url, mode, head))
        db.commit()
        cursor.close()

    return link
