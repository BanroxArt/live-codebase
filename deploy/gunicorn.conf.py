# gunicorn settings for the container image.
#
# One worker on purpose: the admin listener (metrics, version, kill
# switches) runs inside the worker so it reports and controls the same
# process that serves traffic. Two workers would fight over ADMIN_PORT and
# each would expose only its own counters. Scale with threads here and with
# replicas in the orchestrator.

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = 1
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
# At least DRAIN_SECONDS, so in-flight requests finish after readiness drops.
graceful_timeout = 30
wsgi_app = "livesvc.server:wsgi()"
accesslog = None  # the app writes its own structured access log


def post_worker_init(worker):
    import signal
    import threading

    from livesvc.server import start_admin

    app = worker.wsgi
    start_admin(app)

    # gunicorn installs its SIGTERM handler before this hook runs. Wrap it so
    # readiness fails first and gunicorn only starts its graceful stop after
    # DRAIN_SECONDS, the same order server.serve() uses.
    stop = signal.getsignal(signal.SIGTERM)

    def on_term(signum, frame):
        app.health.drain()
        if callable(stop):
            threading.Timer(app.settings.drain_seconds, stop, (signum, frame)).start()

    signal.signal(signal.SIGTERM, on_term)
