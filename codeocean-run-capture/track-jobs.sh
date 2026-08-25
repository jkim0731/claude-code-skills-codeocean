#!/usr/bin/env bash
# track-jobs.sh — Poll and track computation status from a jobs tracking file.
#
# Monitors computation IDs until they finish (completed/failed). Detects silent
# monitor failures via exit_code (monitor mode: 0=captured, 1=no-capture).
# Poll interval is automatically adjusted based on historical run times.
#
# Usage:
#   ./track-jobs.sh jobs.tracking.csv              # auto-detect capsule from file
#   ./track-jobs.sh jobs.tracking.csv --capsule-id <id>  # explicit capsule
#   POLL=300 ./track-jobs.sh jobs.tracking.csv     # override poll interval (seconds)
#   nohup ./track-jobs.sh jobs.tracking.csv > track.log 2>&1 &
#
# Expects CSV format (created by run_per_session.sh / run_per_subject.sh):
#   item,computation_id,capsule_id,state,submitted_ts
#
# Updates state column: submitted -> completed|failed
# Tracks run times in .run-history.json for next-run poll interval estimation.

set -u -o pipefail

TRACKING_FILE="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$SCRIPT_DIR/scripts/co_run_capture.py"
HISTORY_SCRIPT="$SCRIPT_DIR/run-history.sh"
TIMEOUT="${TIMEOUT:-86400}"  # give up after this many seconds (default 24h)
POLL="${POLL:-}"              # can be overridden by --poll flag or POLL env var
CAPSULE_ID="${CAPSULE_ID:-}"  # can be overridden by --capsule-id flag or CAPSULE_ID env var

[[ -f "$TOOL" ]] || { echo "ERROR: tool not found at $TOOL" >&2; exit 1; }
[[ -n "$TRACKING_FILE" ]] || { echo "Usage: $0 <jobs.tracking.csv> [--capsule-id <id>] [--poll <seconds>]" >&2; exit 2; }
[[ -f "$TRACKING_FILE" ]] || { echo "ERROR: tracking file not found: $TRACKING_FILE" >&2; exit 2; }
[[ -f "$HISTORY_SCRIPT" ]] || { echo "ERROR: history script not found at $HISTORY_SCRIPT" >&2; exit 1; }

# Source the history library
source "$HISTORY_SCRIPT"

# Parse arguments (skip first arg which is TRACKING_FILE)
shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --capsule-id)
      CAPSULE_ID="$2"
      shift 2
      ;;
    --poll)
      POLL="$2"
      shift 2
      ;;
    *)
      echo "WARNING: unknown argument $1" >&2
      shift
      ;;
  esac
done

# Extract capsule_id from tracking file if not provided
if [[ -z "$CAPSULE_ID" ]]; then
  CAPSULE_ID=$(head -1 "$TRACKING_FILE" | grep -oE 'capsule_id' > /dev/null && \
    tail -n +2 "$TRACKING_FILE" 2>/dev/null | head -1 | cut -d ',' -f 3 || echo "")
  [[ -n "$CAPSULE_ID" ]] || {
    echo "⚠ Could not auto-detect capsule_id from tracking file."
    echo "  Please provide it: $0 $TRACKING_FILE --capsule-id <id>"
    echo "  Defaulting to 180-second (3-minute) poll interval."
    CAPSULE_ID=""
  }
fi

# Determine poll interval
if [[ -n "$POLL" ]]; then
  echo "Using explicit POLL=$POLL seconds"
else
  POLL=$(source "$HISTORY_SCRIPT" && recommend_poll "$CAPSULE_ID")
  echo "Recommended poll interval (based on capsule history): ${POLL}s"
fi

start_time=$(date +%s)

while true; do
  # Count states
  total=$(tail -n +2 "$TRACKING_FILE" 2>/dev/null | wc -l)
  done_count=$(tail -n +2 "$TRACKING_FILE" 2>/dev/null | grep -cE ",completed," || true)
  failed_count=$(tail -n +2 "$TRACKING_FILE" 2>/dev/null | grep -cE ",failed," || true)
  pending_count=$((total - done_count - failed_count))

  if (( total == 0 )); then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] No jobs to track."
    break
  fi

  echo "[$(date +'%Y-%m-%d %H:%M:%S')] Status: $done_count done, $failed_count failed, $pending_count pending (total $total)"

  # Poll each pending job
  updated=0
  while IFS=',' read -r item comp_id state rest; do
    [[ "$item" == "item" ]] && continue  # skip header
    [[ -z "$comp_id" ]] && continue
    [[ "$state" == "completed" ]] || [[ "$state" == "failed" ]] && continue  # already done

    # Query status
    status_out=$("$TOOL" status --computation-id "$comp_id" 2>&1) || {
      # Retry once on transient error
      sleep 2
      status_out=$("$TOOL" status --computation-id "$comp_id" 2>&1) || {
        echo "  ⚠ could not fetch status for $item ($comp_id)"
        continue
      }
    }

    # Parse: id<tab>state=<state><tab>name=<name>
    computed_state=$(echo "$status_out" | cut -f 2 | cut -d '=' -f 2)
    
    # For monitor mode: also check exit_code
    exit_code_out=$("$TOOL" status --computation-id "$comp_id" 2>&1 | grep -oE "exit_code=[0-9]+" | cut -d '=' -f 2 || true)
    exit_code="${exit_code_out:-}"

    if [[ "$computed_state" == "completed" ]] || [[ "$computed_state" == "failed" ]]; then
      # Mark as done
      reason=""
      if [[ "$computed_state" == "failed" ]]; then
        reason="state=failed"
      elif [[ -n "$exit_code" ]] && [[ "$exit_code" != "0" ]]; then
        # Monitor mode: exit_code != 0 means target failed (silent failure)
        reason="exit_code=$exit_code (no-capture)"
      fi

      # Update tracking file (simple sed replacement; not atomic but safe for append-only)
      # Get submitted timestamp to calculate duration
      submitted_ts=$(tail -n +2 "$TRACKING_FILE" 2>/dev/null | grep "^${item}," | cut -d ',' -f 4 || echo "")
      now_ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      
      sed -i.bak "s|^${item},${comp_id},submitted,|${item},${comp_id},${computed_state},|" "$TRACKING_FILE" || true
      rm -f "$TRACKING_FILE.bak"

      # Record run duration for this capsule (for next-run poll estimation)
      if [[ -n "$submitted_ts" ]] && [[ -n "$CAPSULE_ID" ]]; then
        submitted_epoch=$(date -d "$submitted_ts" +%s 2>/dev/null || echo 0)
        now_epoch=$(date +%s)
        duration_sec=$((now_epoch - submitted_epoch))
        if (( duration_sec > 0 )); then
          source "$HISTORY_SCRIPT"
          record_run "$CAPSULE_ID" "$duration_sec"
        fi
      fi

      if [[ "$computed_state" == "completed" ]]; then
        if [[ -n "$reason" ]]; then
          echo "  [⚠ WARN]  $item  ($comp_id)  COMPLETED but $reason"
        else
          echo "  [✓ DONE]   $item  ($comp_id)"
        fi
      else
        echo "  [✗ FAIL]   $item  ($comp_id)  $reason"
      fi
      updated=$((updated + 1))
    fi
  done < "$TRACKING_FILE"

  # Check timeout
  elapsed=$(($(date +%s) - start_time))
  if (( elapsed >= TIMEOUT )); then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] TIMEOUT: giving up after ${TIMEOUT}s"
    echo "Some jobs may still be running server-side; use 'track-jobs.sh <file>' to resume."
    break
  fi

  # All done?
  if (( pending_count == 0 )); then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] ALL JOBS DONE (completed=$done_count, failed=$failed_count)"
    break
  fi

  # Wait before next poll
  if (( updated == 0 )); then
    sleep "$POLL"
  fi
done

echo
echo "Tracking complete. Final state saved to: $TRACKING_FILE"
