# claude-code-skills-codeocean

Shared [Claude Code](https://claude.com/claude-code) **skills** for driving Code Ocean
(AIND deployment) from inside a capsule. Drop this repo into any capsule at
`code/claude-code-skills/` and Claude Code discovers every `*/SKILL.md` under it.

## Skills

| skill | what it does |
|-------|--------------|
| **codeocean-run-capture** | Run a CO capsule/pipeline with attached data assets and capture the results as a named, tagged data asset. Includes per-session / per-subject batch orchestration (`run_per_session.sh`, `run_per_subject.sh`) and a server-side pipeline-monitor mode. |
| **codeocean-data-assets** | Search, **attach** (mount into the current computation's `/data` — immediate, no restart), and **detach** CO data assets by id / tag / name / subject. |
| **codeocean-app-panel** | Author & validate a capsule's App Panel (`.codeocean/app-panel.json`) and keep its parameters in sync with `code/run_capsule.py` argparse (static checker — every panel param's key must map to an argparse `--flag`). |

Each skill is a folder with a `SKILL.md` (the instructions Claude reads) plus a
`scripts/` directory with the underlying Python/bash tools you can also run by hand.

## Prerequisites

- `pip install codeocean`
- API token in the environment (checked in order): `$CODEOCEAN_TOKEN`, `$API_SECRET`,
  `$CO_TOKEN`, `$CUSTOM_KEY` — or pass `--token`.
- Domain from `$CODEOCEAN_DOMAIN` or `--domain` (default: the AIND deployment,
  `https://codeocean.allenneuraldynamics.org`).

## Use in a capsule

Recommended — add as a git submodule so every capsule shares one source of truth:

```bash
git submodule add <this-repo-url> code/claude-code-skills
git submodule update --init --recursive          # in a fresh clone of the capsule
```

(Or clone / symlink into `code/claude-code-skills/`.) To update the skills everywhere
later: `git -C code/claude-code-skills pull` and commit the new submodule pointer.

## Quick reference

```bash
S=code/claude-code-skills
python $S/codeocean-data-assets/scripts/co_data_assets.py attach --asset <id>
python $S/codeocean-run-capture/scripts/co_run_capture.py run --capsule-id <id> --data-asset <id>[:mount] --wait --capture --result-name <name> --tag <t>
python $S/codeocean-app-panel/scripts/check_app_panel.py <capsule_dir>
```
