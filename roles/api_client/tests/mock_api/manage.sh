#!/usr/bin/env bash
# Manage the local mock API. Requires flock (util-linux) and ps.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${SCRIPT_DIR}/.mock_api.pid"
READY_FILE="${SCRIPT_DIR}/.mock_api.ready"
LOG_FILE="${SCRIPT_DIR}/.mock_api.log"
HOST="${MOCK_API_HOST:-127.0.0.1}"
PORT="${MOCK_API_PORT:-8765}"
TOKEN="${MOCK_API_TOKEN:-demo-token}"
ACTION="${1:-start}"
if [[ $# -gt 1 || ! "$ACTION" =~ ^(start|stop|status|restart)$ ]]; then
  echo "usage: $0 {start|stop|status|restart}" >&2
  exit 2
fi
for dependency in flock ps python3 curl; do
  command -v "$dependency" >/dev/null || { echo "missing dependency: $dependency" >&2; exit 127; }
done
# Serialize lifecycle operations. The server must not inherit this lock.
exec 9>"$SCRIPT_DIR/.mock_api.lock"
flock -n 9 || { echo "another mock API lifecycle command is running" >&2; exit 1; }

pid=""
instance=""

is_running() {
  local args state server_args executable
  [[ -f "$PID_FILE" ]] || return 1
  read -r pid instance <"$PID_FILE" || return 1
  [[ "$pid" =~ ^[1-9][0-9]*$ && "$instance" =~ ^[a-f0-9]{32}$ ]] || return 1

  args="$(ps -ww -p "$pid" -o args=)" || return 1
  state="$(ps -p "$pid" -o stat=)" || return 1

  # Reject zombie processes
  [[ "$state" != *Z* ]] || return 1

  # Match the script and instance exactly, including with versioned Python paths.
  server_args=" $SCRIPT_DIR/server.py --instance $instance"
  [[ "$args" == *"$server_args" ]] || return 1
  executable="${args%"$server_args"}"
  [[ "${executable##*/}" =~ ^(Python|python3(\.[0-9]+)?)$ ]]
}

stop() {
  if ! is_running; then
    # Old PID-only files cannot prove ownership; never signal their process.
    rm -f "$PID_FILE" "$READY_FILE"
    echo "mock API is not running (no verified process)"
    return 0
  fi
  local stopped_pid="$pid"
  kill -- "$pid" || return 1
  for ((attempt=0; attempt<50; attempt++)); do
    if ! is_running; then
      rm -f "$PID_FILE" "$READY_FILE"
      echo "mock API stopped (was pid $stopped_pid)"
      return 0
    fi
    sleep 0.1
  done
  echo "mock API did not stop; retaining process record for retry" >&2
  return 1
}

start() {
  if is_running; then
    echo "mock API already running (pid $pid)"
    return 0
  fi
  rm -f "$PID_FILE" "$READY_FILE"
  instance="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
  MOCK_API_HOST="$HOST" MOCK_API_PORT="$PORT" MOCK_API_TOKEN="$TOKEN" \
    MOCK_API_READY_FILE="$READY_FILE" \
    nohup python3 "$SCRIPT_DIR/server.py" --instance "$instance" \
    9>&- </dev/null >"$LOG_FILE" 2>&1 &
  local child=$!
  printf '%s %s\n' "$child" "$instance" >"$PID_FILE"
  # Only this child can write its readiness marker, after binding the port.
  # Always reap/stop a child that fails readiness, including on interruption.
  trap 'kill "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; rm -f "$PID_FILE" "$READY_FILE"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  for ((attempt=0; attempt<50; attempt++)); do
    if ! kill -0 "$child" 2>/dev/null; then
      break
    fi
    if [[ -f "$READY_FILE" && "$(cat "$READY_FILE")" == "$instance" ]] &&
      curl --noproxy '*' --connect-timeout 1 --max-time 1 -sf "http://${HOST}:${PORT}/health" >/dev/null &&
      is_running; then
      trap - EXIT INT TERM
      echo "mock API started (pid $child) → http://${HOST}:${PORT}"
      echo "  logs: $LOG_FILE"
      return 0
    fi
    sleep 0.1
  done
  echo "mock API failed to become healthy; see $LOG_FILE" >&2
  exit 1
}

case "$ACTION" in
  start) start ;;
  stop) stop ;;
  status)
    if is_running; then echo "running (pid $pid)"; else echo "stopped (no verified process)"; exit 1; fi
    ;;
  restart) stop; start ;;
esac
