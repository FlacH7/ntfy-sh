"""ntfy-sh -- push notifications for long-running compute jobs.

Self-contained package (standard library only, zero dependencies) to send
notifications to your phone through https://ntfy.sh (or a self-hosted
server). Designed to monitor experiments that run for hours on a remote
server over SSH + ``nohup``.

Quick setup
-----------
1. Pick a long, random topic name (the topic IS the credential)::

       NTFY_CHANNEL=my-secret-topic-8f3k2q

   and add the variable to your ``.env`` (or export it in the shell).
2. Install the ``ntfy`` app on your phone (Android/iOS) and subscribe to
   that exact topic.
3. Test::

       python -m ntfy_sh test

Typical usage
-------------
>>> from ntfy_sh import notify_success, notify_on_critical_error
>>> notify_success("Training finished", topic="my-topic")
>>>
>>> @notify_on_critical_error(topic="my-topic", title="[proj] ERROR")
... def long_pipeline(**cfg):
...     ...

Full documentation in the repository README.
"""

from .client import (
    DEFAULT_SERVER,
    VALID_PRIORITIES,
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
from .decorators import (
    format_duration,
    format_exception,
    notify_calls,
    notify_on_critical_error,
    notify_on_error,
    notify_on_success,
    watch,
)

__version__ = "1.0.0"

__all__ = [
    # client
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
    # decorators
    "notify_on_success",
    "notify_on_critical_error",
    "notify_on_error",
    "notify_calls",
    "watch",
    "format_duration",
    "format_exception",
]
