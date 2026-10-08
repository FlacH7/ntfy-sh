"""Tests for the CLI (:mod:`ntfy_sh.__main__`)."""

from __future__ import annotations

import os

import pytest
from conftest import FakeResponse, RecordingUrlopen, make_http_error

from ntfy_sh.__main__ import _load_env_file, main


def _clean_topic_env(monkeypatch):
    monkeypatch.delenv("NTFY_CHANNEL", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)


class TestParser:
    def test_send_requires_message(self, monkeypatch):
        _clean_topic_env(monkeypatch)
        with pytest.raises(SystemExit):
            main(["send"])

    def test_unknown_command(self, monkeypatch):
        _clean_topic_env(monkeypatch)
        with pytest.raises(SystemExit):
            main(["explode"])

    def test_command_is_required(self, monkeypatch):
        _clean_topic_env(monkeypatch)
        with pytest.raises(SystemExit):
            main([])


class TestNoTopic:
    def test_exit_code_2_without_topic(self, monkeypatch, tmp_path):
        _clean_topic_env(monkeypatch)
        monkeypatch.chdir(tmp_path)  # no .env anywhere up the tree
        assert main(["ping"]) == 2

    def test_helpful_message_on_stderr(self, monkeypatch, tmp_path, capsys):
        _clean_topic_env(monkeypatch)
        monkeypatch.chdir(tmp_path)
        main(["ping"])
        err = capsys.readouterr().err
        assert "no topic configured" in err
        assert "--topic" in err


class TestSendCommand:
    def test_ok(self, monkeypatch, capsys):
        _clean_topic_env(monkeypatch)
        recorder = RecordingUrlopen([FakeResponse(b'{"id": "cli1"}')])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        code = main(["send", "hello", "--topic", "cli-topic"])
        assert code == 0
        out = capsys.readouterr().out
        assert "[OK]" in out
        assert "cli-topic" in out
        assert "cli1" in out
        req = recorder.requests[0]
        assert req.full_url == "https://ntfy.sh/cli-topic"
        assert req.data.decode() == "hello"

    def test_all_options(self, monkeypatch, capsys):
        _clean_topic_env(monkeypatch)
        recorder = RecordingUrlopen([FakeResponse(b'{"id": "cli2"}')])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        code = main(
            [
                "send", "body text",
                "--topic", "cli-topic",
                "--title", "Cli Title",
                "--priority", "high",
                "--tags", "fire,chart",
                "--click", "https://x.org",
                "--delay", "30min",
                "--markdown",
            ]
        )
        assert code == 0
        h = recorder.requests[0].headers
        assert h["Title"] == "Cli Title"
        assert h["Priority"] == "high"
        assert h["Tags"] == "fire,chart"
        assert h["Click"] == "https://x.org"
        assert h["Delay"] == "30min"
        assert h["Markdown"] == "true"

    def test_server_option(self, monkeypatch):
        _clean_topic_env(monkeypatch)
        recorder = RecordingUrlopen([FakeResponse()])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        main(["send", "m", "--topic", "t", "--server", "https://n.example.org"])
        assert recorder.requests[0].full_url == "https://n.example.org/t"

    def test_topic_from_env(self, monkeypatch):
        monkeypatch.setenv("NTFY_CHANNEL", "env-cli")
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        recorder = RecordingUrlopen([FakeResponse()])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        main(["send", "m"])
        assert recorder.requests[0].full_url.endswith("/env-cli")

    def test_topic_env_precedence(self, monkeypatch):
        # NTFY_TOPIC (new name) wins over NTFY_CHANNEL (legacy alias)
        monkeypatch.setenv("NTFY_CHANNEL", "legacy")
        monkeypatch.setenv("NTFY_TOPIC", "modern")
        recorder = RecordingUrlopen([FakeResponse()])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        main(["send", "m"])
        assert recorder.requests[0].full_url.endswith("/modern")

    def test_error_exit_code_1(self, monkeypatch, capsys):
        _clean_topic_env(monkeypatch)
        recorder = RecordingUrlopen([make_http_error(403, b"forbidden")])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        code = main(["send", "m", "--topic", "t"])
        assert code == 1
        assert "[ERROR]" in capsys.readouterr().err

    def test_json_output(self, monkeypatch, capsys):
        _clean_topic_env(monkeypatch)
        recorder = RecordingUrlopen([FakeResponse(b'{"id": "j1", "event": "message"}')])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        main(["send", "m", "--topic", "t", "--json"])
        out = capsys.readouterr().out
        assert '"id": "j1"' in out


class TestPingCommand:
    def test_ok(self, monkeypatch, capsys):
        _clean_topic_env(monkeypatch)
        recorder = RecordingUrlopen([FakeResponse()])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        assert main(["ping", "--topic", "t"]) == 0
        assert "[OK]" in capsys.readouterr().out
        assert recorder.requests[0].headers["Priority"] == "min"


class TestTestCommand:
    def test_ok_markdown(self, monkeypatch, capsys):
        _clean_topic_env(monkeypatch)
        recorder = RecordingUrlopen([FakeResponse()])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        assert main(["test", "--topic", "t"]) == 0
        req = recorder.requests[0]
        assert req.headers["Markdown"] == "true"
        assert req.headers["Title"] == "ntfy-sh test"
        body = req.data.decode()
        assert "topic **t** is working" in body
        assert "Python" in body

    def test_priority_option(self, monkeypatch):
        _clean_topic_env(monkeypatch)
        recorder = RecordingUrlopen([FakeResponse()])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        main(["test", "--topic", "t", "--priority", "low"])
        assert recorder.requests[0].headers["Priority"] == "low"


class TestLoadEnvFile:
    def test_loads_topic_from_env_file(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("NTFY_CHANNEL=file-topic\n")
        monkeypatch.delenv("NTFY_CHANNEL", raising=False)
        monkeypatch.chdir(tmp_path)
        try:
            path = _load_env_file()
            assert path == tmp_path / ".env"
            assert os.environ["NTFY_CHANNEL"] == "file-topic"
        finally:
            os.environ.pop("NTFY_CHANNEL", None)

    def test_closest_env_wins(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("NTFY_CHANNEL=outer\n")
        nested = tmp_path / "nested"
        nested.mkdir()
        (nested / ".env").write_text("NTFY_CHANNEL=inner\n")
        monkeypatch.delenv("NTFY_CHANNEL", raising=False)
        monkeypatch.chdir(nested)
        try:
            _load_env_file()
            assert os.environ["NTFY_CHANNEL"] == "inner"
        finally:
            os.environ.pop("NTFY_CHANNEL", None)

    def test_does_not_overwrite_shell(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("NTFY_CHANNEL=file-topic\n")
        monkeypatch.setenv("NTFY_CHANNEL", "shell-topic")  # reverted by monkeypatch
        monkeypatch.chdir(tmp_path)
        _load_env_file()
        assert os.environ["NTFY_CHANNEL"] == "shell-topic"

    def test_export_prefix_and_quotes(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text(
            'export NTFY_LOADTEST1 = "quoted value"\n'
            "NTFY_LOADTEST2='single'\n"
            "# comment line\n"
            "\n"
            "BROKEN LINE WITHOUT EQUALS\n"
        )
        monkeypatch.chdir(tmp_path)
        try:
            _load_env_file()
            assert os.environ["NTFY_LOADTEST1"] == "quoted value"
            assert os.environ["NTFY_LOADTEST2"] == "single"
        finally:
            os.environ.pop("NTFY_LOADTEST1", None)
            os.environ.pop("NTFY_LOADTEST2", None)

    def test_no_env_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _load_env_file() is None

    def test_cli_uses_env_file(self, tmp_path, monkeypatch):
        (tmp_path / ".env").write_text("NTFY_CHANNEL=from-file\n")
        monkeypatch.delenv("NTFY_CHANNEL", raising=False)
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        monkeypatch.chdir(tmp_path)
        recorder = RecordingUrlopen([FakeResponse()])
        monkeypatch.setattr("ntfy_sh.client.urllib.request.urlopen", recorder)
        try:
            assert main(["ping"]) == 0
            assert recorder.requests[0].full_url.endswith("/from-file")
        finally:
            os.environ.pop("NTFY_CHANNEL", None)
