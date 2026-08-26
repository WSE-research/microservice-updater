import sys
import sqlite3
import os
from git.repo import Repo
from json import loads
import subprocess
import proxy
from rollout import update_service_containers
import docker
from docker.errors import NotFound
import logging


def stop_service(docker_mode: str, s_id: str):
    """
    Stops a docker container

    :param docker_mode: initialization mode
    :param s_id: microservice id
    """
    # single dockerfile used
    if docker_mode in ['docker', 'dockerfile']:
        docker_client = docker.from_env()

        logging.info(f'Stopping container {s_id}')

        try:
            # get, stop and remove container
            container = docker_client.containers.get(s_id)
            container.stop()
            container.remove()
            removed = True
        # container doesn't exist
        except NotFound:
            removed = False

        # with the proxy profile the service runs under a colored name and
        # owns a generated proxy route (issue #149, phase 2)
        if proxy.proxy_enabled():
            removed = proxy.remove_service(docker_client, s_id) or removed

        if not removed:
            logging.warning(f'Container {s_id} not found!')
    # docker-compose used
    elif docker_mode == 'docker-compose':
        logging.info('Stopping containers with docker-compose')
        subprocess.run(['docker-compose', 'down'])


if __name__ == '__main__':
    base_dir = os.getcwd()

    service_id = sys.argv[1]

    if file_string := sys.argv[2]:
        files = loads(file_string)
    else:
        files = {}

    volumes = loads(sys.argv[3])

    with sqlite3.connect('services/services.db') as db:
        cursor = db.cursor()

        # check, if service exists
        cursor.execute('SELECT docker_root, mode, port, image, tag, health_path FROM repos'
                       ' WHERE id = ?', (service_id,))

        # service exists
        if service := cursor.fetchone():
            cursor.execute('UPDATE repos SET state = \'UPDATING\' WHERE id = ?', (service_id,))
            db.commit()

            # pull the newest commits from remote server
            if os.path.exists(f'services/{service_id}/.git'):
                repo = Repo(f'services/{service_id}/.git')
                repo.head.reset('--hard')
                repo.remote('origin').pull()

            # update custom files
            service_dir = os.path.normpath(os.path.join('services', service_id))
            for file in files:
                # custom files have to stay inside the service's directory
                file_path = os.path.normpath(os.path.join(service_dir, file))
                if not file_path.startswith(service_dir + os.sep):
                    logging.warning(f'Skipping invalid file path {file}')
                    continue

                os.makedirs(os.path.dirname(file_path), exist_ok=True)

                with open(file_path, 'w') as f:
                    f.write(files[file])

            os.chdir(f'services/{service_id}/{service[0]}')

            docker_root, mode, port, image, tag, health_path = service

            # prepare the new version while the old container keeps serving,
            # then swap with readiness check and rollback (issue #149)
            update_service_containers(docker.from_env(), service_id, mode, db,
                                      cursor, port, image, tag, health_path,
                                      volumes)

            os.chdir(base_dir)
