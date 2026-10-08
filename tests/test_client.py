"""Tests for :mod:`ntfy_sh.client`."""

from __future__ import annotations

import base64
import logging
import urllib.error

import pytest
from conftest import FakeResponse, RecordingUrlopen, body_str, header, make_http_error, ok_response

from ntfy_sh import (
    DEFAULT_SERVER,
    NtfyClient,
    NtfyError,
    configure,
    get_default_client,
    notify,
    notify_error,
    notify_info,
    notify_success,
    notify_warning,
    ping,
    reset_default_client,
)
from ntfy_sh.client import (
    MAX_MESSAGE_CHARS,
    _env_flag,
    _normalize_priority,
    _normalize_tags,
    _sanitize_header,
)

# ---------------------------------------------------------------------------
# Construction / configuration
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_topic_from_argument(self):
        assert NtfyClient(topic="my-topic").topic == "my-topic"

    def test_topic_from_env(self, monkeypatch):
        monkeypatch.setenv("NTFY_CHANNEL", "env-topic")
        assert NtfyClient().topic == "env-topic"

    def test_argument_overrides_env(self, monkeypatch):
        monkeypatch.setenv("NTFY_CHANNEL", "env-topic")
        assert NtfyClient(topic="arg-topic").topic == "arg-topic"

    def test_no_topic_is_none(self):
        assert NtfyClient().topic is None

    def test_topic_is_stripped(self):
        assert NtfyClient(topic="  padded  ").topic == "padded"

    def test_server_default(self, monkeypatch):
        monkeypatch.delenv("NTFY_SERVER", raising=False)
        assert NtfyClient(topic="t").server == DEFAULT_SERVER

    def test_server_from_env(self, monkeypatch):
        monkeypatch.setenv("NTFY_SERVER", "https://ntfy.example.org/")
        assert NtfyClient(topic="t").server == "https://ntfy.example.org"

    def test_server_trailing_slash_removed(self):
        client = NtfyClient(topic="t", server="https://x.org///")
        assert client.server == "https://x.org"

    def test_enabled_property_requires_topic(self):
        assert NtfyClient(topic="t").enabled is True
        assert NtfyClient().enabled is False

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_disabled_via_env(self, monkeypatch, value):
        monkeypatch.setenv("NTFY_ENABLED", value)
        assert NtfyClient(topic="t").enabled is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything"])
    def test_enabled_via_env(self, monkeypatch, value):
        monkeypatch.setenv("NTFY_ENABLED", value)
        assert NtfyClient(topic="t").enabled is True

    def test_enabled_constructor_argument_wins(self, monkeypatch):
        monkeypatch.setenv("NTFY_ENABLED", "0")
        assert NtfyClient(topic="t", enabled=True).enabled is True

    def test_retries_clamped_to_zero(self):
        assert NtfyClient(topic="t", retries=-5).retries == 0

    def test_repr(self):
        text = repr(NtfyClient(topic="t"))
        assert "NtfyClient" in text and "off" not in text

    def test_env_flag_helper(self, monkeypatch):
        monkeypatch.setenv("NTFY_ENABLED", "off")
        assert _env_flag("NTFY_ENABLED") is False
        monkeypatch.setenv("NTFY_ENABLED", "1")
        assert _env_flag("NTFY_ENABLED") is True
        monkeypatch.delenv("NTFY_ENABLED", raising=False)
        assert _env_flag("NTFY_ENABLED") is True


class TestAuth:
    def test_basic_header(self):
        client = NtfyClient(topic="t", auth="user:secret")
        assert client._auth_header.startswith("Basic ")
        decoded = base64.b64decode(client._auth_header[6:]).decode()
        assert decoded == "user:secret"

    def test_bearer_token(self):
        client = NtfyClient(topic="t", auth="tk_abc123")
        assert client._auth_header == "Bearer tk_abc123"

    def test_no_auth(self):
        assert NtfyClient(topic="t")._auth_header is None


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

class TestNormalizePriority:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("min", "min"), ("low", "low"), ("default", "default"),
            ("high", "high"), ("urgent", "urgent"),
            ("HIGH", "high"), ("  Urgent  ", "urgent"),
            (4, "high"), (5, "urgent"),
            (None, None), ("bogus", None), (9, None), (True, None), (False, None),
        ],
    )
    def test_values(self, raw, expected):
        assert _normalize_priority(raw) == expected


class TestNormalizeTags:
    def test_string_with_commas(self):
        assert _normalize_tags("fire, chart") == ["fire", "chart"]

    def test_iterable(self):
        assert _normalize_tags(("a", "b")) == ["a", "b"]

    def test_empty_entries_dropped(self):
        assert _normalize_tags("a,,b,") == ["a", "b"]

    def test_none(self):
        assert _normalize_tags(None) is None

    def test_only_empty(self):
        assert _normalize_tags(" , ") is None


class TestSanitizeHeader:
    def test_newlines_replaced(self):
        # \r becomes a plain space, \n becomes " / "
        assert _sanitize_header("a\nb\rc") == "a / b c"

    def test_strips(self):
        assert _sanitize_header("  x  ") == "x"

    def test_non_string(self):
        assert _sanitize_header(42) == "42"


# ---------------------------------------------------------------------------
# send() -- guards
# ---------------------------------------------------------------------------

class TestSendGuards:
    def test_disabled_returns_none(self, capture_send):
        client = NtfyClient(topic="t", enabled=False)
        assert client.send("hello") is None
        assert capture_send.requests == []

    def test_no_topic_returns_none(self, capture_send):
        assert NtfyClient().send("hello") is None
        assert capture_send.requests == []

    def test_invalid_topic_returns_none(self, capture_send):
        client = NtfyClient(topic="t", raise_on_error=False)
        assert client.send("m", topic="bad topic!") is None
        assert capture_send.requests == []

    def test_invalid_topic_can_raise(self):
        client = NtfyClient(topic="valid-topic", raise_on_error=True)
        with pytest.raises(NtfyError, match="Invalid ntfy topic"):
            client.send("m", topic="spaces not allowed")

    @pytest.mark.parametrize("bad", ["has spaces", "cólon:", "a" * 65, "#hash"])
    def test_invalid_topic_regex(self, bad):
        client = NtfyClient(topic="ok", raise_on_error=True)
        with pytest.raises(NtfyError):
            client.send("m", topic=bad)

    def test_valid_topic_regex(self):
        # only check the regex accepts valid names here; full sends are
        # covered by TestRequestConstruction.
        import ntfy_sh.client as c

        assert c._TOPIC_RE.match("ok-topic_1")
        assert c._TOPIC_RE.match("A" * 64)


# ---------------------------------------------------------------------------
# send() -- request construction
# ---------------------------------------------------------------------------

class TestRequestConstruction:
    def test_url_method_and_body(self, capture_send):
        client = NtfyClient(topic="my-topic", server="https://n.example.org")
        result = client.send("hello world", title="T")
        assert result == {"id": "abc123", "event": "message"}
        req = capture_send.requests[0]
        assert req.full_url == "https://n.example.org/my-topic"
        assert req.get_method() == "POST"
        assert body_str(req) == "hello world"

    def test_topic_argument_overrides_client(self, capture_send):
        NtfyClient(topic="client-topic").send("m", topic="call-topic")
        req = capture_send.requests[0]
        assert req.full_url.endswith("/call-topic")

    def test_full_headers(self, capture_send):
        actions = [{"action": "view", "label": "Open", "url": "https://x"}]
        client = NtfyClient(topic="t")
        client.send(
            "m",
            title="The Title",
            priority="urgent",
            tags=("fire", "chart_with_upwards_trend"),
            click="https://example.org/click",
            delay="30min",
            markdown=True,
            email="me@example.org",
            icon="https://example.org/icon.png",
            filename="report.txt",
            actions=actions,
            cache=False,
            firebase=False,
        )
        req = capture_send.requests[0]
        h = req.headers
        assert h["Title"] == "The Title"
        assert h["Priority"] == "urgent"
        assert h["Tags"] == "fire,chart_with_upwards_trend"
        assert h["Click"] == "https://example.org/click"
        assert h["Delay"] == "30min"
        assert h["Markdown"] == "true"
        assert h["Email"] == "me@example.org"
        assert h["Icon"] == "https://example.org/icon.png"
        assert h["Filename"] == "report.txt"
        assert h["Actions"] == (
            '[{"action": "view", "label": "Open", "url": "https://x"}]'
        )
        assert h["Cache"] == "no"
        assert h["Firebase"] == "no"
        assert "Authorization" not in h

    def test_header_values_sanitized(self, capture_send):
        NtfyClient(topic="t").send("m", title="line1\nline2")
        assert header(capture_send.requests[0], "Title") == "line1 / line2"

    def test_auth_header_sent(self, capture_send):
        client = NtfyClient(topic="t", auth="u:p")
        client.send("m")
        assert header(capture_send.requests[0], "Authorization").startswith("Basic ")

    def test_extra_headers(self, capture_send):
        client = NtfyClient(topic="t")
        # NOTE: urllib.request capitalizes header keys ("Custom" stays intact;
        # multi-word keys get lower-cased after the first char, which is
        # irrelevant on the wire since HTTP headers are case-insensitive).
        client.send("m", extra_headers={"Custom": "v"})
        assert header(capture_send.requests[0], "Custom") == "v"

    def test_numeric_priority(self, capture_send):
        NtfyClient(topic="t").send("m", priority=5)
        assert header(capture_send.requests[0], "Priority") == "urgent"

    def test_invalid_priority_ignored_with_warning(self, capture_send, caplog):
        with caplog.at_level(logging.WARNING, logger="ntfy_sh"):
            NtfyClient(topic="t").send("m", priority="bogus")
        req = capture_send.requests[0]
        assert "Priority" not in req.headers
        assert any("invalid priority" in rec.message for rec in caplog.records)

    def test_message_truncated(self, capture_send):
        client = NtfyClient(topic="t")
        client.send("x" * 5000)
        sent = body_str(capture_send.requests[0])
        assert len(sent) == MAX_MESSAGE_CHARS + len("\n[... message truncated]")
        assert sent.endswith("[... message truncated]")

    def test_short_message_not_truncated(self, capture_send):
        NtfyClient(topic="t").send("short")
        assert body_str(capture_send.requests[0]) == "short"

    def test_json_response_parsed(self, monkeypatch):
        recorder = RecordingUrlopen([FakeResponse(b'{"id": "q9", "event": "message"}')])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        result = NtfyClient(topic="t").send("m")
        assert result == {"id": "q9", "event": "message"}

    def test_non_json_response_returns_raw(self, monkeypatch):
        recorder = RecordingUrlopen([FakeResponse(b"plain text")])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        result = NtfyClient(topic="t").send("m")
        assert result == {"raw": "plain text"}


# ---------------------------------------------------------------------------
# send() -- error handling and retries
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_http_error_returns_none(self, monkeypatch):
        recorder = RecordingUrlopen([make_http_error(403, b"reserved topic")])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        client = NtfyClient(topic="t")
        assert client.send("m") is None

    def test_http_error_can_raise(self, monkeypatch):
        recorder = RecordingUrlopen([make_http_error(429, b"rate limited")])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        client = NtfyClient(topic="t", raise_on_error=True, retries=0)
        with pytest.raises(NtfyError, match="HTTP 429"):
            client.send("m")

    def test_raise_on_error_per_call(self, monkeypatch):
        recorder = RecordingUrlopen([make_http_error(500, b"boom")])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        client = NtfyClient(topic="t", raise_on_error=False, retries=0)
        with pytest.raises(NtfyError, match="HTTP 500"):
            client.send("m", raise_on_error=True)

    def test_network_error_returns_none(self, monkeypatch):
        recorder = RecordingUrlopen([urllib.error.URLError("no network")])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        client = NtfyClient(topic="t", retries=0)
        assert client.send("m") is None

    def test_transient_429_is_retried(self, monkeypatch):
        recorder = RecordingUrlopen(
            [make_http_error(429), make_http_error(429), ok_response({"id": "r1"})]
        )
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        monkeypatch.setattr("ntfy_sh.client.time.sleep", lambda s: None)
        client = NtfyClient(topic="t", retries=2)
        assert client.send("m") == {"id": "r1"}
        assert len(recorder.requests) == 3

    def test_transient_5xx_is_retried(self, monkeypatch):
        recorder = RecordingUrlopen([make_http_error(503), ok_response({"id": "r2"})])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        monkeypatch.setattr("ntfy_sh.client.time.sleep", lambda s: None)
        client = NtfyClient(topic="t", retries=1)
        assert client.send("m") == {"id": "r2"}
        assert len(recorder.requests) == 2

    def test_network_error_is_retried(self, monkeypatch):
        recorder = RecordingUrlopen(
            [urllib.error.URLError("down"), ok_response({"id": "r3"})]
        )
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        monkeypatch.setattr("ntfy_sh.client.time.sleep", lambda s: None)
        client = NtfyClient(topic="t", retries=1)
        assert client.send("m") == {"id": "r3"}

    def test_non_transient_not_retried(self, monkeypatch):
        recorder = RecordingUrlopen([make_http_error(403)])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        client = NtfyClient(topic="t", retries=3)
        assert client.send("m") is None
        assert len(recorder.requests) == 1

    def test_exhausted_retries_return_none(self, monkeypatch):
        recorder = RecordingUrlopen([make_http_error(429), make_http_error(429)])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        monkeypatch.setattr("ntfy_sh.client.time.sleep", lambda s: None)
        client = NtfyClient(topic="t", retries=1)
        assert client.send("m") is None
        assert len(recorder.requests) == 2

    def test_backoff_is_called(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr("ntfy_sh.client.time.sleep", sleeps.append)
        recorder = RecordingUrlopen([make_http_error(429), make_http_error(429)])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        NtfyClient(topic="t", retries=1).send("m")
        assert sleeps == [1.0]


# ---------------------------------------------------------------------------
# Semantic shortcuts
# ---------------------------------------------------------------------------

class TestShortcuts:
    def test_info(self, capture_send):
        NtfyClient(topic="t").info("m")
        h = capture_send.requests[0].headers
        assert h["Priority"] == "low"
        assert h["Title"] == "Information"
        assert h["Tags"] == "information_source"

    def test_success(self, capture_send):
        NtfyClient(topic="t").success("m")
        h = capture_send.requests[0].headers
        assert h["Priority"] == "high"
        assert h["Tags"] == "white_check_mark,tada"

    def test_warning(self, capture_send):
        NtfyClient(topic="t").warning("m")
        h = capture_send.requests[0].headers
        assert h["Priority"] == "high"
        assert h["Tags"] == "warning"

    def test_error(self, capture_send):
        NtfyClient(topic="t").error("m")
        h = capture_send.requests[0].headers
        assert h["Priority"] == "urgent"
        assert h["Tags"] == "rotating_light"

    def test_ping(self, capture_send):
        NtfyClient(topic="t").ping()
        h = capture_send.requests[0].headers
        assert h["Priority"] == "min"
        assert body_str(capture_send.requests[0]) == "ping"

    def test_shortcuts_forward_kwargs(self, capture_send):
        NtfyClient(topic="t").success("m", topic="other")
        assert capture_send.requests[0].full_url.endswith("/other")


# ---------------------------------------------------------------------------
# Default client (process-wide singleton)
# ---------------------------------------------------------------------------

class TestDefaultClient:
    def test_created_from_env(self, monkeypatch):
        monkeypatch.setenv("NTFY_CHANNEL", "singleton-topic")
        assert get_default_client().topic == "singleton-topic"

    def test_cached_across_calls(self, monkeypatch):
        monkeypatch.setenv("NTFY_CHANNEL", "first")
        first = get_default_client()
        monkeypatch.setenv("NTFY_CHANNEL", "second")
        assert get_default_client() is first  # cached

    def test_reset_recreates(self, monkeypatch):
        monkeypatch.setenv("NTFY_CHANNEL", "first")
        get_default_client()
        reset_default_client()
        monkeypatch.setenv("NTFY_CHANNEL", "second")
        assert get_default_client().topic == "second"

    def test_configure(self):
        client = configure(topic="cfg-topic", server="https://s.example.org")
        assert get_default_client() is client
        assert client.topic == "cfg-topic"

    def test_module_functions_use_default(self, monkeypatch):
        recorder = RecordingUrlopen([FakeResponse() for _ in range(6)])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        configure(topic="mod-topic")
        notify("raw")
        notify_info("i")
        notify_success("s")
        notify_warning("w")
        notify_error("e")
        ping()
        priorities = [r.headers.get("Priority") for r in recorder.requests]
        assert priorities == [None, "low", "high", "high", "urgent", "min"]
        topics = {r.full_url.rsplit("/", 1)[-1] for r in recorder.requests}
        assert topics == {"mod-topic"}

    def test_module_functions_silent_without_topic(self, capture_send):
        # no NTFY_CHANNEL configured -> everything is a silent no-op
        assert notify("m") is None
        assert notify_success("m") is None
        assert ping() is None
        assert capture_send.requests == []
