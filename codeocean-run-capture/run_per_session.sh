#!/usr/bin/env bash
# run_per_session.sh — PER-SESSION approach.
#
#   >>> Runs the target capsule ONCE PER SESSION. <<<
#   For each session it attaches the requested per-session assets + any fixed
#   assets, runs the capsule, and captures the result named
#     <raw session name>_<PROCESS_SUFFIX>_<date>_<time>.
#   Timestamp reflects CAPTURE time (server-side in monitor mode; post-completion
#   in direct mode). Derived inputs (e.g. *_processed_*) still name from the raw
#   session (via --input-name).
#
# Which sessions to run: a plain list (one session NAME per line) OR a CSV column
# (see COLUMN / INCLUDE_* below), e.g. a QC/cohort table.
#
# Which assets to attach per session: SESSION_ASSETS (templates, {s}=session) +
# FIXED_ASSETS (literal names). Mix any of: raw ophys, processed ophys, lp-eye,
# processed-behavior, coreg-id-table, models, ...
#
# For the SUBJECT-level approach (one run over ALL of a subject's sessions, e.g.
# ROICaT), use run_per_subject.sh.
#
# Usage:
#   ./run_per_session.sh sessions.txt
#   ./run_per_session.sh cohort.csv                 # COLUMN picks the session column
#   SESSIONS="name1 name2" ./run_per_session.sh
#   DRY_RUN=1 ./run_per_session.sh cohort.csv
#   USE_MONITOR=1 WAIT=0 ./run_per_session.sh sessions.txt   # server-side fire-and-forget
#
# Auth: token from $CODEOCEAN_TOKEN/$API_SECRET (+ $CODEOCEAN_DOMAIN). Billable.

set -u -o pipefail

# ------------------------------- CONFIG (edit me) -------------------------------
CAPSULE_ID="${CAPSULE_ID:-54a4898c-01a0-4710-be33-4a528bc8b4b4}"   # e.g. LP eye-tracking

# Per-session assets to attach. Templates; {s} is replaced with the session name.
# Attach any combination the task needs (resolved by name -> newest Ready match):
#   "{s}"                    raw ophys (the session asset itself)
#   "{s}_processed"          processed ophys
#   "{s}_lp-eye"             eye-tracking (LP)
#   "{s}_processed-behavior" processed behavior
# (confirm exact derived-name suffixes for your project with `find-asset`)
SESSION_ASSETS=(
  "{s}"
)

# Fixed assets attached to EVERY run, by literal NAME (models, coreg-id-table, ...):
FIXED_ASSETS=(
  "lightningPose-eye-model_multiplane-ophys-raw-video_2026-07-11"
  "lightningPose-eye-model_multiplane-ophys-clahe-video_2026-07-11"
)

PROCESS_SUFFIX="${PROCESS_SUFFIX:-lp-eye}"     # captured name = <raw session>_<suffix>_<capture date_time>
TAGS=(derived multiplane-ophys lp-eye)
COMMON_META=("experiment type=multiplane-ophys" "data level=derived")

USE_MONITOR="${USE_MONITOR:-0}"   # 1 = via aind pipeline-monitor capsule (server-side capture-time naming)
FORCE_RAW_NAME="${FORCE_RAW_NAME:-0}"  # (monitor only) 1 = force raw-stripped name client-side (SUBMIT time)
WAIT="${WAIT:-1}"                 # 1 = wait per job (throttled + pass/fail); 0 = fire-and-forget
MAX_JOBS="${MAX_JOBS:-4}"
DRY_RUN="${DRY_RUN:-0}"

# CSV input (used only when the input file ends in .csv):
COLUMN="${COLUMN:-session}"       # column holding the session name (header name or index)
INCLUDE_COLUMN="${INCLUDE_COLUMN:-}"   # optional: keep only rows where this column is truthy/==INCLUDE_VALUE
INCLUDE_VALUE="${INCLUDE_VALUE:-}"
# -------------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$SCRIPT_DIR/scripts/co_run_capture.py"
READER="$SCRIPT_DIR/scripts/read_items.py"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs_per_session}"
STATUS_DIR="$LOG_DIR/status"
mkdir -p "$STATUS_DIR"
[[ -f "$TOOL" ]] || { echo "ERROR: tool not found at $TOOL" >&2; exit 1; }

read_list() {
  python3 "$READER" "$1" ${COLUMN:+--column "$COLUMN"} \
    ${INCLUDE_COLUMN:+--include-col "$INCLUDE_COLUMN"} ${INCLUDE_VALUE:+--include-val "$INCLUDE_VALUE"}
}

sessions=()
if [[ $# -ge 1 && -f "$1" ]]; then
  while IFS= read -r line; do [[ -n "$line" ]] && sessions+=("$line"); done < <(read_list "$1")
elif [[ -n "${SESSIONS:-}" ]]; then
  read -r -a sessions <<< "$SESSIONS"
else
  echo "usage: $0 <sessions.txt|cohort.csv>   (or set SESSIONS='name1 name2 ...')" >&2; exit 2
fi
(( ${#sessions[@]} > 0 )) || { echo "No sessions to run." >&2; exit 2; }

mode="direct"; [[ "$USE_MONITOR" == "1" ]] && mode="monitor"
echo "APPROACH:      PER-SESSION (one run per session)"
echo "Capsule:       $CAPSULE_ID"
echo "Mode:          $mode   Wait: $WAIT   Max parallel: $MAX_JOBS   Dry run: $DRY_RUN"
echo "Sessions:      ${#sessions[@]}   Suffix: $PROCESS_SUFFIX"
echo "Session assets:${SESSION_ASSETS[*]:-(none)}"
echo "Fixed assets:  ${FIXED_ASSETS[*]:-(none)}"
echo

subject_of() { echo "$1" | grep -oE '_[0-9]{6}_' | head -1 | tr -d '_' || true; }

run_one() {
  local sess="$1"
  local safe="${sess//\//_}"
  local log="$LOG_DIR/${safe}.log"
  local subj; subj="$(subject_of "$sess")"

  # --input-name forces the captured-name base to the raw session, regardless of
  # which assets are attached / in what order.
  local -a cmd=( python "$TOOL" run --capsule-id "$CAPSULE_ID" --input-name "$sess" )
  local tpl a; for tpl in "${SESSION_ASSETS[@]:-}"; do [[ -n "$tpl" ]] && cmd+=( --data-asset-name "${tpl//\{s\}/$sess}" ); done
  for a in "${FIXED_ASSETS[@]:-}"; do [[ -n "$a" ]] && cmd+=( --data-asset-name "$a" ); done
  cmd+=( --process-name-suffix "$PROCESS_SUFFIX" )
  local t; for t in "${TAGS[@]:-}"; do [[ -n "$t" ]] && cmd+=( --tag "$t" ); done
  [[ -n "$subj" ]] && cmd+=( --tag "$subj" --meta "subject id=$subj" )
  local m; for m in "${COMMON_META[@]:-}"; do [[ -n "$m" ]] && cmd+=( --meta "$m" ); done
  if [[ "$USE_MONITOR" == "1" ]]; then
    cmd+=( --monitor )
    [[ "$FORCE_RAW_NAME" == "1" ]] && cmd+=( --client-name )
  else
    cmd+=( --capture )
  fi
  [[ "$WAIT" == "1" ]] && cmd+=( --wait ) || cmd+=( --no-wait )

  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[DRY] '; printf '%q ' "${cmd[@]}"; printf '\n'; echo dry > "$STATUS_DIR/$safe"; return 0
  fi
  echo "[start] $sess  -> $log"
  if "${cmd[@]}" > "$log" 2>&1; then echo "[ok]    $sess"; echo ok > "$STATUS_DIR/$safe"
  else echo "[FAIL]  $sess  (see $log)"; echo fail > "$STATUS_DIR/$safe"; fi
}

for sess in "${sessions[@]}"; do
  run_one "$sess" &
  while (( $(jobs -rp | wc -l) >= MAX_JOBS )); do wait -n 2>/dev/null || true; done
done
wait

verb="completed"; [[ "$WAIT" == "1" ]] || verb="submitted"
n_ok=$(grep -lx ok   "$STATUS_DIR"/* 2>/dev/null | wc -l)
n_fail=$(grep -lx fail "$STATUS_DIR"/* 2>/dev/null | wc -l)
echo; echo "==== per-session summary: $n_ok $verb, $n_fail failed, ${#sessions[@]} total ===="
if (( n_fail > 0 )); then echo "failed:"; grep -lx fail "$STATUS_DIR"/* 2>/dev/null | sed 's#.*/##'; exit 1; fi
