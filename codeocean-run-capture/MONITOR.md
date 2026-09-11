# Path 1 (`--monitor`) — Extended Reference

## Payload size

The monitor capsule has a 4096-char parameter limit. The script omits mount from
data assets automatically (~84 chars saved/asset). Explicit mounts
(`--data-asset <id>:<custom_mount>`) are preserved.

If you still hit the limit: reduce tags, shorten asset names, or switch to `--no-monitor`.

## Naming — when `data_description.json` is absent

If the capsule does NOT write `data_description.json`, pass `--process-name-suffix <suffix>`.
The monitor then names the asset `<first_input_asset_name>_<suffix>_<capture_ts>`.

## Registry (optional)

Maps friendly capsule names to UUIDs and auto-fills tags/suffix:

```bash
# Generate from XLSX (one-time)
python scripts/build_registry.py /path/to/CO_capsule_infos_*.xlsx .co-registry.json

# Use
python "$S" run --registry .co-registry.json --capsule lp-eye --data-asset-name <name>
```
