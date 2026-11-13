FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-deps --wheel-dir /wheels .

FROM python:3.12-slim
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl gunicorn==23.0.0 && rm -rf /wheels
COPY deploy/flags.json /etc/livesvc/flags.json
COPY deploy/gunicorn.conf.py /etc/livesvc/gunicorn.conf.py
ARG RELEASE=unknown
# No APP_ENV here: the image starts as dev. A prod deploy sets APP_ENV=prod
# together with AUTH_SIGNING_KEY and ADMIN_TOKEN, and config refuses prod
# without the signing key.
ENV RELEASE=$RELEASE FLAGS_PATH=/etc/livesvc/flags.json PYTHONUNBUFFERED=1
USER 10001
# Public API only. The admin listener (ADMIN_PORT, default 9090) binds to
# loopback unless ADMIN_HOST is set; expose it to the cluster, not the edge.
EXPOSE 8080
CMD ["gunicorn", "--config", "/etc/livesvc/gunicorn.conf.py"]
