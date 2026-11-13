from __future__ import annotations

import io
import json
from typing import Any

import pytest

from livesvc.app import App
from livesvc.auth import mint
from livesvc.config import Settings
from livesvc.data import Store
from livesvc.flags import Flags, parse_flags

ADMIN = "t" * 40
SIGNING = "k" * 40


def bearer(subject: str, role: str, ttl_s: float = 300) -> dict[str, str]:
    return {"authorization": f"Bearer {mint(SIGNING, subject, role, ttl_s)}"}


class Client:
    def __init__(self, app: App, admin: bool = False) -> None:
        self.app = app
        self.wsgi = app.admin if admin else app

    def request(
        self, method: str, path: str, headers: dict[str, str] | None = None
    ) -> tuple[int, dict, Any]:
        path, _, qs = path.partition("?")
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": qs,
            "wsgi.input": io.BytesIO(b""),
        }
        for k, v in (headers or {}).items():
            environ["HTTP_" + k.upper().replace("-", "_")] = v
        captured: dict[str, Any] = {}

        def start_response(status: str, hdrs: list[tuple[str, str]]) -> None:
            captured["status"] = int(status.split()[0])
            captured["headers"] = {k.lower(): v for k, v in hdrs}

        body = b"".join(self.wsgi(environ, start_response))
        ctype = captured["headers"]["content-type"]
        parsed = json.loads(body) if ctype == "application/json" else body.decode()
        return captured["status"], captured["headers"], parsed

    def get(self, path: str, **headers: str):
        return self.request("GET", path, {k.replace("_", "-"): v for k, v in headers.items()})

    def post(self, path: str, **headers: str):
        return self.request("POST", path, {k.replace("_", "-"): v for k, v in headers.items()})


@pytest.fixture
def store():
    s = Store(":memory:", "test")
    s.add_user("u1", "Dana", "dana@example.com", plan="pro")
    s.add_user("u2", "Eli", "eli@example.com")
    s.grant("u2", "analytics", "v3")
    yield s
    s.close()


@pytest.fixture
def flags():
    return Flags(
        "test",
        parse_flags({"new_recs": {"owner": "growth", "rollout": {"test": 50}, "allow": ["qa"]}}),
    )


@pytest.fixture
def app(store, flags):
    settings = Settings(env="test", release="abc123", admin_token=ADMIN, auth_signing_key=SIGNING)
    a = App(settings, store, flags)
    yield a
    a.health.close()


@pytest.fixture
def client(app):
    return Client(app)


@pytest.fixture
def admin_client(app):
    return Client(app, admin=True)
