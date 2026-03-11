#!/usr/bin/env python3
"""
slurm_monitor.py — Long-running daemon that polls the Slurm REST API and sends
Telegram notifications via notify.py when jobs are submitted or finished.

Usage:
    python slurm_monitor.py

Requirements:
    pip install requests

Slurm REST API docs: https://slurm.schedmd.com/rest.html
JWT token:           scontrol token username=<user> lifespan=86400
"""

import logging
import signal
import time

import requests

import notify  # sibling module: notify.py

# ── Configuration ─────────────────────────────────────────────────────────────

# Slurm REST daemon base URL (typically port 6820)
SLURM_API_URL = "http://slurm-head-node:6820"

# API version — match your slurmrestd; check with: slurmrestd --version
SLURM_API_VERSION = "v0.0.40"

# JWT token: run `scontrol token username=<user> lifespan=86400` on the head node
SLURM_JWT_TOKEN = "your_jwt_token_here"

# Optional proxy for Slurm REST calls (usually not needed); set {} to disable
SLURM_API_PROXIES: dict = {}

# How often to poll (seconds)
POLL_INTERVAL = 30

# Only monitor jobs belonging to these users; empty set = monitor ALL users
WATCH_USERS: set[str] = set()

# ── Terminal states ───────────────────────────────────────────────────────────

TERMINAL_STATES = frozenset({
    "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT",
    "NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "DEADLINE", "OUT_OF_MEMORY",
})

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Field normalisation helpers ───────────────────────────────────────────────

def _num(field) -> int:
    """
    Slurm REST API v0.0.39+ wraps numeric fields as:
        {"number": 123, "set": true, "infinite": false}
    Older versions return plain integers. Handle both.
    """
    if isinstance(field, dict):
        return int(field.get("number", 0) or 0)
    return int(field or 0)


def _state(field) -> str:
    """job_state may be a string or a list of strings."""
    if isinstance(field, list):
        return (field[0] if field else "UNKNOWN").upper()
    return str(field or "UNKNOWN").upper()


def _exit_code(field) -> str:
    """
    Exit code may be:
      - plain int
      - {"return_code": {"number": 0, ...}, "signal": {...}}
    """
    if isinstance(field, dict):
        rc = field.get("return_code", field)
        if isinstance(rc, dict):
            return str(rc.get("number", 0))
        return str(rc)
    return str(field) if field is not None else "N/A"


def _normalise(raw: dict) -> dict:
    """Return a flat, stable dict with the fields notify.py expects."""
    return {
        "job_id":          str(raw.get("job_id", "N/A")),
        "name":            raw.get("name", "N/A"),
        "user_name":       raw.get("user_name", "N/A"),
        "partition":       raw.get("partition", "N/A"),
        "nodes":           raw.get("nodes", "N/A"),
        "job_state":       _state(raw.get("job_state")),
        "exit_code":       _exit_code(raw.get("exit_code")),
        "start_time":      _num(raw.get("start_time")),
        "end_time":        _num(raw.get("end_time")),
        "standard_output": raw.get("standard_output", "") or "",
        "standard_error":  raw.get("standard_error",  "") or "",
    }

# ── Monitor class ─────────────────────────────────────────────────────────────

class SlurmMonitor:
    """
    State machine that tracks job lifecycle changes.

    _known_jobs     : job_id → current state string  (for all active jobs)
    _notified_done  : set of job_ids we have already sent a finish notification for
                      (guards against sending duplicates while a terminal job
                       lingers in the API before being purged)
    """

    def __init__(self) -> None:
        self._running        = True
        self._known_jobs:    dict[str, str] = {}
        self._notified_done: set[str]       = set()

        self._session = requests.Session()
        self._session.headers.update({
            "X-SLURM-USER-TOKEN": SLURM_JWT_TOKEN,
            "Content-Type":       "application/json",
        })
        self._session.proxies = SLURM_API_PROXIES

    # ── REST API ───────────────────────────────────────────────────────────────

    def _fetch_jobs(self) -> list[dict] | None:
        url = f"{SLURM_API_URL}/slurm/{SLURM_API_VERSION}/jobs"
        try:
            resp = self._session.get(url, timeout=15)
            resp.raise_for_status()
            return resp.json().get("jobs", [])
        except Exception as exc:
            log.error("Failed to fetch jobs from Slurm REST API: %s", exc)
            return None   # None signals a transient error; skip this cycle

    # ── Bootstrap ─────────────────────────────────────────────────────────────

    def _bootstrap(self) -> None:
        """
        Snapshot all currently known jobs on startup.
        No notifications are sent — we don't know the history of these jobs.
        """
        log.info("Bootstrapping: loading existing Slurm jobs…")
        raw_jobs = self._fetch_jobs()
        if raw_jobs is None:
            log.warning("Bootstrap failed; will retry on first poll.")
            return

        for raw in raw_jobs:
            job    = _normalise(raw)
            job_id = job["job_id"]
            state  = job["job_state"]

            if WATCH_USERS and job["user_name"] not in WATCH_USERS:
                continue

            self._known_jobs[job_id] = state
            if state in TERMINAL_STATES:
                self._notified_done.add(job_id)  # already done, skip later

        log.info(
            "Bootstrap complete — %d jobs loaded (%d already terminal).",
            len(self._known_jobs), len(self._notified_done),
        )

    # ── Per-poll processing ────────────────────────────────────────────────────

    def _process(self, raw_jobs: list[dict]) -> None:
        current_ids: set[str] = set()

        for raw in raw_jobs:
            job    = _normalise(raw)
            job_id = job["job_id"]
            state  = job["job_state"]

            if WATCH_USERS and job["user_name"] not in WATCH_USERS:
                continue

            current_ids.add(job_id)

            # ── Brand-new job ──────────────────────────────────────────────────
            if job_id not in self._known_jobs and job_id not in self._notified_done:
                log.info(
                    "New job: id=%s name=%s user=%s state=%s",
                    job_id, job["name"], job["user_name"], state,
                )
                self._known_jobs[job_id] = state
                self._safe_notify(notify.notify_submitted, job, "notify_submitted")

                # Edge-case: job already terminal by the time we first see it
                if state in TERMINAL_STATES:
                    log.info("Job %s already terminal on first sight.", job_id)
                    self._safe_notify(notify.notify_finished, job, "notify_finished")
                    self._notified_done.add(job_id)

            # ── Known job: watch for terminal transition ───────────────────────
            else:
                prev = self._known_jobs.get(job_id, state)
                if prev != state:
                    log.info("Job %s state change: %s → %s", job_id, prev, state)
                self._known_jobs[job_id] = state

                if state in TERMINAL_STATES and job_id not in self._notified_done:
                    log.info(
                        "Job finished: id=%s name=%s state=%s exit_code=%s",
                        job_id, job["name"], state, job["exit_code"],
                    )
                    self._safe_notify(notify.notify_finished, job, "notify_finished")
                    self._notified_done.add(job_id)

        # ── Evict jobs that have left the API (purged by Slurm) ────────────────
        gone = set(self._known_jobs.keys()) - current_ids
        for job_id in gone:
            log.debug("Job %s no longer in API response, evicting.", job_id)
            del self._known_jobs[job_id]
        self._notified_done -= gone   # safe to forget; job cannot reappear

    def _safe_notify(self, fn, job: dict, label: str) -> None:
        try:
            fn(job)
        except Exception as exc:
            log.error("%s raised an exception: %s", label, exc)

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        signal.signal(signal.SIGINT,  self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)

        self._bootstrap()
        log.info("Polling every %d seconds. Press Ctrl-C to stop.", POLL_INTERVAL)

        while self._running:
            raw_jobs = self._fetch_jobs()
            if raw_jobs is not None:
                self._process(raw_jobs)
            time.sleep(POLL_INTERVAL)

        log.info("Monitor stopped cleanly.")

    def _handle_stop(self, *_) -> None:
        log.info("Shutdown signal received — finishing current cycle…")
        self._running = False


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    SlurmMonitor().run()
