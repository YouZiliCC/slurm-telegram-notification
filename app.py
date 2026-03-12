"""
app.py — Flask-based HTTP daemon that receives Slurm lifecycle events
and sends Telegram notifications via notify.py.

Architecture:
    Slurm hooks (PrologSlurmctld / EpilogSlurmctld)
        ──curl──▶  Flask daemon  ──▶  Telegram

Endpoints:
    POST /notify/submit   — new job submitted / starting
    POST /notify/finish   — job reached terminal state

Request body: JSON with job fields.
    Minimal:  {"job_id": "12345"}
    Full:     raw `scontrol show job <id> --json` .jobs[0] object

Run (development):
    python main.py [--host 127.0.0.1] [--port 8080]

Run (production):
    gunicorn -w 4 -b 127.0.0.1:8080 app:app
"""

import os
import logging
from dotenv import load_dotenv
from functools import wraps
from flask import Flask, request, jsonify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

env_path = os.path.join(os.path.dirname(__file__), ".env")
if not os.path.exists(env_path):
    logging.warning(".env file not found; using environment variables")
else:
    logging.info("Loading configuration from .env file")
    load_dotenv(env_path)

try:
    import notify
    logging.info("notify module imported successfully")
except ImportError as e:
    logging.error(f"Failed to import notify module: {e}")
    exit(1)

import db
db.init_db()
logging.info("Message database initialised (max %d visible in Telegram)", db.MAX_MESSAGES)


def _cleanup_overflow() -> None:
    """Delete Telegram messages that exceed the MAX_MESSAGES window."""
    for rec in db.get_overflow_records():
        for mid in rec["telegram_msg_ids"]:
            try:
                notify.delete_message(mid)
            except Exception as exc:
                log.warning("Failed to delete Telegram message %s: %s", mid, exc)
        db.clear_telegram_ids(rec["id"])

# ── Configuration ─────────────────────────────────────────────────────────────

AUTH_TOKEN = os.getenv("AUTH_TOKEN", "").strip() 
WATCH_USERS: set[str] = set([user.strip() for user in os.getenv("WATCH_USERS", "").split(',') if user.strip()])

# ── Flask application ─────────────────────────────────────────────────────────

app = Flask(__name__)

# ── Field normalisation helpers ───────────────────────────────────────────────
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


# ── Auth decorator ────────────────────────────────────────────────────────────

def require_auth(f):
    """Skip auth check if AUTH_TOKEN is empty; otherwise verify Bearer token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if AUTH_TOKEN:
            hdr = request.headers.get("Authorization", "")
            token = hdr.removeprefix("Bearer ").strip() if hdr else ""
            if token != AUTH_TOKEN:
                return jsonify(error="forbidden"), 403
        return f(*args, **kwargs)
    return decorated


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/notify/start", methods=["POST"])
@require_auth
def handle_start():
    """Handle job start notification."""
    raw = request.get_json(silent=True)
    if not raw:
        return jsonify(error="empty or invalid JSON body"), 400

    job = _normalise(raw)

    # User filter
    if WATCH_USERS and job["user_name"] not in WATCH_USERS:
        return jsonify(status="skipped", reason="user not in WATCH_USERS"), 200

    log.info("Job started: id=%s name=%s user=%s",
             job["job_id"], job["name"], job["user_name"])
    try:
        msg_ids = notify.notify_started(job)
        summary = f"Job {job['job_id']} ({job['name']}) started — user={job['user_name']}"
        db.record_message("start", job["job_id"], summary, msg_ids)
        _cleanup_overflow()
        return jsonify(status="ok"), 200
    except Exception as exc:
        log.error("notify_started failed: %s", exc)
        return jsonify(error=str(exc)), 500


@app.route("/notify/finish", methods=["POST"])
@require_auth
def handle_finish():
    """Handle job completion notification."""
    raw = request.get_json(silent=True)
    if not raw:
        return jsonify(error="empty or invalid JSON body"), 400

    job = _normalise(raw)

    # User filter
    if WATCH_USERS and job["user_name"] not in WATCH_USERS:
        return jsonify(status="skipped", reason="user not in WATCH_USERS"), 200

    log.info("Job finished: id=%s name=%s state=%s exit=%s",
             job["job_id"], job["name"], job["job_state"], job["exit_code"])
    try:
        msg_ids = notify.notify_finished(job)
        summary = f"Job {job['job_id']} ({job['name']}) finished — state={job['job_state']} exit={job['exit_code']}"
        db.record_message("finish", job["job_id"], summary, msg_ids)
        _cleanup_overflow()
        return jsonify(status="ok"), 200
    except Exception as exc:
        log.error("notify_finished failed: %s", exc)
        return jsonify(error=str(exc)), 500


@app.route("/messages", methods=["GET"])
@require_auth
def recent_messages():
    """Return the most recent sent notifications (up to MAX_MESSAGES)."""
    return jsonify(messages=db.get_recent_messages()), 200


@app.route("/health", methods=["GET"])
def health():
    """Simple health-check endpoint."""
    return jsonify(status="ok"), 200
