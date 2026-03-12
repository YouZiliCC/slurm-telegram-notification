#!/bin/bash
# on_finish.sh — EpilogSlurmctld hook: notify daemon when a job finishes.

DAEMON_URL="${SLURM_TG_NOTIFY_URL:-http://127.0.0.1:8080}"
# AUTH_TOKEN=""  # uncomment and set if the daemon requires authentication

# ── Gather job info via scontrol --json (richest source) ──────────────────────
if command -v jq &>/dev/null && scontrol show job "$SLURM_JOB_ID" --json &>/dev/null; then
    PAYLOAD=$(scontrol show job "$SLURM_JOB_ID" --json 2>/dev/null \
        | jq -c '.jobs[0] // empty')
fi

# ── Fallback: build JSON from environment variables ──────────────────────────
if [ -z "$PAYLOAD" ]; then
    PAYLOAD=$(jq -nc \
        --arg job_id    "${SLURM_JOB_ID}" \
        --arg name      "${SLURM_JOB_NAME}" \
        --arg user_name "${SLURM_JOB_USER}" \
        --arg partition "${SLURM_JOB_PARTITION}" \
        --arg nodes     "${SLURM_JOB_NODELIST}" \
        --arg exit_code "${SLURM_JOB_EXIT_CODE}" \
        '{job_id: $job_id, name: $name, user_name: $user_name, partition: $partition,
          nodes: $nodes, exit_code: $exit_code}' \
        2>/dev/null)
fi

# ── Last resort: plain string construction (no jq) ──────────────────────────
if [ -z "$PAYLOAD" ]; then
    PAYLOAD="{\"job_id\":\"${SLURM_JOB_ID}\",\"name\":\"${SLURM_JOB_NAME}\",\"user_name\":\"${SLURM_JOB_USER}\",\"partition\":\"${SLURM_JOB_PARTITION}\",\"nodes\":\"${SLURM_JOB_NODELIST}\",\"exit_code\":\"${SLURM_JOB_EXIT_CODE}\"}"
fi

# ── POST to daemon ───────────────────────────────────────────────────────────
CURL_ARGS=(-s -X POST "$DAEMON_URL/notify/finish"
           -H "Content-Type: application/json"
           -d "$PAYLOAD")

# [ -n "$AUTH_TOKEN" ] && CURL_ARGS+=(-H "Authorization: Bearer $AUTH_TOKEN")

curl "${CURL_ARGS[@]}" --max-time 10 >/dev/null 2>&1 || true

exit 0  # never block job cleanup
