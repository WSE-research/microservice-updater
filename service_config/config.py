import re
from sqlite3 import Cursor


class PortAlreadyUsedException(Exception):
    def __init__(self, port):
        self.message = f'Port {port} already used'


class InvalidPortMappingException(Exception):
    def __init__(self):
        self.message = 'Invalid port mapping provided'


modes = [
    'docker',
    'docker-compose',
    'dockerfile'
]


def regexp(expr, item):
    reg = re.compile(expr)
    return re.match(reg, item) is not None


def check_ports(ports: str, cursor: Cursor):
    for port in ports.split(','):
        if not re.match(r'^\d+:\d+$', port):
            raise InvalidPortMappingException()

        external = port.split(':')[0]
        # the external port has to start a mapping (beginning of the string or
        # right after a comma), otherwise e.g. 80 would collide with 8080:80
        cursor.execute("select port from repos WHERE port REGEXP ?", (f'(.*,)?{external}:.*',))
        existing_mappings = cursor.fetchall()

        if len(existing_mappings):
            raise PortAlreadyUsedException(external)

    return True
