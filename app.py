from flask import Flask, request, jsonify
import os
import sqlite3
import re
from config import modes
from tasks.init_repo import load_repository
from tasks.exceptions import RepositoryAlreadyExistsException
import subprocess

# create directory for service repositories
if 'services' not in os.listdir():
    os.mkdir('services')

# initialize database for service management
with sqlite3.connect('services.db') as db:
    cursor = db.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS repos(id TEXT PRIMARY KEY, url TEXT, mode TEXT,'
                   'state TEXT, port TEXT, docker_root TEXT)')
    cursor.close()
    db.commit()

app = Flask(__name__)


@app.route('/service/<string:service_id>', methods=['GET', 'POST', 'DELETE'])
def update_service(service_id: str):
    """
    Accesses a service

    :param service_id: id of the requested service
    :return: GET - information about the service, POST - initialize update, DELETE - remove a service
    """
    with sqlite3.connect('services.db') as service_db:
        # search service
        service_cursor = service_db.cursor()
        service_cursor.execute('SELECT * FROM repos WHERE id = ?', (service_id,))

        # service exists
        if service_data := service_cursor.fetchone():
            _, url, mode, docker_state, port, docker_root = service_data

            if (method := request.method) == 'GET':
                # load all docker container states
                services = subprocess.run(['docker', 'ps', '-a'], capture_output=True, encoding='utf-8')

                # image was build successfully in the past
                if docker_state != 'BUILD FAILED':
                    # foreach existing docker container
                    for output in services.stdout.split('\n')[1:]:
                        # current container belongs to service_id
                        if service_id in output:
                            # container stopped
                            if 'Exited' in output:
                                new_state = 'STOPPED'
                            # container running
                            elif 'Up' in output:
                                new_state = 'RUNNING'
                            # default case - keep current state
                            else:
                                new_state = docker_state

                            # update state in db
                            service_cursor.execute('UPDATE repos SET state = ? WHERE id = ?',
                                                   (new_state, service_id))
                            service_db.commit()

                            # stop, if at least one container of the service stopped
                            if new_state == 'STOPPED':
                                break

                # get current state
                service_cursor.execute('SELECT state FROM repos WHERE id = ?', (service_id,))
                state = service_cursor.fetchone()[0]

                additional_data = {}

                # add additional service information depending on initialization mode
                if mode == 'docker':
                    additional_data['port'] = port

                return jsonify({'id': service_id, 'url': url, 'mode': mode, 'state': state,
                                'docker_root': docker_root} | additional_data), 200
            # service update requested
            elif method == 'POST':
                # start background task to update the service
                subprocess.Popen(['python', 'tasks/update_service.py', service_id])
                return 'Update initiated', 200
            else:
                subprocess.run(['python', 'tasks/delete_repo.py', service_id])
                return f'{service_id} removed', 200
        # service does not exist
        else:
            return f'{service_id} not found', 404


@app.route('/service', methods=['GET', 'POST'])
def hello_world():
    """
    Endpoint to get all services or register new ones

    :return: GET - list of all service ids, POST - service id for a new service
    """
    if request.method == 'GET':
        return jsonify(os.listdir('services')), 200
    else:
        # backend requires JSON data
        if request.content_type != 'application/json':
            return 'Json required!', 400

        try:
            data = request.json

            # load git clone URL and initialization mode
            url = data['url']
            mode = data['mode']
            port = ''

            # mode "docker" requires external port mapping
            if mode == 'docker' and 'port' not in data:
                return 'missing parameters', 400
            elif mode == 'docker':
                # check port mapping format
                if re.match(r'^\d+:\d+$', data['port']):
                    port = data['port']
                # invalid format
                else:
                    return 'invalid port mapping', 400

            # repository relative directory containing the Dockerfile or docker-compose.yml
            docker_root = data['docker_root'] if 'docker_root' in data else '.'

            # requested mode not supported
            if mode not in modes:
                return 'unsupported mode', 400

            try:
                # register new service
                service_id = load_repository(url, mode, port, docker_root)

                # start new service
                subprocess.Popen(['python', 'tasks/start_service.py', service_id, mode, docker_root])
            # service already existing
            except RepositoryAlreadyExistsException:
                return "Service already existing", 400
        # Missing arguments in JSON payload
        except KeyError:
            return 'Missing argument', 400

        return jsonify({'id': service_id, 'state': 'CREATED'}), 200


if __name__ == '__main__':
    app.run()
