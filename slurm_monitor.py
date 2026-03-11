#!/usr/bin/env python3
"""
slurm_monitor.py — HTTP daemon that receives Slurm lifecycle events
and sends Telegram notifications via notify.py.

Architecture:
    Slurm hooks (PrologSlurmctld / EpilogSlurmctld)
        ──curl──▶  this daemon  ──▶  Telegram

Usage:
    python slurm_monitor.py [--host 127.0.0.1] [--port 8080]

Endpoints:
    POST /notify/submit   — new job submitted / starting
    POST /notify/finish   — job reached terminal state

Request body: JSON with job fields.
    Minimal:  {"job_id": "12345"}
    Full:     raw `scontrol show job <id> --json` .jobs[0] object
"""

import argparse
import json
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import notify  # sibling module: notify.py

# ── Configuration ─────────────────────────────────────────────────────────────

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8080

# Optional Bearer token — if non-empty, every request must carry a matching
# "Authorization: Bearer <token>" header.  Set "" to disable.
AUTH_TOKEN = ""

# Only process notifications for these users; empty set = accept all.
WATCH_USERS: set[str] = set()

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Field normalisation helpers ───────────────────────────────────────────────
# scontrol --json output uses nested dicts for numbers/states in newer Slurm
# versions.  These helpers flatten them into plain types that notify.py expects.

def _num(field) -> int:
    if isinstance(field, dict):
        return int(field.get("number", 0) or 0)
    return int(field or 0)


def _state(field) -> str:
    if isinstance(field, list):
        return (field[0] if field else "UNKNOWN").upper()
    return str(field or "UNKNOWN").upper()


def _exit_code(field) -> str:
    if isinstance(field, dict):
        rc = field.get("return_code", field)
        if isinstance(rc, dict):
            return str(rc.get("number", 0))
        return str(rc)
    return str(field) if field is not None else "N/A"


def _normalise(raw: dict) -> dict:
    """Flatten raw scontrol / hook JSON into the dict notify.py expects."""
    # If the caller sent the full scontrol wrapper, unwrap it.
    if "jobs" in raw and isinstance(raw["jobs"], list) and raw["jobs"]:
        raw = raw["jobs"][0]

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

# ── HTTP server ───────────────────────────────────────────────────────────────

class ThreadedHTTPServer(HTTPServer):
    """Handle each request in a new daemon thread."""
    def process_request(self, request, client_address):
        t = threading.Thread(target=self.process_request_thread,
                             args=(request, client_address), daemon=True)
        t.start()

    def process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


class NotifyHandler(BaseHTTPRequestHandler):
    """Thin HTTP handler: authenticate → parse JSON → normalise → notify."""

    def do_POST(self):
        # ── Auth ───────────────────────────────────────────────────────────
        if AUTH_TOKEN:
            hdr = self.headers.get("Authorization", "")
            token = hdr.removeprefix("Bearer ").strip() if hdr else ""
            if token != AUTH_TOKEN:
                self._respond(403, {"error": "forbidden"})
                return

        # ── Read body ──────────────────────────────────────────────────────
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._respond(400, {"error": "empty body"})
            return
        try:
            raw = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError) as exc:
            self._respond(400, {"error": f"invalid JSON: {exc}"})
            return

        job = _normalise(raw)

        # ── User filter ───────────────────────────────────────────────────
        if WATCH_USERS and job["user_name"] not in WATCH_USERS:
            self._respond(200, {"status": "skipped", "reason": "user not in WATCH_USERS"})
            return

        # ── Route ──────────────────────────────────────────────────────────
        if self.path == "/notify/submit":
            log.info("Job submitted: id=%s name=%s user=%s",
                     job["job_id"], job["name"], job["user_name"])
            self._safe_call(notify.notify_submitted, job)

        elif self.path == "/notify/finish":
            log.info("Job finished: id=%s name=%s state=%s exit=%s",
                     job["job_id"], job["name"], job["job_state"], job["exit_code"])
            self._safe_call(notify.notify_finished, job)

        else:
            self._respond(404, {"error": "not found"})

    # ── Helpers ────────────────────────────────────────────────────────────

    def _safe_call(self, fn, job: dict) -> None:
        try:
            fn(job)
            self._respond(200, {"status": "ok"})
        except Exception as exc:
            log.error("%s failed: %s", fn.__name__, exc)
            self._respond(500, {"error": str(exc)})

    def _respond(self, code: int, body: dict) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Slurm Telegram notification daemon")
    parser.add_argument("--host", default=LISTEN_HOST, help="bind address")
    parser.add_argument("--port", type=int, default=LISTEN_PORT, help="listen port")
    args = parser.parse_args()

    server = ThreadedHTTPServer((args.host, args.port), NotifyHandler)

    def _shutdown(*_):
        log.info("Shutdown signal received.")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("Listening on %s:%d — press Ctrl-C to stop.", args.host, args.port)
    server.serve_forever()
    log.info("Daemon stopped.")


if __name__ == "__main__":
    main()
