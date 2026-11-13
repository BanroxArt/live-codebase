"""The WSGI application.

Plain WSGI on purpose: no framework to learn, nothing hidden, and every
middleware concern (request ids, tracing, metrics, access logs, error
handling) is in ``__call__`` where you can read it top to bottom. Swapping in
FastAPI or Flask later changes the routing, not the ideas.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from livesvc import logs
from livesvc.config import Settings
from livesvc.data import PurposeError, Store
from livesvc.flags import Flags
from livesvc.health import Health
from livesvc.metrics import Registry
from livesvc.tracing import Tracer, make_tracer, parse_traceparent

log = logging.getLogger("livesvc.http")
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
STATUS_TEXT = {
    200: "OK",
    202: "Accepted",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


@dataclass(slots=True)
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    params: dict[str, str]
    request_id: str

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


@dataclass(slots=True)
class Response:
    status: int
    body: Any
    headers: tuple[tuple[str, str], ...] = ()
    content_type: str = "application/json"


Handler = Callable[[Request], Response]


def _query(qs: str) -> dict[str, str]:
    from urllib.parse import parse_qsl

    return dict(parse_qsl(qs, keep_blank_values=True))


def _headers(environ: dict[str, Any]) -> dict[str, str]:
    out = {}
    for k, v in environ.items():
        if k.startswith("HTTP_"):
            out[k[5:].replace("_", "-").lower()] = v
    if "CONTENT_TYPE" in environ:
        out["content-type"] = environ["CONTENT_TYPE"]
    return out


class App:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        flags: Flags,
        tracer: Tracer | None = None,
        metrics: Registry | None = None,
        health: Health | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.flags = flags
        self.tracer = tracer or make_tracer(settings.trace_sample_rate)
        self.metrics = metrics or Registry()
        self.health = health or Health()
        self.metrics.counter("http_requests_total", "Requests by route, method and status.")
        self.metrics.histogram("http_request_duration_seconds", "Request latency by route.")
        self.metrics.counter("flag_evaluations_total", "Flag evaluations by flag and result.")
        self.health.register("database", store.ping)
        self.health.register("flags", self._flags_loaded)
        self._routes: list[tuple[str, re.Pattern[str], str, Handler]] = []
        self.route("GET", "/healthz", self.healthz)
        self.route("GET", "/readyz", self.readyz)
        self.route("GET", "/metrics", self.metrics_page)
        self.route("GET", "/version", self.version)
        self.route("GET", "/api/users/{user_id}/profile", self.profile)
        self.route("GET", "/api/recommendations", self.recommendations)
        self.route("GET", "/admin/flags", self.admin_flags)
        self.route("POST", "/admin/flags/{name}/kill", self.admin_kill)
        self.route("POST", "/admin/flags/{name}/revive", self.admin_revive)

    def _flags_loaded(self) -> None:
        if not self.flags.loaded:
            raise RuntimeError("flags not loaded")

    def route(self, method: str, template: str, handler: Handler) -> None:
        pattern = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", template) + "$")
        self._routes.append((method, pattern, template, handler))

    def _match(self, method: str, path: str) -> tuple[str, Handler | None, dict[str, str], bool]:
        path_matched = False
        for m, pattern, template, handler in self._routes:
            match = pattern.match(path)
            if match:
                path_matched = True
                if m == method:
                    return template, handler, match.groupdict(), True
        return ("unmatched" if not path_matched else path), None, {}, path_matched

    # WSGI entry point
    def __call__(self, environ: dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        t0 = time.perf_counter()
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/") or "/"
        headers = _headers(environ)
        incoming = headers.get("x-request-id", "")
        rid = incoming if REQUEST_ID.match(incoming) else uuid.uuid4().hex
        rid_token = logs.request_id.set(rid)
        template, handler, params, path_ok = self._match(method, path)
        parent = parse_traceparent(headers.get("traceparent"))
        try:
            with self.tracer.span(f"{method} {template}", parent=parent, method=method) as span:
                if handler is None:
                    resp = Response(
                        405 if path_ok else 404,
                        {"error": "method not allowed" if path_ok else "not found"},
                    )
                else:
                    req = Request(
                        method, path, _query(environ.get("QUERY_STRING", "")), headers, params, rid
                    )
                    try:
                        resp = handler(req)
                    except Exception:
                        log.exception("unhandled error", extra={"route": template})
                        resp = Response(500, {"error": "internal error", "request_id": rid})
                span.set("http.status_code", resp.status)
                traceparent = span.context.traceparent()
                # Logged inside the span so the access log line carries the trace id.
                elapsed = time.perf_counter() - t0
                labels = {"route": template, "method": method, "status": str(resp.status)}
                self.metrics.inc("http_requests_total", **labels)
                self.metrics.observe("http_request_duration_seconds", elapsed, route=template)
                if template not in ("/healthz", "/readyz", "/metrics"):
                    log.info(
                        "request",
                        extra={
                            "route": template,
                            "method": method,
                            "status": resp.status,
                            "ms": round(elapsed * 1000, 2),
                        },
                    )
            if resp.content_type == "application/json":
                body = json.dumps(resp.body, separators=(",", ":")).encode()
            else:
                body = str(resp.body).encode()
            hdrs = [
                ("Content-Type", resp.content_type),
                ("Content-Length", str(len(body))),
                ("X-Request-Id", rid),
                ("traceparent", traceparent),
                *resp.headers,
            ]
            start_response(f"{resp.status} {STATUS_TEXT.get(resp.status, '')}".strip(), hdrs)
            return [body]
        finally:
            logs.request_id.reset(rid_token)

    # Handlers
    def healthz(self, req: Request) -> Response:
        return Response(200, {"status": "ok", "uptime_s": round(self.health.uptime(), 1)})

    def readyz(self, req: Request) -> Response:
        ok, results = self.health.ready()
        body = {
            "ready": ok,
            "checks": [{"name": r.name, "ok": r.ok, "detail": r.detail} for r in results],
        }
        return Response(200 if ok else 503, body)

    def metrics_page(self, req: Request) -> Response:
        return Response(200, self.metrics.render(), content_type="text/plain; version=0.0.4")

    def version(self, req: Request) -> Response:
        return Response(
            200,
            {
                "service": self.settings.service,
                "env": self.settings.env,
                "release": self.settings.release,
            },
        )

    def profile(self, req: Request) -> Response:
        purpose = req.query.get("purpose", "service")
        fields = [f for f in req.query.get("fields", "id,display_name,plan").split(",") if f]
        actor = req.header("x-actor") or "anonymous"
        with self.tracer.span("db.read", purpose=purpose):
            try:
                result = self.store.read(
                    req.params["user_id"], purpose, fields, actor=actor, request_id=req.request_id
                )
            except PurposeError as exc:
                return Response(400, {"error": str(exc)})
        if result is None:
            return Response(404, {"error": "not found"})
        if result.decision.startswith("denied"):
            return Response(403, {"error": result.decision, "withheld": list(result.withheld)})
        return Response(200, {"data": result.data, "withheld": list(result.withheld)})

    def recommendations(self, req: Request) -> Response:
        user = req.header("x-user-id")
        ev = self.flags.evaluate("new_recs", user)
        self.metrics.inc("flag_evaluations_total", flag="new_recs", result=str(ev.enabled).lower())
        if ev.enabled:
            items = ["guide-observability", "guide-flags", "guide-rollback"]
            variant = "new"
        else:
            items = ["guide-getting-started"]
            variant = "control"
        return Response(
            200, {"variant": variant, "items": items}, headers=(("X-Flag-Reason", ev.reason),)
        )

    def _authorized(self, req: Request) -> bool:
        token = self.settings.admin_token
        if not token:
            return False  # admin endpoints are off unless a token is configured
        got = req.header("authorization") or ""
        return got.startswith("Bearer ") and hmac.compare_digest(got[7:].encode(), token.encode())

    def admin_flags(self, req: Request) -> Response:
        if not self._authorized(req):
            return Response(401, {"error": "unauthorized"})
        return Response(200, self.flags.snapshot())

    def admin_kill(self, req: Request) -> Response:
        if not self._authorized(req):
            return Response(401, {"error": "unauthorized"})
        self.flags.kill(req.params["name"])
        return Response(202, {"killed": sorted(self.flags.killed())})

    def admin_revive(self, req: Request) -> Response:
        if not self._authorized(req):
            return Response(401, {"error": "unauthorized"})
        self.flags.revive(req.params["name"])
        return Response(202, {"killed": sorted(self.flags.killed())})


def create_app(settings: Settings) -> App:
    store = Store(settings.database_path, settings.env)
    flags = Flags(settings.env, kill_switches=settings.kill_switches, path=settings.flags_path)
    return App(settings, store, flags)
