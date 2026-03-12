import os
import time
import requests
from datetime import timedelta

# ── Telegram Configuration ────────────────────────────────────────────────────

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID           = os.getenv("CHAT_ID", "-000000000").strip()
MESSAGE_THREAD_ID = os.getenv("MESSAGE_THREAD_ID", "0").strip()
_PROXY_URL        = os.getenv("PROXIES", "").strip()
PROXIES           = {"https": _PROXY_URL} if _PROXY_URL else {}

# ── Behaviour ─────────────────────────────────────────────────────────────────

MAX_LOG_BYTES = int(os.getenv("MAX_LOG_BYTES", "1048576"))
RETRY_COUNT   = int(os.getenv("RETRY_COUNT", "3"))
RETRY_DELAY   = int(os.getenv("RETRY_DELAY", "5"))

# ── Internal helpers ──────────────────────────────────────────────────────────

def _base_url() -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def _escape_html(text) -> str:
    """Minimal HTML escaping required by Telegram's HTML parse mode."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_runtime(start: int, end: int) -> str:
    if start and end and end > start:
        return str(timedelta(seconds=end - start))
    return "N/A"


def _post_with_retry(url: str, **kwargs) -> requests.Response | None:
    """POST to Telegram with simple retry on failure."""
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.post(url, proxies=PROXIES, timeout=15, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            print(f"[notify] attempt {attempt}/{RETRY_COUNT} failed: {exc}")
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)
    print("[notify] all retries exhausted — notification may be lost.")
    return None


def _send_message(html: str) -> int | None:
    """Send an HTML message and return the Telegram message_id, or None."""
    resp = _post_with_retry(
        f"{_base_url()}/sendMessage",
        data={
            "chat_id":           CHAT_ID,
            "text":              html,
            "parse_mode":        "HTML",
            "message_thread_id": MESSAGE_THREAD_ID,
        },
    )
    if resp is not None:
        try:
            return resp.json()["result"]["message_id"]
        except (KeyError, ValueError):
            pass
    return None


def _send_log_file(path: str, caption: str) -> int | None:
    """Upload a log file; tail the last MAX_LOG_BYTES if the file is too large."""
    if not os.path.exists(path):
        return

    file_size = os.path.getsize(path)
    filename  = os.path.basename(path)

    with open(path, "rb") as f:
        if file_size > MAX_LOG_BYTES:
            f.seek(-MAX_LOG_BYTES, os.SEEK_END)
            content = b"[...log truncated, showing last portion...]\n" + f.read()
        else:
            content = f.read()

    resp = _post_with_retry(
        f"{_base_url()}/sendDocument",
        data={
            "chat_id":           CHAT_ID,
            "message_thread_id": MESSAGE_THREAD_ID,
            "caption":           caption,
        },
        files={"document": (filename, content)},
    )
    if resp is not None:
        try:
            return resp.json()["result"]["message_id"]
        except (KeyError, ValueError):
            pass
    return None


def delete_message(message_id: int) -> bool:
    """Delete a single message from the Telegram chat. Returns True on success."""
    resp = _post_with_retry(
        f"{_base_url()}/deleteMessage",
        data={"chat_id": CHAT_ID, "message_id": message_id},
    )
    return resp is not None and resp.ok


# ── Public API ────────────────────────────────────────────────────────────────

def notify_started(job: dict) -> list[int]:
    """Send a Telegram message when a Slurm job starts. Returns message_ids."""
    job_id    = job.get("job_id",    "N/A")
    job_name  = job.get("name",      "N/A")
    user      = job.get("user_name", "N/A")
    partition = job.get("partition", "N/A")
    state     = job.get("job_state", "N/A")

    html = (
        "📥 <b>Slurm Job Started</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"<b>ID:</b> <code>{_escape_html(job_id)}</code>\n"
        f"<b>Name:</b> {_escape_html(job_name)}\n"
        f"<b>User:</b> {_escape_html(user)}\n"
        f"<b>Partition:</b> {_escape_html(partition)}\n"
        f"<b>State:</b> {_escape_html(state)}"
    )
    msg_ids: list[int] = []
    mid = _send_message(html)
    if mid:
        msg_ids.append(mid)
    return msg_ids


def notify_finished(job: dict) -> list[int]:
    """Send a Telegram message (+ log files) when a Slurm job finishes. Returns message_ids."""
    job_id    = job.get("job_id",    "N/A")
    job_name  = job.get("name",      "N/A")
    user      = job.get("user_name", "N/A")
    partition = job.get("partition", "N/A")
    nodes     = job.get("nodes",     "N/A")
    state     = job.get("job_state", "N/A")
    exit_code = job.get("exit_code", "N/A")
    stdout    = job.get("standard_output", "")
    stderr    = job.get("standard_error",  "")

    runtime = _format_runtime(
        int(job.get("start_time", 0) or 0),
        int(job.get("end_time",   0) or 0),
    )
    icon = "✅" if str(state).upper() == "COMPLETED" or str(exit_code) == "0" else "❌"

    html = (
        f"{icon} <b>Slurm Job Finished</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"<b>ID:</b> <code>{_escape_html(job_id)}</code>\n"
        f"<b>Name:</b> {_escape_html(job_name)}\n"
        f"<b>User:</b> {_escape_html(user)}\n"
        f"<b>Partition:</b> {_escape_html(partition)}\n"
        f"<b>Nodes:</b> {_escape_html(nodes)}\n"
        f"<b>Runtime:</b> {_escape_html(runtime)}\n"
        f"<b>State:</b> {_escape_html(state)}\n"
        f"<b>Exit Code:</b> {_escape_html(exit_code)}"
    )
    msg_ids: list[int] = []
    mid = _send_message(html)
    if mid:
        msg_ids.append(mid)

    # Upload stdout / stderr log files as attachments
    for log_path in filter(None, [stdout, stderr]):
        caption = f"Job {job_id} — {os.path.basename(log_path)}"
        fid = _send_log_file(log_path, caption)
        if fid:
            msg_ids.append(fid)
    return msg_ids
