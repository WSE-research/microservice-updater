import sys
import os
import sqlite3
import subprocess


def start_service(service_id: str, mode: str, db, cursor, port=None):
    """
    Builds a docker image and starts a corresponding container

    :param service_id: id of the microservice
    :param mode: initialization mode
    :param db: reference to SQLite database
    :param cursor: cursor of db
    :param port: provided port mapping
    """
    if mode == 'docker':
        # build docker image
        build = subprocess.run(['docker', 'build', '.', '-t', f'{service_id}:latest'])

        # build failed
        if build.returncode:
            cursor.execute('UPDATE repos SET state = \'BUILD FAILED\' WHERE id = ?', (service_id,))
            db.commit()
        else:
            # start container
            subprocess.run(['docker', 'run', '-itd', '-p', port, '-name', service_id])
            cursor.execute('UPDATE repos SET state = \'RUNNING\' WHERE id = ?', (service_id,))
            db.commit()
    elif mode == 'docker-compose':
        # build docker images
        build = subprocess.run(['docker-compose', 'build'])

        # build failed
        if build.returncode:
            cursor.execute('UPDATE repos SET state = \'BUILD FAILED\' WHERE id = ?', (service_id,))
            db.commit()
        else:
            # start services
            subprocess.run(['docker-compose', 'up', '-d'])
            cursor.execute('UPDATE repos SET state = \'RUNNING\' WHERE id = ?', (service_id,))
            db.commit()


if __name__ == '__main__':
    # load service id, initialization mode, docker directory and port mapping
    s_id = sys.argv[1]
    docker_mode = sys.argv[2]
    docker_directory = sys.argv[3]
    p = sys.argv[4]

    base_path = os.getcwd()

    with sqlite3.connect('services.db') as database:
        os.chdir(f'{base_path}/services/{s_id}/{docker_directory}')
        db_cursor = database.cursor()

        # check if service exists
        db_cursor.execute('SELECT url FROM repos WHERE id = ?', (s_id,))

        if fetch_url := db_cursor.fetchone():
            start_service(s_id, docker_mode, database, db_cursor, p)

        os.chdir(base_path)
