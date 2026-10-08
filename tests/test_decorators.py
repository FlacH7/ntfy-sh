"""Tests for :mod:`ntfy_sh.decorators`."""

from __future__ import annotations

import sys

import pytest
from conftest import DummyClient

from ntfy_sh import decorators as dec
from ntfy_sh import (
    format_duration,
    format_exception,
    notify_calls,
    notify_on_critical_error,
    notify_on_success,
    watch,
)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

class TestFormatDuration:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "0s"),
            (37.4, "37s"),
            (36.6, "37s"),  # rounded
            (252, "4m 12s"),
            (65, "1m 05s"),
            (3745, "1h 02m 25s"),
            (15126.3, "4h 12m 06s"),
            (-5, "0s"),  # negative clamped
        ],
    )
    def test_values(self, seconds, expected):
        assert format_duration(seconds) == expected


class TestFormatException:
    def _raise(self):
        try:
            raise ValueError("boom")
        except ValueError as exc:
            return exc

    def test_contains_exception(self):
        text = format_exception(self._raise())
        assert "ValueError: boom" in text
        assert text.startswith("Traceback")

    def test_tail_trimming(self):
        text = format_exception(self._raise(), tail=3)
        assert "[...] traceback trimmed" in text
        assert "ValueError: boom" in text
        assert text.startswith("Traceback")

    def test_short_traceback_not_trimmed(self):
        text = format_exception(self._raise(), tail=50)
        assert "[... traceback trimmed" not in text


class TestLabel:
    def test_module_and_name(self):
        def some_function():
            pass

        label = dec._label(some_function)
        assert label.endswith(".some_function")

    def test_nested_qualname(self):
        class C:
            def method(self):
                pass

        assert dec._label(C.method).endswith("C.method")


# ---------------------------------------------------------------------------
# notify_on_success
# ---------------------------------------------------------------------------

class TestNotifyOnSuccess:
    def test_notifies_with_duration(self):
        dummy = DummyClient()

        @notify_on_success(client=dummy)
        def work():
            return 42

        assert work() == 42
        kinds = dummy.kinds()
        assert kinds == ["success"]
        kind, message, _ = dummy.calls[0]
        assert "work" in message
        assert "finished OK" in message

    def test_send_result(self):
        dummy = DummyClient()

        @notify_on_success(client=dummy, send_result=True)
        def work():
            return {"metric": 0.97}

        work()
        assert "0.97" in dummy.calls[0][1]

    def test_without_duration(self):
        dummy = DummyClient()

        @notify_on_success(client=dummy, include_duration=False)
        def work():
            pass

        work()
        assert "in " not in dummy.calls[0][1]

    def test_notify_start(self):
        dummy = DummyClient()

        @notify_on_success(client=dummy, notify_start=True)
        def work():
            pass

        work()
        assert dummy.kinds() == ["info", "success"]
        assert "starting" in dummy.calls[0][1]

    def test_bare_decoration_without_parens(self):
        # client kwarg cannot be used in bare form; use default client path
        # by monkeypatching get_default_client indirectly through configure.
        from ntfy_sh import configure

        recorder = DummyClient()
        configure(topic="t")

        import ntfy_sh.client as c

        # Point the decorators' default client resolution at the recorder.
        dec.get_default_client = lambda: recorder
        try:

            @notify_on_success
            def work():
                pass

            work()
        finally:
            # restore the real function
            dec.get_default_client = c.get_default_client

        assert recorder.kinds() == ["success"]

    def test_exception_propagates_untouched(self):
        dummy = DummyClient()

        @notify_on_success(client=dummy)
        def work():
            raise RuntimeError("inner failure")

        with pytest.raises(RuntimeError, match="inner failure"):
            work()
        # success decorator does NOT notify errors
        assert dummy.kinds() == []

    def test_send_failure_does_not_break(self):
        dummy = DummyClient(fail=True)

        @notify_on_success(client=dummy)
        def work():
            return "ok"

        assert work() == "ok"  # notification failure is swallowed

    def test_kwargs_forwarded(self):
        dummy = DummyClient()

        @notify_on_success(client=dummy, topic="custom-topic", title="Custom")
        def work():
            pass

        work()
        _, _, kw = dummy.calls[0]
        assert kw["topic"] == "custom-topic"
        assert kw["title"] == "Custom"


# ---------------------------------------------------------------------------
# notify_on_critical_error
# ---------------------------------------------------------------------------

class TestNotifyOnCriticalError:
    def _decorated(self, dummy, **opts):
        @notify_on_critical_error(client=dummy, **opts)
        def work():
            raise ValueError("catastrophe")

        return work

    def test_notifies_and_reraises(self):
        dummy = DummyClient()
        work = self._decorated(dummy)
        with pytest.raises(ValueError, match="catastrophe"):
            work()
        kinds = dummy.kinds()
        assert kinds == ["error"]
        message = dummy.calls[0][1]
        assert "work" in message
        assert "ValueError: catastrophe" in message
        assert "failed after" in message

    def test_traceback_included(self):
        dummy = DummyClient()
        work = self._decorated(dummy)
        with pytest.raises(ValueError):
            work()
        assert "Traceback" in dummy.calls[0][1]

    def test_traceback_excluded(self):
        dummy = DummyClient()
        work = self._decorated(dummy, include_traceback=False)
        with pytest.raises(ValueError):
            work()
        assert "Traceback" not in dummy.calls[0][1]

    def test_no_reraise_returns_none(self):
        dummy = DummyClient()

        @notify_on_critical_error(client=dummy, re_raise=False)
        def work():
            raise ValueError("swallowed")

        assert work() is None
        assert dummy.kinds() == ["error"]

    def test_system_exit_not_caught_by_default(self):
        dummy = DummyClient()

        @notify_on_critical_error(client=dummy)
        def work():
            sys.exit(1)

        with pytest.raises(SystemExit):
            work()
        assert dummy.kinds() == []

    def test_catch_system_exit_code_nonzero(self):
        dummy = DummyClient()

        @notify_on_critical_error(client=dummy, catch_system_exit=True)
        def work():
            sys.exit(3)

        with pytest.raises(SystemExit):
            work()
        assert dummy.kinds() == ["error"]
        assert "sys.exit(3)" in dummy.calls[0][1]

    def test_catch_system_exit_ignores_zero(self):
        dummy = DummyClient()

        @notify_on_critical_error(client=dummy, catch_system_exit=True)
        def work():
            sys.exit(0)

        with pytest.raises(SystemExit):
            work()
        assert dummy.kinds() == []

    def test_catch_system_exit_ignores_none(self):
        dummy = DummyClient()

        @notify_on_critical_error(client=dummy, catch_system_exit=True)
        def work():
            sys.exit()

        with pytest.raises(SystemExit):
            work()
        assert dummy.kinds() == []

    def test_notify_start(self):
        dummy = DummyClient()
        work = self._decorated(dummy, notify_start=True)
        with pytest.raises(ValueError):
            work()
        assert dummy.kinds() == ["info", "error"]

    def test_send_failure_does_not_mask_original_error(self):
        dummy = DummyClient(fail=True)
        work = self._decorated(dummy)
        with pytest.raises(ValueError, match="catastrophe"):
            work()  # original exception still propagates

    def test_error_kwargs_forwarded(self):
        dummy = DummyClient()

        @notify_on_critical_error(
            client=dummy, topic="ops", title="[ops] ERROR", priority="urgent"
        )
        def work():
            raise ValueError("x")

        with pytest.raises(ValueError):
            work()
        _, _, kw = dummy.calls[0]
        assert kw["topic"] == "ops"
        assert kw["title"] == "[ops] ERROR"


# ---------------------------------------------------------------------------
# notify_calls
# ---------------------------------------------------------------------------

class TestNotifyCalls:
    def test_success_path(self):
        dummy = DummyClient()

        @notify_calls(client=dummy, notify_start=True)
        def work():
            return 7

        assert work() == 7
        assert dummy.kinds() == ["info", "success"]
        assert "finished OK" in dummy.calls[-1][1]
        assert "Monitor -- OK" == dummy.calls[-1][2]["title"]

    def test_error_path(self):
        dummy = DummyClient()

        @notify_calls(client=dummy)
        def work():
            raise KeyError("missing")

        with pytest.raises(KeyError):
            work()
        assert dummy.kinds() == ["error"]
        assert "Monitor -- ERROR" == dummy.calls[-1][2]["title"]
        assert "KeyError" in dummy.calls[-1][1]

    def test_error_not_notified_when_disabled(self):
        dummy = DummyClient()

        @notify_calls(client=dummy, on_error=False)
        def work():
            raise KeyError("missing")

        with pytest.raises(KeyError):
            work()
        assert dummy.kinds() == []

    def test_success_not_notified_when_disabled(self):
        dummy = DummyClient()

        @notify_calls(client=dummy, on_success=False)
        def work():
            return 1

        work()
        assert dummy.kinds() == []

    def test_bare_form(self):
        from ntfy_sh import configure

        recorder = DummyClient()
        configure(topic="t")
        dec.get_default_client = lambda: recorder
        try:

            @notify_calls
            def work():
                return 1

            work()
        finally:
            import ntfy_sh.client as c

            dec.get_default_client = c.get_default_client

        assert recorder.kinds() == ["success"]


# ---------------------------------------------------------------------------
# watch (context manager)
# ---------------------------------------------------------------------------

class TestWatch:
    def test_success_notifies(self):
        dummy = DummyClient()
        with watch("stage-1", client=dummy):
            pass
        assert dummy.kinds() == ["success"]
        kind, message, kw = dummy.calls[0]
        assert "stage-1" in message
        assert "finished OK" in message
        assert kw["title"] == "stage-1 -- OK"

    def test_error_notifies_and_propagates(self):
        dummy = DummyClient()
        with pytest.raises(RuntimeError, match="block failed"):
            with watch("stage-2", client=dummy):
                raise RuntimeError("block failed")
        assert dummy.kinds() == ["error"]
        message = dummy.calls[0][1]
        assert "stage-2" in message
        assert "RuntimeError: block failed" in message
        assert "Traceback" in message  # included by default

    def test_no_traceback_when_disabled(self):
        dummy = DummyClient()
        with pytest.raises(RuntimeError):
            with watch("stage-3", client=dummy, include_traceback=False):
                raise RuntimeError("x")
        assert "Traceback" not in dummy.calls[0][1]

    def test_notify_start(self):
        dummy = DummyClient()
        with watch("stage-4", client=dummy, notify_start=True):
            pass
        assert dummy.kinds() == ["info", "success"]

    def test_send_failure_does_not_break_block(self):
        dummy = DummyClient(fail=True)
        with watch("stage-5", client=dummy):
            result = "done"
        assert result == "done"

    def test_send_failure_does_not_mask_exception(self):
        dummy = DummyClient(fail=True)
        with pytest.raises(ValueError, match="orig"):
            with watch("stage-6", client=dummy):
                raise ValueError("orig")

    def test_custom_title(self):
        dummy = DummyClient()
        with watch("s", client=dummy, title="My Title"):
            pass
        assert dummy.calls[0][2]["title"] == "My Title"

    def test_priorities(self):
        dummy = DummyClient()
        with watch("s", client=dummy):
            pass
        assert dummy.calls[0][2]["priority"] == "high"
        with pytest.raises(RuntimeError):
            with watch("s", client=dummy):
                raise RuntimeError()
        assert dummy.calls[-1][2]["priority"] == "urgent"
