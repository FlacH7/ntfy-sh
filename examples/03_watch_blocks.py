"""Monitoring loose code blocks with the watch() context manager.

Use it when the interesting unit is a block, not a function.

Run:  python examples/03_watch_blocks.py
"""

import time

from ntfy_sh import watch

with watch("Stage A: embedding", notify_start=True):
    time.sleep(0.5)  # ... your computation here ...

# The block failing is reported and the exception still propagates
# (a notification must never change control flow):
try:
    with watch("Stage B: dynamics (expected to fail)"):
        time.sleep(0.2)
        raise ValueError("singular matrix in stage B")
except ValueError:
    print("caught the exception as usual — the phone already knows")

with watch("Stage C: selection"):
    time.sleep(0.3)
