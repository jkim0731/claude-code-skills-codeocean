#!/usr/bin/env bash
# run-history.sh — Bash library for tracking capsule run durations.
# 
# Used by track-jobs.sh to estimate poll intervals based on previous run times.
# Stores temporary history in /scratch/tmp/run-history.json (per-session learning).
# Format: {"capsule_id": [{"duration_sec": 120, "ts": "2026-08-25T16:00:00Z"}, ...], ...}

RUN_HISTORY_FILE="${RUN_HISTORY_FILE:-/scratch/tmp/run-history.json}"

# Initialize history file if it doesn't exist
init_history() {
  if [[ ! -f "$RUN_HISTORY_FILE" ]]; then
    echo '{}' > "$RUN_HISTORY_FILE"
  fi
}

# Record a run: record_run <capsule_id> <duration_seconds>
record_run() {
  local capsule_id="$1"
  local duration_sec="$2"
  init_history
  
  python3 << PYTHON
import json, pathlib, datetime
hist_file = pathlib.Path("$RUN_HISTORY_FILE")
try:
  data = json.loads(hist_file.read_text())
except:
  data = {}

if "$capsule_id" not in data:
  data["$capsule_id"] = []

data["$capsule_id"].append({
  "duration_sec": $duration_sec,
  "ts": datetime.datetime.utcnow().isoformat() + "Z"
})

# Keep only last 100 runs per capsule (rolling window)
if len(data["$capsule_id"]) > 100:
  data["$capsule_id"] = data["$capsule_id"][-100:]

hist_file.write_text(json.dumps(data, indent=2))
PYTHON
}

# Get recommended poll interval: recommend_poll <capsule_id>
# Returns seconds; uses max(median(previous_runs) / 2, 180) or 180 if no history
recommend_poll() {
  local capsule_id="$1"
  init_history
  
  python3 << PYTHON
import json, pathlib, statistics
hist_file = pathlib.Path("$RUN_HISTORY_FILE")
try:
  data = json.loads(hist_file.read_text())
except:
  data = {}

runs = data.get("$capsule_id", [])
if not runs:
  print(180)  # default 3 minutes
else:
  durations = [r.get("duration_sec", 0) for r in runs if r.get("duration_sec")]
  if durations:
    # Use max(median / 2, 180)
    median = statistics.median(durations)
    recommended = max(int(median / 2), 180)
    print(recommended)
  else:
    print(180)
PYTHON
}
