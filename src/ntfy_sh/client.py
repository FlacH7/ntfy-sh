"""Lightweight HTTP client for the `ntfy` notification service.

Designed to monitor long-running compute jobs (SSH + ``nohup``) without
coupling scientific code to notification infrastructure.

Key features
------------
* **Zero dependencies**: only the standard library is used
  (``urllib.request``), so the folder can be copied as-is into any project.
* **Fail-safe by design**: by default a network/HTTP error *never* interrupts
  the host program; it is logged as ``logging.warning`` and
  :meth:`NtfyClient.send` returns ``None``.
* **The topic acts as the password**: in ntfy anyone who knows the channel
  name can read and write. The topic is resolved in this order:

  1. explicit ``topic`` argument in the call,
  2. topic of the client (constructor / :func:`configure`),
  3. environment variable ``NTFY_CHANNEL``.

* **Configurable server** (``NTFY_SERVER``): the official ntfy.sh instance
  or a self-hosted one.
* **Retries** with a short backoff (one extra attempt by default).

Recognized environment variables (all optional):

``NTFY_CHANNEL``
    Default topic. Empty or missing -> notifications disabled.
``NTFY_SERVER``
    Default server (default ``https://ntfy.sh``).
``NTFY_ENABLED``
    ``0`` / ``false`` / ``no`` / ``off`` -> silence the module entirely
    without touching any code.

HTTP API reference: https://docs.ntfy.sh/publish/
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from typing import Any

__all__ = [
    "NtfyClient",
    "NtfyError",
    "configure",
    "get_default_client",
    "reset_default_client",
    "notify",
    "notify_info",
    "notify_success",
    "notify_warning",
    "notify_error",
    "ping",
    "DEFAULT_SERVER",
    "VALID_PRIORITIES",
]

LOGGER = logging.getLogger("ntfy_sh")

#: Official public instance of the service.
DEFAULT_SERVER = "https://ntfy.sh"

#: Valid protocol priorities (from lowest to highest urgency).
VALID_PRIORITIES = ("min", "low", "default", "high", "urgent")

#: Numeric aliases accepted by ntfy (1-5).
_PRIORITY_ALIASES = {1: "min", 2: "low", 3: "default", 4: "high", 5: "urgent"}

#: Default timeout (s): a notification must never stall a run.
DEFAULT_TIMEOUT = 10.0

#: Practical body size limit (ntfy truncates at ~4 KB).
MAX_MESSAGE_CHARS = 3900

#: Marker appended when the message is trimmed.
_TRUNCATION_MARKER = "\n[... message truncated]"

#: A valid topic: alphanumeric, hyphens and underscores.
_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class NtfyError(RuntimeError):
    """Failed to send a notification (network, HTTP != 2xx, invalid topic...)."""


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _normalize_priority(priority: Any) -> str | None:
    """Return the canonical priority ('min'...'urgent') or None if invalid."""
    if priority is None:
        return None
    if isinstance(priority, bool):  # bool is a subclass of int: avoid True==1
        return None
    if isinstance(priority, int):
        return _PRIORITY_ALIASES.get(priority)
    p = str(priority).strip().lower()
    return p if p in VALID_PRIORITIES else None


def _normalize_tags(tags: str | Iterable[str] | None) -> list[str] | None:
    """Normalize ``'a,b'`` | iterable | None -> ``['a', 'b']`` | None."""
    if tags is None:
        return None
    if isinstance(tags, str):
        parts = [p.strip() for p in tags.split(",")]
    else:
        parts = [str(p).strip() for p in tags]
    out = [p for p in parts if p]
    return out or None


def _sanitize_header(value: Any) -> str:
    """HTTP headers cannot contain line breaks: they are replaced by ' / '."""
    return str(value).replace("\r", " ").replace("\n", " / ").strip()


def _env_flag(env_name: str) -> bool:
    """Read a boolean flag from an environment variable (default True)."""
    raw = os.getenv(env_name, "1")
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class NtfyClient:
    """HTTP client to publish notifications on an ntfy topic.

    Parameters
    ----------
    topic:
        Topic to publish to. If ``None`` it is read from the ``NTFY_CHANNEL``
        environment variable.
    server:
        Base URL of the server (default ``https://ntfy.sh`` or ``$NTFY_SERVER``).
    timeout:
        Timeout in seconds for each HTTP attempt.
    enabled:
        Master switch (default: value of ``$NTFY_ENABLED``). With
        ``enabled=False`` all calls are silent no-ops.
    raise_on_error:
        If ``True``, send failures raise :class:`NtfyError` instead of just
        logging a warning and returning ``None``. In a scientific run this is
        almost never desirable; it is mostly used in test scripts and in the
        CLI.
    retries:
        Extra attempts on network errors or HTTP 429 (default 1, with a short
        backoff).
    auth:
        Credentials if the topic is protected: ``"user:password"`` or an
        access token ``"tk_..."`` (requires an ntfy.sh account or a
        self-hosted server with authentication).
    """

    def __init__(
        self,
        topic: str | None = None,
        server: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        enabled: bool | None = None,
        raise_on_error: bool = False,
        retries: int = 1,
        auth: str | None = None,
    ) -> None:
        if enabled is None:
            enabled = _env_flag("NTFY_ENABLED")
        self._enabled = bool(enabled)
        self.topic = (topic or os.getenv("NTFY_CHANNEL") or "").strip() or None
        self.server = (
            server or os.getenv("NTFY_SERVER") or DEFAULT_SERVER
        ).strip().rstrip("/")
        self.timeout = float(timeout)
        self.raise_on_error = bool(raise_on_error)
        self.retries = max(0, int(retries))
        self._auth_header = self._build_auth_header(auth)

    # ------------------------------------------------------------------

    @staticmethod
    def _build_auth_header(auth: str | None) -> str | None:
        if not auth:
            return None
        if ":" in auth:  # user:password -> Basic
            cred = base64.b64encode(auth.encode("utf-8")).decode("ascii")
            return f"Basic {cred}"
        return f"Bearer {auth}"  # access token

    @property
    def enabled(self) -> bool:
        """True if the client can send (switch on and topic set)."""
        return bool(self._enabled and self.topic)

    def __repr__(self) -> str:  # pragma: no cover
        state = "on" if self.enabled else "off"
        return f"<NtfyClient {state} server={self.server!r} topic={self.topic!r}>"

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send(
        self,
        message: str,
        *,
        title: str | None = None,
        priority: str | None = None,
        tags: str | Iterable[str] | None = None,
        click: str | None = None,
        actions: Iterable[dict[str, Any]] | None = None,
        delay: str | None = None,
        markdown: bool = False,
        email: str | None = None,
        icon: str | None = None,
        filename: str | None = None,
        cache: bool = True,
        firebase: bool = True,
        topic: str | None = None,
        extra_headers: dict[str, str] | None = None,
        raise_on_error: bool | None = None,
    ) -> dict[str, Any] | None:
        """Publish ``message`` to the topic and return the JSON response.

        Returns ``None`` (without raising) if the client is disabled, if no
        topic is set, or if the send failed and ``raise_on_error`` is false.

        Key parameters
        --------------
        title:
            Notification title.
        priority:
            ``'min' | 'low' | 'default' | 'high' | 'urgent'`` (or 1-5).
            An invalid priority is ignored with a warning.
        tags:
            Emojis by *shortcode*, e.g. ``('white_check_mark', 'tada')``
            or ``'fire,chart'``.
        click:
            URL opened when the notification is tapped.
        actions:
            Action buttons, e.g.::

                [{"action": "view", "label": "Open repo",
                  "url": "https://github.com/user/repo"}]

        delay:
            Delayed delivery, e.g. ``'30min'``, ``'11h'``, ``'9am'``
            or ``'tomorrow, 9:00'`` (max 3 days on ntfy.sh).
        markdown:
            Render the body as Markdown.
        email:
            Also send a copy by email.
        icon / filename:
            See the API reference at https://docs.ntfy.sh/publish/.
        cache:
            ``False`` -> header ``Cache: no`` (the message does not stay in
            the topic history for late subscribers).
        firebase:
            ``False`` -> header ``Firebase: no`` (useful for self-hosted).
        topic:
            Explicit topic for this call (overrides the client's).
        extra_headers:
            Additional headers (escape hatch for new ntfy features).
        raise_on_error:
            Overrides, for this call only, the constructor option.
        """
        raise_opt = self.raise_on_error if raise_on_error is None else bool(raise_on_error)

        if not self._enabled:
            LOGGER.debug(
                "[ntfy-sh] disabled (enabled=False); skipped: %r",
                title or str(message)[:40],
            )
            return None

        dest = (topic or self.topic or "").strip()
        if not dest:
            LOGGER.debug(
                "[ntfy-sh] no topic configured (NTFY_CHANNEL); skipped: %r",
                title or str(message)[:40],
            )
            return None

        if not _TOPIC_RE.match(dest):
            return self._fail(
                NtfyError(
                    f"Invalid ntfy topic: {dest!r} "
                    "(expected [A-Za-z0-9_-], max 64 chars)"
                ),
                raise_opt,
            )

        # --- Body ----------------------------------------------------
        message = "" if message is None else str(message)
        if len(message) > MAX_MESSAGE_CHARS:
            message = message[:MAX_MESSAGE_CHARS] + _TRUNCATION_MARKER

        # --- Headers ---------------------------------------------------
        headers: dict[str, str] = {}
        if title:
            headers["Title"] = _sanitize_header(title)
        prio = _normalize_priority(priority)
        if prio:
            headers["Priority"] = prio
        elif priority is not None:
            LOGGER.warning("[ntfy-sh] invalid priority (%r); ignored.", priority)
        tags_n = _normalize_tags(tags)
        if tags_n:
            headers["Tags"] = ",".join(tags_n)
        if click:
            headers["Click"] = _sanitize_header(click)
        if delay:
            headers["Delay"] = _sanitize_header(delay)
        if markdown:
            headers["Markdown"] = "true"
        if email:
            headers["Email"] = _sanitize_header(email)
        if icon:
            headers["Icon"] = _sanitize_header(icon)
        if filename:
            headers["Filename"] = _sanitize_header(filename)
        if actions:
            headers["Actions"] = json.dumps(list(actions))
        if not cache:
            headers["Cache"] = "no"
        if not firebase:
            headers["Firebase"] = "no"
        if self._auth_header:
            headers["Authorization"] = self._auth_header
        if extra_headers:
            for key, value in extra_headers.items():
                headers[str(key)] = _sanitize_header(value)

        url = f"{self.server}/{dest}"
        data = message.encode("utf-8")

        last_error: NtfyError | None = None
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(
                    url, data=data, headers=headers, method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(body)
                except ValueError:
                    return {"raw": body}
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", errors="replace")[:200]
                except Exception:  # pragma: no cover
                    pass
                last_error = NtfyError(
                    f"HTTP {exc.code} publishing to {url}: {detail or exc.reason}"
                )
                # 429 (rate limit) and 5xx are transient -> retry;
                # the remaining 4xx usually do not recover.
                transient = exc.code == 429 or exc.code >= 500
                if not transient or attempt >= self.retries:
                    return self._fail(last_error, raise_opt)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = NtfyError(f"Network error contacting {url}: {exc}")
                if attempt >= self.retries:
                    return self._fail(last_error, raise_opt)

            if attempt < self.retries:
                time.sleep(1.0 + attempt)  # short backoff: 1 s, 2 s, ...

        return self._fail(last_error, raise_opt)  # pragma: no cover

    def _fail(self, exc: NtfyError, raise_it: bool):
        if raise_it:
            raise exc
        LOGGER.warning("%s", exc)
        return None

    # ------------------------------------------------------------------
    # Semantic shortcuts
    # ------------------------------------------------------------------

    def info(
        self, message: str, *, title: str = "Information",
        tags: str | Iterable[str] | None = ("information_source",),
        priority: str | None = "low", **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Informational notice (low priority)."""
        return self.send(message, title=title, tags=tags, priority=priority, **kwargs)

    def success(
        self, message: str, *, title: str = "Completed",
        tags: str | Iterable[str] | None = ("white_check_mark", "tada"),
        priority: str | None = "high", **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Success notice (high priority)."""
        return self.send(message, title=title, tags=tags, priority=priority, **kwargs)

    def warning(
        self, message: str, *, title: str = "Warning",
        tags: str | Iterable[str] | None = ("warning",),
        priority: str | None = "high", **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Warning notice (high priority)."""
        return self.send(message, title=title, tags=tags, priority=priority, **kwargs)

    def error(
        self, message: str, *, title: str = "ERROR",
        tags: str | Iterable[str] | None = ("rotating_light",),
        priority: str | None = "urgent", **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Critical error notice (urgent priority: rings and vibrates)."""
        return self.send(message, title=title, tags=tags, priority=priority, **kwargs)

    def ping(
        self, message: str = "ping", *, title: str = "ntfy ping",
        tags: str | Iterable[str] | None = ("bell",),
        priority: str | None = "min", **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Minimal notification to verify channel connectivity."""
        return self.send(message, title=title, tags=tags, priority=priority, **kwargs)


# ---------------------------------------------------------------------------
# Default client for the process (lazy singleton)
# ---------------------------------------------------------------------------

_DEFAULT_CLIENT: NtfyClient | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_client() -> NtfyClient:
    """Return the default client, creating it on first use.

    The default client resolves its topic/server from the ``NTFY_CHANNEL`` /
    ``NTFY_SERVER`` environment variables at CREATION time (it is cached).
    If you change the environment at runtime, call
    :func:`reset_default_client` or :func:`configure`.
    """
    global _DEFAULT_CLIENT
    with _DEFAULT_LOCK:
        if _DEFAULT_CLIENT is None:
            _DEFAULT_CLIENT = NtfyClient()
        return _DEFAULT_CLIENT


def reset_default_client() -> None:
    """Forget the default client (the next call re-creates it)."""
    global _DEFAULT_CLIENT
    with _DEFAULT_LOCK:
        _DEFAULT_CLIENT = None


def configure(
    topic: str | None = None,
    server: str | None = None,
    **kwargs: Any,
) -> NtfyClient:
    """Configure the default client for the process.

    Example::

        import ntfy_sh
        ntfy_sh.configure(topic="my-topic")
        ntfy_sh.notify_success("all good")
    """
    global _DEFAULT_CLIENT
    with _DEFAULT_LOCK:
        _DEFAULT_CLIENT = NtfyClient(topic=topic, server=server, **kwargs)
        return _DEFAULT_CLIENT


# ---------------------------------------------------------------------------
# Module-level functions (shortcuts over the default client)
# ---------------------------------------------------------------------------

def notify(message: str, **kwargs: Any) -> dict[str, Any] | None:
    """Send a raw notification (see :meth:`NtfyClient.send`)."""
    return get_default_client().send(message, **kwargs)


def notify_info(message: str, **kwargs: Any) -> dict[str, Any] | None:
    """Informational notice (low priority)."""
    return get_default_client().info(message, **kwargs)


def notify_success(message: str, **kwargs: Any) -> dict[str, Any] | None:
    """Success notice (high priority)."""
    return get_default_client().success(message, **kwargs)


def notify_warning(message: str, **kwargs: Any) -> dict[str, Any] | None:
    """Warning notice (high priority)."""
    return get_default_client().warning(message, **kwargs)


def notify_error(message: str, **kwargs: Any) -> dict[str, Any] | None:
    """Critical error notice (urgent priority)."""
    return get_default_client().error(message, **kwargs)


def ping(message: str = "ping", **kwargs: Any) -> dict[str, Any] | None:
    """Connectivity ping against the configured topic."""
    return get_default_client().ping(message, **kwargs)
