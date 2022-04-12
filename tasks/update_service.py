import sys
import sqlite3
import os
from git.repo import Repo
import subprocess
from tasks.start_service import start_service


def stop_service(docker_mode: str, s_id: str):
    """
    Stops a docker container

    :param docker_mode: initialization mode
    :param s_id: microservice id
    """
    if docker_mode == 'docker':
        subprocess.run(['docker', 'container', 'stop', s_id])
        subprocess.run(['docker', 'container', 'rm', s_id])
    elif docker_mode == 'docker-compose':
        subprocess.run(['docker-compose', 'down'])


if __name__ == '__main__':
    base_dir = os.getcwd()

    service_id = sys.argv[1]

    with sqlite3.connect('services.db') as db:
        cursor = db.cursor()

        # check, if service exists
        cursor.execute('SELECT docker_root, mode, port FROM repos WHERE id = ?', (service_id,))

        if service := cursor.fetchone():
            cursor.execute('UPDATE repos SET state = \'UPDATING\' WHERE id = ?', (service_id,))
            db.commit()

            # pull the newest commits from remote server
            repo = Repo(f'services/{service_id}/.git')
            repo.remote('origin').pull()

            os.chdir(f'services/{service_id}/{service[0]}')

            mode = service[1]

            # build new images and containers
            stop_service(mode, service_id)
            start_service(service_id, mode, db, cursor, service[2])

            os.chdir(base_dir)
