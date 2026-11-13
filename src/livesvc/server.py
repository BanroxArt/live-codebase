"""Run the service with the standard library server and drain on SIGTERM.

Shutdown order: SIGTERM arrives, readiness starts failing, we wait
``DRAIN_SECONDS`` for the load balancer to notice and in-flight requests to
finish, then stop. Skipping the wait is how deploys drop requests.

``wsgiref`` is fine for a reference and for tests. In a container, run the
same ``App`` under gunicorn or uWSGI: ``gunicorn 'livesvc.server:wsgi()'``.
"""

from __future__ import annotations

import logging
import signal
import threading
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

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


def serve() -> None:
    settings = config.load()
    logs.configure(settings.log_level, settings.service, settings.env, settings.release)
    app = create_app(settings)
    httpd = make_server(
        settings.host,
        settings.port,
        app,
        server_class=_ThreadingServer,
        handler_class=_QuietHandler,
    )
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
    app.store.close()
    log.info("stopped")


if __name__ == "__main__":
    serve()
