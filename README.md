# Microservice Updater

The *Microservice Updater* is a webservice that updates Git repositories
automatically and rebuilds related Docker services. Therefore, a simple
REST-API is provided.

## Installation
Before starting the service create the file `api-keys.json`
in the projects' `services` subdirectory. There you have to add the API keys as a
list of strings. If this file isn't provided, the service will generate a random key.

The service requires a valid SSL certificate. Please edit the `.env` file
to provide the path to your certificate. `SSL_CERT_FILE` is the path to 
your certificate. `SSL_KEY_FILE` is the path to your keyfile of the certificate.

Run `docker-compose up -d` to start the service. You can change the external
port of this service by editing the variable `SERVICE_PORT` in the `.env`
file.

## Automatic registration of services
To register a service at the Microservice updater, use the API endpoint `/service`.
The following example provides the configuration to start a nginx server
for HTTP/HTTPS connections. This service can be used to deploy GitHub projects
automatically with the [docker-service-updater](https://github.com/MindMaster98/docker-service-updater)
GitHub Action.

```json
{
  "API-KEY": "<YOUR-KEY>",
  "mode": "dockerfile",
  "image": "nginx",
  "tag": "alpine",
  "port": "8080:80,8443:443"
}
```

```shell
curl --location 'http://localhost:9000/service' \
--header 'Content-Type: application/json' \
--data '{
    "mode": "dockerfile",
    "image": "nginx",
    "tag": "alpine",
    "port": "8080:80,8444:443",
    "API-KEY": "<YOUR-KEY>"
}'
```

## API endpoints
The API provides the following endpoints:
* `/service`
  * GET-Request: provides a list of all monitored git repositories
  * POST-Request: registers a new docker service to monitor
    ```json
    {
      "API-KEY": "a49bc0...",
      "url": "URL to clone your Git repository (optional)",
      "mode": "supported docker mode",
      "docker_root": "path to Dockerfile or docker-compose.yml (optional)",
      "port": "port mapping (eg. '80:80' or '80:80,443:443') (optional)",
      "image": "dockerhub image (optional)",
      "tag": "dockerhub image tag (optional)",
      "health_path": "HTTP readiness probe path, eg. '/health' (optional)",
      "files": {
        "path_and_file_name": "file content"
      },
      "volumes": [
        "host_path:container_path"
      ]
    }
    ```
    | WARNING: Volumes have to be provided at each update process. <br/>Otherwise, the container doesn't mount the volumes after recreation! |
    |----------------------------------------------------------------------------------------------------------------------------------------|
    The service supports multiple ways to monitor git repositories:
    1. **Git repository with single Dockerfile**: The user has to specify the
    parameters `url` and `port`. The `mode` has to be `docker`.
    2. **Git repository with docker-compose**: `url` has to be specified.
    The needed `mode` is `docker-compose`.
    3. **Pre-build image from Dockerhub**: To use a pre-built image provide
    the parameters `image`, `tag` and `port`. Set `mode` to `dockerfile`.
  
  ### Custom files
  You can change the configuration by providing custom files. Therefore, you need to set the `files`
  parameter in the `POST` request body. To access the data you provide a `KEY-VALUE-PAIR` in the `files`
  value. The `KEY` has to be the relative path from the root of the repository to the file you want to
  insert in the registered service. The `VALUE` has to be the content you want to store inside the service.
  
  #### EXAMPLE
  ```json
  {
    "files": {
      "/config/data.conf": "SETTING1=5"
    }
  }
  ```
  The updater creates the `data.conf` file in the subdirectory `config` and inserts the content `SETTING1=5`.
  
  **Remark**: If custom files have been provided via the `files` parameter, they have to be submitted
  in all `UPDATE` requests, otherwise they get lost.
  
  If the registration is successful, you'll receive the following response:
  ```json
  {
    "id": "$SERVICE_ID", 
    "state": "CREATED"
  }
  ```
  You need `$SERVICE_ID` to access the service state and trigger updates or deletions. As the service
  runs a background task to start the container, you don't get a failure message during the registration.
  Therefore, verify the service state via the API.
* `/service/$SERVICE_ID`
  * `GET`-Request: Get the current state of the registration process. Response:
    ```json
    {
     "id": "$SERVICE_ID",
     "errors": "ERROR_MESSAGE"
    }
    ```
  * `POST`-Request: initializes an update of `$SERVICE_ID`
    ```json
    {
      "API-KEY": "a49bc0...",
      "files": {
        "path_and_file_name": "file content"
      }
    }
    ```
    **Remark**: The updated image is built (or pulled) **while the old container keeps
    serving**; only then are the containers swapped, so the downtime is reduced to the
    swap itself. If the build fails or the new container does not become ready (see
    `health_path` below), the previous image is restored and the service state becomes
    `UPDATE FAILED` — the reason is available via the `GET` request. The container is
    still recreated from scratch, so all data inside the container is lost; use
    `volumes` for persistent data.

    ### Readiness probe (`health_path`)
    If a `health_path` (e.g. `/health`) is configured for a service, an update is only
    considered successful once the new container answers `HTTP 200` on that path
    (probed via the container's bridge IP and the mapped port, timeout 60 s). Without
    a configured `health_path`, the running container state counts as ready. The probe
    requires the updater to reach the managed containers — the provided
    `docker-compose.yml` attaches the updater to the default bridge network for that
    purpose (`network_mode: bridge`).
    
  * `PATCH`-Request: Updates the settings of your service. You can change `port` and `tag` of the Docker image
    as well as the `health_path` readiness probe (an empty string removes the probe).
    The service will be rebuilt after the application of the changes. If you used `volumes` you need to provide them in
    the request body again.
    ```json
    {
      "API-KEY": "a49bc0...",
      "port": "80:80,443:443",
      "tag": "nightly",
      "health_path": "/health"
    }
    ```

  * `DELETE`-Request: stop and delete `$SERVICE_ID`
    ```json
    {
      "API-KEY": "a49bc0..."
    }
    ```
