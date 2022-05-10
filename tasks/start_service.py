import sys
import os
import sqlite3
import subprocess


def start_service(service_id: str, mode: str, db, cursor, port, dockerfile, tag):
    """
    Builds a docker image and starts a corresponding container

    :param service_id: id of the microservice
    :param mode: initialization mode
    :param db: reference to SQLite database
    :param cursor: cursor of db
    :param port: provided port mapping
    :param dockerfile: image from dockerhub
    :param tag: tag of dockerfile
    """
    if mode == 'docker':
        # build docker image
        try:
            subprocess.run(['docker', 'build', '.', '-t', f'{service_id}:latest'], check=True,
                           capture_output=True)

            # start container
            subprocess.run(['docker', 'run', '-itd', '-p', port, '--name', service_id, f'{service_id}:latest'],
                           check=True, capture_output=True)
            cursor.execute('UPDATE repos SET state = \'RUNNING\' WHERE id = ?', (service_id,))
            db.commit()

        except subprocess.CalledProcessError as e:
            with open('error.txt', 'wb') as f:
                f.write(e.stderr)

            cursor.execute('UPDATE repos SET state = \'BUILD FAILED\' WHERE id = ?', (service_id,))
            db.commit()

    elif mode == 'docker-compose':
        # build docker images
        try:
            subprocess.run(['docker-compose', 'build'], check=True, capture_output=True)

            # start services
            subprocess.run(['docker-compose', 'up', '-d'])
            cursor.execute('UPDATE repos SET state = \'RUNNING\' WHERE id = ?', (service_id,))
            db.commit()
        except subprocess.CalledProcessError as e:
            with open('error.txt', 'wb') as f:
                f.write(e.stderr)

            cursor.execute('UPDATE repos SET state = \'BUILD FAILED\' WHERE id = ?', (service_id,))
            db.commit()

    elif mode == 'dockerfile':
        try:
            subprocess.run(['docker', 'pull', f'{dockerfile}:{tag}'], check=True, capture_output=True)
            subprocess.run(['docker', 'run', '-itd', '-p', port, '--name', dockerfile, f'{dockerfile}:{tag}'],
                           check=True, capture_output=True)
            cursor.execute('UPDATE repos SET state = \'RUNNING\' WHERE id = ?', (dockerfile,))
            db.commit()
        except subprocess.CalledProcessError as e:
            with open('error.txt', 'wb') as f:
                f.write(e.stderr)

            cursor.execute('UPDATE repos SET state = \'BUILD FAILED\' WHERE id = ?', (dockerfile,))
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

    base_path = os.getcwd()

    with sqlite3.connect('services/services.db') as database:
        os.chdir(f'{base_path}/services/{s_id}/{docker_directory}')
        db_cursor = database.cursor()

        # check if service exists
        db_cursor.execute('SELECT url FROM repos WHERE id = ?', (s_id,))

        if fetch_url := db_cursor.fetchone():
            start_service(s_id, docker_mode, database, db_cursor, p, docker_file, docker_tag)

        os.chdir(base_path)
