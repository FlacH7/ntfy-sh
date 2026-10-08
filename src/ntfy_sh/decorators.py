"""Decorators and context manager to monitor long-running functions.

Typical use cases (runs over SSH + ``nohup``)::

    from ntfy_sh import notify_on_critical_error, notify_success

    # 1) Be notified if a critical exception kills the run (and re-raise it
    #    so the process dies as it should):
    @notify_on_critical_error(topic=TOPIC, title="[project] ERROR")
    def pipeline(**cfg):
        ...

    # 2) Be notified when a long function finishes successfully:
    @notify_on_success(topic=TOPIC)
    def train_model(**cfg):
        ...

    # 3) Full monitor (start + success + error):
    @notify_calls(topic=TOPIC, notify_start=True)
    def experiment(**cfg):
        ...

    # 4) Loose code blocks:
    with watch("A6 common-rank (SS1)"):
        run_a6()

Philosophy: a notification must NEVER alter the behavior of the code it
monitors. Decorators only observe; if the send itself fails, a warning is
logged and execution continues (unless the decorated function fails, in
which case the original exception is re-raised intact).
"""

from __future__ import annotations

import functools
import logging
import time
import traceback
from collections.abc import Callable
from typing import Any, TypeVar

from .client import NtfyClient, get_default_client

__all__ = [
    "notify_on_success",
    "notify_on_critical_error",
    "notify_on_error",
    "notify_calls",
    "watch",
    "format_duration",
    "format_exception",
]

LOGGER = logging.getLogger("ntfy_sh.decorators")

#: Lines of traceback included in the error notification.
TRACEBACK_TAIL_LINES = 12

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    """Format seconds as ``'1h 04m 30s'`` / ``'4m 12s'`` / ``'37s'``."""
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_exception(exc: BaseException, tail: int = TRACEBACK_TAIL_LINES) -> str:
    """Render the traceback of ``exc`` trimmed to the last ``tail`` lines.

    If trimmed, the first line (``Traceback ...``) is always kept so the
    format remains recognizable on the phone.
    """
    chunks = traceback.format_exception(type(exc), exc, exc.__traceback__)
    lines = [ln for chunk in chunks for ln in chunk.splitlines()]
    if len(lines) > tail:
        header = [lines[0]] if lines[0].startswith("Traceback") else []
        lines = header + ["    [...] traceback trimmed"] + lines[-tail:]
    return "\n".join(lines)


def _label(func: Callable) -> str:
    """Short label of a function: ``module.Name`` (or ``Class.method``)."""
    module = getattr(func, "__module__", "") or ""
    module = module.rsplit(".", 1)[-1]
    name = getattr(func, "__qualname__", None) or getattr(func, "__name__", "function")
    return f"{module}.{name}" if module else name


def _client_for(client: NtfyClient | None) -> NtfyClient:
    return client if client is not None else get_default_client()


def _safe(fn: Callable[[], Any]) -> None:
    """Run ``fn`` swallowing any error (the notification must not break anything)."""
    try:
        fn()
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("[ntfy-sh] failed to send notification: %s", exc)


def _decorator_apply(decorator: Callable[[F], F], obj: Any) -> Any:
    """Allow using the decorator with or without parentheses."""
    return decorator(obj) if obj is not None else decorator


def _merge_kwargs(base: dict[str, Any], **defaults: Any) -> dict[str, Any]:
    """``dict(base)`` where ``defaults`` only apply if the key is missing."""
    merged = dict(defaults)
    merged.update(base)
    return merged


# ---------------------------------------------------------------------------
# notify_on_critical_error
# ---------------------------------------------------------------------------

def notify_on_critical_error(
    _func: F | None = None,
    *,
    topic: str | None = None,
    client: NtfyClient | None = None,
    title: str = "Critical ERROR",
    include_traceback: bool = True,
    re_raise: bool = True,
    notify_start: bool = False,
    catch_system_exit: bool = False,
    priority: str = "urgent",
    tags: Any = ("rotating_light",),
    **send_kwargs: Any,
):
    """Decorate a function to be notified (urgent) if a critical exception escapes.

    The notification includes the exception type and message, the elapsed
    time and (by default) the last lines of the traceback. After notifying,
    the exception is RE-RAISED so execution stops exactly as it would
    without the decorator: notifying does not fix anything.

    Can be used with or without parentheses::

        @notify_on_critical_error
        def f(): ...

        @notify_on_critical_error(topic="my-topic", title="[X] error")
        def f(): ...

    Parameters
    ----------
    topic:
        Destination topic (default: that of the default client, which reads
        ``$NTFY_CHANNEL``).
    client:
        Explicit :class:`~ntfy_sh.client.NtfyClient` (default: the process
        default).
    re_raise:
        Re-raise the exception after notifying (default ``True``).
        ``False`` only if you know very well why: swallowing the exception
        can leave the run in an inconsistent state.
    catch_system_exit:
        Also notify on ``SystemExit`` with a code other than 0 (e.g.
        ``sys.exit(1)`` caused by invalid parameters). ``SystemExit`` with
        code 0 (``--help``) never generates a notification. Default
        ``False``.
    notify_start:
        Also send an informational notice when entering the function.
    """

    def decorator(func: F) -> F:
        err_kw = _merge_kwargs(
            send_kwargs, topic=topic, title=title,
            priority=priority, tags=tags,
        )
        start_kw = _merge_kwargs(send_kwargs, topic=topic, title="Starting")

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ntfy_client = _client_for(client)
            if notify_start:
                _safe(lambda: ntfy_client.info(
                    f"{_label(func)} starting...", **start_kw,
                ))
            t0 = time.monotonic()
            try:
                return func(*args, **kwargs)
            except SystemExit as exc:
                if catch_system_exit and exc.code not in (None, 0):
                    exit_code = exc.code
                    elapsed = format_duration(time.monotonic() - t0)
                    label = _label(func)
                    _safe(lambda: ntfy_client.error(
                        f"{label} ended with sys.exit({exit_code!r}) after "
                        f"{elapsed}.\n"
                        "Execution stopped before completing "
                        "(check the log: parameters/JSON/config).",
                        **err_kw,
                    ))
                raise
            except Exception as exc:
                elapsed = format_duration(time.monotonic() - t0)
                parts = [
                    f"{_label(func)} failed after {elapsed}.",
                    f"Exception: {type(exc).__name__}: {exc}",
                ]
                if include_traceback:
                    parts += ["", format_exception(exc)]
                _safe(lambda: ntfy_client.error("\n".join(parts), **err_kw))
                if re_raise:
                    raise
                return None

        return wrapper  # type: ignore[return-value]

    return _decorator_apply(decorator, _func)


#: Short alias.
notify_on_error = notify_on_critical_error


# ---------------------------------------------------------------------------
# notify_on_success
# ---------------------------------------------------------------------------

def notify_on_success(
    _func: F | None = None,
    *,
    topic: str | None = None,
    client: NtfyClient | None = None,
    title: str = "Completed",
    include_duration: bool = True,
    send_result: bool = False,
    notify_start: bool = False,
    priority: str = "high",
    tags: Any = ("white_check_mark", "tada"),
    **send_kwargs: Any,
):
    """Decorate a function to be notified when it finishes without errors.

    The notification includes the duration (by default) and optionally the
    ``repr()`` of the result (``send_result=True``, trimmed to 300 chars).
    Any exception propagates intact: this decorator does NOT notify errors
    (for that use :func:`notify_on_critical_error` or :func:`notify_calls`).
    """

    def decorator(func: F) -> F:
        ok_kw = _merge_kwargs(
            send_kwargs, topic=topic, title=title,
            priority=priority, tags=tags,
        )
        start_kw = _merge_kwargs(send_kwargs, topic=topic, title="Starting")

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ntfy_client = _client_for(client)
            if notify_start:
                _safe(lambda: ntfy_client.info(
                    f"{_label(func)} starting...", **start_kw,
                ))
            t0 = time.monotonic()
            result = func(*args, **kwargs)
            message = f"{_label(func)} finished OK"
            if include_duration:
                message += f" in {format_duration(time.monotonic() - t0)}"
            if send_result:
                message += f"\nResult: {repr(result)[:300]}"
            _safe(lambda: ntfy_client.success(message, **ok_kw))
            return result

        return wrapper  # type: ignore[return-value]

    return _decorator_apply(decorator, _func)


# ---------------------------------------------------------------------------
# notify_calls: start + success + error in a single decorator
# ---------------------------------------------------------------------------

def notify_calls(
    _func: F | None = None,
    *,
    topic: str | None = None,
    client: NtfyClient | None = None,
    title: str = "Monitor",
    notify_start: bool = False,
    on_success: bool = True,
    on_error: bool = True,
    include_duration: bool = True,
    include_traceback: bool = True,
    re_raise: bool = True,
    success_priority: str = "high",
    error_priority: str = "urgent",
    **send_kwargs: Any,
):
    """Monitor a function: optional start notice + success + critical error.

    Union of :func:`notify_on_success` and :func:`notify_on_critical_error`::

        @notify_calls(topic="my-topic", notify_start=True)
        def long_experiment():
            ...
    """

    def decorator(func: F) -> F:
        ok_kw = _merge_kwargs(
            send_kwargs, topic=topic, title=f"{title} -- OK",
            priority=success_priority, tags=("white_check_mark", "tada"),
        )
        err_kw = _merge_kwargs(
            send_kwargs, topic=topic, title=f"{title} -- ERROR",
            priority=error_priority, tags=("rotating_light",),
        )
        start_kw = _merge_kwargs(send_kwargs, topic=topic, title="Starting")

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ntfy_client = _client_for(client)
            label = _label(func)
            if notify_start:
                _safe(lambda: ntfy_client.info(f"{label} starting...", **start_kw))
            t0 = time.monotonic()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                if on_error:
                    elapsed = format_duration(time.monotonic() - t0)
                    message = (
                        f"{label} failed after {elapsed}.\n"
                        f"Exception: {type(exc).__name__}: {exc}"
                    )
                    if include_traceback:
                        message += "\n\n" + format_exception(exc)
                    _safe(lambda: ntfy_client.error(message, **err_kw))
                if re_raise:
                    raise
                return None
            if on_success:
                message = f"{label} finished OK"
                if include_duration:
                    message += f" in {format_duration(time.monotonic() - t0)}"
                _safe(lambda: ntfy_client.success(message, **ok_kw))
            return result

        return wrapper  # type: ignore[return-value]

    return _decorator_apply(decorator, _func)


# ---------------------------------------------------------------------------
# watch: context manager
# ---------------------------------------------------------------------------

class watch:
    """Context manager that notifies the fate of a block of code.

    Example::

        from ntfy_sh import watch

        with watch("A6 common-rank (SS1)", topic="my-topic"):
            run_a6()

    On clean exit -> success notification with the duration.
    On exception -> urgent notification with the traceback; the exception
    propagates intact (``__exit__`` returns ``False``).
    """

    def __init__(
        self,
        label: str,
        *,
        topic: str | None = None,
        client: NtfyClient | None = None,
        title: str | None = None,
        notify_start: bool = False,
        success_priority: str = "high",
        error_priority: str = "urgent",
        include_traceback: bool = True,
        **send_kwargs: Any,
    ) -> None:
        self.label = str(label)
        self._client = client
        self.notify_start = notify_start
        self.include_traceback = include_traceback
        self.ok_kw = _merge_kwargs(
            send_kwargs, topic=topic,
            title=title or f"{label} -- OK",
            priority=success_priority, tags=("white_check_mark",),
        )
        self.err_kw = _merge_kwargs(
            send_kwargs, topic=topic,
            title=title or f"{label} -- ERROR",
            priority=error_priority, tags=("rotating_light",),
        )
        self.start_kw = _merge_kwargs(send_kwargs, topic=topic, title="Starting")
        self._t0: float | None = None

    def __enter__(self) -> watch:
        self._t0 = time.monotonic()
        if self.notify_start:
            _safe(lambda: _client_for(self._client).info(
                f"{self.label} starting...", **self.start_kw,
            ))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        ntfy_client = _client_for(self._client)
        elapsed = format_duration(time.monotonic() - (self._t0 or time.monotonic()))
        if exc is None:
            _safe(lambda: ntfy_client.success(
                f"{self.label} finished OK in {elapsed}", **self.ok_kw,
            ))
        else:
            message = (
                f"{self.label} failed after {elapsed}.\n"
                f"Exception: {type(exc).__name__}: {exc}"
            )
            if self.include_traceback and exc.__traceback__ is not None:
                message += "\n\n" + format_exception(exc)
            _safe(lambda: ntfy_client.error(message, **self.err_kw))
        return False  # never swallow the exception
