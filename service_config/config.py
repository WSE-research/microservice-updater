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


def check_ports(ports: str, cursor: Cursor, service_id: str | None = None):
    """
    Validate a port mapping and make sure no other service uses its external ports

    :param ports: mapping like '8080:80,8443:443'
    :param cursor: cursor of the service database
    :param service_id: service the mapping is meant for; its own stored
        mapping is not a conflict (e.g. a PATCH adding 80:80 to 443:443)
    :raises InvalidPortMappingException, PortAlreadyUsedException
    :return: True if the mapping is valid and free
    """
    for port in ports.split(','):
        if not re.match(r'^\d+:\d+$', port):
            raise InvalidPortMappingException()

        external = port.split(':')[0]
        # the external port has to start a mapping (beginning of the string or
        # right after a comma), otherwise e.g. 80 would collide with 8080:80
        cursor.execute("select port from repos WHERE port REGEXP ? AND id IS NOT ?",
                       (f'(.*,)?{external}:.*', service_id))
        existing_mappings = cursor.fetchall()

        if len(existing_mappings):
            raise PortAlreadyUsedException(external)

    return True
