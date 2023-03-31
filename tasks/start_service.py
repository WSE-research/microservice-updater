import sys
import os
import sqlite3
import subprocess
import docker
import logging
from docker.errors import APIError, BuildError, ImageNotFound
from json import loads


def start_service(service_id: str, mode: str, db, cursor, port, dockerfile, tag, volumes: list[str]):
    """
    Builds a docker image and starts a corresponding container

    :param service_id: id of the microservice
    :param mode: initialization mode
    :param db: reference to SQLite database
    :param cursor: cursor of db
    :param port: provided port mapping
    :param dockerfile: image from dockerhub
    :param tag: tag of dockerfile
    :param volumes: list of volume mappings
    """
    # service has an environment file
    if '.env' in os.listdir():
        # read environment variables
        with open('.env') as f:
            env = [line.strip('\n\r') for line in f.readlines()]
    else:
        env = None

    # docker image from git repository
    if mode == 'docker':
        docker_client = docker.from_env()

        # build docker image
        try:
            # get ports
            ex_port, in_port = port.split(':')

            logging.info('Building local Dockerfile...')
            # build docker image
            image, _ = docker_client.images.build(path='.', tag=service_id, rm=True)

            logging.info('Starting container from local Dockerfile')
            # start container
            docker_client.containers.run(f'{service_id}:latest', detach=True, ports={int(in_port): int(ex_port)},
                                         name=service_id, restart_policy={'Name': 'always'}, environment=env,
                                         volumes=volumes)

            cursor.execute('UPDATE repos SET state = \'RUNNING\' WHERE id = ?', (service_id,))
            db.commit()
        # image build failed
        except (APIError, BuildError) as e:
            logging.error('Build process failed!')
            logging.error(e.explanation if e is APIError else e.msg)
            # write error message
            with open('error.txt', 'w') as f:
                f.write(e.explanation if e is APIError else e.msg)

            # set state to BUILD FAILED
            cursor.execute('UPDATE repos SET state = \'BUILD FAILED\' WHERE id = ?', (service_id,))
            db.commit()

    # docker-compose from git repository
    elif mode == 'docker-compose':
        # build docker images
        try:
            logging.info('Build from docker-compose...')
            # build docker containers
            subprocess.run(['docker-compose', 'build'], check=True, capture_output=True)

            logging.info('Start from docker-compose...')
            # start services
            subprocess.run(['docker-compose', 'up', '-d'])
            cursor.execute('UPDATE repos SET state = \'RUNNING\' WHERE id = ?', (service_id,))
            db.commit()
        # build failed
        except subprocess.CalledProcessError as e:
            logging.error('Build process failed!')
            logging.error(e.stderr)
            # write error message
            with open('error.txt', 'wb') as f:
                f.write(e.stderr)

            # set state to BUILD FAILED
            cursor.execute('UPDATE repos SET state = \'BUILD FAILED\' WHERE id = ?', (service_id,))
            db.commit()

    # docker image from docker hub
    elif mode == 'dockerfile':
        docker_client = docker.from_env()

        try:
            # set image name and port mapping
            image_name = f'{dockerfile}:{tag}'
            ex_port, in_port = port.split(':')

            logging.info('Pulling docker image...')
            # pull image and start container
            docker_client.images.pull(dockerfile, tag)

            logging.info('Start container with pulled image...')
            docker_client.containers.run(image_name, detach=True, tty=True, ports={int(in_port): int(ex_port)},
                                         name=service_id, restart_policy={'Name': 'always'}, environment=env,
                                         volumes=volumes)

            cursor.execute('UPDATE repos SET state = \'RUNNING\' WHERE id = ?', (service_id,))
            db.commit()
        # image pull failed
        except (APIError, ImageNotFound) as e:
            logging.error('docke pull failed!')
            logging.error(e)
            # write error message
            with open('error.txt', 'w') as f:
                f.write(e.explanation)

            # set state to BUILD failed
            cursor.execute('UPDATE repos SET state = \'BUILD FAILED\' WHERE id = ?', (service_id,))
            db.commit()


if __name__ == '__main__':
    # load service id, initialization mode, docker directory and port mapping
    s_id = sys.argv[1]
    docker_mode = sys.argv[2]
    docker_directory = sys.argv[3]

    if docker_mode in ['docker', 'dockerfile']:
        p = sys.argv[4]

        if docker_mode == 'dockerfile':
            docker_file = sys.argv[5]
            docker_tag = sys.argv[6]
        else:
            docker_file = None
            docker_tag = None
    else:
        p = '.'
        docker_file = None
        docker_tag = None

    volume_mapping = loads(sys.argv[7])

    base_path = os.getcwd()

    with sqlite3.connect(os.path.join('services', 'services.db')) as database:
        os.chdir(os.path.join(base_path, 'services', s_id, docker_directory))
        db_cursor = database.cursor()

        # check if service exists
        db_cursor.execute('SELECT url FROM repos WHERE id = ?', (s_id,))

        if fetch_url := db_cursor.fetchone():
            start_service(s_id, docker_mode, database, db_cursor, p, docker_file, docker_tag, volume_mapping)

        os.chdir(base_path)
