---
name: codeocean-run-capture
description: Run a Code Ocean capsule or pipeline with attached data assets and capture results as a named/tagged data asset. Use for launching CO runs and capturing outputs.
---

# Code Ocean: Run & Capture

Use this skill to attach input assets, run a capsule/pipeline, and capture results.

Captured assets follow the Allen Institute / Neural Dynamics naming pattern:

```
<session>_<workflow-suffix>_<timestamp>
```

Example: `multiplane-ophys_779891_2025-03-21_14-14-28_lp-eye_2025-04-01_10-00-00`

The workflow suffix (e.g., `lp-eye`, `roicat`) tells you what processing was done.
Use a registry to standardize these across your team.

## Default behavior

`run` uses **monitor mode by default**.

```bash
python "$CLAUDE_SKILL_DIR/scripts/co_run_capture.py" run \
  --capsule-id <target_capsule_id> \
  --data-asset-name <input_asset_name> \
  --process-name-suffix <suffix> \
  --tag derived
```

For long jobs, prefer fire-and-forget:

```bash
python "$CLAUDE_SKILL_DIR/scripts/co_run_capture.py" run --no-wait \
  --capsule-id <target_capsule_id> \
  --data-asset-name <input_asset_name> \
  --process-name-suffix <suffix> \
  --tag derived
```

## Fallback policy

Use direct Code Ocean API mode only when monitor mode fails for the specific run.
Fallback instructions are in [API_FALLBACK.md](API_FALLBACK.md).

**Note on sharing**: When using direct mode, the script can't auto-share captured assets. You'll see:

```
WARNING: could not set sharing (everyone=viewer): 403 Client Error: Forbidden
```

The asset was created fine. Just share it manually in Code Ocean:
1. Go to the asset
2. Click Share → Add permissions
3. Select "Everyone" as "Viewer"

## Common commands

- `find-asset --name <substr>`
- `describe-params --capsule-id <id>`
- `run --capsule-id <id> ...`
- `capture --computation-id <id> --result-name <name>`
- `status --computation-id <id>`

## Parameter safety

Always inspect parameter mode first:

```bash
python "$CLAUDE_SKILL_DIR/scripts/co_run_capture.py" describe-params --capsule-id <id>
```

Pipelines require named parameters. Use `--named-param key=value` when uncertain.

## Batch helpers

- `run_per_session.sh`: one run per session (monitor-first).
- `run_per_subject.sh`: one run per subject.

Both accept either plain-text lists or CSV input.

### Automatic job tracking with smart polling

Both scripts automatically **write computation IDs to a tracking file** as jobs are submitted.
Use `track-jobs.sh` to poll job status in the background with **intelligent poll intervals**:

```bash
# After running with --no-wait (default)
./run_per_session.sh sessions.txt
# Output: Tracking jobs -> tracking/sessions_20260825_162500.csv

# In another terminal, poll for completion:
# Poll interval auto-calculated from capsule history (or 180s default)
nohup ./track-jobs.sh tracking/sessions_20260825_162500.csv > track.log 2>&1 &

# Or manually check once:
./track-jobs.sh tracking/sessions_20260825_162500.csv

# Specify capsule ID explicitly (if auto-detect fails):
./track-jobs.sh tracking/sessions_20260825_162500.csv --capsule-id <id>

# Override poll interval (seconds):
./track-jobs.sh tracking/sessions_20260825_162500.csv --poll 600
# or use env var:
POLL=600 ./track-jobs.sh tracking/sessions_20260825_162500.csv
```

**Tracking file format (CSV)**:
- `item`: session or subject name
- `computation_id`: the 36-char UUID returned by co_run_capture.py
- `capsule_id`: the capsule id (for poll interval estimation)
- `state`: `submitted`, `completed`, `failed`
- `submitted_ts`: ISO 8601 timestamp

**Poll interval strategy**:
- If `--poll <seconds>` is passed or `POLL` env var is set, use that explicitly.
- Otherwise, `track-jobs.sh` looks up the capsule's run history from `/scratch/tmp/run-history.json`.
- Sets poll interval to `max(median(previous_runs) / 2, 180 seconds)`.
- If no history exists, defaults to 180 seconds (3 minutes).
- Automatically records each job's duration when it completes, so the next run polls smarter.
- **Note**: Run history is temporary (per-session in `/scratch/tmp/`); it's learning data to optimize future polling.

## Known gotchas

### Wrong capture name when capsule writes its own `data_description.json`

When a capsule calls `process_json_files` (AIND convention), it writes a
`data_description.json` into results. The monitor reads this file and uses that name
as the captured asset name — **do NOT also pass `--process-name-suffix`**.

`--process-name-suffix` makes the monitor use the **input data asset name** (not
`data_description.json`) as the base, then appends the suffix. For pipelines with
intermediate steps this embeds the intermediate step name in the output:

```bash
# BAD — input asset name is used as base → intermediate step leaks in
run --monitor ... \
  --data-asset-name "multiplane-ophys_..._cortical-zstack-registration_<ts>" \
  --process-name-suffix cortical-zstack-segmentation
# → name: multiplane-ophys_..._cortical-zstack-registration_<ts>_cortical-zstack-segmentation_<ts2>

# GOOD — omit --process-name-suffix; the capsule's data_description.json is authoritative
# → name: multiplane-ophys_..._cortical-zstack-segmentation_<ts>  (correct two-part form)
run --monitor ... \
  --data-asset-name "multiplane-ophys_..._cortical-zstack-registration_<ts>" \
  --tag derived --tag cortical-zstack-segmentation --tag <subject_id>
```

**Rule**: if the capsule's `run_capsule.py` calls `process_json_files(...)` or writes
`data_description.json` itself, **always omit `--process-name-suffix`**. Pass it only
for capsules that do not write their own `data_description.json`.

### Flat `--param` silently ignored by capsules using `--flag` style argparse

When a capsule uses `argparse` with named flags (`--roi_diameter`, `--xy_resolution`,
etc.), Code Ocean receives flat positional values from `--param` but the script's
argparse does not map them — all flags fall back to their defaults silently.

```bash
# BAD — roi_diameter won't be set
run --monitor --capsule-id 0a174d03-... --param 30 --param 0.78125 ...

# GOOD — use --named-param
run --monitor --capsule-id 0a174d03-... \
  --named-param roi_diameter=30 --named-param xy_resolution=0.78125 ...
```

**Rule**: always use `--named-param key=value` for capsule parameters.

## Team Capsule Registry (Optional)

A **registry** is an optional JSON file (`.co-registry.json`) that maps friendly capsule names to their UUIDs and standard processing conventions. This enables your team to:

- **Use friendly names** instead of UUIDs  
  e.g., `--capsule lp-eye` (instead of `--capsule-id 550e8400-...`)

- **Auto-fill conventions**  
  e.g., `--process-name-suffix` and `--tag` are auto-applied from the registry

- **Enforce team standards**  
  Derived assets use consistent naming across team members, so downstream analysis scripts can reliably find them

### Generate a registry from your XLSX capsule info

If your team maintains a capsule info spreadsheet (e.g., `CO_capsule_infos_*.xlsx`):

```bash
# Convert your XLSX → .co-registry.json (one-time setup)
python scripts/build_registry.py /path/to/CO_capsule_infos_*.xlsx .co-registry.json
```

See [capsule_info.example.csv](capsule_info.example.csv) for the required columns and format.

The `.co-registry.json` file is **not tracked in git** (see `.gitignore`). It's auto-generated from your XLSX.

### Regenerate when XLSX updates

When your team updates `CO_capsule_infos_*.xlsx`:

```bash
git pull
python scripts/build_registry.py /path/to/CO_capsule_infos_*.xlsx .co-registry.json
```

### Use the registry in commands

```bash
# With registry: friendly names + auto-filled conventions
python co_run_capture.py run --registry .co-registry.json --capsule lp-eye \
  --data-asset-name multiplane-ophys_779891_2025-03-21 ...

# The registry auto-fills:
#   --capsule-id 550e8400-...
#   --process-name-suffix lp-eye
#   --tag lp-eye --tag derived

# Without registry: explicitly pass everything
python co_run_capture.py run \
  --capsule-id 550e8400-... \
  --data-asset-name multiplane-ophys_779891_2025-03-21 ...
```

### Interactive setup (first run)

If you run `co_run_capture.py` without a registry (and not in a script), you'll be prompted:

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                       CAPSULE REGISTRY (Optional)                            ║
╚══════════════════════════════════════════════════════════════════════════════╝

A registry is a JSON file that maps friendly capsule names to their UUIDs...

Do you have a lookup table (XLSX/CSV) with capsule info? [y/n/skip]:
```

- **y**: Provide the XLSX path; the registry will be generated for you
- **n**: Skip registry; you'll provide all parameters explicitly
- **skip**: Don't ask again for this run

## Guardrails

- Running capsules and creating assets are billable actions.
- Confirm capsule id, inputs, result naming, and tags before running unless user already approved.
- Ensure mounts match what the target capsule expects.
