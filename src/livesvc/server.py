"""Run the service with the standard library server and drain on SIGTERM.

Shutdown order: SIGTERM arrives, readiness starts failing, we wait
``DRAIN_SECONDS`` for the load balancer to notice and in-flight requests to
finish, then stop. Skipping the wait is how deploys drop requests.

Two listeners: the public API on ``HOST:PORT`` and the admin surface
(metrics, version, flag controls) on ``ADMIN_HOST:ADMIN_PORT``, which
defaults to loopback. Publish only the public port.

``wsgiref`` is fine for a reference and for tests. In a container, run the
same ``App`` under gunicorn with ``deploy/gunicorn.conf.py``, which starts the
admin listener inside the worker (see ``start_admin``).
"""

from __future__ import annotations

import logging
import signal
import threading
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
from wsgiref.types import WSGIApplication

from livesvc import config, logs
from livesvc.app import App, create_app

log = logging.getLogger("livesvc.server")


class _ThreadingServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass  # the app writes its own structured access log


def wsgi() -> App:
    settings = config.load()
    logs.configure(settings.log_level, settings.service, settings.env, settings.release)
    return create_app(settings)


def _server(host: str, port: int, app: WSGIApplication) -> WSGIServer:
    return make_server(
        host,
        port,
        app,
        server_class=_ThreadingServer,
        handler_class=_QuietHandler,
    )


def start_admin(app: App) -> WSGIServer:
    """Serve ``app.admin`` on the admin address from a background thread.

    Under gunicorn this runs once per worker, so keep ``workers = 1`` and
    scale with threads or replicas; two workers would race for the port.
    The admin listener shares the worker's ``App``, so metrics and kill
    switches are the ones the public listener actually uses.
    """
    s = app.settings
    httpd = _server(s.admin_host, s.admin_port, app.admin)
    threading.Thread(target=httpd.serve_forever, name="admin-http", daemon=True).start()
    log.info("admin listening", extra={"host": s.admin_host, "port": s.admin_port})
    return httpd


def serve() -> None:
    settings = config.load()
    logs.configure(settings.log_level, settings.service, settings.env, settings.release)
    app = create_app(settings)
    httpd = _server(settings.host, settings.port, app)
    admin = start_admin(app)
    stop = threading.Event()

    def on_term(signum: int, _frame: object) -> None:
        log.info("draining", extra={"signal": signum, "seconds": settings.drain_seconds})
        app.health.drain()
        threading.Timer(settings.drain_seconds, stop.set).start()

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    log.info(
        "listening",
        extra={"host": settings.host, "port": settings.port, "release": settings.release},
    )
    stop.wait()
    httpd.shutdown()
    admin.shutdown()
    app.health.close()
    app.store.close()
    log.info("stopped")


if __name__ == "__main__":
    serve()
