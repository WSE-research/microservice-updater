"""
Low-downtime update rollout for registered services (issue #149, phase 1).

The updated image is prepared while the old container keeps serving; only
then are the containers swapped. If the new container does not become ready,
the previous image is restored, so a broken update never takes a service
down for longer than the swap itself.
"""
import logging
import os
import subprocess
import time

import requests
from docker.errors import APIError, BuildError, ImageNotFound, NotFound

READINESS_TIMEOUT = 60


def parse_ports(port: str):
    """
    Translate a port mapping string into the docker-py ports argument

    :param port: mapping like '8080:80,8443:443'
    :return: dict of (internal port, external port) pairs
    """
    ports = {}
    for port_mapping in port.split(','):
        ex_port, in_port = port_mapping.split(':')
        ports[in_port] = ex_port
    return ports


def read_env_file():
    """
    Read the service's .env file from the current directory, if present

    :return: list of environment variable lines or None
    """
    if os.path.exists('.env'):
        with open('.env') as f:
            return [line.strip('\n\r') for line in f.readlines()]
    return None


def run_container(docker_client, image_ref: str, service_id: str, ports: dict,
                  env, volumes, tty=False):
    """Start a detached service container with the standard settings"""
    return docker_client.containers.run(image_ref, detach=True, tty=tty,
                                        ports=ports, name=service_id,
                                        restart_policy={'Name': 'always'},
                                        environment=env, volumes=volumes)


def prepare_image(docker_client, service_id: str, mode: str, image: str, tag: str):
    """
    Build or pull the updated image without touching the running container

    :return: reference of the prepared image
    :raises APIError, BuildError, ImageNotFound
    """
    if mode == 'docker':
        logging.info('Building updated image...')
        docker_client.images.build(path='.', tag=f'{service_id}:next', rm=True)
        return f'{service_id}:next'

    logging.info('Pulling updated image...')
    docker_client.images.pull(image, tag)
    return f'{image}:{tag}'


def wait_until_ready(container, external_port: str, internal_port: str,
                     health_path: str, timeout=READINESS_TIMEOUT):
    """
    Wait until the container is considered ready.

    With a configured health_path the container has to answer HTTP 200 on it
    (probed via its bridge IP and via the host-mapped port); without one the
    running state counts as ready.

    :return: True if the container became ready within the timeout
    """
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        container.reload()

        if container.status == 'exited':
            return False

        if health_path:
            ip = container.attrs['NetworkSettings']['IPAddress']
            urls = [f'http://{ip}:{internal_port}{health_path}'] if ip else []
            urls.append(f'http://localhost:{external_port}{health_path}')

            for url in urls:
                try:
                    if requests.get(url, timeout=2).status_code == 200:
                        return True
                except requests.RequestException:
                    continue
        elif container.status == 'running':
            return True

        time.sleep(1)

    return False


def set_state(db, cursor, service_id: str, state: str, error_message=''):
    """Persist the service state and write error.txt in the current directory"""
    cursor.execute('UPDATE repos SET state = ? WHERE id = ?', (state, service_id))
    db.commit()

    with open('error.txt', 'w') as f:
        f.write(error_message)


def rollback(docker_client, service_id: str, rollback_image, ports: dict,
             env, volumes, tty):
    """Restart the previous image after a failed update, if one exists"""
    if not rollback_image:
        return

    logging.info(f'Rolling back {service_id} to the previous image...')
    try:
        run_container(docker_client, rollback_image, service_id, ports, env,
                      volumes, tty)
    except APIError as e:
        logging.error(f'Rollback of {service_id} failed: {e}')


def update_single_container_service(docker_client, service_id: str, mode: str,
                                    db, cursor, port: str, image: str, tag: str,
                                    health_path: str, volumes):
    """
    Update a 'docker' or 'dockerfile' service with minimal downtime.

    The new image is prepared first (the old container keeps serving and is
    untouched if that fails), then the containers are swapped. If the new
    container does not become ready, the previous image is restored and the
    state is set to UPDATE FAILED.

    :return: True if the update succeeded
    """
    ports = parse_ports(port)
    first_mapping = port.split(',')[0].split(':')
    external_port, internal_port = first_mapping[0], first_mapping[1]
    env = read_env_file()
    tty = mode == 'dockerfile'

    # prepare the new image; on failure the old container keeps serving
    try:
        image_ref = prepare_image(docker_client, service_id, mode, image, tag)
    except (APIError, BuildError, ImageNotFound) as e:
        message = (e.explanation if isinstance(e, APIError) else e.msg) or str(e)
        logging.error(f'Preparing the updated image failed: {message}')
        set_state(db, cursor, service_id, 'UPDATE FAILED', message)
        return False

    # swap containers, remembering the old image as rollback anchor
    rollback_image = None
    try:
        old_container = docker_client.containers.get(service_id)
        rollback_image = old_container.image.id
        old_container.stop()
        old_container.remove()
    except NotFound:
        logging.warning(f'No running container for {service_id} found')

    if mode == 'docker':
        # promote the staged build to the service's regular tag
        docker_client.images.get(image_ref).tag(service_id, 'latest')
        image_ref = f'{service_id}:latest'

    try:
        new_container = run_container(docker_client, image_ref, service_id,
                                      ports, env, volumes, tty)
    except APIError as e:
        message = e.explanation or str(e)
        logging.error(f'Starting the updated container failed: {message}')
        rollback(docker_client, service_id, rollback_image, ports, env, volumes, tty)
        set_state(db, cursor, service_id, 'UPDATE FAILED', message)
        return False

    if wait_until_ready(new_container, external_port, internal_port, health_path):
        set_state(db, cursor, service_id, 'RUNNING')
        return True

    # readiness failed: restore the previous image
    message = f'updated container did not become ready within {READINESS_TIMEOUT}s'
    logging.error(message)
    try:
        new_container.stop()
        new_container.remove()
    except APIError as e:
        logging.error(f'Removing the failed container failed: {e}')

    rollback(docker_client, service_id, rollback_image, ports, env, volumes, tty)
    set_state(db, cursor, service_id, 'UPDATE FAILED', message)
    return False


def update_compose_service(service_id: str, db, cursor):
    """
    Update a docker-compose service: build first (the old containers keep
    serving), then let 'up -d' recreate only the changed containers instead
    of tearing everything down beforehand.

    :return: True if the update succeeded
    """
    try:
        logging.info('Building updated docker-compose images...')
        subprocess.run(['docker-compose', 'build'], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        message = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or str(e))
        logging.error(f'Build process failed: {message}')
        set_state(db, cursor, service_id, 'UPDATE FAILED', message)
        return False

    logging.info('Recreating changed containers...')
    subprocess.run(['docker-compose', 'up', '-d'])
    set_state(db, cursor, service_id, 'RUNNING')
    return True
