from git import Repo
import sqlite3
from tasks.exceptions import RepositoryAlreadyExistsException
import os


def load_repository(url: str, mode: str, port: str, docker_root: str, dockerfile='.', tag='.', files=None):
    """
    Clone a repository and store configuration into database

    :param files: Dictionary with (file_path, file_content) pairs
    :param port: Port Mapping for Dockerfile setups
    :param url: Git Clone URL
    :param mode: mode of docker execution
    :param docker_root: directory of repo with Dockerfile/docker-compose.yml
    :param dockerfile: docker image name from dockerhub
    :param tag: tag of dockerfile
    :raises RepositoryAlreadyExistsException
    :return: id of the created repository
    """
    if files is None:
        files = {}
    if dockerfile:
        link = dockerfile.replace('/', '-')
    else:
        link = '-'.join(url.lower().replace('//', '').split('/')[1:]).replace('.git', '')

    repo_path = f'services/{link}'

    # repository already exists
    if os.path.exists(repo_path):
        raise RepositoryAlreadyExistsException()

    if url != '':
        # clone repository
        Repo.clone_from(url, repo_path)
    else:
        os.mkdir(repo_path)

    for file in files:
        with open(f'{repo_path}/{file}', 'w') as f:
            f.write(files[file].replace('..', '.'))

    with sqlite3.connect('services/services.db') as db:
        cursor = db.cursor()

        # store configuration in SQLite db
        cursor.execute('INSERT INTO repos VALUES (?, ?, ?, "INITIALIZING", ?, ?, ?, ?)',
                       (link, url, mode, port, docker_root, dockerfile, tag))
        db.commit()
        cursor.close()

    return link
