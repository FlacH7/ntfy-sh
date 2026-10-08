# ntfy-sh

[![CI](https://github.com/FlacH7/ntfy-sh/actions/workflows/ci.yml/badge.svg)](https://github.com/FlacH7/ntfy-sh/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ntfy-sh.svg)](https://pypi.org/project/ntfy-sh/)
[![Python](https://img.shields.io/pypi/pyversions/ntfy-sh.svg)](https://pypi.org/project/ntfy-sh/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Dependencies](https://img.shields.io/badge/dependencies-0-success.svg)](https://pypi.org/project/ntfy-sh/)

**Zero-dependency Python client for [ntfy.sh](https://ntfy.sh) push notifications, built to monitor long-running jobs.**

You launch a multi-hour experiment on a server over SSH with `nohup` and walk
away. With `ntfy-sh`, the process itself tells your phone when it finishes,
when a job inside the batch fails, or when a critical exception kills it. No
more discovering the next morning that it died after 20 minutes.

- **Zero dependencies** — pure standard library (`urllib`, `json`, `logging`,
  `threading`, `time`). Works on any bare CPython 3.10+ installation: locked-down
  HPC clusters, minimal containers, air-gapped boxes with an egress proxy.
- **Decorators & context manager** to monitor *functions and code blocks*, not
  just send messages: `@notify_on_critical_error`, `@notify_on_success`,
  `@notify_calls`, `watch()`.
- **Fail-safe by design** — a failed notification can *never* break the host
  program. Network down? ntfy.sh rate-limiting you? Your experiment keeps
  running; you get a log warning at most.
- **Full ntfy publish API** — title, priorities, emoji tags, click URLs,
  action buttons, delayed delivery (watchdog pattern), markdown, email,
  attachments metadata, `Cache`/`Firebase` headers, extra headers escape
  hatch, Basic/Bearer auth for reserved topics and self-hosted servers.
- **CLI included** — `python -m ntfy_sh test` (or the `ntfy-sh` console
  script) verifies your channel end-to-end in seconds.

Looking for the Julia sibling? See
[NtfySh.jl](https://github.com/FlacH7/NtfySh.jl) — same philosophy, same
API shape, written in idiomatic Julia.

---

## Contents

1. [Installation](#installation)
2. [Quickstart](#quickstart)
3. [Configuration](#configuration)
4. [Core API](#core-api)
5. [Monitoring tools](#monitoring-tools)
6. [CLI](#cli)
7. [`send()` reference](#send-reference)
8. [Priorities](#priorities)
9. [Emoji tags](#emoji-tags)
10. [Recipes](#recipes)
11. [Service limits](#service-limits)
12. [Design principles](#design-principles)
13. [Comparison with alternatives](#comparison-with-alternatives)
14. [Troubleshooting](#troubleshooting)
15. [Contributing & publishing](#contributing--publishing)

## Installation

```bash
pip install ntfy-sh
```

No transitive dependencies. No compiled extensions. Python 3.10+.

From source:

```bash
git clone https://github.com/FlacH7/ntfy-sh.git
cd ntfy-sh
pip install .
```

## Quickstart

1. **Pick a topic (the topic is the credential).** In ntfy there are no
   accounts for public topics: anyone who knows the name can read and
   publish. Choose something long and random, e.g. `myproj-batch-8f3k2qmx`
   (never `test`, `alerts`, or anything guessable). Export it:

   ```bash
   export NTFY_CHANNEL=myproj-batch-8f3k2qmx
   # or add it to a .env file next to your scripts
   ```

2. **Subscribe on your phone.** Install the
   [ntfy app](https://ntfy.sh/docs/subscribe/phone/) (Android: Play Store /
   F-Droid, iOS: App Store) and subscribe to the *exact* same topic. On
   Android, grant the notification permission and consider enabling instant
   (WebSocket) delivery per-topic so `high`/`urgent` break through Do Not
   Disturb.

3. **Verify the channel:**

   ```bash
   python -m ntfy_sh test
   ```

   You should get a notification within a couple of seconds.

4. **Use it from Python:**

   ```python
   from ntfy_sh import notify_success, notify_on_critical_error

   @notify_on_critical_error(title="[myproj] ERROR", catch_system_exit=True)
   def main():
       ...  # hours of work
       notify_success("Run finished in 3h 12m")

   if __name__ == "__main__":
       main()
   ```

Without `NTFY_CHANNEL` set, every call is a silent no-op — you can ship the
same code to a machine with no configuration and nothing changes.

## Configuration

| Variable        | Default           | Meaning                                                        |
|-----------------|-------------------|----------------------------------------------------------------|
| `NTFY_CHANNEL`  | *(empty = off)*   | Default topic. Without it the whole module is a no-op.          |
| `NTFY_TOPIC`    | *(empty)*         | Alias for `NTFY_CHANNEL` (CLI checks it first).                 |
| `NTFY_SERVER`   | `https://ntfy.sh` | Server URL (for self-hosted ntfy).                             |
| `NTFY_ENABLED`  | `1`               | `0`/`false`/`no`/`off` silences everything without code changes.|

The CLI additionally auto-loads a `.env` file from the current directory or
any parent (first match wins, shell variables take precedence, nothing is
overwritten). The library itself deliberately does **not** read `.env`
files, so it stays dependency-free and predictable.

Topic resolution order for every send: call argument `topic=` → client
topic (`NtfyClient(...)` / `configure(topic=...)`) → `NTFY_CHANNEL`.

## Core API

### Module-level shortcuts

```python
from ntfy_sh import notify, notify_info, notify_success, notify_warning, notify_error, ping

notify("raw message", title="Title", priority="high", tags=("fire",))
notify_info("starting preprocessing")            # low priority
notify_success("Training finished in 3h 12m")    # ✅ high priority
notify_warning("3 jobs failed, the rest are OK") # ⚠️ high priority
notify_error("OOM: the SS3 job died")            # 🚨 urgent
ping()                                           # minimal bell, priority 'min'
```

All of them accept the same options as [`send()`](#send-reference).

### Explicit client

```python
from ntfy_sh import NtfyClient

client = NtfyClient(
    topic="my-topic",
    server="https://ntfy.example.org",  # optional: self-hosted
    timeout=5.0,                        # seconds per attempt
    retries=2,                          # retries on 429 / 5xx / network errors
    # auth="user:password",             # or "tk_..." access token
    # raise_on_error=True,              # NOT recommended in production
)
client.success("done")
```

### Configuring the process-wide default client

```python
import ntfy_sh
ntfy_sh.configure(topic="my-topic")
ntfy_sh.notify_success("all good")
```

## Monitoring tools

This is the part that sets `ntfy-sh` apart from plain ntfy wrappers: you
monitor *code*, not just send messages.

### `@notify_on_critical_error` — the disaster notifier

Notifies with `urgent` priority if an exception escapes the function, then
**re-raises it** (execution stops exactly as it would without the decorator;
notifying does not cure anything):

```python
from ntfy_sh import notify_on_critical_error

@notify_on_critical_error(title="[myproj] critical ERROR", catch_system_exit=True)
def main():
    ...
```

| Option              | Default  | Meaning                                                    |
|---------------------|----------|-------------------------------------------------------------|
| `include_traceback` | `True`   | Append the last 12 lines of the traceback.                  |
| `catch_system_exit` | `False`  | Also notify on `sys.exit(code != 0)` (e.g. invalid params JSON). `--help` (exit 0) never notifies. |
| `notify_start`      | `False`  | Also send an informational notice on entry.                 |
| `re_raise`          | `True`   | Re-raise after notifying. Leave it `True`.                  |
| `topic` / `client`  | —        | Destination (default: `$NTFY_CHANNEL` via default client).  |

### `@notify_on_success` — the happy-end notifier

```python
from ntfy_sh import notify_on_success

@notify_on_success(title="[myproj] Training finished", send_result=True)
def train():
    ...  # notifies "module.train finished OK in 4h 02m 11s"
```

### `@notify_calls` — the full monitor

Combines the two above (+ optional start notice) in one decorator:

```python
from ntfy_sh import notify_calls

@notify_calls(title="[myproj] Experiment", notify_start=True)
def experiment():
    ...
```

### `watch()` — loose code blocks

```python
from ntfy_sh import watch

with watch("A6 common-rank (SS1)"):
    run_a6()  # notifies the fate of the block; the exception still propagates
```

### `format_duration` / `format_exception`

Public helpers used by the decorators, in case you build your own messages:
`format_duration(15126.3)` → `"4h 12m 06s"`.

## CLI

```text
python -m ntfy_sh test [--priority high] [--topic T] [--server URL]
python -m ntfy_sh ping [--topic T] [--server URL]
python -m ntfy_sh send "message" [--title T] [--priority P] [--tags a,b]
                        [--click URL] [--delay 30min] [--markdown] [--json]
                        [--topic T] [--server URL]
```

After `pip install ntfy-sh`, the same commands are available as the
`ntfy-sh` console script. Unlike the library, the CLI *does* exit non-zero
on failure (exit 2 = no topic, exit 1 = send failed): it is a diagnostic
tool. It auto-discovers `.env` files as described in
[Configuration](#configuration).

## `send()` reference

`NtfyClient.send(message, **options)` and its mirror `notify(message, **options)`:

| Option           | Type                | Meaning                                             |
|------------------|---------------------|-----------------------------------------------------|
| `title`          | `str`               | Notification title.                                 |
| `priority`       | `str` or `int` 1-5  | `min, low, default, high, urgent` (see below).      |
| `tags`           | list or `'a,b'`     | Emojis by shortcode (see below).                    |
| `click`          | `str`               | URL opened when the notification is tapped.         |
| `actions`        | list of dicts       | Action buttons (see recipes).                       |
| `delay`          | `str`               | Delayed delivery: `'30min'`, `'11h'`, `'9am'`, `'tomorrow, 9:00'` (max 3 days). |
| `markdown`       | `bool`              | Render the body as Markdown.                        |
| `email`          | `str`               | Also email a copy.                                  |
| `icon`           | `str` (URL)         | Notification icon.                                  |
| `filename`       | `str`               | Display name when used as attachment.               |
| `cache`          | `bool` (`True`)     | `False` → `Cache: no` (not kept in topic history).  |
| `firebase`       | `bool` (`True`)     | `False` → `Firebase: no` (useful for self-hosted).  |
| `topic`          | `str`               | Topic for this call only (overrides the client's).  |
| `extra_headers`  | `dict`              | Raw extra headers (escape hatch for new ntfy features). |
| `raise_on_error` | `bool`              | Overrides the constructor option for this call.     |

**Return semantics**: the JSON response dict from the server
(`{"id": "...", "event": "message", ...}`) on success; `None` if the client
was disabled, had no topic, or the send failed (in the default silent
mode). Bodies are automatically truncated at ~3.9 KB with an explicit
`[... message truncated]` marker.

**Robustness**: retries (default 1 extra attempt) apply only to transient
failures — HTTP 429 and 5xx, and network errors — with a short 1 s/2 s
backoff. Other 4xx codes fail immediately: they do not recover.

## Priorities

| Value     | Approximate behavior on the phone                          |
|-----------|--------------------------------------------------------------|
| `min`     | Not even a pop-up; only visible inside the app.             |
| `low`     | No sound, no vibration.                                      |
| `default` | Sound/vibration per system settings.                         |
| `high`    | Rings even in Do Not Disturb (per-topic instant delivery).  |
| `urgent`  | Max volume/vibration; on Android it insists until seen.     |

Shortcut defaults: `info→low`, `success→high`, `warning→high`,
`error→urgent`, `ping→min`.

## Emoji tags

| Shortcode                    | Emoji | Shortcode                  | Emoji |
|------------------------------|-------|----------------------------|-------|
| `white_check_mark`           | ✅    | `rotating_light`           | 🚨    |
| `tada`                       | 🎉    | `warning`                  | ⚠️    |
| `fire`                       | 🔥    | `information_source`       | ℹ️    |
| `chart_with_upwards_trend`   | 📈    | `chart_with_downwards_trend` | 📉  |
| `brain`                      | 🧠    | `computer`                 | 💻    |
| `hourglass`                  | ⏳    | `bell`                     | 🔔    |
| `rocket`                     | 🚀    | `bug`                      | 🐛    |
| `satellite_antenna`          | 📡    | `zzz`                      | 💤    |

Chain several: `tags=("brain", "chart_with_upwards_trend")`. Full list:
[ntfy emoji shortcodes](https://docs.ntfy.sh/publish/#tags-emojis).

## Recipes

### Canary — know at minute 0 that the channel works

Send a notification when the run *starts*. If your phone stays silent in
the first minutes, kill the process and fix the `.env`: you just saved
hours of a useless run.

```python
notify_info("Batch started: 25 jobs (window 100-200 s). Host: gpu-server.")
```

### Deferred watchdog — silence means death

A process killed by `kill -9` or the OOM killer cannot notify anyone.
Schedule a delayed message at startup: if the final `[OK]` has not arrived
by the time the watchdog fires, the run died silently on the way.

```python
notify(
    "If no [OK] arrived before this notice, the run died silently "
    "(kill -9 / OOM). Check the log.",
    title="Batch watchdog",
    delay="11h",
    priority="default",
)
```

### Action buttons

```python
notify_success(
    "Run v5 finished",
    title="[myproj] Batch OK",
    tags=("white_check_mark", "brain"),
    click="https://github.com/FlacH7/myproj",   # on tap
    actions=[{
        "action": "view", "label": "Open repo",
        "url": "https://github.com/FlacH7/myproj",
    }],
)
```

### Silencing without touching code

```bash
NTFY_ENABLED=0 nohup python -m mypipeline ...
```

### `.env` + config module integration

```python
# config.py — like any other variable
NTFY_CHANNEL = os.getenv("NTFY_CHANNEL")

# at the call site
from config import NTFY_CHANNEL
notify_success("finished", topic=NTFY_CHANNEL or None)
```

## Service limits

The [ntfy.sh](https://ntfy.sh) free tier:

- **Rate limit**: ~60 messages/hour per IP with an initial burst of 60. For
  a typical batch (start + one per failure + final) that is plenty; do not
  use it as a log sink.
- **Message size**: ~4 KB (this client truncates at 3.9 KB).
- **`delay`**: maximum 3 days.
- **Reliability**: ntfy.sh is best-effort with no SLA. For guarantees,
  self-host the server and point `NTFY_SERVER` at it.
- **Timeouts**: the client uses 10 s per attempt and 1 retry by default —
  a notification must never stall your run.

## Design principles

1. **A notification is an observer, not a participant.** Every sending path
   is wrapped so that failure degrades to a `logging.warning`. Decorators
   re-raise the *original* exception untouched. `watch` never swallows.
2. **Zero dependencies is a feature.** The value proposition is "copy this
   into any environment and it works". Adding a dep would delete the reason
   to choose this package.
3. **The topic is the credential.** The API surface assumes it: topics are
   validated, never logged above DEBUG, and the docs nudge you toward long
   random names, reserved topics, or self-hosting.
4. **Escape hatches over feature lock-in.** `extra_headers` exists so new
   ntfy server features can be used before this client catches up.

## Comparison with alternatives

|                        | ntfy-sh        | ntfy-wrapper | ntfy-client | aiontfy | Apprise |
|------------------------|----------------|--------------|-------------|---------|---------|
| Runtime dependencies   | **0**          | requests + typer + rich + xkcdpass | click + requests + websocket-client | aiohttp | many |
| Monitoring decorators / context manager | **yes** | no | no | no | no |
| Fail-safe semantics documented | **yes** | partial | no | no | n/a |
| CLI                    | **yes** (stdlib) | yes (typer) | yes (click) | no | yes |
| Receiving / subscribing | no | no | **yes** | **yes** | no |
| Multi-service backends | no | no | no | no | **100+** |
| Self-hosted server     | **yes** | yes | yes | yes | yes |
| Auth (Basic/token)     | **yes** | partial | yes | yes | yes |

Use [Apprise](https://github.com/caronc/apprise) if you need many notification
services behind one API. Use [ntfy-client](https://github.com/iacchus/ntfy-client)
or [aiontfy](https://github.com/tr4nt0r/aiontfy) to *subscribe* to topics from
Python. Use **ntfy-sh** to keep an eye on long-running *code* from anywhere
with zero dependencies.

## Troubleshooting

| Symptom                              | Likely cause                                            |
|--------------------------------------|---------------------------------------------------------|
| Nothing arrives                      | App topic differs from `NTFY_CHANNEL` (compare char by char). |
| Nothing arrives, warning in the log  | Server has no Internet egress (port 443) or needs a proxy: set `https_proxy`. |
| `[ERROR] HTTP 403`                   | Topic is reserved by another account: pick another one. |
| `[ERROR] HTTP 429`                   | Rate limit: too many messages in a row.                 |
| Only arrives when the app is opened  | (Android) instant delivery disabled; enable WebSocket per topic in the app. |
| `Invalid ntfy topic`                 | Topic contains spaces, `:` or `#`. Only `[A-Za-z0-9_-]`, max 64. |

Internal logging: the module logs under the `ntfy_sh` logger — with
`logging.basicConfig(level=logging.DEBUG)` you will see every skipped/sent
notification.

## Contributing & publishing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The test
suite is fully offline (network is mocked), so `pytest` runs anywhere.

Releases follow [Semantic Versioning](https://semver.org/); see
[CHANGELOG.md](CHANGELOG.md). Publishing to PyPI is automated via GitHub
Actions Trusted Publishing (OIDC): creating a GitHub **Release** triggers
the [publish workflow](.github/workflows/publish.yml).

## License

[MIT](LICENSE) — © 2026 FlacH7.
