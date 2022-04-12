import sys
import shutil
from tasks.update_service import stop_service
import sqlite3
import os

service_id = sys.argv[1]
base_dir = os.getcwd()

with sqlite3.connect('services.db') as db:
    cursor = db.cursor()

    # check if service exists
    cursor.execute('SELECT mode, docker_root FROM repos WHERE id = ?', (service_id,))

    if output := cursor.fetchone():
        mode = output[0]
        root = output[1]

        os.chdir(f'services/{service_id}/{root}')

        # stop container and delete git repository
        stop_service(mode, service_id)
        shutil.rmtree(f'{base_dir}/services/{service_id}')
        cursor.execute('DELETE FROM repos WHERE id = ?', (service_id,))
        db.commit()

        os.chdir(base_dir)
