---
name: codeocean-run-capture
description: Run a Code Ocean capsule or pipeline with attached data assets and capture results as a named/tagged data asset. Use for launching CO runs and capturing outputs.
---

# Code Ocean: Run & Capture

```bash
S="$CLAUDE_SKILL_DIR/scripts/co_run_capture.py"
```

## Guardrails

- Running capsules and creating assets are **billable**. Confirm capsule id, inputs, and tags before running.

## Path 1 — `--monitor` (default, fire-and-forget)

Server-side capture. Session does not need to stay alive.

```bash
python "$S" run \
  --capsule-id <id> \
  --data-asset <id1> --data-asset <id2> \
  --tag derived --tag <subject_id> \
  --no-wait
```

- **Omit `--process-name-suffix`** when the capsule writes its own `data_description.json` (AIND convention). The monitor uses that file for the asset name. Passing a suffix uses the input asset name as the base instead — wrong for multi-step pipelines.
- Payload limit: 4096 chars. If you hit it, see [MONITOR.md](MONITOR.md).
- Permissions: `everyone=viewer` set automatically.

## Path 2 — `--no-monitor` (session-managed)

Direct API. No payload limit. Session must stay alive to capture, or capture manually later.

```bash
python "$S" run --no-monitor \
  --capsule-id <id> \
  --data-asset <id1> \
  --result-name "<name from capsule's data_description.json>" \
  --tag derived --tag <subject_id>
```

- If unsure of the name, capture with any name then fix: `python "$S" rename-from-dd --asset-id <id>`
- Permissions: `everyone=viewer` set automatically on capture.

## Params — always check first

```bash
python "$S" describe-params --capsule-id <id>
```

Capsules using `argparse` flags require `--named-param key=value`, not `--param value` (flat params are silently ignored).

## Other commands

```bash
python "$S" find-asset --name <substr>
python "$S" status --computation-id <id>
python "$S" capture --computation-id <id> --result-name <name> --tag derived
```

For batch runs and job tracking, see [BATCH.md](BATCH.md).
