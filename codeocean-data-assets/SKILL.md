---
name: codeocean-data-assets
description: Search, attach, and detach Code Ocean data assets. Use when asked to find CO data assets (by tag / name / subject / query), mount assets into the current capsule session's /data (attach — immediate, no restart), or unmount them (detach). Separate from codeocean-run-capture (which runs capsules + captures results).
---

# codeocean-data-assets

Manage Code Ocean **data assets** for the current session: **search**, **attach** (mount into `/data`), **detach**. Attach/detach act on the **current computation** (`$CO_COMPUTATION_ID`) via `computations.attach_data_assets` / `detach_data_assets`, so an attached asset appears under `/data` **immediately** — it's an S3 symlink, **no capsule restart**. Search + attach mirror `lamf_analysis/code_ocean/code_ocean_utils.py`; detach is added.

Only needs `pip install codeocean` + a token in `$API_SECRET`/`$CODEOCEAN_TOKEN` (also `$CO_TOKEN`/`$CUSTOM_KEY`); domain defaults to `codeocean.allenneuraldynamics.org`.

## Use it
```bash
S=code/claude-code-skills/codeocean-data-assets/scripts/co_data_assets.py

# search (read-only)
python $S search --tag lp-eye --subject 782149 --type result
python $S search --query multiplane-ophys_782149_2025-03-29 --type dataset
python $S search --name multiplane-ophys --tag raw --subject 782149 --contains 2025-03

# attach to THIS session's /data (immediate; select by ids and/or a search)
python $S attach --asset <id1> --asset <id2>
python $S attach --tag lp-eye --subject 782149 --type result           # attach all matches
python $S attach --tag lp-eye --subject 782149 --type result --dry-run  # preview selection, no attach

# detach from THIS session
python $S detach --tag lp-eye --subject 782149 --type result
```

## Selection model
- **`search`** builds a `DataAssetSearchParams` from `--query` and/or `SearchFilter`s (`--tag` repeatable, `--name` substring, `--subject` as a tag), optional `--type dataset|result`, paginated; `--contains` is a client-side name filter; `--max` caps totals.
- **`attach`/`detach`** act on the union of explicit `--asset <id>` (repeatable) and any search selector given. Always preview with `--dry-run` first for bulk ops. `--mount NAME` sets the mount for a single asset (otherwise assets mount under their own names).

## Notes
- Billable/outward: attach/detach change the live session's mounts (not billable compute, but confirm intent for bulk changes).
- `$CO_COMPUTATION_ID` identifies the current session; pass `--computation-id` to target a specific computation.
- For launching capsules + capturing results, use the separate **codeocean-run-capture** skill.
