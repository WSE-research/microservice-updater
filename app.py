from flask import Flask, request, jsonify
import os
import sqlite3
from service_config.config import modes, check_ports
from tasks.init_repo import load_repository
from tasks.exceptions import *
import subprocess
import json
from git import GitCommandError
from base64 import b64encode
import logging

# create directory for service repositories
if 'services' not in os.listdir():
    os.mkdir('services')

if 'api-keys.json' not in os.listdir():
    logging.warning('No API keys provided. Generating random key...')

    keys = [b64encode(os.urandom(16)).decode()]
    with open('api-keys.json', 'w') as api_keys:
        json.dump(keys, api_keys)
else:
    with open('api-keys.json') as file:
        keys = json.load(file)

# initialize database for service management
with sqlite3.connect('services/services.db') as db:
    cursor = db.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS repos(id TEXT PRIMARY KEY, url TEXT, mode TEXT,'
                   'state TEXT, port TEXT, docker_root TEXT, image TEXT, tag TEXT)')
    cursor.close()
    db.commit()

app = Flask(__name__)


def check_volumes(volumes):
    if type(volumes) is not list:
        raise InvalidVolumeMappingException('Volume mapping list expected')
    if any([len(volume.split(':')) != 2 for volume in volumes]):
        raise InvalidVolumeMappingException('Invalid volume mapping format provided')


def valid(docker_mode: str):
    """
    Check proper configuration for selected docker mode

    :param docker_mode: docker mode name
    :return: True, if all needed parameters are provided for the given docker_mode, otherwise False
    """
    if docker_mode == 'docker':
        return 'port' in request.json
    elif docker_mode == 'dockerfile':
        return 'port' in request.json and 'image' in request.json and 'tag' in request.json
    else:
        return True


@app.route('/service/<string:service_id>', methods=['POST', 'DELETE', 'GET'])
def update_service(service_id: str):
    """
    Accesses a service

    :param service_id: id of the requested service
    :return: GET - information about the service, POST - initialize update, DELETE - remove a service
    """
    if request.method != 'GET' and request.content_type != 'application/json':
        logging.warning('Missing JSON payload')
        return 'JSON payload expected', 400

    if request.method != 'GET' and ('API-KEY' not in request.json or request.json['API-KEY'] not in keys):
        logging.warning('Invalid API key provided or missing')
        return 'valid API-KEY required', 400

    with sqlite3.connect('services/services.db') as service_db:
        # search service
        service_cursor = service_db.cursor()
        service_cursor.execute('SELECT * FROM repos WHERE id = ?', (service_id,))

        # service exists
        if service_data := service_cursor.fetchone():
            _, url, mode, docker_state, port, docker_root, image, tag = service_data

            # service update requested
            if (method := request.method) == 'POST':
                logging.info(f'Updating {service_id}...')

                payload = request.json

                files = payload['files'] if 'files' in payload else {}
                volumes = payload['volumes'] if 'volumes' in payload else []

                try:
                    check_volumes(volumes)

                    # start background task to update the service
                    subprocess.Popen(['python', 'tasks/update_service.py', service_id, json.dumps(files),
                                      json.dumps(volumes)])
                    return 'Update initiated', 200
                except InvalidVolumeMappingException as e:
                    logging.error(f'Invalid volume mapping provided: {e}')
                    return e.message, 400
            elif method == 'DELETE':
                delete_process = subprocess.run(['python', 'tasks/delete_repo.py', service_id])

                if delete_process.returncode == 0:
                    return f'{service_id} removed', 200
                else:
                    logging.error(f'deletion of {service_id} failed')
                    return 'deletion not completed', 500
            else:
                logging.info(f'Fetching state of {service_id}...')
                if os.path.exists(f'services/{service_id}/error.txt'):
                    with open(f'services/{service_id}/error.txt') as f:
                        return jsonify({
                            'id': service_id,
                            'state': docker_state,
                            'errors': f.read()
                        }), 200
                else:
                    return jsonify({'id': service_id, 'state': docker_state, 'errors': None}), 200
        # service does not exist
        else:
            logging.warning(f'Service {service_id} not found.')
            return f'{service_id} not found', 404


@app.route('/service', methods=['GET', 'POST'])
def manage_services():
    """
    Endpoint to get all services or register new ones

    :return: GET - list of all service ids, POST - service id for a new service
    """
    if request.method == 'GET':
        logging.info('Requesting services...')
        output = os.listdir('services')
        output.remove('services.db')
        return jsonify(output), 200
    else:
        # backend requires JSON data
        if request.content_type != 'application/json':
            return 'Json required!', 400

        try:
            data = request.json

            if 'API-KEY' not in data or data['API-KEY'] not in keys:
                logging.warning('API key invalid or missing!')
                return 'valid API-KEY required', 400

            # load git clone URL and initialization mode
            url = data['url'] if 'url' in data else ''
            files = data['files'] if 'files' in data else {}
            volumes = data['volumes'] if 'volumes' in data else []
            mode = data['mode']
            port = ''
            image = ''
            tag = ''

            check_volumes(volumes)

            # mode "docker" requires external port mapping
            if mode in ['docker', 'dockerfile'] and not valid(mode):
                logging.warning(f'Invalid configuration for mode {mode}')
                return 'missing parameters', 400
            elif mode in ['docker', 'dockerfile']:
                image = data['image'] if mode == 'dockerfile' else ''
                tag = data['tag'] if mode == 'dockerfile' else ''

                # check port mapping format
                if check_ports(data['port']):
                    port = data['port']
                # invalid format
                else:
                    logging.warning('Invalid port mapping!')
                    return 'invalid port mapping', 400

            # repository relative directory containing the Dockerfile or docker-compose.yml
            docker_root = data['docker_root'] if 'docker_root' in data else '.'

            # requested mode not supported
            if mode not in modes:
                logging.warning(f'Unsupported mode selected: {mode}')
                return 'unsupported mode', 400

            try:
                # register new service
                service_id = load_repository(url, mode, port, docker_root, image, tag, files)

                # start new service
                subprocess.Popen(['python', 'tasks/start_service.py', service_id, mode, docker_root, port, image, tag,
                                  json.dumps(volumes)])
            # service already existing
            except RepositoryAlreadyExistsException:
                logging.error(f'service already exists!')
                return 'Service already existing', 400
            # Git clone failed
            except GitCommandError as e:
                logging.error('Cloning repository failed!')
                return jsonify({'error': e.stderr}), 400
        # Missing arguments in JSON payload
        except KeyError as e:
            logging.error(f'Needed parameters not provided! {e}')
            return 'Missing argument', 400
        except InvalidVolumeMappingException as e:
            logging.error(f'Invalid volume mapping provided: {e}')
            return e.message, 400

        return jsonify({'id': service_id, 'state': 'CREATED'}), 200


if __name__ == '__main__':
    app.run()
