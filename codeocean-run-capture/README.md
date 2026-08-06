# codeocean-run-capture

Portable tooling to **attach data assets → run a Code Ocean capsule/pipeline →
capture the results as a named, tagged data asset**, using the `codeocean` python
client directly (only `pip install codeocean` required — no `lamf_analysis` /
`aind-*` dependencies).

Two orchestration scripts sit on top of the tool:

- **`run_per_session.sh`** — **PER-SESSION**: one capsule run per session.
- **`run_per_subject.sh`** — **PER-SUBJECT**: one run over *all* of a subject's
  sessions (e.g. ROICaT; also HCR / coregistration).

Both take the list of sessions/subjects from a plain text file **or a CSV column**.

## Deploy
- **As a Claude Code skill**: copy this folder into a capsule's `.claude/skills/`.
- **As plain scripts**: copy the folder (or just `scripts/`) into `code/` and run
  with `python` / `bash`.

## Auth
- **token** (first found): `--token`, `$CODEOCEAN_TOKEN`, `$API_SECRET`, `$CO_TOKEN`, `$CUSTOM_KEY`.
- **domain**: `--domain`, `$CODEOCEAN_DOMAIN`, else `https://codeocean.allenneuraldynamics.org`.
- Network egress to the CO API host is required.

## The tool (`scripts/co_run_capture.py`)

| command | what it does |
|---|---|
| `find-asset --name <substr>` | search data assets → `id  state  name` |
| `run` | attach assets, run a capsule/pipeline; direct (`--capture`) or `--monitor` |
| `capture` | create a data asset from a finished computation id |
| `status` | print a computation's state |
| `describe-params` | inspect a target's **parameter configuration** — capsule vs pipeline, app-panel params, and whether to pass them **flat** or **named** |

## Parameter configuration — flat vs named vs positional

Capsules and pipelines consume parameters **differently**, and a **pipeline
silently ignores flat positional parameters** — the run "succeeds" but every value
falls back to its default (e.g. `acquisition_data_type` stays `single` instead of
the `multiplane` you passed). So always check the target first:

```bash
python scripts/co_run_capture.py describe-params --capsule-id <id>      # auto-detects kind
python scripts/co_run_capture.py describe-params --pipeline-id <id>
```

It prints the detected **kind**, the app-panel parameters (`idx / param_name /
default / category`), and the required **PARAMETER MODE**:

- **pipeline → NAMED**: pass `--named-param param_name=value`. Run pipelines with
  `--pipeline-id` (or `--kind pipeline`); the tool submits via `RunParams.pipeline_id`.
- **capsule (app panel) → FLAT**: pass `--param <value>` in the printed `idx` order,
  or `--named-param`.
- **capsule (no app panel) → positional CLI**: `--param` in the order its code expects.

`run` does this automatically (`--kind auto`, `--param-mode auto`):

1. **auto-detects** capsule vs pipeline (a pipeline has a `versions` array and no
   `cloned_from_url`); override with `--kind {capsule,pipeline}`.
2. **routes params**: pipeline → named; capsule → flat. For a pipeline, flat
   `--param` values are **auto-mapped onto the app-panel param_names by order**
   (count must match) so existing flat-param call sites keep working.
3. **verifies after submit** that the requested values actually landed on the
   computation, warning loudly on any mismatch (`--no-verify-params` to skip).

> Fixed assets baked into a pipeline (models, schemas) attach **automatically** —
> do not re-attach them (the API rejects the run with *"data asset already
> attached"*); pass only the variable input(s), e.g. the raw session.

## Capsule registry — `--capsule <name|id|suffix>`
`capsule_registry.json` (built from the CO capsule-info spreadsheet via
`scripts/build_registry.py`) maps each capsule → its **id**, **suffix**, **result tags**,
**required data type**, and **pre-attached asset**. Pass `--capsule` to `run` or
`batch_monitor` and it auto-fills `--capsule-id`, `--process-name-suffix`, and the
result `--tag`s from the registry — so any registered capsule runs with the correct
naming/tagging without hand-specifying them. Explicit `--capsule-id/--process-name-suffix/--tag`
always override. Match is by exact id, exact name, exact suffix, or a unique name-substring.

```bash
# rebuild the registry when the spreadsheet changes:
python scripts/build_registry.py /path/to/CO_capsule_infos.xlsx
# run any capsule by name/suffix (tags + suffix come from the registry):
python scripts/co_run_capture.py run --capsule lp-eye --monitor --data-asset <session_id> ...
python scripts/batch_monitor.py  --capsule HCR-ROI-label --items-file cohort.csv --column raw_asset_id
```
Generalizes across data types: the per-subject tag/metadata auto-derivation only fires on
`multiplane-ophys_<subject>_…` names (a no-op otherwise), and derived-name stripping/dedup
knows every registry suffix. The registry currently holds 18 runnable capsules + 7 pipeline-monitors.

### Run modes
- **Direct** (`--capture`): this process runs the target, waits, and creates the
  results asset itself. The name is built **after completion** → **capture-time**
  timestamp, with the raw-stripped base.
- **Monitor** (`--monitor`): serialize `PipelineMonitorSettings` and launch the
  pipeline-monitor capsule (`567b5b98…`); it runs the target + captures
  **server-side** (robust for long runs; `--no-wait` = fire-and-forget). JSON
  capped at 4096 chars; `mount` omitted (assets mount under their own names).

### Captured-asset naming — reflects CAPTURE time
Name = `<raw input name>_<process-name-suffix>_<YYYY-MM-DD>_<HH-MM-SS>`.

- **Direct mode**: built post-completion → capture-time timestamp; base is the
  raw name of `--input-name` (or the first `--data-asset-name`), stripping any
  derived tail (`*_processed_*`, `_nwb`, …).
- **Monitor mode (default)**: the monitor names it **at capture time** — it uses
  the output's `data_description.json` name (raw base, capture-time) when the
  capsule writes one, else `<first attached asset>_<suffix>_<capture-ts>`.
- `--client-name` (monitor only): force the raw-stripped name client-side —
  **NOTE: submit-time** timestamp. Use only if you must guarantee the raw base and
  the capsule/monitor won't produce it.
- `--result-name` overrides everything (verbatim). `--name-tz` sets the tz.

## Concurrency-capped batch — `scripts/batch_monitor.py`
For many long monitor runs, use this instead of firing all at once. It mirrors the
aind pipeline-monitor control loop: keeps at most `--max-jobs` monitor computations
in flight (default 10), submits the next as each finishes, and prints live
**submitted / active / finished(completed+failed) / queued** counts. Resumable via a
state CSV (`--state-file`, defaults next to the items file).

```bash
python scripts/batch_monitor.py --capsule-id 54a4898c-... \
  --items-file sessions.txt --max-jobs 10 --poll 120 \
  --process-name-suffix lp-eye --tag lp-recheck
# CSV column + include filter, dry-run preview:
python scripts/batch_monitor.py --capsule-id ... --items-file cohort.csv \
  --column session --include-col include --dry-run
```
Session assets are attached with `mount` = asset name (required by the run API on
this deployment); models pre-attached to the capsule are not re-sent.

### Completion is judged by `exit_code` + a real captured asset — NOT by `state`
**The pipeline-monitor lies about success.** When its *target* run fails, the monitor
computation still reports `state=completed` **and** `end_status=succeeded` — only
`exit_code` tells the truth (`0`=captured, `1`=raised/no-capture). `has_results` is
`False` for good *and* bad monitors (the monitor writes just its own `output` log; the
scientific output is a *separate* captured data asset), so it must not be used as a gate.
(Diagnosed 2026-07-24: a 149-session batch reported 149/149 "completed" but 15 never
captured — all had `exit_code=1`.)

So `batch_monitor`:
- marks an item **done only if `exit_code==0`** (via `classify_outcome`), else **failed**
  (retryable with `--retry-failed` / `--max-retries`);
- with **`--verify-capture`** (default **on**) it then confirms a **READY result data
  asset actually exists** (provenance: capsule + input session in `data_assets`, +
  `--require-data-asset`/`--require-commit`); a run that finished but captured nothing is
  marked `failed (no-capture-asset)`. `--no-verify-capture` disables this second gate.

Note: Code Ocean's `query`/`name` search filters do **not** match a derived capture by its
base name — only a **tag** search does. Both `--verify-capture` and `--skip-existing` search
by the capture tags (+ subject), so **always pass the result `--tag`s** (the registry supplies
them via `--capsule`) or capture verification/dedup can't find the asset.

## PER-SESSION — `run_per_session.sh`
Runs the target capsule once per session; captures each result
`<raw session>_<suffix>_<capture date_time>`.

```bash
./run_per_session.sh sessions.txt                       # direct, wait+capture per session
./run_per_session.sh cohort.csv                         # sessions from a CSV column
USE_MONITOR=1 WAIT=0 ./run_per_session.sh sessions.txt  # server-side fire-and-forget
DRY_RUN=1 ./run_per_session.sh cohort.csv               # preview
```

**Which sessions** (config): plain list, or CSV via `COLUMN` (default `session`)
with optional `INCLUDE_COLUMN`/`INCLUDE_VALUE` row filter. See
`sessions.example.txt` / `sessions.example.csv`.

**Which assets to attach** (config): `SESSION_ASSETS` are per-session templates
(`{s}` = session), `FIXED_ASSETS` are literal names attached to every run. Mix any
combination the task needs:

| asset type | example selector |
|---|---|
| raw ophys | `"{s}"` |
| processed ophys | `"{s}_processed"` |
| eye-tracking (LP) | `"{s}_lp-eye"` |
| processed behavior | `"{s}_processed-behavior"` |
| models / coreg-id-table / … | literal name in `FIXED_ASSETS` |

(Confirm exact derived-name suffixes for your project with `find-asset`.) The
captured-name base is always the raw session (`--input-name`), regardless of which
assets are attached. `FORCE_RAW_NAME=1` adds `--client-name` in monitor mode.

## PER-SUBJECT — `run_per_subject.sh`
Runs one capsule per subject, over all of that subject's sessions. Fires a
subject-level capsule with a `subject_id` **named** parameter; that capsule gathers
the sessions and captures results itself (no 4096 limit).

Default target = ROICaT monitor `d6c4c877` ("Jinho_pipeline_monitor_ROICat"),
which gathers a subject's processed ophys sessions and runs ROICaT compute
`0f51d117` across them.

```bash
./run_per_subject.sh subjects.txt
./run_per_subject.sh cohort.csv          # subjects from a CSV column (COLUMN=subject_id)
SUBJECTS="779891 767022" ./run_per_subject.sh
DRY_RUN=1 ./run_per_subject.sh subjects.txt
```

**Config**: `CAPSULE_ID`, `NAMED_PARAMS` (default matches the ROICaT monitor app
panel: `dff_long_window`, `max_jobs`, `test`, `sleep`, `ignore_not_processed`),
`SUBJECT_ASSETS` (per-subject templates, `{subj}` = subject id — e.g.
`coreg-id-table_{subj}`, `HCR_{subj}`), `FIXED_ASSETS`, `WAIT` (default 0),
CSV `COLUMN`/`INCLUDE_*`.

**Other subject-level combinations** — swap `CAPSULE_ID` + `NAMED_PARAMS` and add
the inputs each capsule needs (HCR, coregistration, …) via `SUBJECT_ASSETS` /
`FIXED_ASSETS`.

## Notes / guardrails
- Running capsules and creating data assets are **billable, outward-facing**
  actions — confirm capsule id, asset ids/mounts, result name and tags first.
- **Mounts** omitted by default → CO mounts each asset under its own name.
- Only `codeocean` is required; monitor-JSON uses the
  `aind_codeocean_pipeline_monitor` models when installed, else a hand-built dict.
