#!/usr/bin/env bash
# run_per_subject.sh — PER-SUBJECT approach.
#
#   >>> Runs ONE capsule per SUBJECT, over ALL of that subject's sessions. <<<
#   Default target: the ROICaT monitor capsule d6c4c877 ("Jinho_pipeline_monitor_
#   ROICat"), fired with a `subject_id` NAMED parameter; it internally gathers the
#   subject's processed ophys sessions, runs ROICaT (0f51d117) across them, and
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
CAPSULE_ID="${CAPSULE_ID:-d6c4c877-9755-4837-9322-3cd9d562ad8b}"   # ROICaT monitor (subject-level)

# NAMED parameters for that capsule (subject_id is added per-subject). Defaults
# match the ROICaT monitor's app panel; edit for HCR / coregistration capsules.
NAMED_PARAMS=(
  "dff_long_window=1800"
  "max_jobs=10"
  "test=0"              # 0 = real run, 1 = quick test subset
  "sleep=600"
  "ignore_not_processed=1"
)

# Per-subject assets to attach (templates; {subj}=subject id). Usually empty for
# ROICaT. Examples for other tasks:
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
mkdir -p "$STATUS_DIR"
[[ -f "$TOOL" ]] || { echo "ERROR: tool not found at $TOOL" >&2; exit 1; }

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
  if "${cmd[@]}" > "$log" 2>&1; then echo "[ok]    subject $subj"; echo ok > "$STATUS_DIR/$safe"
  else echo "[FAIL]  subject $subj  (see $log)"; echo fail > "$STATUS_DIR/$safe"; fi
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
if (( n_fail > 0 )); then echo "failed:"; grep -lx fail "$STATUS_DIR"/* 2>/dev/null | sed 's#.*/##'; exit 1; fi
