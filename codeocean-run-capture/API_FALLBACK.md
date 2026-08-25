# API fallback (use only if monitor mode fails)

This path is a fallback for cases where monitor mode cannot be used successfully.

## 1) Run directly

```bash
python "$CLAUDE_SKILL_DIR/scripts/co_run_capture.py" run --no-monitor \
  --capsule-id <target_capsule_id> \
  --data-asset-name <input_asset_name> \
  --named-param key=value \
  --wait --capture \
  --result-name <result_name> \
  --tag derived
```

## 2) If you launched with `--no-wait`, capture later

```bash
python "$CLAUDE_SKILL_DIR/scripts/co_run_capture.py" capture \
  --computation-id <computation_id> \
  --result-name <result_name> \
  --tag derived
```

## 3) Troubleshooting

- Verify parameter mode first:
  `describe-params --capsule-id <id>` or `--pipeline-id <id>`.
- Prefer `--named-param key=value` when possible.
- Ensure input assets are attached at expected mount paths.
