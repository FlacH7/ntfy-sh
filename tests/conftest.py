"""Shared fixtures and helpers for the ntfy-sh test suite.

The whole suite runs offline: ``urllib.request.urlopen`` is monkeypatched
so no request ever leaves the machine.
"""

from __future__ import annotations

import io
import json

import pytest

from ntfy_sh import reset_default_client


class FakeResponse:
    """Minimal stand-in for the ``HTTPResponse`` returned by ``urlopen``."""

    def __init__(self, body=b'{"id": "abc123", "event": "message"}', status=200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_http_error(code: int, detail: bytes = b"error detail"):
    """Build a real ``urllib.error.HTTPError`` with a readable body."""
    import urllib.error

    return urllib.error.HTTPError(
        "https://ntfy.sh/some-topic", code, "reason", None, io.BytesIO(detail)
    )


class RecordingUrlopen:
    """Callable that records every ``Request`` passed to ``urlopen``.

    ``script`` is a sequence of outcomes: ``FakeResponse`` objects are
    returned, ``Exception`` instances are raised.
    """

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class DummyClient:
    """In-memory client that records semantic calls (for decorator tests)."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    # mirror the NtfyClient interface
    def info(self, message, **kw):
        return self._record("info", message, kw)

    def success(self, message, **kw):
        return self._record("success", message, kw)

    def warning(self, message, **kw):
        return self._record("warning", message, kw)

    def error(self, message, **kw):
        return self._record("error", message, kw)

    def ping(self, message="ping", **kw):
        return self._record("ping", message, kw)

    def _record(self, kind, message, kw):
        if self.fail:
            raise RuntimeError("simulated notification failure")
        self.calls.append((kind, message, kw))
        return {"id": "dummy", "kind": kind}

    # helpers used by assertions
    def kinds(self):
        return [k for k, _, _ in self.calls]

    def last(self):
        return self.calls[-1] if self.calls else None


@pytest.fixture(autouse=True)
def _isolate_default_client():
    """Keep the process-wide default client isolated between tests."""
    reset_default_client()
    yield
    reset_default_client()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove ntfy environment variables before each test."""
    for var in ("NTFY_CHANNEL", "NTFY_TOPIC", "NTFY_SERVER", "NTFY_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def capture_send(monkeypatch):
    """Patch ``urlopen`` with a single successful fake response.

    Returns the ``RecordingUrlopen`` so tests can inspect the request.
    """
    recorder = RecordingUrlopen([FakeResponse()])
    monkeypatch.setattr(
        "ntfy_sh.client.urllib.request.urlopen", recorder
    )
    return recorder


def body_str(req) -> str:
    return req.data.decode("utf-8")


def header(req, name):
    return req.headers[name]


def ok_response(payload=None):
    return FakeResponse(json.dumps(payload or {"id": "z1"}).encode("utf-8"))
