# Contributing to ntfy-sh

Thanks for your interest in improving **ntfy-sh**! Bug reports, documentation
fixes, and feature ideas are all welcome.

## Ground rules

- **Zero dependencies is a hard constraint.** The package must keep working
  with a bare CPython interpreter. Anything that needs `requests`, `pydantic`,
  `typer`, etc. will be rejected — use `urllib` (or propose an optional extra
  instead of a hard dependency).
- **Fail-safe is a design contract.** A notification must never alter the
  behavior of the host program. If you add a feature, ask yourself: "what
  happens if ntfy.sh is unreachable?" The answer must be "nothing breaks".
- The public API is stable (semver). Breaking changes need a very good reason
  and a major version bump.

## Development setup

```bash
git clone https://github.com/FlacH7/ntfy-sh.git
cd ntfy-sh
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .[test]
```

## Running the tests

The test suite is fully offline (network access is mocked), so it is safe to
run anywhere:

```bash
pytest -v
```

Add tests for any new behavior. Tests live in `tests/` and mirror the module
layout (`test_client.py`, `test_decorators.py`, `test_cli.py`).

## Linting

```bash
pip install ruff
ruff check .
```

CI runs the same command; fix all warnings before opening a PR.

## Testing against a real ntfy topic (optional)

The suite never contacts the network, but you can manually verify end-to-end:

```bash
export NTFY_CHANNEL=my-own-random-test-topic-9x7q
python -m ntfy_sh test
```

(Subscribe to the same topic in the [ntfy app](https://ntfy.sh) first.)

## Submitting changes

1. Fork the repo and create a branch: `git switch -c my-fix`.
2. Make your change, with tests.
3. Run `pytest -v` and `ruff check .` — both must pass.
4. Open a Pull Request describing **what** and **why** (link any issue).

## Reporting bugs

Open an issue with:

- ntfy-sh version (`pip show ntfy_sh`) and Python version;
- whether you use ntfy.sh or a self-hosted server (version if known);
- the log output with `logging.basicConfig(level=logging.DEBUG)` — the
  internal logger is named `ntfy_sh`;
- a minimal reproduction snippet.

**Never paste your real topic name or access tokens in a public issue** —
topics are credentials. Redact them.

## Security notes

- The topic name acts as a password (anyone who knows it can read/write).
  Prefer long random names; consider a registered+reserved topic or a
  self-hosted server with auth for anything sensitive.
- Auth strings (`user:password`, `tk_...` tokens) must be provided via
  environment variables or code, never committed.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
