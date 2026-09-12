# Path 1 (`--monitor`) — Extended Reference

## Payload size

The monitor capsule has a 4096-char parameter limit. The script keeps mount names
in the payload for all assets — omitting them causes the monitor's SDK to reconstruct
`mount=None` and serialize it as `"mount": null`, which CO rejects with 400. This issue is in aind-codeocean-pipeline-monitor (using old SDK).

If you hit the limit: reduce tags, shorten asset names, or switch to `--no-monitor`.

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
