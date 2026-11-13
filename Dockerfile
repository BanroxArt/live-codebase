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
ARG RELEASE=unknown
ENV RELEASE=$RELEASE APP_ENV=prod FLAGS_PATH=/etc/livesvc/flags.json PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
# gunicorn's graceful timeout should be at least DRAIN_SECONDS.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--graceful-timeout", "30", "livesvc.server:wsgi()"]
