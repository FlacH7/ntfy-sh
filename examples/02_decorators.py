"""Monitoring long-running functions with decorators.

The classic pattern for experiments that run for hours over SSH + nohup:
you want to know when it finished, and ESPECIALLY when it died.

Run:  python examples/02_decorators.py
"""

import random
import time

from ntfy_sh import notify_calls, notify_on_critical_error, notify_on_success


@notify_on_critical_error(title="[demo] critical ERROR", catch_system_exit=True)
def main():
    """Top-level guard: if ANY exception escapes, the phone gets an urgent
    notification with the traceback before the process dies."""
    run_batch()


@notify_calls(title="[demo] batch", notify_start=True)
def run_batch():
    """Full monitor: start + success + error notifications."""
    total = 3
    failures = 0
    for i in range(1, total + 1):
        train_one_job(i)  # each job is individually watched too
    if failures:
        raise RuntimeError(f"{failures}/{total} jobs failed")


@notify_on_success(title="[demo] job", send_result=False)
def train_one_job(i: int):
    """Simulated training step."""
    time.sleep(0.2)
    if random.random() < 0.25:  # flaky!
        raise RuntimeError(f"simulated OOM in job {i}")
    return {"loss": 0.01 * i}


if __name__ == "__main__":
    main()
