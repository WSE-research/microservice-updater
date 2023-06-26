FROM docker:dind
ENV PYTHONUNBUFFERED=1
RUN apk add --update --no-cache python3 && ln -sf python3 /usr/bin/python
RUN apk add --no-cache git libffi-dev openssl-dev gcc libc-dev make python3-dev
RUN python3 -m ensurepip
RUN pip3 install --no-cache --upgrade pip setuptools
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"
COPY . .
RUN pip3 install -r requirements.txt
RUN pip3 install gunicorn docker-compose
CMD gunicorn -w 8 -b 0.0.0.0:9000 --certfile=ssl/server.crt --keyfile=ssl/server.key app:app