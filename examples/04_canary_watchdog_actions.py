"""Production recipes: canary, deferred watchdog, and action buttons.

These three patterns cover 90% of real-world monitoring needs:

1. CANARY — notify at the START so you know within minutes that the
   channel works (instead of discovering at hour 6).
2. DEFERRED WATCHDOG — a process killed by `kill -9` or the OOM killer
   cannot notify anyone. Schedule a delayed message at startup: if the
   final [OK] has not arrived by then, the run died silently.
3. ACTION BUTTONS — let the notification link back to the repo/logs.

Run:  python examples/04_canary_watchdog_actions.py
"""

from ntfy_sh import notify, notify_success

REPO_URL = "https://github.com/FlacH7/ntfy-sh"

# --- 1. Canary ---------------------------------------------------------------
notify(
    "Batch v5 started: 25 jobs on gpu-server. "
    "If this did not ring, kill the process and fix the .env!",
    title="[myproj] Canary",
    priority="high",
    tags=("rocket",),
)

# --- 2. Deferred watchdog ------------------------------------------------------
# ntfy.sh delivers this after 11h (max delay: 3 days). The final notification
# of the batch should arrive well before this one does.
notify(
    "No [OK] arrived before this watchdog: the run died silently "
    "(kill -9 / OOM). Check the log.",
    title="[myproj] Watchdog",
    delay="11h",
    priority="default",
    tags=("hourglass",),
)

# --- 3. Success with buttons ---------------------------------------------------
notify_success(
    "Batch v5 finished: 25/25 jobs OK, best rank r=12",
    title="[myproj] Batch OK",
    tags=("white_check_mark", "chart_with_upwards_trend"),
    click=REPO_URL,                                   # opens on tap
    actions=[{
        "action": "view",
        "label": "Open repo",
        "url": REPO_URL,
    }],
)
