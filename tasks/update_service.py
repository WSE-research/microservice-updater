import sys
import sqlite3
import os
from git.repo import Repo
from json import loads
import subprocess
from start_service import start_service


def stop_service(docker_mode: str, s_id: str):
    """
    Stops a docker container

    :param docker_mode: initialization mode
    :param s_id: microservice id
    """
    if docker_mode in ['docker', 'dockerfile']:
        subprocess.run(['docker', 'container', 'stop', s_id])
        subprocess.run(['docker', 'container', 'rm', s_id])
    elif docker_mode == 'docker-compose':
        subprocess.run(['docker-compose', 'down'])


if __name__ == '__main__':
    base_dir = os.getcwd()

    service_id = sys.argv[1]

    if file_string := sys.argv[2]:
        files = loads(file_string)
    else:
        files = {}

    with sqlite3.connect('services/services.db') as db:
        cursor = db.cursor()

        # check, if service exists
        cursor.execute('SELECT docker_root, mode, port, image, tag FROM repos WHERE id = ?', (service_id,))

        if service := cursor.fetchone():
            cursor.execute('UPDATE repos SET state = \'UPDATING\' WHERE id = ?', (service_id,))
            db.commit()

            # pull the newest commits from remote server
            if os.path.exists(f'services/{service_id}/.git'):
                repo = Repo(f'services/{service_id}/.git')
                repo.head.reset('--hard')
                repo.remote('origin').pull()

                for file in files:
                    file_path = f'services/{service_id}/{file.replace("..", ".")}'

                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    
                    with open(file_path, 'w') as f:
                        f.write(files[file])

            os.chdir(f'services/{service_id}/{service[0]}')

            mode = service[1]

            # build new images and containers
            stop_service(mode, service_id)
            start_service(service_id, mode, db, cursor, service[2], service[3], service[4])

            os.chdir(base_dir)
