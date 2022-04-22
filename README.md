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
      "tag": "dockerhub image tag (optional)"
    }
    ```
    The service supports multiple ways to monitor git repositories:
    1. **Git repository with single Dockerfile**: The user has to specify the
    parameters `url` and `port`. The `mode` has to be `docker`.
    2. **Git repository with docker-compose**: `url` has to be specified.
    The needed `mode` is `docker-compose`.
    3. **Pre-build image from Dockerhub**: To use a pre-build image provide
    the parameters `image`, `tag` and `port`. Set `mode` to `dockerfile`

* `https://$HOST:$SERVICE_PORT/service/$SERVICE_ID`
  * POST-Request: initializes an update of `$SERVICE_ID`
    ```json
    {
      "API-KEY": "a49bc0..."
    }
    ```
  * DELETE-Request: stop and delete `$SERVICE_ID`
    ```json
    {
      "API-KEY": "a49bc0..."
    }
    ```
