from flask import Flask, request, jsonify
import os
import sqlite3
from config import modes
from tasks.init_repo import load_repository
from tasks.exceptions import RepositoryAlreadyExistsException

if 'services' not in os.listdir():
    os.mkdir('services')

db = sqlite3.connect('services.db')
cursor = db.cursor()
cursor.execute('CREATE TABLE IF NOT EXISTS repos(url TEXT PRIMARY KEY, mode TEXT, head TEXT, state TEXT)')
cursor.close()
db.commit()
db.close()

app = Flask(__name__)


@app.route('/service', methods=['GET', 'POST'])
def hello_world():
    if request.method == 'GET':
        return jsonify(os.listdir('services')), 200
    else:
        if request.content_type != 'application/json':
            return 'Json required!', 400

        try:
            data = request.json

            url = data['url']
            mode = data['mode']

            docker_root = data['docker_root'] if 'docker_root' in data else '.'

            if mode not in modes:
                return 'unsupported mode', 400

            try:
                service_id = load_repository(url, mode)
            except RepositoryAlreadyExistsException:
                return "Service already existing", 400
        except KeyError:
            return 'Missing argument', 400

        return jsonify({'id': service_id, 'state': 'CREATED'}), 200


if __name__ == '__main__':
    app.run()
