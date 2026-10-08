"""CLI for the ntfy-sh module: quick test and sending from the terminal.

Usage::

    python -m ntfy_sh test
        Full-field test notification to the $NTFY_CHANNEL topic.

    python -m ntfy_sh ping
        Minimal ping (priority 'min', makes no noise).

    python -m ntfy_sh send "message" --title "Title" --priority high
        Arbitrary send.

Common options for all subcommands::

    --topic MY-TOPIC    Destination topic (default: $NTFY_TOPIC or $NTFY_CHANNEL)
    --server URL        Server (default: $NTFY_SERVER or https://ntfy.sh)

Topic resolution, in order of precedence:

    1. --topic (command argument);
    2. NTFY_TOPIC or NTFY_CHANNEL variable exported in the shell;
    3. NTFY_TOPIC/NTFY_CHANNEL read from a .env file in the current
       directory or any parent directory (see ``_load_env_file``). This
       makes ``python -m ntfy_sh test`` work from the repo root without
       exporting anything in the shell.

Unlike the library (silent on failure so it never breaks a run), the CLI
DOES return a non-zero exit code if the notification could not be sent:
it is a diagnostic tool.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

from .client import NtfyClient, NtfyError

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _load_env_file() -> Path | None:
    """Load a ``.env`` (if any) from the CWD or any parent directory.

    Minimal stdlib-only parser meant for the CLI: it makes
    ``python -m ntfy_sh test`` work from the repo root without exporting
    anything in the shell. The LIBRARY deliberately does not use this:
    ``client.py`` only sees process environment variables so it stays
    100% portable.

    Rules (compatible with python-dotenv in the essentials):

    * looks for ``.env`` in the current directory and UP to the root;
      uses the FIRST one found (closest to the CWD wins);
    * ignores blank lines and comments starting with ``#``;
    * accepts the ``export `` prefix and spaces around ``=``:
      ``NTFY_CHANNEL = value`` and ``NTFY_CHANNEL=value`` are equivalent;
    * strips surrounding quotes from the value (``"..."`` or ``'...'``);
    * does NOT overwrite variables that already exist in the environment:
      what is exported in the shell (or set by the process itself) wins,
      just like ``load_dotenv()`` by default.

    Trailing comments (``KEY=val # note``) are NOT supported: put them on
    their own line. On any error returns None silently -- this is a
    diagnostic convenience, not a critical dependency.

    Returns
    -------
    Path | None
        Path of the .env file that was loaded (None if none found or it
        failed to parse).
    """
    try:
        here = Path.cwd()
        env_path: Path | None = None
        for cand in (here, *here.parents):
            if (cand / ".env").is_file():
                env_path = cand / ".env"
                break
        if env_path is None:
            return None
        for raw in env_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export ") or line.startswith("export\t"):
                line = line[7:].lstrip()
            key, _, val = line.partition("=")
            key = key.strip()
            if not _ENV_KEY_RE.match(key):
                continue  # odd key: ignore the line, do not break the CLI
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1].strip()
            if key not in os.environ:
                os.environ[key] = val
        return env_path
    except Exception:
        return None


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--topic", "-t", default=None,
        help="Destination topic. Default: $NTFY_TOPIC or $NTFY_CHANNEL.",
    )
    parser.add_argument(
        "--server", default=None,
        help="ntfy server. Default: $NTFY_SERVER or https://ntfy.sh.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ntfy-sh",
        description=(
            "Push notifications for long-running jobs. "
            "Set NTFY_CHANNEL in your .env or use --topic."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_test = sub.add_parser(
        "test", help="Test notification with all fields set.",
    )
    _add_common_args(p_test)
    p_test.add_argument("--priority", default="high")

    p_ping = sub.add_parser("ping", help="Minimal ping (priority 'min').")
    _add_common_args(p_ping)

    p_send = sub.add_parser("send", help="Send a message.")
    _add_common_args(p_send)
    p_send.add_argument("message", help="Body of the message.")
    p_send.add_argument("--title", default=None)
    p_send.add_argument(
        "--priority", default=None,
        help="min | low | default | high | urgent",
    )
    p_send.add_argument(
        "--tags", default=None,
        help="Emoji shortcodes separated by comma, e.g. 'fire,chart'.",
    )
    p_send.add_argument("--click", default=None, help="URL opened on tap.")
    p_send.add_argument(
        "--delay", default=None,
        help="Delayed delivery: 30min, 11h, 9am, ...",
    )
    p_send.add_argument(
        "--markdown", action="store_true", help="Render as Markdown.",
    )
    p_send.add_argument(
        "--json", action="store_true",
        help="Print the raw JSON response from the server.",
    )

    return parser


def _test_message(topic: str, server: str) -> str:
    return (
        f"If you can read this, topic **{topic}** is working.\n\n"
        f"- Server : `{server}`\n"
        f"- Python : `{platform.python_version()}`\n"
        f"- Host   : `{platform.node() or 'unknown'}`\n"
        f"- Date   : `{datetime.now().isoformat(timespec='seconds')}`\n"
        f"- ID     : `{uuid.uuid4().hex[:8]}`\n\n"
        "You can dismiss this message in the app by swiping it away."
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # The CLI (unlike the library) DOES read the repo's .env:
    # it is a diagnostic tool meant to be launched from the repo root.
    # It never overwrites what is already exported in the shell.
    _load_env_file()

    topic = (
        args.topic or os.getenv("NTFY_TOPIC") or os.getenv("NTFY_CHANNEL") or ""
    ).strip()
    if not topic:
        print(
            "ERROR: no topic configured. Options:\n"
            "  1) NTFY_CHANNEL=my-topic in the repo's .env, running the\n"
            "     command from the repo root (or any subdirectory);\n"
            "  2) export NTFY_CHANNEL=my-topic in the shell;\n"
            "  3) --topic my-topic in the command itself.",
            file=sys.stderr,
        )
        return 2

    # The CLI does want exceptions: it is a diagnostic tool.
    client = NtfyClient(
        topic=topic, server=args.server, raise_on_error=True,
    )

    try:
        if args.command == "test":
            server = args.server or os.getenv("NTFY_SERVER") or "https://ntfy.sh"
            response = client.send(
                _test_message(topic, server),
                title="ntfy-sh test",
                priority=args.priority,
                tags=("white_check_mark", "gear"),
                markdown=True,
            )
        elif args.command == "ping":
            response = client.ping()
        else:  # send
            kwargs = {}
            if args.title:
                kwargs["title"] = args.title
            if args.priority:
                kwargs["priority"] = args.priority
            if args.tags:
                kwargs["tags"] = args.tags
            if args.click:
                kwargs["click"] = args.click
            if args.delay:
                kwargs["delay"] = args.delay
            if args.markdown:
                kwargs["markdown"] = True
            response = client.send(args.message, **kwargs)
    except NtfyError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    msg_id = (response or {}).get("id", "?")
    if args.command == "send" and getattr(args, "json", False):
        print(json.dumps(response, indent=2))
    else:
        print(f"[OK] Notification sent to topic '{topic}' (id={msg_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
