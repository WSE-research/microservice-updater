# Microservice Updater

The *Microservice Updater* is a webservice that updates Git repositories
automatically and rebuilds related Docker services. Therefore, a simple
REST-API is provided.

## Installation
Before starting the service it is necessary to create the file `api-keys.json`
in the project's root directory. There you have to add the API keys as a
list of strings.

The service requires a valid SSL certificate. Please replace the files in
the `ssl` directory.

Run `docker-compose up -d` to start the service. You can change the external
port of this service by editing the variable `SERVICE_PORT` in the `.env`
file.

## Automatic registration of services
In the directory `service_config` is a tool `init_services` that allows
a user to register a list of services at his Microservice Updater. The configuration
has to be done with the `service_config.json`, an example for registering a
nginx service on localhost is provided.

## API endpoints
The API provides the following endpoints:
* `https://$HOST:$SERVICE_PORT/service`
  * GET-Request: provides a list of all monitored git repositories
  * POST-Request: registers a new docker service to monitor
    ```json
    {
      "API-KEY": "a49bc0...",
      "url": "git clone URL (optional)",
      "mode": "supported docker mode",
      "docker_root": "path to Dockerfile or docker-compose.yml (optional)",
      "port": "port mapping (optional)",
      "image": "dockerhub image (optional)",
      "tag": "dockerhub image tag (optional)",
      "files": {
        "path_and_file_name": "file content"
      }
    }
    ```
    The service supports multiple ways to monitor git repositories:
    1. **Git repository with single Dockerfile**: The user has to specify the
    parameters `url` and `port`. The `mode` has to be `docker`.
    2. **Git repository with docker-compose**: `url` has to be specified.
    The needed `mode` is `docker-compose`.
    3. **Pre-build image from Dockerhub**: To use a pre-built image provide
    the parameters `image`, `tag` and `port`. Set `mode` to `dockerfile`.
  
  **Remark**: If custom files have been provided via the `files` parameter, they have to be submitted
  in all `UPDATE` requests, otherwise they get lost.

* `https://$HOST:$SERVICE_PORT/service/$SERVICE_ID`
  * GET-Request: Get the current state of the registration process. Response:
    ```json
    {
     "id": "$SERVICE_ID",
     "errors": "ERROR_MESSAGE"
    }
    ```
  * POST-Request: initializes an update of `$SERVICE_ID`
    ```json
    {
      "API-KEY": "a49bc0...",
      "files": {
        "path_and_file_name": "file content"
      }
    }
    ```
  * DELETE-Request: stop and delete `$SERVICE_ID`
    ```json
    {
      "API-KEY": "a49bc0..."
    }
    ```
