import re

modes = [
    'docker',
    'docker-compose',
    'dockerfile'
]


def check_ports(port: str):
    return all([re.match(r'^\d+:\d+$', port_mapping) for port_mapping in port.split(',')])
