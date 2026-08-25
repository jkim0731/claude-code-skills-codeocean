#!/usr/bin/env bash
# run_per_subject.sh — PER-SUBJECT approach.
#
#   >>> Runs ONE capsule per SUBJECT, over ALL of that subject's sessions. <<<
#   Default target: a subject-level monitor capsule fired with a `subject_id`
#   NAMED parameter; it can gather a subject's sessions, run the target compute,
#   captures results (capture-time naming server-side). No 4096-char JSON limit.
#
# Which subjects to run: a plain list (one subject id per line) OR a CSV column
# (COLUMN / INCLUDE_* below).
#
# Which assets to attach: usually none (the capsule gathers sessions itself). For
# capsules that need explicit inputs, use SUBJECT_ASSETS (templates, {subj}=subject)
# and/or FIXED_ASSETS — mix any of: coreg-id-table, HCR raw, processed ophys, etc.
#
# For the PER-SESSION approach (one run per session, e.g. LP eye-tracking), use
# run_per_session.sh.
#
# Usage:
#   ./run_per_subject.sh subjects.txt
#   ./run_per_subject.sh cohort.csv            # COLUMN picks the subject column
#   SUBJECTS="779891 767022" ./run_per_subject.sh
#   DRY_RUN=1 ./run_per_subject.sh subjects.txt
#
# Auth: token from $CODEOCEAN_TOKEN/$API_SECRET (+ $CODEOCEAN_DOMAIN). Billable.

set -u -o pipefail

# ------------------------------- CONFIG (edit me) -------------------------------
CAPSULE_ID="${CAPSULE_ID:-}"   # required: set subject-level capsule id

# NAMED parameters for that capsule (subject_id is added per-subject).
NAMED_PARAMS=(
  "dff_long_window=1800"
  "max_jobs=10"
  "test=0"              # 0 = real run, 1 = quick test subset
  "sleep=600"
  "ignore_not_processed=1"
)

# Per-subject assets to attach (templates; {subj}=subject id). Usually empty.
# Examples for other tasks:
#   "coreg-id-table_{subj}"   coregistration id table
#   "HCR_{subj}"              HCR raw asset(s)
SUBJECT_ASSETS=()

# Fixed assets attached to every run, by literal NAME:
FIXED_ASSETS=()

WAIT="${WAIT:-0}"        # 0 = fire-and-forget (these run for hours); 1 = wait
MAX_JOBS="${MAX_JOBS:-4}"
DRY_RUN="${DRY_RUN:-0}"

# CSV input (used only when the input file ends in .csv):
COLUMN="${COLUMN:-subject_id}"
INCLUDE_COLUMN="${INCLUDE_COLUMN:-}"
INCLUDE_VALUE="${INCLUDE_VALUE:-}"
# -------------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$SCRIPT_DIR/scripts/co_run_capture.py"
READER="$SCRIPT_DIR/scripts/read_items.py"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs_per_subject}"
STATUS_DIR="$LOG_DIR/status"
TRACKING_DIR="${TRACKING_DIR:-$SCRIPT_DIR/tracking}"
mkdir -p "$STATUS_DIR" "$TRACKING_DIR"
[[ -f "$TOOL" ]] || { echo "ERROR: tool not found at $TOOL" >&2; exit 1; }
[[ -n "$CAPSULE_ID" ]] || { echo "ERROR: set CAPSULE_ID before running." >&2; exit 2; }

read_list() {
  python3 "$READER" "$1" ${COLUMN:+--column "$COLUMN"} \
    ${INCLUDE_COLUMN:+--include-col "$INCLUDE_COLUMN"} ${INCLUDE_VALUE:+--include-val "$INCLUDE_VALUE"}
}

subjects=()
if [[ $# -ge 1 && -f "$1" ]]; then
  while IFS= read -r line; do [[ -n "$line" ]] && subjects+=("$line"); done < <(read_list "$1")
elif [[ -n "${SUBJECTS:-}" ]]; then
  read -r -a subjects <<< "$SUBJECTS"
else
  echo "usage: $0 <subjects.txt|cohort.csv>   (or set SUBJECTS='779891 767022 ...')" >&2; exit 2
fi
(( ${#subjects[@]} > 0 )) || { echo "No subjects to run." >&2; exit 2; }

echo "APPROACH:      PER-SUBJECT (one run over all of a subject's sessions)"
echo "Capsule:       $CAPSULE_ID"
echo "Named params:  ${NAMED_PARAMS[*]}"
echo "Subject assets:${SUBJECT_ASSETS[*]:-(none)}   Fixed assets: ${FIXED_ASSETS[*]:-(none)}"
echo "Subjects:      ${#subjects[@]}   Wait: $WAIT   Max parallel: $MAX_JOBS   Dry run: $DRY_RUN"
echo

# Initialize tracking file with header
TRACKING_FILE="$TRACKING_DIR/subjects_$(date +%Y%m%d_%H%M%S).csv"
echo "item,computation_id,capsule_id,state,submitted_ts" > "$TRACKING_FILE"
echo "Tracking jobs -> $TRACKING_FILE"
echo

run_one() {
  local subj="$1"
  local safe="${subj//\//_}"
  local log="$LOG_DIR/${safe}.log"

  local -a cmd=( python "$TOOL" run --capsule-id "$CAPSULE_ID" --named-param "subject_id=$subj" )
  local p; for p in "${NAMED_PARAMS[@]:-}"; do [[ -n "$p" ]] && cmd+=( --named-param "$p" ); done
  local tpl a; for tpl in "${SUBJECT_ASSETS[@]:-}"; do [[ -n "$tpl" ]] && cmd+=( --data-asset-name "${tpl//\{subj\}/$subj}" ); done
  for a in "${FIXED_ASSETS[@]:-}"; do [[ -n "$a" ]] && cmd+=( --data-asset-name "$a" ); done
  [[ "$WAIT" == "1" ]] && cmd+=( --wait ) || cmd+=( --no-wait )

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[DRY] '; printf '%q ' "${cmd[@]}"; printf '\n'; echo dry > "$STATUS_DIR/$safe"; return 0
  fi
  echo "[start] subject $subj  -> $log"
  if output=$("${cmd[@]}" 2>&1); then
    echo "[ok]    subject $subj"; echo ok > "$STATUS_DIR/$safe"
    # Extract computation ID (last line of output)
    comp_id=$(echo "$output" | tail -1)
    if [[ -n "$comp_id" ]] && [[ ${#comp_id} -eq 36 ]]; then
      echo "$subj,$comp_id,$CAPSULE_ID,submitted,$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$TRACKING_FILE"
    fi
    echo "$output" >> "$log"
  else
    echo "[FAIL]  subject $subj  (see $log)"; echo fail > "$STATUS_DIR/$safe"
    echo "$output" >> "$log"
  fi
}

for subj in "${subjects[@]}"; do
  run_one "$subj" &
  while (( $(jobs -rp | wc -l) >= MAX_JOBS )); do wait -n 2>/dev/null || true; done
done
wait

verb="completed"; [[ "$WAIT" == "1" ]] || verb="submitted"
n_ok=$(grep -lx ok   "$STATUS_DIR"/* 2>/dev/null | wc -l)
n_fail=$(grep -lx fail "$STATUS_DIR"/* 2>/dev/null | wc -l)
echo; echo "==== per-subject summary: $n_ok $verb, $n_fail failed, ${#subjects[@]} total ===="
if (( n_fail > 0 )); then echo "failed:"; grep -lx fail "$STATUS_DIR"/* 2>/dev/null | sed 's#.*/##'; fi
echo
echo "Tracking file: $TRACKING_FILE"
if [[ "$WAIT" == "0" ]]; then
  echo "To monitor jobs in background:"
  echo "  nohup ./track-jobs.sh '$TRACKING_FILE' > track.log 2>&1 &"
fi
