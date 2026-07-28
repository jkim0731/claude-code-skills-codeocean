---
name: codeocean-run-capture
description: Run a Code Ocean capsule or pipeline with attached data assets and capture its results as a named, tagged data asset. Use when asked to launch a CO capsule, attach data assets to a run, batch-run a capsule over sessions, or register/capture computation results as a data asset with specific tags/name/metadata (Allen Neural Dynamics or any Code Ocean deployment).
---

# Code Ocean: Run & Capture

Self-contained tooling to **attach data assets → run a capsule → capture results
as a data asset** (with a specific name, tags, and custom metadata), using the
`codeocean` python client directly. No `lamf_analysis` / `aind-*` dependencies.

## When to use
- "Run capsule X on session Y and save the output as a data asset."
- "Attach these assets to the capsule and launch it."
- "Capture the results of computation Z as a tagged data asset."
- Batch/session-level runs (loop the `run` command over sessions).

## Prerequisites
- `pip install codeocean` (only dependency).
- An API token in the environment (checked in order): `$CODEOCEAN_TOKEN`,
  `$API_SECRET`, `$CO_TOKEN`, `$CUSTOM_KEY` — or pass `--token`.
- Domain from `$CODEOCEAN_DOMAIN` or `--domain` (defaults to the AIND deployment).
- Network egress to the Code Ocean API host.

## How to run it
The tool is `scripts/co_run_capture.py`. Invoke with Bash:

```bash
python "$CLAUDE_SKILL_DIR/scripts/co_run_capture.py" <subcommand> [options]
```

(If running outside Claude, just use the path to the script.)

### Subcommands
- `find-asset --name <substr>` — search data assets, print `id  state  name`.
- `run --capsule-id <id> [--data-asset <id>[:mount] ...] [--data-asset-name <name>[:mount] ...]
   [--param V] [--named-param k=v] [--wait|--no-wait]
   [--capture --result-name <name> --tag T --meta k=v --result-mount M --result-path P]`
- `capture --computation-id <id> --result-name <name> [--tag T --meta k=v ...]`
- `status --computation-id <id>`

### Typical flow
1. Resolve asset ids: `find-asset --name <session_or_model>`.
2. Run + capture (waits for completion, then registers results):

```bash
python scripts/co_run_capture.py run \
  --capsule-id 54a4898c-01a0-4710-be33-4a528bc8b4b4 \
  --data-asset <session_id>:<session_mount> \
  --data-asset <raw_model_id>:lightningPose-eye-model_multiplane-ophys-raw-video_2026-07-11 \
  --data-asset <clahe_model_id>:lightningPose-eye-model_multiplane-ophys-clahe-video_2026-07-11 \
  --wait --capture \
  --result-name lightningPose-eye-tracking_<session> \
  --tag derived --tag multiplane-ophys --tag lp-eye --tag <subject_id> \
  --meta "data level=derived" --meta "experiment type=multiplane-ophys" --meta "subject id=<subject_id>"
```

For long runs you can `--no-wait`, note the printed computation id, and `capture`
it later.

### Monitor mode (server-side run + capture)
Add `--monitor` to hand the whole job to the AIND pipeline-monitor capsule
(`567b5b98-8d41-413b-9375-9ca610ca2fd3`), mirroring the `Jinho_pipeline-monitor`
pattern: it serializes `PipelineMonitorSettings` (`run_params` + `capture_settings`)
to a JSON string and launches the monitor capsule with it; the monitor runs the
target and captures results **server-side** (best for long runs; use `--no-wait`
to fire-and-forget). The JSON parameter is capped at 4096 chars (the tool
enforces it); `mount` is omitted so assets mount under their own names.

```bash
python "$CLAUDE_SKILL_DIR/scripts/co_run_capture.py" run --monitor --no-wait \
  --capsule-id <target> --data-asset-name <session> \
  --data-asset-name <model_a> --data-asset-name <model_b> \
  --process-name-suffix lp-eye --tag derived --tag lp-eye
```

### Captured-asset naming — CAPTURE time
Name = `<raw input name>_<process-name-suffix>_<date>_<time>`, raw base (derived
tails like `*_processed_*` stripped). The timestamp reflects **capture time**:
built post-completion in direct mode; named server-side by the monitor in monitor
mode (uses the capsule's `data_description.json` when present). `--client-name`
forces a client-side raw name (submit-time) if needed; `--result-name` overrides.

### Two orchestration scripts (sessions/subjects from a list OR a CSV column)
- **PER-SESSION** — `run_per_session.sh sessions.txt|cohort.csv`: one run per
  session. Config `SESSION_ASSETS` (per-session templates, `{s}`=session) +
  `FIXED_ASSETS` attach any combination — raw ophys `"{s}"`, processed ophys
  `"{s}_processed"`, eye-tracking `"{s}_lp-eye"`, processed-behavior
  `"{s}_processed-behavior"`, models/coreg-id-table (literal). `USE_MONITOR=1 WAIT=0`
  = server-side fire-and-forget. CSV: `COLUMN` (default `session`), optional
  `INCLUDE_COLUMN`/`INCLUDE_VALUE`.
- **PER-SUBJECT** — `run_per_subject.sh subjects.txt|cohort.csv`: one run over ALL
  of a subject's sessions. Fires a subject-level capsule with a `subject_id` named
  param (default: ROICaT monitor `d6c4c877…` → gathers sessions, runs ROICaT
  `0f51d117`). `NAMED_PARAMS` + `SUBJECT_ASSETS` (`{subj}` templates, e.g.
  `coreg-id-table_{subj}`, `HCR_{subj}`) + `FIXED_ASSETS` cover HCR / coregistration.

## Guardrails
- Launching a run and creating data assets are outward-facing, billable actions.
  Confirm the capsule id, asset ids/mounts, result name and tags with the user
  before running, unless they've said to proceed.
- Mounts matter: attach each asset at the mount path the target capsule expects
  (e.g. it may glob on the asset/dir name).

See `README.md` for install, auth details, and how this maps to the AIND
pipeline-monitor pattern.
