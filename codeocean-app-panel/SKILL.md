---
name: codeocean-app-panel
description: Author and validate a Code Ocean capsule's App Panel (.codeocean/app-panel.json) and keep its parameters in sync with the capsule's code/run_capsule.py argparse. Use when adding/editing capsule UI parameters, after pulling an app-panel.json edited in the CO UI, or to check that every panel parameter actually reaches argparse (CO passes panel params to run_capsule.py, so the two MUST match).
---

# codeocean-app-panel

A Code Ocean **App Panel** (`.codeocean/app-panel.json`) defines the parameters a
user sees when running the capsule as an app. CO passes those parameters to the
capsule's command, which for these capsules is `python -u run_capsule.py "$@"`.
So **every user-facing panel parameter must correspond to an argparse argument in
`code/run_capsule.py`** — otherwise the UI value never reaches the code.

## The passing contract (this is what "must match argparse" means)

- CO passes each panel parameter to the command as `--<key> <value>`, where the
  **key = `param_name` if present, else `name`**. So each panel parameter's key must
  equal an argparse flag in `run_capsule.py` (minus the `--`), and `default_value` /
  `value_type` should match the argparse `default` / `type`.
- The CO **UI writes valid schema** — don't hand-fix format. Two valid shapes exist:
  - explicit: `"named_parameters": true` + a `param_name` per arg
    (e.g. training capsule: `param_name` `use_clahe`, `max_epochs`, …);
  - name-as-key: hyphenated `name` used directly as the flag
    (e.g. eye-tracking capsule: `name` `pupil-eye-inflation` ↔ `--pupil-eye-inflation`).
  Both reach argparse; the checker treats `param_name or name` as the key.

## Validate (do this after any panel or argparse edit)

```bash
python scripts/check_app_panel.py <capsule_dir>          # default: /lightningPose-eye-tracking
python scripts/check_app_panel.py <capsule_dir> --json   # machine-readable; exit 1 on mismatch
```

It statically (no imports, no capsule run) parses `app-panel.json` and the
`add_argument(...)` calls in `code/run_capsule.py`, resolving argparse defaults that
point at `utils.X` constants, and reports: missing `named_parameters`, params with no
`param_name`, arg keys with no matching argparse flag, default mismatches, and
argparse flags not exposed in the panel (often intentional, e.g. `--output_dir`).

## Adding / editing a parameter — change BOTH sides

1. **`run_capsule.py`**: add `p.add_argument("--my-flag", type=..., default=...)` and
   wire it (for the eye capsule, params are applied to `utils` constants + env vars in
   `configure_pupil_qc_from_args()` so spawn workers inherit them, and recorded in
   `processing.json`).
2. **`app-panel.json`**: add the parameter **in the CO UI** (it writes valid schema),
   using a key (`name`, or `param_name`) that equals `my-flag`, with matching
   `default_value`/`value_type`; pull the updated file.
3. Run the checker; it should print ✅.

## LP eye-tracking capsule — current parameters

`configure_pupil_qc_from_args()` in `run_capsule.py` (defaults from `utils.py`):

| panel param / arg key            | argparse flag                       | utils constant / env var             | default | range     |
|----------------------------------|-------------------------------------|--------------------------------------|---------|-----------|
| `pupil-eye-inflation`            | `--pupil-eye-inflation`             | `PUPIL_EYE_INFLATION`                | 1.10    | 1–2       |
| `pupil-regularity-2nd-worst-deg` | `--pupil-regularity-2nd-worst-deg`  | `PUPIL_REGULARITY_2ND_WORST_DEG`     | 25.0    | 0–360     |
| `pupil-containment-min`          | `--pupil-containment-min`           | `PUPIL_CONTAINMENT_MIN`              | 0.90    | 0–1       |

This panel uses the name-as-key shape (`name` `pupil-eye-inflation` ↔
`--pupil-eye-inflation`); all three keys and defaults match argparse — the checker
reports ✅.

## Notes
- CO sends parameter values as strings; argparse `type=float`/`int` converts them.
- Ranges (`minimum`/`maximum`) are UI-only — argparse does not clamp; validate in code if needed.
- Edit the panel in the CO UI (valid schema), pull, then run the checker — don't hand-author `app-panel.json`.
- Related skills: **codeocean-run-capture** (run capsule + capture results),
  **codeocean-data-assets** (attach/detach/search assets).
