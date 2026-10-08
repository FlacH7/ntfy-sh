"""Quickstart: the three-minute tour.

Before running: install the ntfy app on your phone, subscribe to a topic
you choose, and export it:

    export NTFY_CHANNEL=my-long-random-topic-4k9xq2

Run:  python examples/01_quickstart.py
"""

from ntfy_sh import notify, notify_error, notify_info, notify_success, notify_warning, ping

# If NTFY_CHANNEL is not set, every call below is a silent no-op,
# so this script is safe to run anywhere.
ping()                                              # minimal connectivity check
notify_info("starting preprocessing")               # low priority, quiet
notify("raw message", title="Raw", priority="default", tags=("bell",))
notify_success("step 1/3 done: preprocessing")      # ✅ high priority
notify_warning("step 2/3 done, but 2 jobs were skipped")
notify_error("step 3/3 FAILED: OOM on worker 4")    # 🚨 urgent: rings

# One-off send to a different topic without changing configuration:
notify("ops message", topic="ops-standby", title="Different topic")
