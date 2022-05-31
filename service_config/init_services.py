import requests
import json

with open('service_config.json') as f:
    initialization_configuration = json.load(f)

host = initialization_configuration['host']
api_key = initialization_configuration['API-KEY']

for i, service in enumerate(initialization_configuration['services']):
    service['API-KEY'] = api_key

    for file in service['files']:
        with open(f'files/{service["files"][file]}') as f:
            service['files'][file] = f.read()

    response = requests.post(f'{host}/service', json=service, headers={'Content-Type': 'application/json'})

    if response.ok:
        print(f'service {i} registered successfully.', response.text)
    else:
        print(f'registration of service {i} failed:', response.text)
