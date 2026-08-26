"""
Reverse-proxy support for zero-downtime rollouts (issue #149, phase 2).

With the optional proxy profile enabled, the registered external port is
owned by an nginx stream proxy instead of the service container. A rollout
then starts the new version *next to* the old one (blue-green), waits until
it is ready and only afterwards switches the proxy route, so no client
request is ever refused.

The managed containers no longer publish the registered port; they publish
their internal ports on an ephemeral loopback port instead, which the proxy
forwards to. nginx runs with host networking, so the updater can add and
remove listeners for arbitrary ports with a config reload -- the proxy
container never has to be recreated when a service is registered or its
port is patched.

Proxying happens on layer 4 (nginx `stream`), so the forwarded protocol
does not matter: HTTP, TLS-terminating services and plain TCP work alike.
"""
import logging
import os

from docker.errors import APIError, NotFound

# the two container name suffixes a proxied service alternates between
COLORS = ('blue', 'green')

DEFAULT_PROXY_CONTAINER = 'microservice-proxy'
DEFAULT_BACKEND_HOST = '127.0.0.1'
DEFAULT_DRAIN_SECONDS = 5

# the rollout chdir's into the service directory, so relative configuration
# has to be resolved against the working directory the updater started in
_BASE_DIR = os.getcwd()

SERVER_TEMPLATE = """
# {external} -> {service_id} ({internal})
server {{
    listen {external};
    proxy_pass {host}:{host_port};
}}
"""


class ProxyUnavailableException(Exception):
    """Raised if the reverse proxy cannot be reached or reloaded"""
    def __init__(self, message):
        super().__init__(message)
        self.message = message


class ProxyConfigurationException(Exception):
    """Raised if no proxy configuration can be derived for a service"""
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def proxy_enabled():
    """
    Check whether the reverse-proxy deployment profile is active

    :return: True, if rollouts should use the blue-green proxy flow
    """
    return os.environ.get('PROXY_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on')


def proxy_container_name():
    """:return: name of the nginx container to reload"""
    return os.environ.get('PROXY_CONTAINER') or DEFAULT_PROXY_CONTAINER


def proxy_conf_dir():
    """:return: absolute path of the directory holding the generated configs"""
    configured = os.environ.get('PROXY_CONF_DIR')

    if not configured:
        configured = os.path.join('proxy', 'conf.d')

    if os.path.isabs(configured):
        return configured

    return os.path.join(_BASE_DIR, configured)


def backend_host():
    """:return: host address the managed containers publish their ports on"""
    return os.environ.get('PROXY_BACKEND_HOST') or DEFAULT_BACKEND_HOST


def drain_seconds():
    """:return: seconds the replaced container keeps running after the switch"""
    try:
        return float(os.environ.get('PROXY_DRAIN_SECONDS', DEFAULT_DRAIN_SECONDS))
    except ValueError:
        return DEFAULT_DRAIN_SECONDS


def container_name(service_id: str, color: str):
    """:return: container name of a service's blue or green version"""
    return f'{service_id}-{color}'


def next_color(color):
    """:return: the color a rollout has to deploy to, given the active one"""
    return 'green' if color == 'blue' else 'blue'


def active_color(docker_client, service_id: str):
    """
    Determine which color currently serves a service

    :return: 'blue', 'green' or None if no colored container exists
    """
    for color in COLORS:
        try:
            docker_client.containers.get(container_name(service_id, color))
            return color
        except NotFound:
            continue

    return None


def find_container(docker_client, service_id: str):
    """
    Look up a service's container, no matter which deployment style created it

    :raises NotFound
    :return: the container serving the service
    """
    for name in [service_id] + [container_name(service_id, color) for color in COLORS]:
        try:
            return docker_client.containers.get(name)
        except NotFound:
            continue

    raise NotFound(f'no container found for service {service_id}')


def remove_container(docker_client, name: str):
    """
    Stop and remove a container if it exists

    :return: True, if a container was removed
    """
    try:
        container = docker_client.containers.get(name)
    except NotFound:
        return False

    try:
        container.stop()
        container.remove()
    except APIError as e:
        logging.error(f'Removing container {name} failed: {e}')
        return False

    return True


def publish_spec(port: str):
    """
    Build the docker port bindings for a proxied container

    The registered external port belongs to the proxy, so the container only
    publishes its internal ports on an ephemeral loopback port.

    :param port: mapping like '8080:80,8443:443'
    :return: docker-py ports argument
    """
    host = backend_host()
    return {mapping.split(':')[1]: (host, None) for mapping in port.split(',')}


def published_ports(container):
    """
    Read the ephemeral host ports docker assigned to a container

    :return: dict of (internal port, host port) pairs
    """
    container.reload()

    bindings = (container.attrs.get('NetworkSettings') or {}).get('Ports') or {}
    ports = {}

    for spec, binding in bindings.items():
        if binding:
            ports[spec.split('/')[0]] = binding[0]['HostPort']

    return ports


def config_path(service_id: str):
    """:return: path of the generated nginx config of a service"""
    return os.path.join(proxy_conf_dir(), f'{service_id}.conf')


def render_config(service_id: str, port: str, host_ports: dict):
    """
    Render the nginx stream listeners of a service

    :param port: registered mapping like '8080:80,8443:443'
    :param host_ports: (internal port, host port) pairs of the target container
    :raises ProxyConfigurationException
    :return: the nginx configuration as a string
    """
    host = backend_host()
    blocks = [f'# managed by the microservice-updater - service: {service_id}']

    for mapping in port.split(','):
        external, internal = mapping.split(':')

        if internal not in host_ports:
            raise ProxyConfigurationException(
                f'container port {internal} of {service_id} is not published')

        blocks.append(SERVER_TEMPLATE.format(service_id=service_id, external=external,
                                             internal=internal, host=host,
                                             host_port=host_ports[internal]))

    return '\n'.join(blocks) + '\n'


def read_config(service_id: str):
    """:return: the current config of a service or None if there is none"""
    try:
        with open(config_path(service_id)) as f:
            return f.read()
    except FileNotFoundError:
        return None


def write_config(service_id: str, content: str):
    """Store the nginx config of a service"""
    path = config_path(service_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, 'w') as f:
        f.write(content)


def remove_config(service_id: str):
    """
    Delete the nginx config of a service

    :return: True, if a config was removed
    """
    try:
        os.remove(config_path(service_id))
        return True
    except FileNotFoundError:
        return False


def reload_proxy(docker_client):
    """
    Apply the generated configuration by reloading nginx

    An nginx reload starts new workers for the new configuration while the
    old workers finish their established connections, so the switch does not
    interrupt clients.

    :raises ProxyUnavailableException
    """
    name = proxy_container_name()

    try:
        container = docker_client.containers.get(name)
    except NotFound:
        raise ProxyUnavailableException(f'proxy container "{name}" not found')

    try:
        exit_code, output = container.exec_run(['nginx', '-s', 'reload'])
    except APIError as e:
        raise ProxyUnavailableException(e.explanation or str(e))

    if exit_code:
        message = output.decode() if isinstance(output, bytes) else str(output)
        raise ProxyUnavailableException(f'nginx reload failed: {message.strip()}')


def switch_route(docker_client, service_id: str, port: str, host_ports: dict):
    """
    Point the proxy at a new container and apply the change atomically

    If the reload fails, the previous configuration is restored, so a broken
    rollout never leaves the service without a route.

    :raises ProxyUnavailableException, ProxyConfigurationException
    """
    content = render_config(service_id, port, host_ports)
    previous = read_config(service_id)

    write_config(service_id, content)

    try:
        reload_proxy(docker_client)
    except ProxyUnavailableException:
        logging.error(f'Restoring the previous proxy route of {service_id}...')

        if previous is None:
            remove_config(service_id)
        else:
            write_config(service_id, previous)

        try:
            reload_proxy(docker_client)
        except ProxyUnavailableException as restore_error:
            logging.error(f'Restoring the proxy route failed: {restore_error.message}')

        raise


def remove_service(docker_client, service_id: str):
    """
    Drop a service's route and all of its colored containers

    :return: True, if a container was removed
    """
    removed = False

    for color in COLORS:
        removed = remove_container(docker_client, container_name(service_id, color)) or removed

    if remove_config(service_id):
        try:
            reload_proxy(docker_client)
        except ProxyUnavailableException as e:
            logging.error(f'Dropping the proxy route of {service_id} failed: {e.message}')

    return removed
