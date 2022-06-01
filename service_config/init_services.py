import requests
import json
from os import environ
import sys

if len(sys.argv) != 2:
    print('Please specify mode "register" or "update"')
    sys.exit(-1)
elif sys.argv[1] not in ['register', 'update']:
    print('Please specify mode "register" or "update"')
    sys.exit(-2)

mode = sys.argv[1]

with open('service_config.json') as f:
    initialization_configuration = json.load(f)

host = environ['secrets.UPDATER-HOST']
api_key = environ['secrets.API-KEY']

d = {}

for i, service in enumerate(initialization_configuration['services']):
    service['API-KEY'] = api_key

    if 'files' in service:
        for file in service['files']:
            with open(f'files/{service["files"][file]}') as f:
                service['files'][file] = f.read()

    if mode == 'register':
        response = requests.post(f'{host}/service', json=service, headers={'Content-Type': 'application/json'},
                                 verify=False)

        if response.ok:
            print(f'service {i} registered successfully.', response.text)

            if 'ids' not in initialization_configuration:
                initialization_configuration['ids'] = []

            initialization_configuration['ids'].append(response.json()['id'])
        else:
            print(f'registration of service {i} failed:', response.text)
    elif mode == 'update':
        if 'ids' in initialization_configuration:
            response = requests.post(f'{host}/service/{initialization_configuration["ids"][i]}',
                                     json=service, headers={'Content-Type': 'application/json'}, verify=False)

            if response.ok:
                print(f'service {i} update initiated successfully.', response.text)
            else:
                print(f'service {i} update failed.', response.text)

    service.pop('API-KEY')

with open('service_config.json', 'w') as f:
    json.dump(initialization_configuration, f, indent=2)
