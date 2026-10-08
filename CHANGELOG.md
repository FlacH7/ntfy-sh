# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-07

### Added

- `NtfyClient`: zero-dependency HTTP client (stdlib `urllib` only) for
  publishing to [ntfy.sh](https://ntfy.sh) or any self-hosted ntfy server.
- Full support for the ntfy publish API: `title`, `priority` (name or 1-5),
  `tags`, `click`, `actions` (buttons), `delay`, `markdown`, `email`,
  `icon`, `filename`, `Cache: no` and `Firebase: no` headers,
  `extra_headers` escape hatch, plus Basic/Bearer authentication.
- Automatic message truncation at ~3.9 KB with an explicit marker.
- Retries with short backoff on transient failures (HTTP 429 and 5xx).
- Fail-safe semantics: a failed notification never breaks the host program
  (logs a warning and returns `None`) unless `raise_on_error=True`.
- Semantic shortcuts: `notify`, `notify_info`, `notify_success`,
  `notify_warning`, `notify_error`, `ping`.
- Process-wide default client with `configure()` / `reset_default_client()`.
- Job-monitoring tools: `@notify_on_critical_error` (with optional
  `catch_system_exit`), `@notify_on_success`, `@notify_calls`, and the
  `watch()` context manager.
- `format_duration()` / `format_exception()` public helpers.
- CLI: `python -m ntfy_sh {test,ping,send}` (also installed as the
  `ntfy-sh` console script), with `.env` auto-discovery from the current
  directory upwards.
- Environment configuration: `NTFY_CHANNEL`, `NTFY_TOPIC`, `NTFY_SERVER`,
  `NTFY_ENABLED`.

[1.0.0]: https://github.com/FlacH7/ntfy-sh/releases/tag/v1.0.0
